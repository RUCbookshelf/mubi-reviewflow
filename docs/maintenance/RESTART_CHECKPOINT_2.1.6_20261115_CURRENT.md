# ReviewFlow 2.1.6 最新重启续接入口 — 2026-11-15 15:03 UTC

用户要求完成当前最小任务后尽快存档并重启；现停止开发和测试。不要依赖旧子智能体恢复，不按旧入口的 A2 未实施状态返工。所有七个当前 worker 已报告停笔、无在途进程/写操作；主控仅完成存档。服务在保存期间出现 StatusServiceRestart，主控恢复后继续同一存档，没有重新开发。

本次目录：`E:/mubi-reviewflow/out/maintenance216/restart_20261115T150352Z/`；同名 `.zip`、`.zip.sha256`，目录内 `MANIFEST.json` 和 `VERIFY.json`。必须核验完成回执，不能仅凭目录存在认为完成。旧 `restart_20261115T012411Z` 原样保留，1104 项旧档不是本次当前状态。

仓库 E:/mubi-reviewflow；分支 maintenance/2.1.6-20261114；HEAD 80dd35fd3a8833bb5fa81eb3ae7b85057fd25a22；产品版本仍 2.1.5。大量原有未提交修改保留。本次维护无 add/commit/push/tag/release/升版本授权；不覆盖、clean 或 reset 整树。AI HTTPS 专项的 GitHub 推送授权不能扩展到维护。

## 最新人类业务裁定（必须先读）

用户明确同意：**已经完成的条目，保留完成时的结论；新成员参与尚未完成的阶段。** 权威合同 `docs/maintenance/TEAM_FLOW_REPAIR_CONTRACT_20261114.md` v2 §9，覆盖旧版对已完成阶段随当前名单/判断变化重新计算或作废的解释。

- 完成按文献×阶段记录，全员一致及合法仲裁形成的 include/exclude 均算完成。未完成、待仲裁仍按当前明确应筛名单及当前角色。
- 人员新增/移除/职责变化不撤销已完成阶段；新成员不补历史意见。初筛已纳入可参与未完成复筛；初筛排除保持停止；两阶段纳入才按角色进入编码/评价。
- 完成阶段只读，保留当时名单、意见、结果和来源；owner 管理身份无业务旁路。不增加重开、补票或强制解锁。
- 主控之前自行推导的 first-current-vote 例外**不采用**。旧 addendum 已标 NOT_ADOPTED；代码草稿仍在但未接路由，不可把用户后来同意新规则追溯当作批准旧例外。
- 完成事实损坏、任务/owner 身份不可信须明确拒绝；旧 CSV/generic latest 不能自动成为完成。不能编造丢失的历史名单。

## 已关闭的限定批次（不要重跑旧矩阵）

A1、A2 借用 writer 已完成限定独立验收。B1 连接资源、历史裁决事实读取、policy 同连接读取、trace prepared 准备、B2a 输入窗口、evidence 四读 helper 六层均限定 Spec/Quality/Execution PASS。不是全部写路由或整树共同验证。

- A2 27 方法 unittest OK、pytest 27P+41sub；执行独核 `out/maintenance216/s2a2_resume_20261115/fixr1/REVIEW_EXECUTION_CC.txt`。
- B1 FIX-R1 11OK/11P+2sub；history 20OK/20P+16sub；policy 8OK/8P+30sub；trace FIX-R2 8OK/8P+7sub；B2a FIX-R1 测试 30OK/30P+13sub。
- evidence a8df/test d37 8OK/8P，35 源/1024757B，最终执行独核 `out/maintenance216/s2b_resource_20261115/evidence_reads/REVIEW_EVIDENCE_EXECUTION_CC.txt`，SHA ef6e1c2184b2803a1128d52e03cae88ab7f043de10bdbe3783e17c6648e75446。
- 所有原失败 raw、工具失败和独审限制保留。B2a 197 源曾显式使用旧 evidence df059/pdf46fa，不能当作当前 a8df/AI5ca 整树验证。

## 当前最小单元：C1 同连接读取 — 产品静态通过、测试静态 FAIL、未运行

| 当前文件 | SHA256 | bytes |
|---|---|---:|
| custom_backend/review_workflow_api.py | e72fe1699662f826f848024ed298de7cdec14851edca8e388a09bf18041db583 | 74677 |
| coscreen/coding.py | d39b802dcc8bcc290b7a8f90de4366c9d362bf375af0123723c73885b17dc79d | 77827 |
| build/tests/test_s2_workflow_connection_reads.py | 6b606574d1d59f43dc170da267403d2370393fc8bca1537f45941833aefc9f72 | 37774 |
| coscreen/research_trace.py | ee42df10cd69651228775047d8ecb17a787ada530d3211e6fd28bd8e4bf5453c | 74540 |
| coscreen/coding_evidence.py | a8df50e8fc89f64a933216a26a756c04955f15ba4417bcc9799f3ffc266aac72 | 49380 |
| custom_backend/team_screening_write.py | 905366172269058781ba6d74664e5d30bd7867ce060b0bcf51333169235c1388 | 45364 |
| custom_backend/team_screening_runtime.py | 08e5ff906b3abaa7d8823495ad0a6edaae12eb2d38b3a3c1327fa8f54cf3b3a0 | 48448 |

C1 API：WorkflowContext 尾部 write_inputs=None；12 read 方法消费同输入窗口连接；4 getter 加 kw conn=None；默认路径保留；不修改 eligible/writable_reason/write_guard/context/notify/trace_actions。产品静审 `out/s2c_read_connections_20261115/REVIEW_C1_READ_STATIC_FLASH.txt` + ADDENDUM，PASS（静态）。ADDENDUM 撤回 AST 重复计数和 B2a 已测新 kwargs 的错误说法：实际无模块级同名副本，12 方法+6 新 helper，a8df 实测来自 evidence35源。

coding_progress_on：与 C1 调用固定签名匹配，native active Row，无隐藏重连/事务所有权；与旧 coding_progress 共用纯计数；其余42既有函数 AST 不变。`progress_on/REVIEW_PROGRESS_ON_CC.txt` 静态 Spec/Quality PASS，SHA 3bf41477355126ce9b63e4c0b19ed022a12fc33577b4fd8060c880497839cbcf。尚无 C1 执行 PASS。

最终 C1 test 6b606574，12 方法。独立静审 `tests/REVIEW_C1_TEST_CC.txt` **FAIL**，未跑任何产品/SQL/pytest/unittest。明确两项阻塞：
1. test_01:332-333、test_10:665-666 在已持 BEGIN IMMEDIATE 的同库再开第二个 BEGIN IMMEDIATE，先锁忙，达不到测试目标。no_row 改 deferred BEGIN 或独立 DB，并保持默认 row_factory。
2. test_04:485-487/503、test_07:563-569 改了 workflow_coding_fields.definition_json，但 mapping 用 coding_dimensions 的实际定义；因此没有 mapping gap，预期409不可达。应改 owner_conn 的 coding_dimensions id=1 name/description，保 dim2 相等；只改 evidence requirements 不能移出 mapped。

**重启后先 actual impact 这四个测试方法，保存6b before，最小修测试夹具，独立差量静审后再新源箱运行。** 不改产品来迎合坏夹具，不继续旧 staged 箱测试冒新候选。

C1 staged 根 `E:/rf216-scratch/c1exec-s2c-3d7k9f/`：197 文件/5117619B，STAGED_NOT_RUN，无 EXECUTION_INDEX/运行记录。map197 只是预期配置，不能称 runtime 实测。冻结工具 controller2951 + c1_adapter；manifest/expected/required/chain/stage脚本齐全。trace 来自 ee42 before；API e72、coding d39、evidence a8、policy905a、pdf 当前AI5ca；B2b草稿/新终局实现不入箱。测试修后在新箱执行，不覆盖此箱。正式 launch 尚未放行。

## 新完成规则：设计已交付，产品尚未实施

三份必读（`out/maintenance216/team_terminal_20261115/`）：
- TERMINAL_READ_SEMANTICS_TERRA.txt（40行）。
- TERMINAL_READ_LAZY_ADDENDUM_TERRA.txt（24行）。
- TERMINAL_PERSISTENCE_RULING_SOL.txt（93行，SHA 07d26eb5f1a058e1542efc6b34cb9b9d9d3b094249232404e9874d80fc61c083）。

主控采用的实施方向：一致完成 seal 与最后一票同 actor 单库事务；仲裁 generic/attestation/seal/team会话状态同 collab 单库事务；改变名单/职责之前在持续 collab 锁内汇集原本地 seal，再与名册变更一次 collab commit。来源登记先于首用投票，完整快照不可变，不用 roster revision 重新开阶段，不假跨库原子。具体 seal schema/接口仍须拆小定稿再实现。

B1 通用单目标保留。B2a 必须 op/stage/keys-aware：先按键验证自足完成事实，只有未完成依赖才加载当前票/ledger。完成A不能被新D缺库/坏库或无关B遮蔽；owner/actor/任务/来源损坏仍拒绝。现 `_required_member_path` 对真正 FileNotFound 记录 missing_members，并非全部缺库立拒；现存坏/不可读/busy和全域加载才是需修边界。TSR也必须按键惰性读取，不能先完整求全域再末尾覆盖终局。不要为保持旧905366字节而错误引入全任务依赖。

## B2b 草稿和测试作者撤回事件（准确保留边界）

产品 team_write_checks.py 5a258b638bed4b2b0914010969e68c9ccc841cab07b32f6c87fa8f3962289be8/26604B 仍含不采用的补票分支，未接线、未运行。测试当前实际1e77f5cd582900f7e0dd4019f1b8b591c9356fe292206801f293f1862587848c/40344B/13方法。Terra REVIEW_BUSINESS_CHECKS_TERRA.txt：旧候选 Spec SCOPE_HOLD、Quality FAIL。

非争议测试缺陷：通用owner未配角色→全阶段awaiting_config；K2域内无票误断article_not_eligible；缺FINAL_EXCLUDE/缺票链及native/active/closed负控。普通fixture应owner非筛选reconciler，owner未配置负控独立；后续按新完成协议一次整合，不能直接运行旧13方法。

Luna一度交df4886a9/51272B/15方法，之后误把较早主控SendMessage当成最新人类只读指令，撤回候选并删除自己新建的 after/test_s2_team_write_checks.py、owner_fixture.patch、OWNER_FIXTURE_STATIC.txt；before保留。**df4886 after/patch/report NOT_PRESERVED**，不重造为已保存证据。最后候选/before实际均1e77，主控已用独立文件hash核实。没有工具权限拒绝；属于消息来源/先后误读，不是人类取消整体开发授权。用户随后重启停笔指令真实有效，现不复做。

## 历史清空接缝：仅 impact，没有 CODE

`out/s2c_history_clear_20261115/impact/IMPACT_CLEAR_HISTORY.txt`、before ee42 完整保留。clear_history prepared=False 扩展尚未开始。graph UNKNOWN/同名污染327 CRITICAL不采信，真实1 HTTP caller+2测试，静态MEDIUM。协议见 `out/maintenance216/s2b_business_20261115/HISTORY_CLEAR_TRANSACTION_RULING_SOL.txt`：全分区整体保护先检、持续collab裁决锁、每物理目标独立commit，不伪跨库atomic。未来还需消费§9所有完成状态。

## 续接顺序与边界

1. 校验本存档清单/ZIP及关键源码，先读当前progress顶部、合同§9和本文件，不自动恢复整个工作树。
2. 修C1两类测试夹具并独立差量审，新候选新箱真实unittest/pytest，随后独立证据核验；接受已闭六层，不重跑旧返工矩阵。
3. 将新完成事实存储、来源登记/归档、按键读取和op-aware输入规划拆小，逐symbol upstream impact后实现；替换不采用的补票草稿，完成真实写路由和历史保护。
4. S3生产授权逐条仲裁、S4当前PRISMA/canonical task_id事件及精确前后端交接；最后停笔EOL、新共同完整源码箱八步和整体独立复核。

custom_frontend只读；不碰真实data/8613/旧证据/隔离区/rf215-auto/rf215-final。产品/测试启动cwd即新scratch，312 -I -B，DATA绝对state、TEMP/TMP/TMPDIR/RF_R9_TMP/RF_R9_TMP_ROOT显式短E，MODE可null。不要读agent output/transcript；不要换模型配置或账户绕配额。普通任务拆oc-ds/cc-ds/g-flash/luna，真实疑难Terra/Sol medium，优先subagent不CreateSession。旧agent不能恢复则重派新agent并交付本文件，不伪造独立审查。

本次备份是源码、方案、工具与可核实原始执行证据的白名单存档；不包含研究数据库/密钥/.git/缓存/虚拟环境，不是用户真实数据备份。原scratch与旧档均保留，没有清理。E盘此前清理已净释放14.22GiB；AI HTTPS专项已单独GitHub推送，不重开。
