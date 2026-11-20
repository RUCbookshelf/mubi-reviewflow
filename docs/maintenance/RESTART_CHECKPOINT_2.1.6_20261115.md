# ReviewFlow 2.1.6 重启交接点（2026-11-15）

> **最新入口：** [RESTART_CHECKPOINT_2.1.6_20261115_CURRENT.md](RESTART_CHECKPOINT_2.1.6_20261115_CURRENT.md)。2026-11-15 15:03 UTC 新存档 `out/maintenance216/restart_20261115T150352Z/` 覆盖本页下方早期状态。A2和六基础层已限定关闭；C1产品静态通过、测试静态FAIL尚未运行；新完成阶段保留规则已获用户确认。下文仅保留01:24 UTC旧交接溯源，不能按“A2未实施”返工。

用户最新指令：保存当前进度、存档，准备重启电脑；重启后子智能体可能无法启动。本次据此暂停开发，不再放行 A2 代码。重启后先读本文件，不能按旧交接中的早期状态重做已完成阶段，也不依赖原子智能体恢复。

存档目录：`E:/mubi-reviewflow/out/maintenance216/restart_20261115T012411Z/`。同名 ZIP、SHA256、归档文件清单及核验结果由本次保存作业生成；先核清单再使用。原始 scratch 证据保留只读，存档复制不改变其结果。

仓库 `E:/mubi-reviewflow`；分支 `maintenance/2.1.6-20261114`；实际 HEAD `80dd35fd3a8833bb5fa81eb3ae7b85057fd25a22`。候选仍未提交，包含大量新文件。没有 add/commit/push/tag/release 或升版本授权，产品版本仍为 2.1.5。不做清理、恢复或覆盖整个工作树。

## 1. 已完成与当前停止位置

- FLOW 纯资格核心、Safety 测试隔离、角色接入修复、S1a format2 账本、S1b 仲裁证明、S1c 只读装载均已完成本轮限定独立验收。S1c 最终70项 unittest、真实 pytest10项及生命周期证据已关闭；不重启旧 R3–R8 返工矩阵。
- Task8 发布验证入口修复已过限定评审；整版完整八步尚未执行。
- **S2-A1 已完成产品/测试静态独审及实际证据独审，可以关闭连接借用原语这一批。** 产品最终四源和测试见下表。
- **整个 S2 尚未接通**：A2 其余九类 writer 尚未改产品，B 全输入保护窗口、C 路由/队列/历史守卫未实施。其后 S3 逐条仲裁会话、S4 PRISMA/任务事件及总前后端交接、最终 EOL/完整八步/整体审查均未完成。

| A1 当前文件 | SHA256 | bytes |
|---|---|---:|
| coscreen/db.py | 55a47eabbe1fa7e33efe552560b8ec908ae93ffdfaec8e7e73e17f8b14f69cec | 122447 |
| coscreen/fulltext.py | 58483e2c506fbb31d1379d4e50a4493c9637ad6c5a4c457b7ae5f15d5d9028a3 | 55605 |
| coscreen/workspace_undo.py | 608284f8d05ac569bd94e9c33a03fe5a24c7f3fc5a193e8c6666958a80ef2fbf | 39558 |
| coscreen/research_trace.py | c06dd23b4d0a1d50a85b9b761c36b7490ff37cf2fbacf1f8ce5ad5d51c982b86 | 67829 |
| build/tests/test_s2a1_borrowed_writer.py | 5b9c3a979d4a6aa586d7ab2bf3e4db9260b46e564b6226406b97332c1a1361c2 | 54422 |

A1 接口：save_decision/save_stage2/undo 的 keyword-only conn=None；begin_save/begin_capture_transaction 的 prepared=False。borrowed 必须 native SQLite Connection+已有事务，所有业务/ledger/undo/trace/request 同连接，不 BEGIN/commit/rollback/close/DDL/重连。默认路径保持旧时钟和首次导入顺序。prepare 在最终资格窗口前，prepared trace 必须确认三表及本 context setting，再复用 is_recording 现读。

## 2. A1 真实结果与工具失败的准确解释

独立终审：`out/maintenance216/review_s2a1_final_evidence_terra.md`。静态：`review_s2a1_fixr1_static_terra.md`。作者与修复报告同目录 `s2a1_borrowed_writer_author_report.md`、`s2a1_borrowed_writer_fixr1_report.md`、`s2a1_test_fix_sol_r1_report.md`。

- RED `E:/rf216-scratch/r-c2ce66`：17唯一方法，child rc1，errors=15；其中 C12b 两个 subtest 各报错，实际14方法受影响、另3方法通过。均为旧接口不接受 conn/prepared 的 TypeError，不扩称15个独立事务反例。
- GREEN `E:/rf216-scratch/g-7aa167`：同17唯一方法全部通过，无 skip，child rc0。
- pytest `E:/rf216-scratch/p-640929`：相同17唯一节点通过，另4 subtests通过，无 skip，child rc0；**原 runner/CLI rc2，meta/summary=failed，runtime.json snapshot=null，exit-state.json为空，原样保留。**

pytest 原 stdout 第8行的 runtime marker 嵌在 node 名后，byte offset453、1-based列132；主解释器 PID31540/PPID37576。exit 第33行byte32068，cleanup 第29行byte28524。旧工具只认行首，故拒收。Terra已只读从原始字节定位复核，原 before/after 在真实时点已采样；235源六组逐项一致、实际模块/path/hash、uv一级关系、时间序、六env退出恢复、默认data初末、rawhash和实际清理全部通过，故实际执行证据限定PASS，原工具FAIL不改。

时间序示例 pytest：50225625000000 < 50225640000000 < 50230265000000 < 50232234000000 < 50232500000000 < 50232515000000，对应 before结束/launch/runtime/exit/wait/after开始。pytest40个自有目录均不存在，借用state根保留；bare两run generation真实closed，pytest不冒称本模块关闭session owner。

本轮 runner：`E:/rf216-scratch/s1c-final-harness-s2a1-modmap-3892e1b59d5b/runner.py`，SHA4af5c2da96c4c34fc723ec51702f7ab56fefef9232f2f8601ddcef85399b66ee。支持 --required-modules 映射，A1为db/fulltext/workspace_undo/research_trace/__test__。**未来需修嵌行marker解析，不能再因相同格式问题重复跑已通过A1。** 新工具草稿已停笔：E:/rf216-scratch/s2a2-marker-runner-16509d659561/runner.py，SHA28d9bbb2aed1e02fcf5369bfb4820c49f3829595ab3bc56e2cdb6b3055a32859 /65994B；仅extract_primary_snapshot改为按raw bytes定位嵌行marker，**尚未AST/编译/解析正反控，不可当可信工具**。原4af5不变。草稿复制到存档drafts/marker_runner；重启后先静态语法及最小纯解析正反控，核行首/嵌行/坏JSON/多候选和PID门，再接受。所有本轮智能体均已确认停笔，无在途测试/工具进程或写入。

## 3. A2 已准备，尚未正式整合/执行

GLM只读 before/10份 upstream impact：`out/maintenance216/team_flow_integration_design_evidence/s2a2/`。全部UNKNOWN（索引不含coscreen），补静态消费者main/workspace_undo_api/coding_consensus_api，风险MEDIUM；无可信HIGH/CRITICAL。正式动手前仍按每个实际symbol补必要impact，新增函数再次修改也适用。

四before：coding.py bced92d3d7394175d898616b68a366c5fec01831edcedd957f744f9b132e4d0a；coding_evidence.py 013b9f55f94bf4f1213782e404c0435fef8a440a55130fae18d5b0fd46a75a92；fulltext.py为上表58483；coding_consensus.py 6ab96129fcb53a0a1339024e23db11afdb96ba726baa30e73b35cf5e515695b5。

既定九writer：coding.save_coding_value/save_coding_values_batch；coding_evidence.save_form/stage_upload；fulltext.add_highlight_rects/delete_highlight；coding_consensus.append_rater_record/append_consensus_record/freeze_consensus。均追加kw-only conn=None，共用唯一算法体；既有_write本体不动。coding batch入口import及逐行_now按原语义保留。三consensus统一conn，显式窗口前接口固定 `prepare_coding_consensus_storage(db: StrPath) -> None`，短自有连接执行原_SCHEMA并commit/close，失败原样上抛；collab trace准备完整性留B/C。

GC定案：borrowed stage_upload只同conn落行及写新文件，不GC、不监听外部commit、不新增deferred回调/返回协议。caller rollback后可有待回收孤儿，不能删仍被已提交数据引用的文件。standalone破坏性GC也移到真实业务commit后，清理SQL用独立后续事务；清理失败不能冒业务已回滚。B/C于commit与释放后调用现有cleanup_staging。

Sol草稿原目录 `E:/rf216-scratch/a2-sol-draft-20261115-01/`，本次存档复制到 `drafts/a2/`。a2_case_increment.py SHA eaf177ffeb6d3eb0ed07d04514f9be742de33e6a03ef942e10e945c88e378d7e /21110B/9方法；a1_frozen_reference.py为5b9c原字节。仅AST/组合compile，未运行产品/SQL/tests。覆盖九writer、batch第二写真实触发失败、普通/canonical表单真实附图、upload/GC/文件引用保护、default正控。正式整合插入5b9c __main__前，共26方法；复用稳定fixture/Recorder/lifecycle，仅在首次snapshot前真实加载coding/coding_evidence/coding_consensus并补三实际模块file/hash及required map。草稿不是独立driver或测试PASS。

## 4. 用户最新业务与模型裁定

裁决后对应条目/阶段的轨迹、历史只读，禁止编辑/删除/撤销/恢复/覆盖，owner无旁路；后端保护窗口强制，证明过期不自动解锁。批量清除历史触及受保护内容也不得豁免；不得仅从actions=false猜裁决。未裁决合法撤销、其他条目/阶段和授权查看导出保留，不设计重开/整任务冻结。

正式合同 `docs/maintenance/TEAM_FLOW_REPAIR_CONTRACT_20261114.md` §8；集成设计§16；前端规格 `out/maintenance216/history_read_only_frontend_spec.md`，已有真实组件定位、十语言文案，固定reason history_read_only_after_arbitration及词典键team.history.read_only_after_arbitration。**只是合同/规格已入盘，后端历史守卫和UI尚未实施。** 不在A1/A2底层原语偷偷开collab库判资格。

模型：用户2026-11-15明确egg配额已完，普通worker直接oc-ds/g-flash/cc-ds，不再先尝试egg。复杂安全/事务GLM，独立异构Terra；五轮上限直接Terra子智能体，仍无法解决再Sol。原子智能体可不可恢复不影响读取本存档；不可用时主控可按已授权范围继续实施和隔离验证，**独立异构评审仍须单列待完成，不能以主控自审冒充**。不改账户/模型权限配置来绕不可用状态。

## 5. 重启后的最短续做顺序

1. 核存档manifest/SHA256和上表当前五文件；仅定位差异，不覆盖未知新改动。最新调度状态看progress顶部，不看其底部早期“只收集交接”历史。
2. 接受已完成的A1限定PASS，保留pytest工具FAIL；若嵌行解析新工具未验收，按已知小缺陷修新副本，保raw byte位置和全部原身份/时序/hash门，不重造框架或重复A1矩阵。
3. 直接按既定A2九writer合同实施，整合九方法草稿，实际隔离验证及异构复核后进入B/C。减少重复准备和额外留证，不再次扩长期计划。
4. B/C全输入保留：canonical task RLock→collab BEGIN IMMEDIATE→锁内现读owner/roster/roles→owner及规范排序其余输入/目标，含users.db，物理去重；实际writer借conn，单目标commit，其余输入rollback释放，通知在commit及释放后。准备/同步在窗外，catalog marker漂移完整释放后有限重试。历史只读在有task/item/stage上下文的路由/服务落实。
5. S3 server/desktop逐条仲裁及当前绑定；S4现行PRISMA/canonical task_id事件/总FE-BE交接；全部停笔后限定EOL、新共同源码箱完整八步、最终异构审查与台账收口。未获发布授权。

## 6. 运行与数据边界

custom_frontend全部只读；不碰真实data和8613。任何产品/API/pytest/unittest启动时cwd就是新纯源码scratch，不从主仓启动后chdir。Python用E:/rf216-scratch/validation-venv312/Scripts/python.exe（3.12.13）；-I -B。COBOOKSHELF_DATA_DIR为绝对scratch，TEMP/TMP/TMPDIR/RF_R9_TMP/RF_R9_TMP_ROOT五键显式短E；MODE可null。只读源码/工具Node作业不导产品。

不把真实数据库、密钥、.git、缓存、工具残留复制进验证源箱；旧验证箱/日志/包只读。真实数据无事故前快照，不能声称逐字节回滚或绝对未写。不碰rf215-auto/rf215-final/legacy-shell隔离区，含users.db目录不清理。

完整八步入口build/run_validation.py：单进程pytest build/tests -q、三个Python校验器、三个Node静态校验器、一个含24文件的node --test。不得靠分段/skip/ignore/放宽容差压红。本机Node22，未做远程Actions/三平台native/iOS真机；不冒已完成。

GitNexus每个既有symbol改前upstream impact并报告blast；可信HIGH/CRITICAL先通报。MCP不可用时既有CLI node .gitnexus/run.cjs impact SYMBOL -r mubi-reviewflow --direction upstream；UNKNOWN不计0，裸名同名污染不采信。提交前detect_changes义务保留，但当前无提交授权。
