# -*- coding: utf-8 -*-
"""P10（2.1.6 单次 reserve 写窗）keyed write window 测试候选。

candidate6（c5 全量继承＋run3 3F/0E 定因修；主控指令三项）。run3 实况：
T1 采样器 seen=None（2793 样本零 BUSY）、S2/T4b 停点双检出但 ok=True。
本机复跑闭环定因（scripts/park_probe.py＋逐用例复跑）：① **停点检出信号
错锚**——collab RESERVED 是 manager __enter__ 即持的 B1 屏障（恒真全窗
口）：以 collab busy 作检出（S1/S2）在 worker 刚 enter、尚处 plan 前即
触发；以 owner busy 作检出（T4b park2）会命中 attempt-1 残留（清理逆序
关闭前 owner 仍 BUSY）→两次注入全落 attempt-1→被 attempt-2 起点的
sync_catalog/新 plan 吸收为基线→重验一致→ok=True（run3 S2-park2-
detected marker=4、T4b-park2 collab_busy=True 实锤）。② **T1 固定 0.3s
编排竞态**——单跑/顺序跑 PASS、run3 负载下 miss：采样器在门释放后才启
动＝只在「停点→扫描开始」的 retry 尾窗内可捕，负载下错过即零捕获（c5
采样器首命中即 return 的早退语义雪上加霜）。

c6 修法（主控指令逐条；断言语义与产品零改动，c5 的 holder DELETE 夹具
门/非静默清理/自愈/守卫排干全部保留）：
1. **停点统一锚定 accounts 门**（``_p10_wait_park``）：产品事实＝
   reserve_candidates 序 [collab, owner, accounts(users.db), actor,
   members, witnesses]（adata 排序事实使 users.db 恒为 others 首位）。
   停点＝E_a（users.db BEGIN IMMEDIATE RESERVED）＋「owner RESERVED
   free→busy 转移」（唯一非恒真外部可观测：worker 本 attempt 的 reserve
   已过 owner、必停在下一位 accounts 的 BEGIN IMMEDIATE busy handler
   内）；users.db 外部探针 BUSY 作伴随双锚（门在持＝停点成因核验——
   门在持期间恒 BUSY，不作相位信号）。**禁用 collab busy 作检出**（恒
   真）。park2/重臂（``_p10_rearm_accounts_gate``）：先观测 users busy
   （worker 抢回 attempt-1 accounts）→users free（attempt-1 清理已闭
   accounts）→自旋重抢 E_a（attempt-2 到 accounts 前有 sync+plan+
   prepare 数百 ms 余量）→owner free→busy 转移＝attempt-2 停点。
   T2/T4b/T5/S1/S2 全部停点统一改此原语（T4a 见修法 4）。
2. **T1 采样器连续窗口**：collab_begin 检出后**立即** armed（E_b 门释
   放前、plan 扫描开始前；stats 行打印 armed 证据），采样窗=
   collab_begin→owner_busy=True（reserve 完成信号，主线程观测后
   stop_event 收尾）；**seen 只认 commit 相 BUSY**（BEGIN IMMEDIATE 成
   功而 UPDATE/COMMIT 阶段 OperationalError＝EXCLUSIVE 升级被活动
   SHARED 阻挡＝mech D1 实证；门/保留连接的写锁 BUSY 记 begin_busy 不
   算证据），命中后不再早退（连续观测至窗口闭合，c5 首命中即 return
   的早退删除）。
3. **S1/S2 注入向量改 marker 侧偏移**（``_p10_skew_owner_marker``）：
   账号停点上 owner 已被 worker 保留连接持有写锁（写-写互斥）——对
   owner 的 bump 会与门互等 15s 超时；actor（bob.db）排序在 accounts
   之后、停点时未 reserve，可无竞争写入。偏移 marker（+7）→产品重验
   ``_member_marker_revision``（marker != owner 当前 revision）同走
   _CatalogMarkerDrift 支：S2 停点 1 偏移→attempt-1 重验漂移→attempt-2
   sync 修复→停点 2 再偏移→重验再漂移→耗尽 409 catalog_sync_stale
   （attempt-2 基线=plan 读、注入=停点施加、重验=读当前——409 随停点
   正确自然恢复）。T4b 换 holder 副本（v2/v3）与 T2 损坏副本同理。
4. **T4a 检出锚定 plan 扫描本身**：E_b 门＋collab 检出弃用——采样器以
   修法 2 语义连续观测 holder，**commit 相 BUSY＝plan 发现环对 holder
   的真实 SHARED 读取进行中**（resolve 身份头点查或 seal 扫描），检出
   即在扫描中段换入 owner 字节副本（结构上必在 _prepare_inputs 的
   owner 身份复查之前）→typed owner_db_unreadable。夹具扩为 60 键 60
   seal（扫描窗数百 ms ≫ 1ms 检出+os.replace，确定性余量；park_probe
   实测扫描窗 100-375ms）。

诊断打印保留为常备观测（_p10_diag 前缀，纯 print 零流改变）。

目标产品候选（本文件唯一针对的实现；SHA 已在箱外独立核验一致）：
- E:/rf216-scratch/p10-write-window-20261119T050358Z-3754/candidate/
  team_screening_write.py
  SHA-256 ac458c23ce67e5d36c367b095ece10bbaa2c52c26ea6267973ebc879001b037a
- 同箱 candidate/team_screening_runtime.py
  SHA-256 f228121557569c35b2d3b7155969386fbd9fd81773a966c205809133fd23acde
- 同箱 candidate/team_keyed_reads.py
  SHA-256 32a7143255c50d6f92ae163fe5e3117989ccb620ae3824b0aea78ff4874f81c0
- 已过 g-flash 复审 PASS 10/10。

装载协议（P03 candidate4 配方，三文件同用；scripts/loader_probe.py 实测）：
主仓根 E:/mubi-reviewflow 插入 sys.path 供 coscreen 全族与 custom_backend
依赖解析；候选副本经箱根/candidate/ 以 importlib 文件位置加载，exec_module
前先注册 sys.modules[全名 stem]，失败路径 identity-guard 回滚再原样上抛；
装载顺序 runtime → keyed_reads → write（后两者 import 前者必须命中候选）。
装载先于任何其他 custom_backend.* 导入；review_workflow_api:492 的惰性
team_screening_runtime 引用因此命中候选。运行解释器必须 -I -B 且 TEMP/TMP
指向测试箱 tmp（不向只读主仓写 __pycache__）。

用例 → 合同映射（5 具名用例，工单钉死名称逐字）：
- EnterPlanCloseReserveOrderTests.test_enter_plan_close_reserve_order
  断言锚 ``events.index('collab_begin')<events.index('plan')<
  events.index('readers_closed')<events.index('reserve')``。**真实锁实证**
  （包规格明文：不能靠 spy 顺序代替锁实证；本文件零 mock/零猴子补丁）：
  ① worker 线程真实进入写窗；② 'collab_begin'＝外部探针连接对 collab
  ``BEGIN IMMEDIATE`` 实测 BUSY（RESERVED 真实在持且横跨整个 plan 段——
  观测发生在 worker 被 E_bob 门挡住的 plan 中途）；③ 'plan'＝**collab_begin
  检出后立即 armed 的预连接高频采样器**（连接在 plan 扫描开始前建好；采样
  窗＝collab_begin→owner_busy；timeout=0 的
  BEGIN IMMEDIATE→UPDATE→COMMIT 循环采样锁状态）实测 **commit 相 BUSY**
  ——BEGIN IMMEDIATE 成功而 COMMIT 阶段 OperationalError＝只有 plan 的
  holder 临时 reader 真实持有 SHARED 才会挡 COMMIT 的 EXCLUSIVE 升级
  （mech_probe D1；E_bob 门持锁期的写锁 BUSY 记 begin_busy、不算证据；
  SHARED 为事务级锁覆盖整个 60 键扫描≈数十-数百 ms ≫ 采样间隔 0.5-2ms
  ——run_216 修复 2＋c6 修法 2，论证见 _p10_shared_sampler_main）；
  ④ 'readers_closed'＝同一写探针成功（SHARED
  已释放、库可写）；⑤ 'reserve'＝owner ``BEGIN IMMEDIATE`` 探针实测 BUSY
  （B1 reserve 真实开始；accounts 门（E_a RESERVED）使 worker 确定性停在
  reserve 中途）；⑥ 停点快照：collab/owner RESERVED 仍在持＋尚未 reserve 的
  witness holder 零打开句柄（os.replace 成功；采样器已 stop+join，其连接
  不计入句柄面）＋gc 活连接计数恰等于
  基线（含两门）−已释放门＋collab＋owner＋被门挡在 BEGIN IMMEDIATE 上的
  accounts 保留连接（该连接对象已开且可达，计入）。
- NoSecondReserveOnWitnessFailureTests.test_no_second_reserve_on_witness_failure
  plan→reserve 真实竞态窗（accounts 门停点＝``_p10_wait_park``：owner
  free→busy＋users 门在持双锚）把 holder 换成 payload_json 损坏
  副本（DROP append-only 触发器→UPDATE 损坏→按产品常量原样重建触发器；F1
  布局门仍过——mech F 实测）：最终重验 read_seal_on 抛
  TerminalFactsUnavailable(corrupt)→typed completion_source_unresolved 直接
  上抛（**非** 409 漂移类、无重算）；B1 每 manager 单次 reserve 以
  「accounts 门停点计数＝1」锚定（到达 reserve 必经门，每尝试恰一停）＋
  异常身份非 HTTPException＋原异常经 __cause__ 链保留。
- OwnerTargetNoSelfSharedTests.test_owner_target_has_no_self_shared_lock
  owner actor 健康窗：yield 体内 gc 活连接计数恰＝基线＋保留集（无游离
  reader）；os.replace 恰在保留集文件上 PermissionError、decoy 文件可换名
  （零句柄）；目标 COMMIT 真实成功且业务行持久化——自持 SHARED 会挡
  EXCLUSIVE 升级使窗失败，三重实证 owner 提交时无本进程 SHARED。
- RetainedFileReplacementTests.test_retained_file_replacement_rejected
  (a) plan 扫描段（采样器 commit 相 BUSY＝发现环对 holder 的 SHARED 读取进
  行中）换 owner.db 字节副本（新 inode）→typed
  owner_db_unreadable（物理身份变化，拒绝重建/顶替；结构上必在
  _prepare_inputs 的 owner 身份复查前）；(b) 竞态窗两次替换
  witness holder（v2/v3 各持不同 quadruple 的合法 seal；停点＝accounts 门
  ``_p10_wait_park``，park2 经 ``_p10_rearm_accounts_gate`` 消除 attempt-1
  残留错检）→尝试 1 四元组漂移
  →全量重算→尝试 2 新 plan 又见证 v2→再被 v3 漂移→409 write_plan_drift
  三键载荷；门停点计数＝2 锚定两次完整尝试。
- ConditionalDependencyDriftTests.test_conditional_dependency_drift_restarts_before_writer
  v1 双见证键 K1/K2→竞态窗换 v2（K1 四元组漂移＋K2 seal 缺席）→尝试 1 漂移
  重算；尝试 2 新 plan：K1 见证 v2、K2 干净负发现→live_dependencies＝
  {(bob_uid,'stage1'):('K2',)}→必需成员库 bob.db/carol.db 入保留集（体内
  os.replace 双双 PermissionError＋gc 计数＋5 实证）；重验一致→writer 前完
  成→恰一次 yield→目标提交持久。
支撑用例（3 个，先例允许 2-3）：
- MarkerDriftSharedRestartTests.test_marker_drift_restarts_via_shared_handler
  漂移二类共用重算：非属主 actor 经真实 sync_shared_articles 建 marker 后，
  尝试 1 的 accounts 门停点偏移 actor marker（owner 在停点上已被保留连接
  写锁持有——bump 会与门互等；actor 排序在 accounts 后、停点时无竞争）→
  _CatalogMarkerDrift→尝试 2 真实 sync 修复 marker→成功；accounts 门停点
  计数＝2 锚定重算真实发生，marker 终值＝owner 当前 revision。
- CatalogSyncStale409Tests.test_catalog_sync_stale_409_payload
  尝试 2 的 accounts 门停点再偏移 marker→marker 二次漂移→409
  catalog_sync_stale 三键载荷钉定（write_plan_drift 载荷由具名用例 4(b)
  钉定）。
- TeamWriteInputsShapeTests.test_team_write_inputs_new_field_shapes
  合同驱动形状：签名参数序 [runtime,info,user,target_kind,operation,
  item_keys]（尾三 keyword-only）；TeamWriteInputs 字段含 operation/
  item_keys/plan；frozen；cleanup_errors 只读 property；健康窗内身份锚
  （inputs.operation is 传入 operation；inputs.plan is scope.keyed_plan()）。

夹具设计（零 mock；全部真实 SQLite 文件/真实产品 API；参照 P09 测试箱）：
- collab＝真实 initialize_task_store＋set_business_role＋F2 prepare＋
  register_protocol_on（trusted_new_task）＋F1 prepare 同文件共存（ddl_scan
  组合扫描零冲突）；seal 经真实 append_seal_on（v2 observation payload：
  holder 库 origin='actor'）。
- owner/成员全库＝真实 db.init_db＋review_workflow.migrate＋articles 种子＋
  latch（静态 SQL 已预检；decisions 对 articles 有 FK——body 写键 K9 先种）。
- holder 库＝真实 db.init_db 安装 P04 ledger（产品 _META_DDL＋种子行；
  run_216 定因修复 3：手写 meta DDL 与产品文本不符→
  library_identity_header_on meta_corrupt→来源解析失败→plan 对 holder 从
  不扫描/不见证/不产生 live 依赖——T5 live={}、T1 无 SHARED 可捕的同根）
  ＋F1 prepare＋确定性 library upsert（v1/v2/v3 同 library 保 F2 token 跨
  版本可解析——t5_plan_probe 实证 plan-2 live={(bob,'stage1'):('K2',)}）
  ＋p10_gate 夹具表（F1 布局门只核保留名集合，无关表不可见——mech/ddl
  实测）；F2 登记＝真实 team_source_lifecycle.make_source_identity＋
  register_source_on。
- accounts＝真实 auth.create_user（bcrypt；类级箱根 adata/ 目录——该目录名
  使 users.db 在 B1 others 规范路径排序中确定先于任务目录内库（ddl_scan
  排序事实核查），accounts 门因此是 reserve 的第一个 other，停点后其余输入
  必然尚未 reserve）。
- runtime＝真实 WorkflowRuntime＋真实 context/lock/sync_catalog；task_lookup
  为返回真实 TaskInfo 的宿主载体（生产由 main._task_or_404 提供）；
  screener_db 为复刻 main._screener_db 语义的真实宿主装配（slug 路径＋幂等
  init_db）。
- 门（全部真实 SQLite 锁，mech_probe 实测语义）：E_a＝accounts RESERVED
  （BEGIN IMMEDIATE 持有不写：不挡 SHARED 读、挡 B1 reserve 的
  BEGIN IMMEDIATE）；E_b/E_c＝holder 大 blob 事务（>页缓存强制 spill 升
  EXCLUSIVE，挡 plan 的**新**读者）。worker 线程跑窗、主线程门控/停点/
  换文件/探针——真实 native interleaving。

SQL 预检纪律（P07 教训）：本文件全部静态 SQL 常量（12 条＋一条夹具 DDL）已
由 scripts/ddl_scan.py 在真实 schema（组合库）上逐条 EXPLAIN+EXEC 验证；DDL
组合扫描（P11 教训）断言四个组合库对象名零冲突、列核对齐（报告附表）。

诚实记录（机制实测项，不伪造测试）：
1. 未提交小写不升 EXCLUSIVE（mech 实测推翻直觉）：挡读者改用大 blob spill
   门；SHARED 存在性用 COMMIT 探针（COMMIT 才需 EXCLUSIVE）。run_216 实跑
   推翻候选 1/2 的主线程重试环（connect 开销落入关键窗＋timeout 内 busy
   重试可能把「曾在」吞成「成功」——mech D2）→ 修复 2 改**预连接采样器
   线程**：连接先建、timeout=0 即刻读锁态（不等待），
   0.5-2ms 间隔 ≪ 60 键扫描窗（数十-数百 ms；park_probe 实测 100-375ms）；
   **c6 修法 2**：armed 前移至 collab_begin 检出后（不等停点/释放门）、
   采样窗延至 owner_busy、**seen 只认 commit 相 BUSY**（begin 相＝写者在
   持——门或保留连接——非 SHARED 证据）、命中不早退；run3 教训＝门释放后
   才启动＋首命中早退在负载下错过整个扫描窗。仍未捕＝诚实红。
2. 重臂竞态（T4(b)/S1/S2 的第二次停点）：c5 的「manager-1 清理释放与主
   线程重臂自旋抢锁」在 c6 显式序列化（``_p10_rearm_accounts_gate``）：先
   观测 users busy（worker 抢回 attempt-1 accounts——若主线程先重抢则
   worker 永远停在旧门上、park2 的 owner free→busy 永不出现＝诚实红，故
   busy→free 转移是重抢的前置条件）→观测 users free（attempt-1 清理已闭
   accounts——逆序关闭序 […, witnesses, members, actor, accounts, owner,
   collab]）→自旋重抢 E_a（attempt-2 到 accounts 前有 sync＋enter＋plan＋
   prepare＋owner reserve 数百 ms 路径余量）→park2 以 owner free→busy
   转移检出（消除 c5 命中 attempt-1 残留 owner BUSY 的错检——run3 T4b/S2
   无 409 的直接根因）。
3. B1 自开连接无 factory 注入口（P09 诚实记录同型）：语句面不在本包断言
   面；锁/句柄/gc 三轨覆盖 P10 合同（固定窗口序＋单次 reserve＋零自持
   SHARED）。
4. 类级 users.db（bcrypt 建号耗时）与箱 adata/ 复用；各用例任务夹具独立
   TemporaryDirectory（TEMP 指箱 tmp）。run_216 泄漏级联教训（修复 1）：
   worker 未在失败路径排干→保留连接滞留→用例临时目录 rmtree WinError32→
   adata 静默滞留→后续类 setUpClass AuthError——三层收口：每门 finally/
   外层守卫 join（幂等）、adata 收尾非静默（重试＋显式红）、setUpClass
   先自愈硬清残留。
5. 损坏副本构造绕 append-only 触发器：DROP→UPDATE 损坏→按产品常量
   tf.TRIGGER_NO_UPDATE_DDL 原样重建（布局门核对象集——mech F 实测通过）。
6. run_216 T5 定因（修复 3，t5_plan_probe 实证）：手写 P04 meta DDL 与产品
   _META_DDL 文本不符→library_identity_header_on meta_corrupt→
   resolve_source_identity library_unverifiable→plan 发现环 continue 于
   扫描前（无 SHARED/无见证/无 live 依赖/selected_paths 空）——T5 尝试 1
   无漂移直接成功（live={}＝观测值）且 T1 采样器无 SHARED 可捕为同根。
   修复：holder 经真实 db.init_db 安装 ledger＋确定性 library upsert。
7. c6 定因实测记录（scripts/park_probe.py，artifacts/park_probe.log）：
   (a) **事务活动期 ``PRAGMA journal_mode=DELETE`` 静默 no-op 返回旧值**
   （首跑 set=wal read=wal）——c5 夹具的 DML 先 commit 再翻 journal 的
   顺序因此是硬前提，绝不可把翻转挪到 commit 前（WAL 下门/采样器的锁证
   据面全失效且无报错）；(b) DELETE 模式下 spill 门（4MB blob>2MB 页缓存）
   确实阻塞产品形态（mode=ro＋timeout=15）的 plan 读者（读者 park 在
   SHARED 获取、门释放后 ≤~200ms 获得并扫描 60 seal 行 100-375ms）——
   run3 T1 miss 非「门不阻塞」而是「采样器启动晚于扫描窗」；(c) c5 T1
   单跑/两用例顺序跑 PASS、全套件负载下 miss（本机复现 run3 的 S2/T4b
   两红、T1 竞态偶现）＝c6 连续窗口修法的直接依据。

撰写协议：本文件每个 def/class 符号（含 __init__/__call__；**无 lambda、无
内嵌 def**——scripts/selfcheck_ast.py AST 自检钉住）在写入前均逐项运行
GitNexus CLI upstream impact（build/tests 不入索引→预期 not found；c6 增量
符号与被改符号 raw 存档箱 impact-raw/c6/，c5 全量清单见 impact-raw/）。
c6 未触碰 build/tests/test_s2b_team_write_inputs.py（c5 已定型，无新改动）。

运行声明：c6 作者在本机复跑了 c5（单跑/顺序跑/全套件——run3 三红中
S2/T4b 稳定复现、T1 竞态偶现）与 park_probe 机制探针定因；c6 改后同法
本机实跑全套件验证（见 candidate6/REPORT.md）。正式判定仍留给独立 runner
（应用产品候选后，TEMP 指箱 tmp 运行
E:/rf216-scratch/validation-venv312/Scripts/python.exe -I -B
<箱>/build/tests/test_216_keyed_write_window.py -v）。禁主仓/前端/真实
data/8613/旧证据箱写入；夹具全部位于测试自有 TemporaryDirectory 或箱
adata/；零 mock（无替换对象/无猴子补丁）；自包含（仅依赖 unittest＋主仓＋
箱候选）。
"""

from __future__ import annotations

import dataclasses
import gc
import hashlib
import importlib.util
import inspect
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

_P10_BOX_ROOT = Path(__file__).resolve().parents[2]
_P10_WRITE_SOURCE = _P10_BOX_ROOT / "candidate" / "team_screening_write.py"
_P10_RUNTIME_SOURCE = _P10_BOX_ROOT / "candidate" / "team_screening_runtime.py"
_P10_READS_SOURCE = _P10_BOX_ROOT / "candidate" / "team_keyed_reads.py"
_P10_REPO_ROOT = Path("E:/mubi-reviewflow")
if not _P10_REPO_ROOT.is_dir():
    raise RuntimeError(
        "P10 测试需要主仓 E:/mubi-reviewflow（候选 import coscreen 全族与 "
        "custom_backend 依赖）；主仓缺位时本文件拒绝静默降级")
for _p10_source in (_P10_WRITE_SOURCE, _P10_RUNTIME_SOURCE, _P10_READS_SOURCE):
    if not _p10_source.is_file():
        raise RuntimeError(
            "P10 测试需要箱内候选副本（箱根/candidate/ 三文件）。缺位时本文件"
            "在模块加载期诚实红（集成时由协调者迁移加载路径）")
if str(_P10_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_P10_REPO_ROOT))


def _p10_load_candidate_module(stem, path):
    """P03 candidate4 装载配方（三文件同用）：exec_module 前注册 sys.modules
    ＋identity-guard 回滚。

    dataclass/``from X import Y`` 在未注册 stem 下 exec 会因模块对象不在
    sys.modules 而炸（P11 教训），故先注册；执行失败时回滚注册（仅当仍为本
    模块对象——identity guard）再原样上抛。同名已注册＝装配错误，拒绝静默
    顶替（候选必须先于任何 custom_backend.* 导入装载）。"""
    previous = sys.modules.get(stem)
    if previous is not None:
        raise RuntimeError(
            "P10 loader collision: %s already registered (%r)" % (stem, previous))
    spec = importlib.util.spec_from_file_location(stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("P10 spec load failed: %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if sys.modules.get(stem) is module:
            del sys.modules[stem]
        raise
    return module


tsr = _p10_load_candidate_module(
    "custom_backend.team_screening_runtime", _P10_RUNTIME_SOURCE)
tkr = _p10_load_candidate_module(
    "custom_backend.team_keyed_reads", _P10_READS_SOURCE)
tws = _p10_load_candidate_module(
    "custom_backend.team_screening_write", _P10_WRITE_SOURCE)

from coscreen import db as dbm  # noqa: E402
from coscreen import review_workflow as wf_mod  # noqa: E402
from coscreen import task_collaboration as collab_mod  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from coscreen import team_terminal_facts as tf  # noqa: E402
from coscreen.team_terminal_facts import TerminalFactsUnavailable  # noqa: E402
from coscreen import team_terminal_registry as tr  # noqa: E402
from coscreen.team_flow_types import KeyedReadPlan, TeamOperation  # noqa: E402
from fastapi import HTTPException  # noqa: E402
import custom_backend.auth as auth  # noqa: E402
from custom_backend.review_workflow_api import WorkflowRuntime  # noqa: E402
from custom_backend.team_source_lifecycle import make_source_identity as tsl_make_source_identity  # noqa: E402

#: 冻结候选 SHA-256（运行时打印实测值供核对；硬断言会制造候选演进假红——
#: P08/P09 先例。装载可行性探针已做硬核对）。
P10_WRITE_CANDIDATE_SHA = (
    "ac458c23ce67e5d36c367b095ece10bbaa2c52c26ea6267973ebc879001b037a")
P10_RUNTIME_CANDIDATE_SHA = (
    "f228121557569c35b2d3b7155969386fbd9fd81773a966c205809133fd23acde")
P10_READS_CANDIDATE_SHA = (
    "32a7143255c50d6f92ae163fe5e3117989ccb620ae3824b0aea78ff4874f81c0")

#: 时间戳字面量（F1 sealed_at 毫秒 Z 形态；T4/T5 各版本 seal 以此区分
#: quadruple）。
P10_TS1 = "2026-11-19T09:00:00.000Z"
P10_TS2 = "2026-11-19T09:30:00.000Z"
P10_TS3 = "2026-11-19T10:00:00.000Z"
P10_VOTE_TS = "2026-11-18T09:00:00+00:00"

#: owner（collab 权威域）library_id——32hex 常量（F2 protocol 行与 payload
#: task.owner_library_id 同值）。
P10_OWNER_LIBRARY = "3c" * 16

#: 账号名与口令（真实 auth 建号；uid 以真实库行为唯一事实源）。
P10_USER_NAMES = ("owner", "bob", "carol")
P10_PASSWORD = "p10-pass-31337"

#: 类级共享账号映射（真实 auth 注册；setUpClass 填充）。
USERS = {}

#: provenance 打印哨兵（每进程一次——P08/P09 先例）。
_P10_PROVENANCE_PRINTED = False

#: 大 blob（4MB > 默认页缓存）：门事务强制 cache_spill 升 EXCLUSIVE
#: （mech_probe C 实测）。
_P10_GATE_BLOB = "x" * (4 * 1024 * 1024)

# ---------------------------------------------------------------------------
# 静态 SQL 常量（全部经 scripts/ddl_scan.py 真实 schema EXPLAIN+EXEC 预检；
# 禁止臆造表列——来源 coscreen.db / team_terminal_facts / auth schema）。
# ---------------------------------------------------------------------------

_P10_INSERT_ARTICLE = (
    "INSERT INTO articles (zotero_key, title, is_duplicate_of, import_order)"
    " VALUES (?, ?, NULL, ?)")
_P10_INSERT_STAGE1_VOTE = (
    "INSERT INTO decisions (zotero_key, decision, exclusion_reason, notes,"
    " tags, screened_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)")
_P10_LATCH = (
    "UPDATE workflow_state SET team_latched=1 WHERE singleton=1")
#: c6 修法 3：S1/S2 的漂移注入向量＝偏移 actor 侧 catalog marker（停点上
#: owner 已被 worker 保留连接持写锁，bump owner 会与 accounts 门互等 15s
#: 超时；actor 排序在 accounts 之后、停点时无竞争可写）。+7 使 marker !=
#: owner 当前 revision→产品重验 _member_marker_revision 同走
#: _CatalogMarkerDrift 支（与 bump owner 殊途同归）。
_P10_SKEW_MARKER = (
    "UPDATE workflow_catalog_sync SET revision=revision+7"
    " WHERE owner_identity=?")
_P10_TOUCH_GATE = (
    "UPDATE p10_gate SET v=? WHERE k=1")
_P10_DROP_NO_UPDATE = (
    "DROP TRIGGER team_terminal_seals_no_update")
_P10_CORRUPT_SEAL = (
    "UPDATE team_terminal_seals SET payload_json=? WHERE seal_id=?")
_P10_COUNT_DECISIONS = (
    "SELECT COUNT(*) FROM decisions WHERE zotero_key=?")
_P10_SELECT_CATALOG_REVISION = (
    "SELECT catalog_revision FROM workflow_state WHERE singleton=1")
_P10_SELECT_SEAL_ID = (
    "SELECT seal_id FROM team_terminal_seals WHERE item_key=?")
_P10_SELECT_MARKER = (
    "SELECT revision FROM workflow_catalog_sync WHERE owner_identity=?")
_P10_UPSERT_LIBRARY = (
    "INSERT INTO team_vote_revision_meta (singleton, format_version,"
    " library_id, seeded_stage1, seeded_stage2, advances)"
    " VALUES (1, 2, ?, 0, 0, 0) ON CONFLICT(singleton) DO UPDATE SET"
    " library_id=excluded.library_id")
_P10_SELECT_SEAL_QUAD = (
    "SELECT seal_id, content_sha256, payload_sha256, sealed_at"
    " FROM team_terminal_seals WHERE item_key=?")
_P10_JOURNAL_PRAGMA = (
    "PRAGMA journal_mode")
_P10_SET_JOURNAL_DELETE = (
    "PRAGMA journal_mode=DELETE")

#: 夹具 DDL（ddl_scan 组合扫描同款）：门表（F1 布局门只核保留名，无关表
#: 不可见——mech/ddl 实测）。P04 身份头表**不手写**（run_216 定因：手写
#: DDL 与产品 ``team_vote_ledger._META_DDL`` 文本不符 →
#: ``library_identity_header_on`` meta_corrupt → 来源解析失败）；holder 库
#: 改经真实 ``db.init_db`` 安装 ledger 后以 _P10_UPSERT_LIBRARY 覆写确定性
#: library（v1/v2/v3 同 library 保持 F2 token 跨版本可解析）。
P10_GATE_DDL = (
    "CREATE TABLE p10_gate (k INTEGER PRIMARY KEY CHECK(k=1), v TEXT)")

#: F1 payload canonical JSON 镜像（独立重实现，钉 F1 合同序列化参数——
#: P08/P09 先例）。
_P10_JSON_KW = {"ensure_ascii": False, "sort_keys": True,
                "separators": (",", ":"), "allow_nan": False}


def _p10_sha256_file(path):
    """文件字节哈希（provenance 与换文件内容核对的观测锚）。"""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _p10_close_quietly(conn):
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


def _p10_borrowed_writer(path):
    """真实产品写连接形态（F2 登记/F1 seal 的 append 入口要求）：
    native＋Row 工厂＋活动 IMMEDIATE 事务。"""
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _p10_member_library_id(uid):
    """payload 内来源 library_id 的确定性 32hex 形态（P08 镜像）。"""
    return "%032x" % uid


def _p10_default_roster():
    """F1 payload 名册（历史快照形态）：owner=reconciler 非必需筛选人；
    bob/carol=coder 必需筛选人（P09 同构、账号 uid 经真实 USERS 映射）。"""
    return [
        {"uid": USERS["owner"], "username": "owner", "role": "reconciler",
         "is_owner": True, "screening_enabled": False,
         "required_screener": False, "source": "owner-source", "config": "ok"},
        {"uid": USERS["bob"], "username": "bob", "role": "coder",
         "is_owner": False, "screening_enabled": None,
         "required_screener": True, "source": "import-source", "config": "ok"},
        {"uid": USERS["carol"], "username": "carol", "role": "coder",
         "is_owner": False, "screening_enabled": None,
         "required_screener": True, "source": "grant-source", "config": "ok"},
    ]


def _p10_make_opinion(uid, decision):
    """单个必需筛选人意见（opinion/source_row 哈希按 F1 合同算法独立重算，
    P08/P09 镜像）。"""
    opinion = {
        "uid": uid, "decision": decision, "exclusion_reason": "", "notes": "",
        "library_id": _p10_member_library_id(uid), "revision": uid + 10,
        "source_row": {"tags": "tag-%d" % uid,
                       "screened_at": "2026-01-02 03:04:05",
                       "updated_at": None if uid % 2 else "2026-01-03 04:05:06"},
        "opinion_sha256": "", "source_row_sha256": "",
    }
    opinion["opinion_sha256"] = hashlib.sha256(json.dumps(
        {"decision": decision.strip().lower(), "exclusion_reason": "",
         "notes": ""}, **_P10_JSON_KW).encode("utf-8")).hexdigest()
    opinion["source_row_sha256"] = hashlib.sha256(json.dumps(
        opinion["source_row"], **_P10_JSON_KW).encode("utf-8")).hexdigest()
    return opinion


def _p10_observation_payload(*, task_id, instance, stage, item_key, decision,
                             sealed_at, registration_prefix):
    """v2 observation seal payload（append origin='actor'/'collab' 均接受；
    同参确定性→同 seal_id——P09 箱已验）。required_uids/绑定覆盖
    required∪observer；observer=owner，其绑定 registration_id 与
    protocol.source_registration_id 对齐（v2 合同：两者必须一致）。"""
    roster = _p10_default_roster()
    required_uids = sorted(entry["uid"] for entry in roster
                           if entry["required_screener"])
    opinions = [_p10_make_opinion(uid, decision) for uid in required_uids]
    owner_uid = USERS["owner"]
    bindings = [
        {"source_user_id": uid,
         "registration_id": "%s-%d" % (registration_prefix, uid),
         "generation": 1, "source_library_id": _p10_member_library_id(uid)}
        for uid in sorted(set(required_uids) | {owner_uid})
    ]
    return {
        "schema": tf.OBSERVATION_PAYLOAD_SCHEMA,
        "task": {"task_id": task_id, "task_instance_id": instance,
                 "owner_user_id": owner_uid, "owner_username": "owner",
                 "owner_library_id": P10_OWNER_LIBRARY},
        "stage": stage, "item_key": item_key, "round_id": None,
        "kind": "unanimous", "trigger": "current_observation",
        "final_decision": decision, "roster": roster,
        "required_uids": required_uids, "opinions": opinions,
        "arbitration": None,
        "protocol": {"source_registration_id":
                     "%s-%d" % (registration_prefix, owner_uid),
                     "roster_revision": 7},
        "source_request": {"operation": "completion_observe",
                           "request_id": "request-p10-%s" % item_key,
                           "request_sha256": "b" * 64},
        "sealed_at": sealed_at,
        "observation": {"host_operation": "completion_observe",
                        "host_stage": "fulltext", "host_item_keys": [item_key],
                        "observed_by_user_id": owner_uid,
                        "basis": "current_snapshot"},
        "source_bindings": bindings,
    }


def _p10_restore_env(key, previous):
    """TRACKED_ENV 还原（auth 建号窗口的 COBOOKSHELF_DATA_DIR；P09 先例）。"""
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous


def _p10_visible_true(info, user):
    """宿主 visible 钩子（真实函数；owner 绑定存在时不触达）。"""
    return True


def _p10_screener_db(info, screener):
    """宿主 screener_db 装配（复刻 main._screener_db 的路径/懒建语义，去掉
    HTTP 撞名分支——夹具无撞名）：slug 路径＋幂等 init_db。"""
    slug = tasks_mod.slugify_screener(screener)
    db = info.dir / ("%s.db" % slug)
    dbm.init_db(db)
    return db


def _p10_replaceable(path):
    """Windows 句柄锁探针（P08 A3-A5 实测机制）：任何打开句柄都令
    os.replace PermissionError；操作本身即断言（泄漏即诚实红）。"""
    moved = Path(str(path) + ".p10probe")
    os.replace(path, moved)
    os.replace(moved, path)
    return Path(path).is_file()


def _p10_count_live_connections():
    """gc 反射计数 sqlite3.Connection（P08 同法；先 gc.collect 清不可达
    循环，观测可达驻留引用）。"""
    gc.collect()
    count = 0
    for obj in gc.get_objects():
        if isinstance(obj, sqlite3.Connection):
            count += 1
    return count


def _p10_open_reserved_gate(path):
    """E_a 门：目标库上持有 RESERVED（BEGIN IMMEDIATE，不写——不挡 SHARED
    读、挡随后 B1 reserve 的 BEGIN IMMEDIATE；mech A/B 实测）。"""
    conn = sqlite3.connect(str(path), timeout=1.0)
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _p10_open_pending_gate(path):
    """E_b/E_c 门：目标库上大 blob 写事务（>页缓存强制 spill→EXCLUSIVE，
    挡**新的**外部读者即 plan 的 holder 读；mech C 实测）。释放走
    _p10_close_quietly（ROLLBACK 丢弃 blob，库字节不变）。"""
    conn = sqlite3.connect(str(path), timeout=1.0)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(_P10_TOUCH_GATE, (_P10_GATE_BLOB,))
    return conn


def _p10_collab_busy(path):
    """RESERVED 探针：外部连接 BEGIN IMMEDIATE(timeout=0) 即 BUSY→True；
    成功（回滚收尾）→False。每次自开自关，无驻留引用。"""
    conn = sqlite3.connect(str(path), timeout=0.0)
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            return True
        conn.rollback()
        return False
    finally:
        conn.close()


def _p10_write_ok(path, timeout):
    """写者探针：BEGIN IMMEDIATE＋门表 no-op UPDATE＋COMMIT。COMMIT 需要
    EXCLUSIVE——被任何 SHARED（活动读事务）阻挡→短 timeout BUSY→False；
    全链成功（无冲突锁）→True。BUSY 路径回滚，不留锁。"""
    conn = sqlite3.connect(str(path), timeout=timeout)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(_P10_TOUCH_GATE, ("seed",))
        conn.commit()
        return True
    except sqlite3.OperationalError:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        return False
    finally:
        conn.close()


def _p10_poll_until(predicate, timeout, interval=0.01):
    """轮询直至 predicate() 为 True（超时 False——诚实红路径）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class _P10BoxFlag:
    """轮询谓词载体（免 lambda）：读 box dict 的指定键布尔值。"""

    def __init__(self, box, key):
        self._box = box
        self._key = key

    def __call__(self):
        return bool(self._box.get(self._key))


def _p10_shared_sampler_main(path, start_event, stop_event, box):
    """SHARED 高频采样器线程体（run_216 修复 2＋c6 修法 2 连续窗口）。

    论证（非 sleep 竞猜）：plan 的 holder 临时 reader 以**事务级** SHARED
    锁覆盖整个 N 键扫描（每键 read_seal_on 含布局检查＋payload 全验证，
    60 键为数十-数百 ms 量级——park_probe 实测 100-375ms）；本采样器
    **预连接**（start_event 前建好连接，消除 connect 延迟落入关键窗），
    start 后以 timeout=0 的 BEGIN IMMEDIATE→no-op UPDATE→COMMIT 循环高频
    采样锁状态。**c6 语义**：(a) 连续窗口——命中不早退（c5 首命中即
    return，配合「门释放后才启动」在负载下错过整个扫描窗＝run3 T1
    seen=None 定因），观测至 stop_event（主线程在 reserve 完成信号
    owner_busy=True 观测后置位）或 30s 上限；(b) **seen 只认 commit 相
    BUSY**——BEGIN IMMEDIATE 成功而 UPDATE/COMMIT 阶段 OperationalError
    （in_transaction=True）＝COMMIT 的 EXCLUSIVE 升级被活动 SHARED 阻挡
    （mech D1 实证）＝plan reader 真实在持；BEGIN IMMEDIATE 即失败
    （in_txn=False→begin_busy）＝写者在持（E_b 门 spill EXCLUSIVE 或
    worker 保留连接 RESERVED），**非 SHARED 证据不算 seen**（连续窗口下
    采样会横跨门持锁期，该区分是硬前提）。timeout=0 不做 busy 等待
    （mech D2 的释放后重试语义会把「曾在」吞成「成功」）。BUSY 路径回滚
    不留言柄。线程内建连接（线程亲和）。
    """
    # timeout=0.0（c3 快审修复 1）：连接级 1.0s 会让 COMMIT 的 busy handler
    # 重试穿越整个 SHARED 窗后成功（OperationalError 不发生→seen 永不置位
    # ——快审在本机 py3.12.13 以 30/60ms SHARED 窗实测复现）；0.0 即刻
    # OperationalError＝采样语义，与设计申报一致。
    conn = sqlite3.connect(str(path), timeout=0.0)
    try:
        box["armed_at"] = time.monotonic()
        start_event.wait(10.0)
        box["started_at"] = time.monotonic()
        deadline = time.monotonic() + 30.0
        samples = 0
        commit_ok = 0
        commit_busy = 0
        begin_busy = 0
        exc_kinds = {}
        while not stop_event.is_set() and time.monotonic() < deadline:
            samples += 1
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(_P10_TOUCH_GATE, ("seed",))
                conn.commit()
                commit_ok += 1
            except sqlite3.OperationalError as exc:
                in_txn = False
                try:
                    in_txn = conn.in_transaction
                except sqlite3.Error:
                    pass
                if not in_txn:
                    begin_busy += 1
                else:
                    commit_busy += 1
                    if not box.get("seen"):
                        box["first_seen_at"] = time.monotonic()
                    box["seen"] = True
                    box["commit_phase"] = True
                kind = "%s/in_txn=%s" % (type(exc).__name__, in_txn)
                exc_kinds[kind] = exc_kinds.get(kind, 0) + 1
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                time.sleep(0.0005)
                continue
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            time.sleep(0.0005)
        box["samples"] = samples
        box["commit_ok"] = commit_ok
        box["commit_busy"] = commit_busy
        box["begin_busy"] = begin_busy
        box["exc_kinds"] = exc_kinds
        box["stopped_at"] = time.monotonic()
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def _p10_diag(tag, *parts):
    """candidate5-diag 诊断打印（时间戳＋统一前缀；仅观测，不改断言语义）。"""
    print("P10DIAG [%09.3fs] %s %s" % (
        time.monotonic() % 100000.0, tag, " ".join(str(p) for p in parts)),
        flush=True)


def _p10_diag_db(tag, path):
    """诊断：单库 journal_mode＋wal/shm 兄弟文件＋大小（ro 连接读 pragma）。"""
    target = Path(path)
    if not target.is_file():
        _p10_diag(tag, target.name, "MISSING")
        return
    mode = "?"
    conn = sqlite3.connect(str(target), timeout=2.0)
    try:
        mode = str(conn.execute(_P10_JOURNAL_PRAGMA).fetchone()[0])
    except sqlite3.Error as exc:
        mode = "ERR:%s" % type(exc).__name__
    finally:
        conn.close()
    wal = (Path(str(target) + "-wal")).is_file()
    shm = (Path(str(target) + "-shm")).is_file()
    _p10_diag(tag, target.name, "journal=%s" % mode,
              "wal=%s" % wal, "shm=%s" % shm, "size=%d" % target.stat().st_size)


def _p10_cleanup_users_root(path):
    """箱 adata/ 的非静默收尾（run_216 定因修复 1②/③）。

    rmtree 失败不再静默：一次直接删除＋gc＋一次重试（Windows 句柄释放
    延迟余量）；仍残留则以异常上抛（把「泄漏连接导致 users.db 滞留」从
    静默级联升级为显式红）。setUpClass 侧同函数做跨类/跨跑自愈清理。
    """
    target = Path(path)
    if not target.exists():
        return
    for attempt in (0, 1):
        try:
            shutil.rmtree(target)
        except OSError:
            pass
        gc.collect()
        time.sleep(0.2)
        if not target.exists():
            return
    raise RuntimeError(
        "P10: 箱 adata/ 收尾失败（连接句柄泄漏导致 users.db 滞留将级联"
        " setUpClass AuthError）——请检查窗口 worker 是否未 join/连接未"
        " 释放: %s" % target)


class _P10ProbeCall:
    """轮询谓词载体（免 lambda 的可调用对象；mode='busy'→RESERVED 探针、
    'free'→非 busy（c6 park2 的 free→busy 转移前半）、'write'→可写探针）。"""

    def __init__(self, path, mode):
        self._path = Path(path)
        self._mode = mode

    def __call__(self):
        if self._mode == "busy":
            return _p10_collab_busy(self._path)
        if self._mode == "free":
            return not _p10_collab_busy(self._path)
        return _p10_write_ok(self._path, 0.5)


def _p10_window_main(runtime, info, user, target_kind, operation, item_keys,
                     body, outcome):
    """worker 线程体：真实进入写窗；body(inputs, outcome) 在 yield 内执行；
    异常原样记录（不吞、不改类型）。"""
    try:
        with tws.retained_team_write_inputs(
                runtime, info, user, target_kind=target_kind,
                operation=operation, item_keys=item_keys) as inputs:
            outcome["inputs"] = inputs
            if body is not None:
                body(inputs, outcome)
        outcome["ok"] = True
    except BaseException as exc:  # noqa: BLE001 - 原样记录供主线程断言
        outcome["error"] = exc


def _p10_start_window(runtime, info, user, *, target_kind, operation,
                      item_keys, body=None, outcome=None):
    """启动 worker 线程跑窗（真实 native interleaving：主线程门控＋worker
    窗体并发）。返回 (thread, outcome)；outcome 可预置 body 探针参数。"""
    if outcome is None:
        outcome = {}
    thread = threading.Thread(
        target=_p10_window_main,
        args=(runtime, info, user, target_kind, operation, item_keys, body,
              outcome))
    thread.daemon = True
    thread.start()
    return thread, outcome


def _p10_body_target_write(inputs, outcome):
    """yield 体（S1/S3）：合同身份锚＋目标业务写（decisions 行；K9 已先种
    article 满足 FK）。"""
    outcome["body_runs"] = outcome.get("body_runs", 0) + 1
    outcome["operation_is"] = inputs.operation is outcome["expected_operation"]
    outcome["item_keys_eq"] = inputs.item_keys == tuple(
        outcome["expected_keys"])
    outcome["plan_type_ok"] = isinstance(inputs.plan, KeyedReadPlan)
    outcome["plan_is_scope_plan"] = inputs.plan is inputs.scope.keyed_plan()
    outcome["all_in_transaction"] = all(
        conn.in_transaction for conn in inputs.connections)
    inputs.target_conn.execute(_P10_INSERT_STAGE1_VOTE,
                               (outcome["write_key"], "include", "", "", "",
                                P10_VOTE_TS, P10_VOTE_TS))


def _p10_body_probe_handles(inputs, outcome):
    """yield 体（T3/T5）：句柄/锁三重探针＋目标业务写。

    - gc 活连接计数（对照 outcome['baseline_gc']＋预期保留数）；
    - os.replace：outcome['paths_locked'] 全部 PermissionError、
      outcome['paths_free'] 全部成功（零句柄证据）；
    - target 身份与全连接活动事务。"""
    outcome["body_runs"] = outcome.get("body_runs", 0) + 1
    outcome["gc_now"] = _p10_count_live_connections()
    locked = {}
    for path in outcome["paths_locked"]:
        try:
            os.replace(path, str(path) + ".p10body")
            os.replace(str(path) + ".p10body", path)
            locked[str(path)] = False
        except PermissionError:
            locked[str(path)] = True
    outcome["locked_map"] = locked
    freed = {}
    for path in outcome["paths_free"]:
        try:
            os.replace(path, str(path) + ".p10body")
            os.replace(str(path) + ".p10body", path)
            freed[str(path)] = True
        except PermissionError:
            freed[str(path)] = False
    outcome["free_map"] = freed
    outcome["target_is_owner"] = inputs.target_conn is inputs.owner_conn
    outcome["all_in_transaction"] = all(
        conn.in_transaction for conn in inputs.connections)
    if outcome.get("write_key"):
        inputs.target_conn.execute(_P10_INSERT_STAGE1_VOTE,
                                   (outcome["write_key"], "include", "", "",
                                    "", P10_VOTE_TS, P10_VOTE_TS))


class _P10TaskLookup:
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


class P10WriteWindowBase(unittest.TestCase):
    """公共基座：类级真实 users.db（auth 建号，箱根 adata/——排序前提）＋
    用例级真实任务夹具（TemporaryDirectory＋真实产品 API 构造），零 mock。"""

    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls._users_root = _P10_BOX_ROOT / "adata"
        # 自愈（run_216 定因修复 1③）：上一类/上一跑的残留 users.db 会令
        # auth.create_user 撞重名 AuthError——先非静默硬清再建。
        _p10_cleanup_users_root(cls._users_root)
        cls._users_root.mkdir(parents=True, exist_ok=True)
        # 连接先关（各用例 finally 已 join worker）再删目录；失败显式红。
        cls.addClassCleanup(_p10_cleanup_users_root, cls._users_root)
        cls.users_root = cls._users_root
        previous = os.environ.get("COBOOKSHELF_DATA_DIR")
        cls.addClassCleanup(_p10_restore_env, "COBOOKSHELF_DATA_DIR", previous)
        os.environ["COBOOKSHELF_DATA_DIR"] = str(cls.users_root)
        for name in P10_USER_NAMES:
            created = auth.create_user(name, P10_PASSWORD)
            USERS[name] = int(created["id"])
        global _P10_PROVENANCE_PRINTED
        if not _P10_PROVENANCE_PRINTED:
            _P10_PROVENANCE_PRINTED = True
            print("P10_TEST_FILE_SHA",
                  _p10_sha256_file(Path(__file__).resolve()))
            print("P10_WRITE_CANDIDATE_SHA",
                  _p10_sha256_file(_P10_WRITE_SOURCE))
            print("P10_RUNTIME_CANDIDATE_SHA",
                  _p10_sha256_file(_P10_RUNTIME_SOURCE))
            print("P10_READS_CANDIDATE_SHA",
                  _p10_sha256_file(_P10_READS_SOURCE))
            print("P10_WRITE_PINNED_SHA", P10_WRITE_CANDIDATE_SHA)
            print("P10_RUNTIME_PINNED_SHA", P10_RUNTIME_CANDIDATE_SHA)
            print("P10_READS_PINNED_SHA", P10_READS_CANDIDATE_SHA)
            print("P10_REPO_TYPES_SHA", _p10_sha256_file(
                _P10_REPO_ROOT / "coscreen" / "team_flow_types.py"))
            print("P10_REPO_FACTS_SHA", _p10_sha256_file(
                _P10_REPO_ROOT / "coscreen" / "team_terminal_facts.py"))
            print("P10_REPO_REGISTRY_SHA", _p10_sha256_file(
                _P10_REPO_ROOT / "coscreen" / "team_terminal_registry.py"))
            print("P10_REPO_LIFECYCLE_SHA", _p10_sha256_file(
                _P10_REPO_ROOT / "custom_backend"
                / "team_source_lifecycle.py"))
            print("P10_REPO_KEYED_INPUTS_SHA", _p10_sha256_file(
                _P10_REPO_ROOT / "custom_backend" / "team_keyed_inputs.py"))
            print("P10_PYTHON", sys.version.split()[0])
            print("P10_SQLITE_VERSION", sqlite3.sqlite_version)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf216_p10_case_")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.op = TeamOperation(name="p10-window", stage="screen")

    # -- 场景构建 helper（全部真实公共 setup API） ---------------------------

    def _task_info(self, task_dir, task_id):
        """真实 TaskInfo dataclass 值（task_id/dir/display_name 最小合同）。"""
        return tasks_mod.TaskInfo(task_id=task_id, dir=task_dir,
                                  display_name=task_id)

    def _make_runtime(self, task_dir, task_id):
        """真实 WorkflowRuntime（宿主装配形状）：真实 context/lock/
        sync_catalog；task_lookup 返回夹具任务真实 TaskInfo；screener_db 为
        复刻生产语义的真实装配。"""
        info = self._task_info(task_dir, task_id)
        return WorkflowRuntime(_P10TaskLookup(task_id, info),
                               _p10_screener_db, self.users_root,
                               _p10_visible_true)

    def _init_team_task(self, name, task_id):
        """真实 initialize_task_store 建协作库＋属主 reconciler 显式选择。"""
        task_dir = self.base / name
        task_dir.mkdir()
        collab_mod.initialize_task_store(
            task_dir, task_id=task_id, owner_user_id=USERS["owner"],
            owner_username="owner")
        collab_mod.set_business_role(
            task_dir / "collaboration.db", actor_user_id=USERS["owner"],
            user_id=USERS["owner"], business_role="reconciler",
            task_id=task_id)
        return task_dir

    def _grant_member(self, task_dir, task_id, username, role="coder"):
        """真实 grant_role（属主授权）。"""
        collab_mod.grant_role(
            task_dir / "collaboration.db", actor_user_id=USERS["owner"],
            user_id=USERS[username], role=role, task_id=task_id)

    def _choice_member(self, task_dir, task_id, username,
                       business_role="coder"):
        """真实 set_business_role（业务角色单选；required_screener 语义）。"""
        collab_mod.set_business_role(
            task_dir / "collaboration.db", actor_user_id=USERS["owner"],
            user_id=USERS[username], business_role=business_role,
            task_id=task_id)

    def _full_screener_db(self, task_dir, username, *, articles=()):
        """真实 init_db＋migrate 全库＋articles 种子＋latch。"""
        path = task_dir / ("%s.db" % tasks_mod.slugify_screener(username))
        dbm.init_db(path)
        wf_mod.migrate(path)
        conn = sqlite3.connect(str(path), timeout=15)
        try:
            for order, key in enumerate(articles):
                conn.execute(_P10_INSERT_ARTICLE, (key, "p10-" + key, order))
            conn.execute(_P10_LATCH)
            conn.commit()
        finally:
            conn.close()
        return path

    def _holder_db(self, path, *, uid, library_id=None):
        """holder 库：真实 ``db.init_db`` 安装 P04 ledger（产品 _META_DDL＋
        种子行——run_216 定因：手写 meta DDL 与产品文本不符致
        library_identity_header_on meta_corrupt、来源解析失败）＋F1 prepare
        ＋确定性 library upsert（默认 %032x(uid)；变体库显式传 v1 的
        library 保持 F2 token 跨版本可解析）＋门表种子。"""
        if library_id is None:
            library_id = _p10_member_library_id(uid)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        dbm.init_db(path)
        tf.prepare_team_terminal_facts(path)
        conn = sqlite3.connect(str(path), timeout=15)
        try:
            conn.execute(P10_GATE_DDL)
            conn.execute("INSERT INTO p10_gate (k, v) VALUES (1, 'seed')")
            conn.execute(_P10_UPSERT_LIBRARY, (library_id,))
            conn.commit()
            # candidate5（diag 数据驱动修）：db.init_db 经 _connect 强制 WAL
            #（diag 实测 owner/bob/carol/users 全 wal、仅 collab=delete）——
            # WAL 读者不阻写者→E_b/E_c 门不挡 plan、采样器 COMMIT 永不
            # BUSY、T4a 换名撞已 reserve 的 owner（WinError 5）。门/采样器
            # 的锁证据面是回滚日志语义——holder 在夹具层显式切回 DELETE
            #（产品侧对 holder 只经 plain ro/rw 连接，不会翻回 WAL）。读回
            # 校验为夹具门（不成立即建库期失败，非用例断言语义）。
            mode = conn.execute(_P10_SET_JOURNAL_DELETE).fetchone()[0]
            readback = conn.execute(_P10_JOURNAL_PRAGMA).fetchone()[0]
            conn.commit()
        finally:
            conn.close()
        if str(readback).lower() != "delete":
            raise RuntimeError(
                "P10 holder 夹具必须为回滚（DELETE）日志模式，实测=%r"
                "（WAL 下锁证据面失效）: %s" % (readback, path))
        _p10_diag("holder-built", Path(path).name,
                  "journal=%s" % readback, "set_result=%s" % mode)
        return Path(path)

    def _prepare_collab(self, task_dir, task_id, instance):
        """collab 组合库：F2 prepare＋protocol 行（trusted_new_task）＋F1
        prepare（collaboration_* 族共存——ddl_scan 组合扫描零冲突）。"""
        collab_path = task_dir / "collaboration.db"
        tr.prepare_team_terminal_registry(collab_path)
        writer = _p10_borrowed_writer(collab_path)
        try:
            tr.register_protocol_on(
                writer, task_id=task_id, task_instance_id=instance,
                owner_user_id=USERS["owner"],
                owner_library_id=P10_OWNER_LIBRARY,
                creation_path="trusted_new_task")
            writer.commit()
        finally:
            _p10_close_quietly(writer)
        tf.prepare_team_terminal_facts(collab_path)
        return collab_path

    def _register_source(self, collab_path, task_dir, instance, uid,
                         library_id, member_path, registration_id):
        """F2 登记一个来源：P06 真 token（make_source_identity）＋
        register_source_on（P08 同款）。"""
        token = tsl_make_source_identity(
            task_dir=task_dir, source_path=Path(member_path),
            source_user_id=uid, source_library_id=library_id)
        writer = _p10_borrowed_writer(collab_path)
        try:
            record = tr.register_source_on(
                writer, task_instance_id=instance, source_user_id=uid,
                source_library_id=library_id, source_path_identity=token,
                registration_id=registration_id)
            writer.commit()
            return record
        finally:
            _p10_close_quietly(writer)

    def _append_holder_seal(self, holder_path, payload):
        """真实 append_seal_on（holder 库 origin='actor'；borrowed 写连接
        commit 收尾）。"""
        writer = _p10_borrowed_writer(holder_path)
        try:
            record = tf.append_seal_on(writer, payload, origin="actor")
            writer.commit()
            return record
        finally:
            _p10_close_quietly(writer)

    def _p10_wait_park(self, owner_db, users_db, *, fresh, tag):
        """停点检出原语（c6 修法 1；T2/T4b/T5/S1/S2 统一）：锚定 accounts
        门本身，禁用 collab busy 作检出信号（manager __enter__ 即持、恒真
        全窗口）。

        产品事实：reserve_candidates 序＝[collab, owner, accounts(users.db
        恒为 others 首位——adata 排序事实), actor, members, witnesses]。
        E_a（users.db BEGIN IMMEDIATE RESERVED）在持时，worker 的 accounts
        保留连接必停在其 BEGIN IMMEDIATE 的 busy handler 内。外部可观测：
        - owner RESERVED（worker 本 attempt 的 reserve 已过 owner——accounts
          的紧前序）；fresh=True 先等 owner FREE（上一 attempt 清理逆序关闭
          [..., accounts, owner, collab] 释放 owner）再等 BUSY——free→busy
          转移＝本 attempt 的新 BUSY（c5 命中 attempt-1 残留 owner BUSY 的
          错检＝run3 T4b/S2 无 409 的直接根因）；
        - users.db 外部探针 BEGIN IMMEDIATE BUSY 作伴随双锚（E_a 门在持
          ＝停点成因核验；门在持期间恒 BUSY，故只作锚不作相位信号）。
        检出后 sleep 0.15 让「已过 owner、停在 accounts」的状态稳定。
        """
        if fresh:
            self.assertTrue(
                _p10_poll_until(_P10ProbeCall(owner_db, "free"), 30.0),
                "%s: 上一 attempt 的 owner 保留连接必须先释放（free→busy "
                "转移的前半）" % tag)
        self.assertTrue(
            _p10_poll_until(_P10ProbeCall(owner_db, "busy"), 30.0),
            "%s: 本 attempt reserve 必须到达 owner（accounts 门停点的"
            "前置特征）" % tag)
        self.assertTrue(_p10_collab_busy(users_db),
                        "%s: accounts 门在持（双锚——worker 确定性停在 "
                        "reserve 内 accounts 的 BEGIN IMMEDIATE 处）" % tag)
        time.sleep(0.15)

    def _p10_rearm_accounts_gate(self, users_db, tag):
        """accounts 门重臂原语（c6 修法 1 的 park2 前置；T4b/S1/S2）。

        c5 的「释放后立即自旋重抢」会在 worker 尚停于旧门上时抢先重抢→
        worker 永远停在 attempt-1、park2 的 owner free→busy 永不出现。序
        列化：① 观测 users BUSY（worker 的 busy retry 抢回 attempt-1 的
        accounts——证明 worker 已在 attempt-1 尾段推进）；② 观测 users
        FREE（attempt-1 清理逆序关闭已闭 accounts；owner/collab 随后毫秒
        级释放）；③ 自旋重抢 E_a——attempt-2 到达 accounts 前必经
        sync＋enter＋plan＋prepare＋owner reserve（数百 ms 路径余量，
        park_probe/复跑实测），主线程 1ms 自旋稳定先到。
        """
        self.assertTrue(
            _p10_poll_until(_P10ProbeCall(users_db, "busy"), 30.0, 0.001),
            "%s: worker 必须先抢回 attempt-1 的 accounts（busy retry）"
            % tag)
        self.assertTrue(
            _p10_poll_until(_P10ProbeCall(users_db, "free"), 30.0, 0.001),
            "%s: attempt-1 清理必须先释放 accounts（重臂窗口 opened）" % tag)
        gate = self._reacquire_gate(users_db)
        _p10_diag(tag, "rearmed", "gate_acquired")
        return gate

    def _p10_skew_owner_marker(self, bob_db, owner_db, tag):
        """S1/S2 漂移注入（c6 修法 3）：accounts 停点内偏移 actor（bob）的
        catalog marker（真实静态 SQL）。

        停点上 owner 已被 worker 保留连接持有写锁（写-写互斥）——bump
        owner catalog_revision 会与 accounts 门互等至 15s 超时；actor 库排
        序在 accounts 之后、停点时尚未 reserve，可无竞争写入。偏移后产品
        重验 ``_member_marker_revision``（marker != owner 当前 revision）
        与 bump owner 殊途同归走 _CatalogMarkerDrift 支。"""
        conn = sqlite3.connect(str(bob_db), timeout=15)
        try:
            conn.execute(_P10_SKEW_MARKER, (Path(owner_db).name,))
            conn.commit()
        finally:
            conn.close()
        _p10_diag(tag, "marker-skewed",
                  "marker=%r" % (self._read_marker(bob_db, owner_db),),
                  "owner_rev=%s" % self._read_owner_revision(owner_db))

    def _reacquire_gate(self, path):
        """重臂 RESERVED 门：自旋抢锁（manager 清理释放后的再门控；重臂竞态
        序列化见 _p10_rearm_accounts_gate/诚实记录 2）。"""
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            conn = None
            try:
                conn = sqlite3.connect(str(path), timeout=0.02)
                conn.execute("BEGIN IMMEDIATE")
                return conn
            except sqlite3.Error:
                if conn is not None:
                    try:
                        conn.close()
                    except sqlite3.Error:
                        pass
            time.sleep(0.001)
        self.fail("重臂 RESERVED 门超时：%s" % path)

    def _owner_path(self, task_dir):
        """owner catalog 路径（slug 锚定）。"""
        return task_dir / ("%s.db" % tasks_mod.slugify_screener("owner"))

    def _read_owner_revision(self, owner_db):
        """candidate5-diag：owner 当前 catalog_revision（诊断打印数据源）。"""
        conn = sqlite3.connect(str(owner_db), timeout=5.0)
        try:
            return conn.execute(_P10_SELECT_CATALOG_REVISION).fetchone()[0]
        except sqlite3.Error as exc:
            return "ERR:%r" % (exc,)
        finally:
            conn.close()

    def _read_marker(self, bob_db, owner_db):
        """candidate5-diag：bob 的 catalog marker（owner_identity 锚定）。"""
        conn = sqlite3.connect(str(bob_db), timeout=5.0)
        try:
            row = conn.execute(
                _P10_SELECT_MARKER, (Path(owner_db).name,)).fetchone()
            return row[0] if row is not None else None
        except sqlite3.Error as exc:
            return "ERR:%r" % (exc,)
        finally:
            conn.close()

    def _user(self, username):
        """窗口 user 入参（真实 uid＋username）。"""
        return {"user_id": USERS[username], "username": username}

    def _assert_clean_state(self, task_dir, paths, runtime, task_id):
        """窗口结束后公共清洁断言：无 journal/wal/shm 残留、规范锁已释放、
        全部夹具库零打开句柄（os.replace 探针）。"""
        leftovers = [name for name in (
            p.name for p in Path(task_dir).rglob("*"))
            if name.endswith("-journal") or name.endswith("-wal")
            or name.endswith("-shm")]
        self.assertEqual(leftovers, [], "P10: 不得残留 journal/wal 文件")
        lock = runtime.lock(task_id)
        self.assertTrue(lock.acquire(blocking=False), "P10: 规范锁已释放")
        lock.release()
        for path in paths:
            self.assertTrue(_p10_replaceable(path),
                            "P10: 窗口退出后零打开句柄: %s" % path)


class EnterPlanCloseReserveOrderTests(P10WriteWindowBase):
    """用例 1：enter→plan→readers closed→reserve 固定序（真实锁实证）。"""

    def test_enter_plan_close_reserve_order(self):
        task_id = "p10-t1-order"
        instance = "inst-p10-t1"
        task_dir = self._init_team_task("t1", task_id)
        collab_path = self._prepare_collab(task_dir, task_id, instance)
        keys = tuple("K%02d" % i for i in range(60))
        owner_db = self._full_screener_db(
            task_dir, "owner", articles=("K9",) + keys)
        holder = self._holder_db(
            task_dir / "bob.db", uid=USERS["bob"],
            library_id=_p10_member_library_id(USERS["bob"]))
        writer = _p10_borrowed_writer(holder)
        try:
            for index in range(60):
                tf.append_seal_on(writer, _p10_observation_payload(
                    task_id=task_id, instance=instance, stage="stage1",
                    item_key="K%02d" % index, decision="include",
                    sealed_at=P10_TS1, registration_prefix="reg-p10-t1"),
                    origin="actor")
            writer.commit()
        finally:
            _p10_close_quietly(writer)
        self._register_source(
            collab_path, task_dir, instance, USERS["bob"],
            _p10_member_library_id(USERS["bob"]), holder, "reg-p10-t1-bob")
        runtime = self._make_runtime(task_dir, task_id)
        # candidate5-diag：夹具终态诊断（journal_mode 为 run2 四象定因主嫌——
        # db._connect/auth._connect 强制 WAL，collab 为回滚模式）。
        _p10_diag("T1-fixture", "built")
        _p10_diag_db("T1-fixture", collab_path)
        _p10_diag_db("T1-fixture", owner_db)
        _p10_diag_db("T1-fixture", holder)
        _p10_diag_db("T1-fixture", self.users_root / "users.db")

        e_bob = _p10_open_pending_gate(holder)
        e_accounts = _p10_open_reserved_gate(self.users_root / "users.db")
        # 基线在两门已开后测（门计入基线；停点算式＝基线−已释放门＋保留集）。
        baseline = _p10_count_live_connections()
        events = []
        sampler = None
        sampler_stop = threading.Event()
        thread, outcome = _p10_start_window(
            runtime, self._task_info(task_dir, task_id), self._user("owner"),
            target_kind="actor", operation=self.op, item_keys=keys)
        try:
            # collab_begin：外部探针实测 collab RESERVED 在持（观测发生在
            # worker 被 E_bob 挡住的 plan 中途——collab 锁横跨整个 plan 段）。
            self.assertTrue(
                _p10_poll_until(_P10ProbeCall(collab_path, "busy"), 30.0),
                "T1: collab RESERVED 必须在 plan 前获得并被外部探针观测")
            events.append("collab_begin")
            _p10_diag("T1-collab_begin", "observed")
            # plan（c6 修法 2）：collab_begin 检出后**立即** armed 采样器
            # （连接在 plan 扫描开始前建好——armed_at 记入 stats 行），采样
            # 窗＝collab_begin→owner_busy（连续观测，不等停点/释放门；
            # run3 定因＝门释放后才启动只在 retry 尾窗可捕、负载下零捕获）。
            # seen 只认 commit 相 BUSY（门持锁期的 begin 相 BUSY 不算证据，
            # 见 _p10_shared_sampler_main）。
            sampler_box = {}
            sampler_start = threading.Event()
            sampler = threading.Thread(
                target=_p10_shared_sampler_main,
                args=(holder, sampler_start, sampler_stop, sampler_box))
            sampler.daemon = True
            sampler.start()
            sampler_start.set()
            time.sleep(0.3)   # worker 确定性阻塞在 plan 的 holder 读上
            _p10_close_quietly(e_bob)
            e_bob = None
            seen_now = _p10_poll_until(_P10BoxFlag(sampler_box, "seen"), 30.0)
            self.assertTrue(seen_now,
                            "T1: plan 的 holder SHARED 必须真实存在（采样器 "
                            "COMMIT timeout=0 实测 commit 相 BUSY）；未捕＝"
                            "诚实红")
            events.append("plan")
            # readers_closed：同一写探针成功＝SHARED 已释放、库可写。
            self.assertTrue(
                _p10_poll_until(_P10ProbeCall(holder, "write"), 30.0),
                "T1: plan 的临时 reader 必须在 reserve 前全部释放")
            events.append("readers_closed")
            # reserve：owner RESERVED（B1 reserve 真实开始；accounts 门使
            # worker 确定性停在 reserve 中途——owner 先于 accounts）。
            self.assertTrue(
                _p10_poll_until(_P10ProbeCall(owner_db, "busy"), 30.0),
                "T1: B1 reserve 必须真实开始（owner RESERVED）")
            events.append("reserve")
            _p10_diag("T1-reserve", "observed")
            # 采样窗口闭合（reserve 完成信号 owner_busy 已观测）：stop+join
            # 先于停点快照——采样器连接句柄会令 Windows os.replace 失败、
            # 也会污染 gc 计数（连续窗口采样器必须显式排干）。
            sampler_stop.set()
            sampler.join(6)
            self.assertFalse(sampler.is_alive(), "T1: 采样器必须结束")
            # 统计打印在断言之前（diag 轮教训：断言红后 print 不再执行）；
            # stats 行确认 armed_at（c6 修法 2：armed 必须先于扫描窗）。
            _p10_diag("T1-sampler-stats", "seen=%s" % sampler_box.get("seen"),
                      "commit_phase=%s" % sampler_box.get("commit_phase"),
                      "armed_at=%s" % (
                          "%0.3f" % sampler_box["armed_at"]
                          if sampler_box.get("armed_at") is not None
                          else "MISSING"),
                      "samples=%s" % sampler_box.get("samples"),
                      "commit_ok=%s" % sampler_box.get("commit_ok"),
                      "commit_busy=%s" % sampler_box.get("commit_busy"),
                      "begin_busy=%s" % sampler_box.get("begin_busy"),
                      "exc_kinds=%s" % (sampler_box.get("exc_kinds"),),
                      "start_latency_ms=%s" % (
                          "%0.1f" % (1000.0 * (
                              sampler_box.get("started_at", 0)
                              - sampler_box.get("armed_at", 0)))
                          if sampler_box.get("armed_at") else "n/a"),
                      "window_ms=%s" % (
                          "%0.1f" % (1000.0 * (
                              sampler_box.get("stopped_at", 0)
                              - sampler_box.get("armed_at", 0)))
                          if sampler_box.get("armed_at") else "n/a"))
            # 停点快照：collab/owner RESERVED 仍在持；尚未 reserve 的 holder
            # 零句柄；gc 活连接＝基线−E_bob(已释放)＋collab＋owner＋accounts
            # 保留连接（B1 已开该连接并阻塞在其 BEGIN IMMEDIATE 上——对象
            # 活跃可达，计入；e_accounts 门保持开、计入基线；采样器已 join、
            # 其连接不计入）。
            self.assertTrue(_p10_collab_busy(collab_path),
                            "T1: 停点 collab RESERVED 仍在持")
            self.assertTrue(_p10_collab_busy(owner_db),
                            "T1: 停点 owner RESERVED 在持")
            self.assertTrue(_p10_replaceable(holder),
                            "T1: 停点 witness holder 零打开句柄（readers "
                            "closed < reserve 的句柄级实证）")
            self.assertEqual(_p10_count_live_connections(), baseline + 2,
                             "T1: 停点 gc 活连接＝基线−E_bob＋collab＋owner＋"
                             "accounts（B1 已开、阻塞在门上；零游离 reader）")
        finally:
            if e_bob is not None:
                _p10_close_quietly(e_bob)
            _p10_close_quietly(e_accounts)
            if sampler is not None:
                sampler_stop.set()
                sampler.join(6)
            # 泄漏根修（run_216 修复 1①）：失败路径也必须排干 worker，
            # 否则其保留连接（collab/accounts 等）滞留句柄→用例临时目录
            # rmtree WinError32→箱 adata 静默滞留→级联 setUpClass AuthError。
            thread.join(90)
        self.assertFalse(thread.is_alive(), "T1: worker 必须结束")
        self.assertIsNone(outcome.get("error"), "T1: 窗口必须成功收尾")
        self.assertTrue(outcome.get("ok"), "T1: 窗口正常退出")
        self.assertEqual(events,
                         ["collab_begin", "plan", "readers_closed", "reserve"],
                         "T1: 固定窗口序断言锚（工单逐字）")
        self.assertEqual(len(outcome["inputs"].plan.witnesses), 60,
                         "T1: 60 键全部经 holder 见证")
        self._assert_clean_state(
            task_dir, (collab_path, owner_db, holder,
                       self.users_root / "users.db"), runtime, task_id)


class NoSecondReserveOnWitnessFailureTests(P10WriteWindowBase):
    """用例 2：见证读取 typed 失败→直接上抛无重算；单 manager 单 reserve。"""

    def test_no_second_reserve_on_witness_failure(self):
        task_id = "p10-t2-witness"
        instance = "inst-p10-t2"
        task_dir = self._init_team_task("t2", task_id)
        collab_path = self._prepare_collab(task_dir, task_id, instance)
        owner_db = self._full_screener_db(task_dir, "owner",
                                          articles=("K1", "K9"))
        holder = self._holder_db(
            task_dir / "bob.db", uid=USERS["bob"],
            library_id=_p10_member_library_id(USERS["bob"]))
        self._append_holder_seal(holder, _p10_observation_payload(
            task_id=task_id, instance=instance, stage="stage1", item_key="K1",
            decision="include", sealed_at=P10_TS1,
            registration_prefix="reg-p10-t2"))
        self._register_source(
            collab_path, task_dir, instance, USERS["bob"],
            _p10_member_library_id(USERS["bob"]), holder, "reg-p10-t2-bob")
        # 损坏副本：字节复制→DROP append-only 触发器→损坏 payload_json→按
        # 产品常量原样重建触发器（F1 布局门仍过；mech F 实测）。
        corrupt = task_dir / "bob.corrupt.db"
        shutil.copyfile(holder, corrupt)
        conn = sqlite3.connect(str(corrupt), timeout=15)
        try:
            seal_id = conn.execute(_P10_SELECT_SEAL_ID, ("K1",)).fetchone()[0]
            conn.execute(_P10_DROP_NO_UPDATE)
            conn.execute(_P10_CORRUPT_SEAL, ("not json at all", seal_id))
            conn.execute(tf.TRIGGER_NO_UPDATE_DDL)
            conn.commit()
        finally:
            conn.close()
        corrupt_sha = _p10_sha256_file(corrupt)
        runtime = self._make_runtime(task_dir, task_id)

        e_accounts = _p10_open_reserved_gate(self.users_root / "users.db")
        thread, outcome = _p10_start_window(
            runtime, self._task_info(task_dir, task_id), self._user("owner"),
            target_kind="actor", operation=self.op, item_keys=("K1",))
        park_count = 0
        try:
            # 停点（c6 修法 1 统一原语：accounts 门＝reserve 第一个 other；
            # owner free→busy＋users 门在持双锚——worker 确定性停在 accounts
            # 的 BEGIN IMMEDIATE 上）：holder 尚未 reserve（零句柄）。
            self._p10_wait_park(
                owner_db, self.users_root / "users.db",
                fresh=False, tag="T2-park")
            self.assertTrue(_p10_replaceable(holder),
                            "T2: 停点 holder 尚未被 reserve（accounts 门＝"
                            "others 首位）")
            park_count += 1
            os.replace(corrupt, holder)   # 竞态窗内换入损坏副本
        finally:
            _p10_close_quietly(e_accounts)
            # 泄漏根修（run_216 修复 1①）：失败路径也必须排干 worker，
            # 否则其保留连接（collab/accounts 等）滞留句柄→用例临时目录
            # rmtree WinError32→箱 adata 静默滞留→级联 setUpClass AuthError。
            thread.join(90)
        self.assertFalse(thread.is_alive(), "T2: worker 必须结束")
        error = outcome.get("error")
        self.assertIsNotNone(error, "T2: 见证读取 typed 失败必须上抛")
        self.assertNotIsInstance(error, HTTPException,
                                 "T2: typed 失败不是 409 漂移类（无重算）")
        self.assertIsInstance(error, tsr.TeamFlowUnavailableError,
                              "T2: typed TeamFlowUnavailableError")
        self.assertEqual(error.reason, "completion_source_unresolved",
                         "T2: reason=completion_source_unresolved")
        self.assertIn("witness seal re-read failed", str(error),
                      "T2: detail 指向见证重验读取失败")
        chained = []
        cursor = error.__cause__
        while cursor is not None and id(cursor) not in (
                id(seen) for seen in chained):
            chained.append(cursor)
            cursor = cursor.__cause__
        self.assertTrue(any(isinstance(seen, TerminalFactsUnavailable)
                            for seen in chained),
                        "T2: 原 TerminalFactsUnavailable 经 __cause__ 链保留")
        self.assertEqual(_p10_sha256_file(holder), corrupt_sha,
                         "T2: 竞态窗换入的损坏副本未被回写/修复（单次尝试）")
        self.assertEqual(park_count, 1,
                         "T2: 单次 reserve 阶段（accounts 门停点计数＝1）——"
                         "typed 失败不触发第二次尝试/同 manager 二次 reserve")
        self._assert_clean_state(
            task_dir, (collab_path, owner_db, holder,
                       self.users_root / "users.db"), runtime, task_id)


class OwnerTargetNoSelfSharedTests(P10WriteWindowBase):
    """用例 3：owner 提交时无本进程 SHARED（句柄/gc/commit 三重实证）。"""

    def test_owner_target_has_no_self_shared_lock(self):
        task_id = "p10-t3-selfshared"
        instance = "inst-p10-t3"
        task_dir = self._init_team_task("t3", task_id)
        collab_path = self._prepare_collab(task_dir, task_id, instance)
        owner_db = self._full_screener_db(task_dir, "owner",
                                          articles=("K1", "K9"))
        holder = self._holder_db(
            task_dir / "bob.db", uid=USERS["bob"],
            library_id=_p10_member_library_id(USERS["bob"]))
        self._append_holder_seal(holder, _p10_observation_payload(
            task_id=task_id, instance=instance, stage="stage1", item_key="K1",
            decision="include", sealed_at=P10_TS1,
            registration_prefix="reg-p10-t3"))
        self._register_source(
            collab_path, task_dir, instance, USERS["bob"],
            _p10_member_library_id(USERS["bob"]), holder, "reg-p10-t3-bob")
        decoy = task_dir / "carol.db"
        sqlite3.connect(str(decoy)).close()
        runtime = self._make_runtime(task_dir, task_id)

        baseline = _p10_count_live_connections()
        outcome = {"paths_locked": [collab_path, owner_db,
                                    self.users_root / "users.db", holder],
                   "paths_free": [decoy],
                   "write_key": "K9"}
        thread, outcome = _p10_start_window(
            runtime, self._task_info(task_dir, task_id), self._user("owner"),
            target_kind="actor", operation=self.op, item_keys=("K1",),
            body=_p10_body_probe_handles, outcome=outcome)
        thread.join(90)
        self.assertFalse(thread.is_alive(), "T3: worker 必须结束")
        self.assertIsNone(outcome.get("error"), "T3: 健康窗必须成功")
        self.assertTrue(outcome.get("ok"),
                        "T3: 窗口正常退出（目标 COMMIT 未被任何本进程 "
                        "SHARED 阻挡——真实提交证据）")
        self.assertTrue(outcome["target_is_owner"],
                        "T3: owner actor 目标＝owner 连接")
        self.assertTrue(outcome["all_in_transaction"],
                        "T3: 保留连接全部持有 B1 写事务")
        self.assertEqual(outcome["gc_now"], baseline + 4,
                         "T3: yield 体内活连接恰＝基线＋保留集 4（collab/"
                         "owner/accounts/holder）——零游离 reader（含 owner "
                         "上的 SHARED）")
        self.assertTrue(all(outcome["locked_map"].values()),
                        "T3: os.replace 恰在保留集文件上 PermissionError: %r"
                        % outcome["locked_map"])
        self.assertTrue(all(outcome["free_map"].values()),
                        "T3: 非保留 decoy 零句柄可换名: %r"
                        % outcome["free_map"])
        conn = sqlite3.connect(str(owner_db), timeout=15)
        try:
            self.assertEqual(conn.execute(
                _P10_COUNT_DECISIONS, ("K9",)).fetchone()[0], 1,
                "T3: 目标业务写在窗口退出后持久（真实 commit）")
        finally:
            conn.close()
        self._assert_clean_state(
            task_dir, (collab_path, owner_db, holder, decoy,
                       self.users_root / "users.db"), runtime, task_id)


class RetainedFileReplacementTests(P10WriteWindowBase):
    """用例 4：保留输入文件替换被拒（owner typed／witness 漂移→重算→409）。"""

    def test_retained_file_replacement_rejected(self):
        # ---- (a) owner 替换→typed owner_db_unreadable ----------------------
        with self.subTest(section="owner-replacement"):
            task_id = "p10-t4a-owner"
            instance = "inst-p10-t4a"
            task_dir = self._init_team_task("t4a", task_id)
            collab_path = self._prepare_collab(task_dir, task_id, instance)
            keys = tuple("K%02d" % i for i in range(60))
            owner_db = self._full_screener_db(
                task_dir, "owner", articles=("K9",) + keys)
            owner_copy = task_dir / "owner.replaced.db"
            shutil.copyfile(owner_db, owner_copy)
            holder = self._holder_db(
                task_dir / "bob.db", uid=USERS["bob"],
                library_id=_p10_member_library_id(USERS["bob"]))
            writer = _p10_borrowed_writer(holder)
            try:
                for index in range(60):
                    tf.append_seal_on(writer, _p10_observation_payload(
                        task_id=task_id, instance=instance, stage="stage1",
                        item_key="K%02d" % index, decision="include",
                        sealed_at=P10_TS1, registration_prefix="reg-p10-t4a"),
                        origin="actor")
                writer.commit()
            finally:
                _p10_close_quietly(writer)
            self._register_source(
                collab_path, task_dir, instance, USERS["bob"],
                _p10_member_library_id(USERS["bob"]), holder,
                "reg-p10-t4a-bob")
            runtime = self._make_runtime(task_dir, task_id)
            # c6 修法 4：弃 E_b 门＋collab 检出（恒真信号禁用），检出锚定
            # plan 扫描本身——采样器（c6 修法 2 语义：armed 先于 worker、
            # 连续观测、seen 只认 commit 相 BUSY）在 worker 启动前 armed，
            # commit 相 BUSY＝plan 发现环对 holder 的真实 SHARED 读取进行中
            # （resolve 身份头点查或 60 键 seal 扫描；夹具扩为 60 键使扫描窗
            # 数百 ms ≫ 1ms 检出+os.replace——确定性余量，park_probe 实测）。
            # 扫描中段换入 owner 副本在结构上必落在 _prepare_inputs 的 owner
            # 身份复查（worker :1020 同款语义）之前→typed owner_db_unreadable。
            sampler_box = {}
            sampler_start = threading.Event()
            sampler_stop = threading.Event()
            sampler = threading.Thread(
                target=_p10_shared_sampler_main,
                args=(holder, sampler_start, sampler_stop, sampler_box))
            sampler.daemon = True
            sampler.start()
            sampler_start.set()
            thread, outcome = _p10_start_window(
                runtime, self._task_info(task_dir, task_id),
                self._user("owner"), target_kind="actor",
                operation=self.op, item_keys=keys)
            try:
                detected = _p10_poll_until(
                    _P10BoxFlag(sampler_box, "commit_phase"), 30.0, 0.001)
                # 采样器先排干（句柄面：其 holder 连接会令后续 os.replace/
                # 清洁断言失真——T1 同款收尾纪律）。
                sampler_stop.set()
                sampler.join(6)
                self.assertFalse(sampler.is_alive(), "T4a: 采样器必须结束")
                owner_busy_at_plan = _p10_collab_busy(owner_db)
                _p10_diag("T4a-planpark", "owner_busy=%s" % owner_busy_at_plan,
                          "detected=%s" % detected,
                          "samples=%s" % sampler_box.get("samples"))
                self.assertTrue(detected,
                                "T4a: plan 扫描的 holder SHARED 必须真实存在"
                                "（采样器 commit 相 BUSY 实测）；未捕＝诚实红")
                self.assertFalse(owner_busy_at_plan,
                                 "T4a: plan 扫描段 owner 尚未 reserve（换入"
                                 "窗口合法）")
                os.replace(owner_copy, owner_db)   # plan 扫描中段替换 owner
            finally:
                sampler_stop.set()
                sampler.join(6)
                # 泄漏根修：同上——失败路径排干 worker（缩进随 subTest）。
                thread.join(90)
            self.assertFalse(thread.is_alive(), "T4a: worker 必须结束")
            error = outcome.get("error")
            self.assertIsNotNone(error, "T4a: owner 替换必须 typed 拒绝")
            self.assertNotIsInstance(error, HTTPException,
                                     "T4a: 非 409（无重算的 typed 拒绝）")
            self.assertIsInstance(error, tsr.TeamFlowUnavailableError)
            self.assertEqual(error.reason, "owner_db_unreadable",
                             "T4a: typed owner_db_unreadable（物理身份变化）")
            self._assert_clean_state(
                task_dir, (collab_path, owner_db, holder,
                           self.users_root / "users.db"), runtime, task_id)
        # ---- (b) witness 两换→漂移重算仍不实→409 write_plan_drift ---------
        with self.subTest(section="witness-replacement-409"):
            task_id = "p10-t4b-witness"
            instance = "inst-p10-t4b"
            task_dir = self._init_team_task("t4b", task_id)
            collab_path = self._prepare_collab(task_dir, task_id, instance)
            owner_db = self._full_screener_db(task_dir, "owner",
                                              articles=("K1", "K9"))
            library = _p10_member_library_id(USERS["bob"])
            holder = self._holder_db(task_dir / "bob.db", uid=USERS["bob"],
                                     library_id=library)
            self._append_holder_seal(holder, _p10_observation_payload(
                task_id=task_id, instance=instance, stage="stage1",
                item_key="K1", decision="include", sealed_at=P10_TS1,
                registration_prefix="reg-p10-t4b"))
            v2 = self._holder_db(task_dir / "bob.v2.db", uid=USERS["bob"],
                                 library_id=library)
            self._append_holder_seal(v2, _p10_observation_payload(
                task_id=task_id, instance=instance, stage="stage1",
                item_key="K1", decision="include", sealed_at=P10_TS2,
                registration_prefix="reg-p10-t4b"))
            v3 = self._holder_db(task_dir / "bob.v3.db", uid=USERS["bob"],
                                 library_id=library)
            self._append_holder_seal(v3, _p10_observation_payload(
                task_id=task_id, instance=instance, stage="stage1",
                item_key="K1", decision="include", sealed_at=P10_TS3,
                registration_prefix="reg-p10-t4b"))
            self._register_source(
                collab_path, task_dir, instance, USERS["bob"], library,
                holder, "reg-p10-t4b-bob")
            runtime = self._make_runtime(task_dir, task_id)
            e_accounts = _p10_open_reserved_gate(
                self.users_root / "users.db")
            thread, outcome = _p10_start_window(
                runtime, self._task_info(task_dir, task_id),
                self._user("owner"), target_kind="actor",
                operation=self.op, item_keys=("K1",))
            park_count = 0
            try:
                try:
                    # 停点 1（c6 修法 1：owner free→busy＋users 门双锚——
                    # worker 确定性停在 attempt-1 reserve 的 accounts 处）：
                    # holder 零句柄（witnesses 排序在 accounts 之后）。
                    self._p10_wait_park(
                        owner_db, self.users_root / "users.db",
                        fresh=False, tag="T4b-park1")
                    self.assertTrue(_p10_replaceable(holder),
                                    "T4b: 停点 1 holder 尚未 reserve")
                    park_count += 1
                    _p10_diag("T4b-park1", "swapping-v2")
                    os.replace(v2, holder)
                finally:
                    _p10_close_quietly(e_accounts)
                # 重臂（c6 修法 1：序列化——先观测 worker 抢回 attempt-1
                # accounts（busy）再观测清理释放（free）再重抢；c5 释放后
                # 立即重抢会令 worker 永停旧门、park2 永不出现）。
                e_accounts = self._p10_rearm_accounts_gate(
                    self.users_root / "users.db", "T4b-rearm")
                try:
                    # 停点 2：owner free→busy 转移＝attempt-2 的新 reserve
                    # （c5 此处命中 attempt-1 残留 owner BUSY→v3 换入过早→
                    # attempt-2 plan 以 v3 为基线→重验一致→ok=True＝run3
                    # T4b 无 409 的直接根因）。
                    self._p10_wait_park(
                        owner_db, self.users_root / "users.db",
                        fresh=True, tag="T4b-park2")
                    self.assertTrue(_p10_replaceable(holder),
                                    "T4b: 停点 2 holder 尚未 reserve")
                    park_count += 1
                    _p10_diag("T4b-park2", "swapping-v3")
                    os.replace(v3, holder)
                finally:
                    _p10_close_quietly(e_accounts)
                thread.join(90)
                self.assertFalse(thread.is_alive(), "T4b: worker 必须结束")
                error = outcome.get("error")
                _p10_diag("T4b-outcome", "error=%r" % (error,),
                          "ok=%s" % outcome.get("ok"))
                self.assertIsNotNone(error, "T4b: 两次漂移后必须 409")
                self.assertIsInstance(error, HTTPException,
                                      "T4b: 漂移耗尽以 HTTPException 上抛")
                self.assertEqual(getattr(error, "status_code", None), 409,
                                 "T4b: 状态码 409")
                self.assertEqual(getattr(error, "detail", None), {
                    "code": "write_plan_drift",
                    "message_key": "team.flow.write_plan_drift",
                    "params": {}}, "T4b: write_plan_drift 三键载荷钉定")
                self.assertEqual(park_count, 2,
                                 "T4b: 两次 reserve 阶段（两次完整尝试＝单次"
                                 " reserve 合同＋至多 2 尝试合同）")
                self._assert_clean_state(
                    task_dir, (collab_path, owner_db, holder,
                               self.users_root / "users.db"), runtime, task_id)
            finally:
                # 泄漏根修（run_216 修复 1①）：任何失败路径（停点断言红、重臂
                # 失败、join 后断言红）都先排干 worker 再放行用例清理（幂等；
                # 否则保留连接滞留→rmtree WinError32→adata 静默级联）。
                thread.join(90)


class ConditionalDependencyDriftTests(P10WriteWindowBase):
    """用例 5：条件性依赖漂移在 writer 前重算（live 依赖→成员库入保留集）。"""

    def test_conditional_dependency_drift_restarts_before_writer(self):
        task_id = "p10-t5-livedep"
        instance = "inst-p10-t5"
        task_dir = self._init_team_task("t5", task_id)
        collab_path = self._prepare_collab(task_dir, task_id, instance)
        self._grant_member(task_dir, task_id, "bob")
        self._choice_member(task_dir, task_id, "bob")
        self._grant_member(task_dir, task_id, "carol")
        self._choice_member(task_dir, task_id, "carol")
        owner_db = self._full_screener_db(task_dir, "owner",
                                          articles=("K1", "K2", "K9"))
        library = _p10_member_library_id(USERS["bob"])
        holder = self._holder_db(task_dir / "bob.db", uid=USERS["bob"],
                                 library_id=library)
        self._append_holder_seal(holder, _p10_observation_payload(
            task_id=task_id, instance=instance, stage="stage1",
            item_key="K1", decision="include", sealed_at=P10_TS1,
            registration_prefix="reg-p10-t5"))
        self._append_holder_seal(holder, _p10_observation_payload(
            task_id=task_id, instance=instance, stage="stage1",
            item_key="K2", decision="include", sealed_at=P10_TS1,
            registration_prefix="reg-p10-t5"))
        v2 = self._holder_db(task_dir / "bob.v2.db", uid=USERS["bob"],
                             library_id=library)
        self._append_holder_seal(v2, _p10_observation_payload(
            task_id=task_id, instance=instance, stage="stage1",
            item_key="K1", decision="include", sealed_at=P10_TS2,
            registration_prefix="reg-p10-t5"))
        # v2 无 K2 seal：尝试 2 的 plan 对 K2 干净负发现→live 依赖。
        carol_db = task_dir / "carol.db"
        sqlite3.connect(str(carol_db)).close()
        self._register_source(
            collab_path, task_dir, instance, USERS["bob"], library,
            holder, "reg-p10-t5-bob")
        runtime = self._make_runtime(task_dir, task_id)

        baseline = _p10_count_live_connections()
        e_accounts = _p10_open_reserved_gate(self.users_root / "users.db")
        outcome = {"paths_locked": [collab_path, owner_db,
                                    self.users_root / "users.db", holder,
                                    carol_db],
                   "paths_free": [],
                   "write_key": "K9"}
        thread, outcome = _p10_start_window(
            runtime, self._task_info(task_dir, task_id), self._user("owner"),
            target_kind="actor", operation=self.op, item_keys=("K1", "K2"),
            body=_p10_body_probe_handles, outcome=outcome)
        park_count = 0
        try:
            # 停点（c6 修法 1 统一原语：owner free→busy＋users 门双锚——
            # worker 停在 attempt-1 reserve 的 accounts 处）。
            self._p10_wait_park(
                owner_db, self.users_root / "users.db",
                fresh=False, tag="T5-park")
            self.assertTrue(_p10_replaceable(holder),
                            "T5: 停点 holder 尚未 reserve")
            self.assertTrue(_p10_replaceable(carol_db),
                            "T5: 停点成员库尚未入保留集（live 依赖未发生）")
            park_count += 1
            os.replace(v2, holder)   # K1 四元组漂移＋K2 见证消失
        finally:
            _p10_close_quietly(e_accounts)
            # 解除局部引用（对齐 T1 的 e_bob 模式；c3 快审修复 2：必须在
            # join **之前**——worker 体内 gc 计数时主线程局部名不得再引用
            # 已关连接，否则 +6≠+5 确定性红）：已关闭但仍被引用的
            # Connection 仍被 gc.get_objects() 计数。
            e_accounts = None
            # 泄漏根修（run_216 修复 1①）：失败路径也必须排干 worker，
            # 否则其保留连接（collab/accounts 等）滞留句柄→用例临时目录
            # rmtree WinError32→箱 adata 静默滞留→级联 setUpClass AuthError。
            thread.join(90)
        thread.join(90)
        self.assertFalse(thread.is_alive(), "T5: worker 必须结束")
        self.assertIsNone(outcome.get("error"), "T5: 重算后窗口必须成功")
        self.assertTrue(outcome.get("ok"), "T5: writer 前完成重算并正常退出")
        self.assertEqual(park_count, 1,
                         "T5: 门只施加于尝试 1（尝试 2 自由完成）")
        self.assertEqual(outcome.get("body_runs"), 1,
                         "T5: 恰一次 yield（writer 在重算后单次执行）")
        plan = outcome["inputs"].plan
        self.assertEqual(
            dict(plan.live_dependencies),
            {(USERS["bob"], "stage1"): ("K2",)},
            "T5: 尝试 2 新 plan 的 live 依赖＝(bob, stage1)→(K2,)（工单锚）")
        self.assertEqual(sorted(plan.witnesses), [("stage1", "K1")],
                         "T5: 尝试 2 见证集只含 K1（K2 无见证入 live）")
        self.assertEqual(outcome["inputs"].item_keys, ("K1", "K2"))
        self.assertTrue(all(outcome["locked_map"].values()),
                        "T5: yield 体内保留集含成员库 bob/carol（os.replace "
                        "PermissionError＝入保留集实证）: %r"
                        % outcome["locked_map"])
        self.assertEqual(outcome["gc_now"], baseline + 5,
                         "T5: 体内活连接恰＝基线＋5（collab/owner/accounts/"
                         "bob/carol——成员库经 live 依赖入保留集；门已释放）")
        conn = sqlite3.connect(str(owner_db), timeout=15)
        try:
            self.assertEqual(conn.execute(
                _P10_COUNT_DECISIONS, ("K9",)).fetchone()[0], 1,
                "T5: writer 业务行持久（重算先于 writer 完成）")
        finally:
            conn.close()
        self._assert_clean_state(
            task_dir, (collab_path, owner_db, holder, carol_db,
                       self.users_root / "users.db"), runtime, task_id)


class MarkerDriftSharedRestartTests(P10WriteWindowBase):
    """支撑 1：漂移二类共用重算（marker 漂移一次→真实 sync 修复→成功）。"""

    def test_marker_drift_restarts_via_shared_handler(self):
        task_id = "p10-s1-marker"
        instance = "inst-p10-s1"
        task_dir = self._init_team_task("s1", task_id)
        collab_path = self._prepare_collab(task_dir, task_id, instance)
        self._grant_member(task_dir, task_id, "bob")
        self._choice_member(task_dir, task_id, "bob")
        owner_db = self._full_screener_db(task_dir, "owner",
                                          articles=("K1", "K9"))
        bob_db = self._full_screener_db(task_dir, "bob", articles=("K1",))
        carol_holder = self._holder_db(
            task_dir / "carol.db", uid=USERS["carol"],
            library_id=_p10_member_library_id(USERS["carol"]))
        self._append_holder_seal(carol_holder, _p10_observation_payload(
            task_id=task_id, instance=instance, stage="stage1", item_key="K1",
            decision="include", sealed_at=P10_TS1,
            registration_prefix="reg-p10-s1"))
        self._register_source(
            collab_path, task_dir, instance, USERS["carol"],
            _p10_member_library_id(USERS["carol"]), carol_holder,
            "reg-p10-s1-carol")
        runtime = self._make_runtime(task_dir, task_id)

        # c6 修法 1/3：停点改 accounts 门（E_a）＋注入改 marker 侧偏移——
        # c5 的「E_b plan 门＋collab busy 检出（恒真）＋bump owner」三重错相：
        # 检出在 enter 即触发（注入落在 plan 前）、bump 在真停点上会与
        # worker 的 owner 写锁互等。新停点上（worker 停在 reserve 的
        # accounts 处）plan 已完成、重验未开始——偏移 marker 恰落在
        # 「attempt-1 基线=plan 读、注入=停点施加、重验=读当前」窗口内。
        e_accounts = _p10_open_reserved_gate(self.users_root / "users.db")
        thread, outcome = _p10_start_window(
            runtime, self._task_info(task_dir, task_id), self._user("bob"),
            target_kind="actor", operation=self.op, item_keys=("K1",))
        try:
            try:
                self._p10_wait_park(
                    owner_db, self.users_root / "users.db",
                    fresh=False, tag="S1-park1")
                _p10_diag("S1-park1-detected",
                          "marker=%r" % (self._read_marker(bob_db, owner_db),),
                          "owner_rev=%s"
                          % self._read_owner_revision(owner_db))
                self._p10_skew_owner_marker(bob_db, owner_db, "S1-park1")
            finally:
                _p10_close_quietly(e_accounts)
            park_count = 1
            # 重臂（c6 修法 1 序列化原语）：worker 抢回 attempt-1 accounts→
            # 清理释放→重抢 E_a；尝试 2 停点证明重算真实发生。
            e_accounts = self._p10_rearm_accounts_gate(
                self.users_root / "users.db", "S1-rearm")
            try:
                self._p10_wait_park(
                    owner_db, self.users_root / "users.db",
                    fresh=True, tag="S1-park2")
                _p10_diag("S1-park2-detected",
                          "marker=%r" % (self._read_marker(bob_db, owner_db),),
                          "owner_rev=%s"
                          % self._read_owner_revision(owner_db))
                self.assertEqual(
                    self._read_marker(bob_db, owner_db),
                    self._read_owner_revision(owner_db),
                    "S1: 尝试 2 停点 marker 已被真实 sync 修复（重算真实"
                    "发生）")
                park_count += 1
            finally:
                _p10_close_quietly(e_accounts)
            thread.join(120)
            self.assertFalse(thread.is_alive(), "S1: worker 必须结束")
            _p10_diag("S1-outcome", "error=%r" % (outcome.get("error"),),
                      "ok=%s" % outcome.get("ok"),
                      "final_marker=%r" % (self._read_marker(bob_db, owner_db),),
                      "final_owner_rev=%s"
                      % self._read_owner_revision(owner_db))
            self.assertIsNone(outcome.get("error"), "S1: marker 修复后必须成功")
            self.assertTrue(outcome.get("ok"), "S1: 共用重算处理器成功路径")
            self.assertEqual(park_count, 2,
                             "S1: 两次 accounts 门停点（marker 漂移触发一次"
                             "完整重算）")
            conn = sqlite3.connect(str(bob_db), timeout=15)
            try:
                marker = conn.execute(
                    _P10_SELECT_MARKER, (owner_db.name,)).fetchone()
                self.assertIsNotNone(marker, "S1: 真实 sync 已写入 marker")
                owner_conn = sqlite3.connect(str(owner_db), timeout=15)
                try:
                    owner_rev = owner_conn.execute(
                        _P10_SELECT_CATALOG_REVISION).fetchone()[0]
                finally:
                    owner_conn.close()
                self.assertEqual(marker[0], owner_rev,
                                 "S1: marker 与 owner 当前 revision 一致")
            finally:
                conn.close()
            self._assert_clean_state(
                task_dir, (collab_path, owner_db, bob_db, carol_holder,
                           self.users_root / "users.db"), runtime, task_id)
        finally:
            # 泄漏根修（run_216 修复 1①）：任何失败路径都先排干 worker 再放行
            # 用例清理（幂等；否则保留连接滞留→rmtree WinError32→adata 静默
            # 级联 setUpClass AuthError）。
            thread.join(120)


class CatalogSyncStale409Tests(P10WriteWindowBase):
    """支撑 2：marker 二次漂移→409 catalog_sync_stale 载荷钉定。"""

    def test_catalog_sync_stale_409_payload(self):
        task_id = "p10-s2-stale"
        instance = "inst-p10-s2"
        task_dir = self._init_team_task("s2", task_id)
        collab_path = self._prepare_collab(task_dir, task_id, instance)
        self._grant_member(task_dir, task_id, "bob")
        self._choice_member(task_dir, task_id, "bob")
        owner_db = self._full_screener_db(task_dir, "owner",
                                          articles=("K1", "K9"))
        self._full_screener_db(task_dir, "bob", articles=("K1",))
        carol_holder = self._holder_db(
            task_dir / "carol.db", uid=USERS["carol"],
            library_id=_p10_member_library_id(USERS["carol"]))
        self._append_holder_seal(carol_holder, _p10_observation_payload(
            task_id=task_id, instance=instance, stage="stage1", item_key="K1",
            decision="include", sealed_at=P10_TS1,
            registration_prefix="reg-p10-s2"))
        self._register_source(
            collab_path, task_dir, instance, USERS["carol"],
            _p10_member_library_id(USERS["carol"]), carol_holder,
            "reg-p10-s2-carol")
        runtime = self._make_runtime(task_dir, task_id)
        bob_db = task_dir / ("%s.db" % tasks_mod.slugify_screener("bob"))

        # c6 修法 1/3（与 S1 同构；差异仅第二次注入→耗尽 409）：accounts 门
        # 停点＋marker 侧偏移。run3 定因＝collab busy 恒真检出在 enter 即触
        # 发、两次 bump 全落 attempt-1 plan 前→attempt-2 的 sync_catalog 把
        # 两次 bump 全吸收为 marker 修复→重验一致→ok=True。c6 停点上注入
        # 恰落「attempt-N 基线=plan 读、注入=停点施加、重验=读当前」窗内：
        # 停点 1 偏移→attempt-1 重验漂移；attempt-2 sync 修复→plan→reserve
        # →停点 2 再偏移→attempt-2 重验再漂移→耗尽 409 catalog_sync_stale。
        e_accounts = _p10_open_reserved_gate(self.users_root / "users.db")
        thread, outcome = _p10_start_window(
            runtime, self._task_info(task_dir, task_id), self._user("bob"),
            target_kind="actor", operation=self.op, item_keys=("K1",))
        try:
            try:
                self._p10_wait_park(
                    owner_db, self.users_root / "users.db",
                    fresh=False, tag="S2-park1")
                _p10_diag("S2-park1-detected",
                          "marker=%r" % (self._read_marker(bob_db, owner_db),),
                          "owner_rev=%s"
                          % self._read_owner_revision(owner_db))
                self._p10_skew_owner_marker(bob_db, owner_db, "S2-park1")
            finally:
                _p10_close_quietly(e_accounts)
            e_accounts = self._p10_rearm_accounts_gate(
                self.users_root / "users.db", "S2-rearm")
            try:
                self._p10_wait_park(
                    owner_db, self.users_root / "users.db",
                    fresh=True, tag="S2-park2")
                _p10_diag("S2-park2-detected",
                          "marker=%r" % (self._read_marker(bob_db, owner_db),),
                          "owner_rev=%s"
                          % self._read_owner_revision(owner_db))
                self._p10_skew_owner_marker(bob_db, owner_db, "S2-park2")
            finally:
                _p10_close_quietly(e_accounts)
            thread.join(120)
            self.assertFalse(thread.is_alive(), "S2: worker 必须结束")
            error = outcome.get("error")
            _p10_diag("S2-outcome", "error=%r" % (error,),
                      "ok=%s" % outcome.get("ok"))
            self.assertIsNotNone(error, "S2: marker 二次漂移必须 409")
            self.assertIsInstance(error, HTTPException,
                                  "S2: 漂移耗尽以 HTTPException 上抛")
            self.assertEqual(getattr(error, "status_code", None), 409,
                             "S2: 状态码 409")
            self.assertEqual(getattr(error, "detail", None), {
                "code": "catalog_sync_stale",
                "message_key": "team.flow.catalog_sync_stale",
                "params": {}}, "S2: catalog_sync_stale 三键载荷钉定")
            self._assert_clean_state(
                task_dir, (collab_path, owner_db, carol_holder,
                           self.users_root / "users.db"), runtime, task_id)
        finally:
            # 泄漏根修（run_216 修复 1①）：任何失败路径都先排干 worker 再放行
            # 用例清理（幂等；否则保留连接滞留→rmtree WinError32→adata 静默
            # 级联 setUpClass AuthError）。
            thread.join(120)


class TeamWriteInputsShapeTests(P10WriteWindowBase):
    """支撑 3：TeamWriteInputs 新字段形状＋窗口内身份锚（合同驱动）。"""

    def test_team_write_inputs_new_field_shapes(self):
        helper = getattr(tws.retained_team_write_inputs, "__wrapped__",
                         tws.retained_team_write_inputs)
        signature = inspect.signature(helper)
        self.assertEqual(list(signature.parameters), [
            "runtime", "info", "user", "target_kind", "operation",
            "item_keys"], "S3: 签名参数序（P10 新参数钉定）")
        for name in ("target_kind", "operation", "item_keys"):
            self.assertIs(signature.parameters[name].kind,
                          inspect.Parameter.KEYWORD_ONLY,
                          "S3: %s 必须为 keyword-only" % name)
        fields = [field.name for field in dataclasses.fields(
            tws.TeamWriteInputs) if not field.name.startswith("_")]
        self.assertEqual(fields, [
            "ctx", "scope", "collab_conn", "owner_conn", "actor_conn",
            "target_conn", "connections", "roster", "operation", "item_keys",
            "plan"], "S3: TeamWriteInputs 字段序（P10 新字段 operation/"
            "item_keys/plan 钉定）")
        self.assertTrue(dataclasses.is_dataclass(tws.TeamWriteInputs))
        self.assertTrue(tws.TeamWriteInputs.__dataclass_params__.frozen)
        self.assertIsInstance(tws.TeamWriteInputs.cleanup_errors, property)
        self.assertIsNone(tws.TeamWriteInputs.cleanup_errors.fset)

        task_id = "p10-s3-shape"
        instance = "inst-p10-s3"
        task_dir = self._init_team_task("s3", task_id)
        collab_path = self._prepare_collab(task_dir, task_id, instance)
        owner_db = self._full_screener_db(task_dir, "owner",
                                          articles=("K1", "K9"))
        runtime = self._make_runtime(task_dir, task_id)
        outcome = {"expected_operation": self.op,
                   "expected_keys": ("K1",), "write_key": "K9"}
        thread, outcome = _p10_start_window(
            runtime, self._task_info(task_dir, task_id), self._user("owner"),
            target_kind="actor", operation=self.op, item_keys=("K1",),
            body=_p10_body_target_write, outcome=outcome)
        thread.join(90)
        self.assertFalse(thread.is_alive(), "S3: worker 必须结束")
        self.assertIsNone(outcome.get("error"), "S3: 无见证净负发现窗成功")
        self.assertTrue(outcome.get("ok"), "S3: 窗口正常退出")
        self.assertTrue(outcome["operation_is"],
                        "S3: inputs.operation is 传入 operation")
        self.assertTrue(outcome["item_keys_eq"],
                        "S3: inputs.item_keys＝规范化键域")
        self.assertTrue(outcome["plan_type_ok"], "S3: inputs.plan 类型")
        self.assertTrue(outcome["plan_is_scope_plan"],
                        "S3: inputs.plan is scope.keyed_plan()（附件同一性）")
        self.assertTrue(outcome["all_in_transaction"],
                        "S3: 保留连接全部持有 B1 写事务")
        self.assertEqual(outcome["inputs"].plan.item_keys, ("K1",),
                         "S3: plan.item_keys＝请求键域")
        self.assertEqual(outcome["inputs"].plan.live_dependencies, {},
                         "S3: 无依赖 plan 的 live_dependencies 恒空")
        conn = sqlite3.connect(str(owner_db), timeout=15)
        try:
            self.assertEqual(conn.execute(
                _P10_COUNT_DECISIONS, ("K9",)).fetchone()[0], 1,
                "S3: 目标业务行持久")
        finally:
            conn.close()
        self._assert_clean_state(
            task_dir, (collab_path, owner_db,
                       self.users_root / "users.db"), runtime, task_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
