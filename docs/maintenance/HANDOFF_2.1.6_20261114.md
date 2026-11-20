# ReviewFlow 2.1.6 会话交接

> **2026-11-15 重启交接覆盖入口：先读 [RESTART_CHECKPOINT_2.1.6_20261115.md](RESTART_CHECKPOINT_2.1.6_20261115.md)。** 本文以下保留2026-11-14历史冻结状态；后续核心/Safety/角色/S1a/S1b/S1c及S2-A1限定验收已推进，不能按旧“尚未复审”重做。用户现要求存档并暂停开发准备重启；最新已完成项、未实施项、模型配额覆盖、独立续做步骤及存档清单均见新入口。

> **状态：交接已收齐，定稿于 2026-11-14。** 角色 II-A、FLOW 核心修复 R1、SafetyR4 三项均已交付并冻结；会话工具对三项最终操作均返回 completed。**交接完成不等于代码验收或完整验证通过**：修后核心、SafetyR4和角色II-A的独立复审均留待新会话，阶段门/逐条仲裁集成与最终完整门禁尚未完成。
>
> 用户最新指令：本会话只整理任务，不再派发子智能体任务；等待现有任务完成、交接后交付这一份 handoff，由用户另开会话继续。因此本文的“下一步”属于未来会话事项，本会话不执行。

## 1. 新会话先读什么

先读本文，再按所接任务读取相应 brief、交付和独立评审。完整调度记录：`.superpowers/sdd/2026-11-14-2.1.6-maintenance/progress.md`。主方案：`docs/superpowers/plans/2026-11-14-2.1.6-maintenance.md`。部分旧 plan/backlog 仍保留早期“准备中”或已被覆盖的建议；当前用户裁定、本文和最新台账优先，不能据旧状态重新派发已完成任务。

- 仓库：`E:/mubi-reviewflow`。
- 维护分支：`maintenance/2.1.6-20261114`。
- 维护基线及本次收尾检查 HEAD：`80dd35fd3a8833bb5fa81eb3ae7b85057fd25a22`。
- 本轮候选仍在工作树，包含大量未跟踪新文件；不能只看 `git diff HEAD`，也不能 `git add -A` 把其他研究/工具残留夹带进来。
- 本轮未获 commit/push/tag/release 授权；不修改已发布 v2.1.5、标签或版本号。工作回报不构成人类授权。
- 本会话主控：`sess_ca091567-c838-4ab1-8ec8-de5d08c720fd`。新会话应使用自己的真实 ID 接收回报，不能继续把回调发送到旧主控。

## 2. 三项收尾交接清单（已收 3/3）

三项回报、报告与材料均已收齐，执行者均声明冻结停笔；最终一次 WaitSessions 对下列三项操作均返回 completed。主控未派发后续复审或修复，也没有代替其他会话批准工具请求。

| 收尾任务 | 会话与主要操作 | 最终交接状态 | 已收材料 |
|---|---|---|---|
| SafetyR4 生命周期修复，纠正轮 3/5 | `sess_62b6d885-a6e7-475d-8a1f-87bfbadf4aa6`；`app-control-9c96cf6ec49b93e446ae6bf816f055d7ecd94a55f0ac85c4c2732410b2a17f8c` | **已交付冻结、停笔，操作completed；待独立定向复审** | 已读 `out/maintenance216/task_safety_r4_report.md`、`validation/testbox_sync_manifest_safetyr4.txt`、`validation/safetyr4_start_snapshot.json`、`validation/safetyr4_scoped_patch.diff`头部；后三路径相对`out/maintenance216/`，详情§5 |
| FLOW 纯核心修复，轮 1/5 | `sess_556651ae-b923-4cc8-b325-de5f9cddf5d7`；`app-control-3f3eb18d52215debf59ef487a633a9af210a245e06eae2db9bc2c7173a99a943` | **已交付、冻结、作者停笔；FIX_R1_DONE_FROZEN，待独立定向复评** | 已读 `out/maintenance216/team_workflow_core_fix_r1_report.md` 与 `review_flowcore_stage1_fix_r1.patch` 头部，并独立重算两文件终态哈希相符；详情§6 |
| FLOW II-A 角色、活动名单、权限读写接入 | `sess_2c495ba0-fa61-48ce-8419-3af9e6f6e699`；原任务 `app-control-0fbe962c039f9f2f32aa32507e4b0745f9987aa0881cf4832f17d6ba08220bd6`；最近续行 `app-control-627472f0d4341010cd4fa0d1d8864d99a6c3da7ddc612a3947f71fa3383d1098` | **已交付、冻结、作者停笔；READY_FOR_REVIEW，尚无独立复审** | 已读取 `out/maintenance216/team_role_stage2a_report.md`、`docs/maintenance/TEAM_FLOW_ROLE_FRONTEND_HANDOFF_20261114.md`、`out/maintenance216/stage2a_freeze/DIFF_INDEX.md` 及start/end清单；scope包完整性限制见§7 |

角色会话一次只读 summary 调用曾返回 `App control failed`；没有据此重开会话或声称会话死亡。核心会话曾出现工具审批待处理并向用户通报；目前其最终交接已收到，该历史状态不再列为当前阻塞。本会话没有代替其批准工具请求。

所有三项均已在用户停止派工指令前提交；该指令后本会话不增加开发、复审、替补会话或完整验证任务。若出现新缺口，记入交接，留待新会话处理。

## 3. 已确认的产品规则

权威文件：

- `TEAM_SCREENING_FLOW_PROPOSAL_20261114.md`：用户对逐条流程图及补充分支明确回复“理解正确，开始吧”。
- `TEAM_FLOW_REPAIR_CONTRACT_20261114.md`：实施红线和前后端边界。
- `OWNER_BUSINESS_ROLE_DECISION_20261114.md`：用户明确的属主角色要求。

推进单位是同一文献、同一筛选阶段/轮次：全部当前应筛成员完成正式二选一判断；一致纳入推进，一致排除停止；纳排分歧等待该条有效授权仲裁，仲裁纳入才能推进。初筛→复筛、复筛→编码/评价都遵守，后者还须当前初筛终局纳入。一篇就绪不要求整批全部完成；同阶段的独立、并行、双盲提交保留。仅排除理由不同不新增冲突门槛。

属主只额外拥有管理权限，业务角色也必须 coder/reconciler 二选一；不能凭属主身份跳过业务或阶段门。缺失/双角色须显式配置，不能按是否投过票猜职责。coder按职责筛选/编码；reconciler按既有 participation 设置参与筛选。0名应筛成员不空集推进，1名明确应筛成员合法。正式初筛和复筛只有 include/exclude；历史 maybe 视为未完成，不能经仲裁当作已完成放行。

当前参与者、权限、历史作者分开。已移除成员不再作为当前必需成员，但历史判断、仲裁、编码/评价、原始按人导出、历史κ、合法离线包保留。不得全局过滤 `screener_options`、`load_screener_decision_packages` 或 generic latest/CSV/history。无grant/无账号/有db均不能单独证明无效身份；A7“无grant+有db就自动收敛/隔离”端点已否决。

`task_kind == team` 的业务规则与 server/desktop 模式正交，desktop team也须有合法逐条仲裁路径；personal/legacy/solo/未关联筛选的合法手工分析不误锁。新资格层采信的仲裁须逐条绑定任务、文献、阶段、轮次、参与集合和当前输入；B变化不误废A，A自身变化使A旧结论失效。通用离线工具和历史消费者不因此改义。

## 4. 各任务实际状态与证据

“旧快照通过”不代表此刻整份候选已通过；本节区分开发、独立评审和最终整合。

| 范围 | 当前可确认状态 | 主要证据/限制 |
|---|---|---|
| 发布守卫、Actions runtime | 初始实现独立 PASS/PASS；Task8后来扩展，整合版待最终审查 | `task_release_report.md`、`review_task1.md`；checkout v5/setup-python v6/upload v6/download v7/softprops v3。upload v5、download v5/v6仍是node20，勿回退 |
| 测试数据根/环境隔离 | SafetyR3独立 FAIL；R4已修毕交接，待独立复审，不能放行完整门禁 | `review_safety_r3.md`；原五文件ignore绕行已由共享入口撤掉，但隔离修复仍须验收 |
| RoB旧保存端点八码 | 后端映射已实现并审过业务合同；测试资源与文档缺口分线修正；实际前端未实现 | `review_task3.md`、`task_safety_r3_report.md`、`handoff_r4_acceptance.md`；不把Task3原整单FAIL直接改写成新整单PASS |
| Bootstrap极端值 | 产品数值修复独立 PASS/PASS；正常样本/随机数语义保留，成功payload有限且严格JSON可序列化 | `review_task4.md`、`test_2_1_6_bootstrap_boundaries.py`；不保留sd=inf作为成功结果，不全局忽略警告 |
| httpx2与无效转义 | 独立评审通过；待最终共同候选完整运行 | `review_task5.md`、`build/requirements-test.txt`；httpx2与直接httpx消费者共存；两处docstring raw前缀 |
| AppStream/Bash | 元数据初始实现评审通过；F7缺Bash明确失败与实际相对路径语法检查已独立通过 | `review_task6.md`、`review_safety_r3.md`；原生Linux appstreamcli和成品验证仍未完成；本机多个Bash入口含别名，不称四种独立实现 |
| CI跳过/时区/浮点 | Phase1/Phase2/P1已交付；P1独立 PASS/PASS，两个df岛巧合钉已替换为明确场景接缝 | `task_portability_report.md`、`task_portability_phase2_report.md`、`task_portability_p1_report.md`、`review_portability_p1.md`；fl1目标文件57P0S，不把局部结果当全目录无skip |
| RoB前端交接 | 示例功能独立29/29通过，R4文字主控验收通过；实际UI和旧409本地化仍待前端 | `docs/maintenance/2.1.6_frontend_handoff.md` SHA256 `91153ed2589ece8f6660bda20a96890dbe0909cfc25c91fe4f8cad4ddc3f9581`；`review_handoff_r3.md`、`handoff_r4_acceptance.md` |
| 清理/外部验收材料 | Task7修后文档独立 PASS/PASS；已证空壳/attr已清，其他数据库目录未清 | `review_task7_r2.md`、Task7报告；真机/账号事项仍开放 |
| Task8统一验证/资产汇集 | 实施完成，有定向和变异证据；从未据此声称最终完整目录全绿 | `task_integration_partial_report.md`、`task8_asset_assembly_spec.md`；EOL和最终完整验证尚未执行 |
| TEAM/FLOW审查与准备 | 审查/模式边界已完成，修复模型甲已定；核心和角色候选仍未独立验收 | `team_workflow_audit.md`、`team_member_roster_recon.md`、`team_workflow_implementation_prep.md`、`team_owner_role_impact.md` |

以上短文件名除前端合同外均在 `out/maintenance216/`，已做只读存在性盘点；三份最新交付报告均实际存在。`RESULTS.md`尚未生成，不代表最终核销已完成。详见§11的实际文件清单与哈希快照。

## 5. SafetyR4 接续所需事实

最新独立结论是 `review_safety_r3.md` 的 Spec FAIL / Quality FAIL：

- A/P1：warnings-as-errors叠加初始化失败和拒删，清理警告遮原异常且环境未恢复。
- B/P1：自有根已不存在时仍重钉失效路径，重试无法结束。
- C/P1：真实单线程 unittest w4→auth→w4，导入计数不能表示执行块生命周期；提前删根，之后还复用cleaned账本。
- D/P1：pytest永久dormant接管，使已存自有/外部裸租约的真实收尾和atexit都失效。

F5测试自有SQLite显式关闭与F7 Bash能力检查已独立 ADDRESSED。F6不属该复审，由HandoffR4另行关闭。原298通过、20×5或CLI退出不能代替宿主返回/异常边界证明。

当前合同：`.superpowers/sdd/2026-11-14-2.1.6-maintenance/safetyr4-brief.md`。明确外层suite/process owner持有根、独立幂等token；原生CLI进程owner与显式嵌入suite finally的恢复合同分开；pytest有已存bare owner时在改环境前安全拒绝，不做半接管；正常pytest握手有明确结束/回滚；诊断不能遮原异常；根已消失按幂等完成；拒删但根仍在才保留安全pin与cleanup_pending重试。

仅test-only infrastructure和六模块生命周期块可改，F5/F7冻结。修复轮计数：SafetyR2=1、SafetyR3=2、SafetyR4=3/5。交付后仍需独立定向复核，但**本会话不再派发**。

### 已收到的 SafetyR4 交付

SafetyR4已完成冻结交接，操作completed，开发者停笔。已读取报告 `out/maintenance216/task_safety_r4_report.md`、终态10文件清单 `out/maintenance216/validation/testbox_sync_manifest_safetyr4.txt`、9文件开工快照 `validation/safetyr4_start_snapshot.json`，以及R3→R4差异 `validation/safetyr4_scoped_patch.diff`头部（后三路径均相对out/maintenance216）。本会话只作报告/哈希核对，不执行新复审。

作者报告：全新箱 `E:/rf216-safetyr4-box` 从稳定 `E:/rf216-testbox` 整拷后同步本批10文件，未带入FLOW角色或核心在途产品；永久真实框架回归17/17、冻结F5/F7与lease混批45、六模块混批263、CLI/discovery通过。首跑17项中3红由短TEMP/外部根断言口径修正，过程见报告§6；实现侧首跑后未再修改。上述均为开发方证据，**修后A–D尚无独立PASS**，旧R3独立FAIL不能直接核销。

接口落实为 `_bare_env.py` 的显式代所有权：`bare_scope()`、进程owner、pytest会话代；独立acquire token，release不删根，六模块setUpModule先`ensure_safe_for_execution()`。conftest在pytest_configure建立会话、初始化失败abort、sessionfinish结束；环境两键的恢复与pending重试由helper共享实现。不存在的自有根幂等完成；仍存在的拒删根留pending并使原成功pytest真实返回非零；已有未结束bare owner的pytest在改环境前拒绝。无显式scope的嵌入runner返回不承诺恢复；缓存后端与新根不一致时明确硬拒，不reload/不清sys.modules。

新增test-only驱动 `build/tests/_safetyr4_driver.py`，终态清单总共10文件；F5/F7文件本轮未改。此箱没有FLOW产品，故其263绿**不能替代角色候选与SafetyR4汇合后的undo/整目录共同回归**。

raw scoped patch头部含 `a/E:/rf216-testbox/...` 与 `b/E:/mubi-reviewflow/...` 的本机路径；它可直接供阅读，未来回放前须明确路径映射，不能盲目在主仓git apply。开工快照和终态manifest保留作逐字节锚点，不以HEAD差异替代R3→R4边界。

原Terra审查会话 `sess_71c0e144-12a6-48c8-9a1a-2bb8493021b3`；只作为将来可尝试复用的定位，不保证根会话仍能续接。旧证据 `E:/rf216-scratch/safetyr3-review-scgqr1c3/` 保留只读。

## 6. FLOW核心修复接续所需事实

最新独立结论 `review_flowcore_stage1_r1.md`：Spec FAIL / Quality FAIL。

| 发现 | 当前修复合同 |
|---|---|
| F1/P1 maybe被当完成并可仲裁终局 | 二选一才完成，maybe→awaiting_members及机读原因，即使有绑定仲裁也不放行，不改历史票 |
| F2/P2 record_id字符串排序取旧记录 | 同一验证过store内按数值追加id判最新适用；created_at用于输入绑定，不能用词典序或墙钟倒退推断新旧 |
| F3/P2 task_id任一None即通配 | 采信仲裁前双方task明确非空且相等；None/空/另一task都不能通过；round严格相等 |
| F4/P3 Iterable生成器重复消费 | 构造时物化required/keys，同一输入重复评估稳定 |
| F5/P4 注释和缺评估诊断 | round=None非通配；过期清单回填文档；链缺输入用None/not_evaluated，与真实awaiting_config分开，不增第六种投票/流程状态 |

合同：`.superpowers/sdd/2026-11-14-2.1.6-maintenance/flowcore-stage1-fix-r1-brief.md`。唯二代码文件 `coscreen/team_screening_eligibility.py`、`build/tests/test_team_screening_eligibility.py`；模块纯stdlib、零I/O/回写，尚无生产接线。

原审查快照完整保留在 `review_flowcore_stage1_candidate_20261114.patch`：模块SHA256 `115380a2f1cf4cb0444357c69adde0d741e030df9edde2b3287919a1ff387caf`；测试SHA256 `3d4f303886c5ef5cae29f1f9b6f0bf4992a92c8ad32eeed96986d4a223ccdd30`。这是**修前**哈希，不是最终候选。

### 已收到的核心修复交付

修复轮1/5已交接、冻结、停笔，状态FIX_R1_DONE_FROZEN；主控已读完整报告及patch头部，并独立重算当前两文件终态SHA256，均与交付一致：

| 文件 | 修后SHA256 |
|---|---|
| `coscreen/team_screening_eligibility.py` | `5e7bd4ad7ca02b96c336c178f6f7c25051da14041a96ce5de74a581a5400aa7b` |
| `build/tests/test_team_screening_eligibility.py` | `ad86ffcdefd279bac6c942df6753752cd3124bf42edd2f68110601df1d67f218` |

报告 `out/maintenance216/team_workflow_core_fix_r1_report.md`；修复差异 `review_flowcore_stage1_fix_r1.patch`，头部含前后四哈希，前态目录 `E:/rf216-scratch/flowcore-fix-r1-prestate-20261114/`。作者报告在全新纯源码箱 `E:/rf216-scratch/flowcore-fixr1-val-20261114` 对前态复现F1–F5红侧，修后55/55单测首跑及复跑通过，源/箱导入与运行前后哈希留证。作者还做了patch回放逐字节相等校验；Windows回放用`git -c core.autocrlf=false -c core.eol=lf apply`避免自动CRLF转换。本会话未重新执行这些测试或patch回放，不替代独立复评。

交付的精确接口：`pending_reasons`为与pending_members同序的`(成员, missing_vote/non_formal_value)`元组；`record_id`收紧为正整数，bool/字符串/非正/None均ValueError（后续装载层不能直接传数字字符串）；最新适用按最大数值ID；task双方显式、round严格相等；required/keys构造时一次物化；链缺键status=None且not_evaluated，真实awaiting_config仍保留。

新会话仍须针对F1–F5做独立定向复评，当前独立结论只覆盖修前FAIL，不能把“作者修毕55绿”核销为Spec/Quality PASS。装载层尚须保障store归属与task填写、同一store的ID可比性、本地历史时间转aware UTC、当前名册与历史名单分离、maybe补二选一引导、结构化错误与队列/写门接线。15个符号impact未命中记UNKNOWN；作者用唯一专属测试消费者说明尚未生产接入，不等于已覆盖未来集成影响。

原DeepSeek复审会话 `sess_493e458d-d775-4f84-828c-ec9bf123cc83`，可在新会话有授权后尝试有界复用；不重复旧38测/旧seam，不扩展到全仓审计。本会话按停止派工指令未发送任何新复评任务。原准备GLM会话已 `proto.sessionNotFound`，因此本轮才补开新的GLM核心修复会话。CreateSession的`$high`后缀曾报missing/ambiguous，随后精确基础模型ID成功；不能声称设置high推理成功。

## 7. 角色II-A及HIGH读写裁定

主brief：`flow-role-stage2a-brief.md`；补充：`flow-role-stage2a-high-read-write-addendum.md`，均在本plan的`.superpowers/sdd/2026-11-14-2.1.6-maintenance/`。

有效单一角色解析须覆盖属主和普通成员；旧双grant选择后，未选角色不能继续赋予另一业务写权限。残留choice不能恢复已撤销成员关系。角色选择可审计、原子；不能通过临时revoke/quarantine切换角色，不删除个人库。管理配置入口不能因业务未配置锁死。需要保留personal owner合法编码路径：它也会调用`authorize_action`，不能全局删除owner=True后误锁solo。

角色动作以现有`_ACTION_ROLES`为准：`coding:submit`是coder；`coding:read_all`、`consensus:read/write/freeze`是reconciler，不能按API文件名将共识写/冻结统归coder。当前activity投影与旧`screener_options`分开，SSE按canonical task_id。只读task_kind派生不能暗中写或latch；暂不可读不能伪装personal/空名单。

新增实跑impact：`_arbitration_store` **HIGH/9**，发现先前漏掉的`tables_api._consensus_gate`注入边。主控已向用户通报HIGH；“原报告无HIGH”结论不再覆盖B3。该gate同时服务SoF汇总读/已存判定展示脱敏与判定PUT：

- 属主保留既有管理、汇总、历史查看，普通coder隐私限制不放宽。
- 带rob_tool的SoF预览/导出属读（即便HTTP是POST）；判定PUT必须有效reconciler。
- 非rob SoF、Table1、原已对可见成员开放的已定稿judgment块保持原可见性。
- 显式区分read/write能力或注册注入；不能把读例外穿到保存/resolve/finish/活动仲裁session。
- 同时核对latest/history/server-merge、appraisal_agreement/grade-input等真实消费者；原paused/双盲守卫不放松，当前session仍是业务仲裁入口。

暂停及随后补充放行消息均曾accepted/queued；未无证声称执行者已收到/已暂停。最终报告应保留真实修改/收件时序及消费者核销。

本批只做角色/名单/精确权限接入；**不代表**逐条资格装载、阶段队列/写闸、现行PRISMA终局派生、两阶段逐条仲裁ready/session及A5收敛已完成。前端仍只交规格。

### 已收到的角色交付与材料核对

角色 II-A 已完成冻结并停笔，READY_FOR_REVIEW。报告和前端合同均已读取；作者报告新独立箱 `E:/rf216-role2a-val-20261114` 的9/9真实API测试及相邻RoB20/20通过。首轮测试构造/响应解包错误与修正过程保留在报告。主控只做材料核对，没有复跑测试或给出独立代码PASS。

- 只读重新计算`stage2a_freeze/end_hashes.txt`全部7个文件哈希：均与当前文件相等。新增`test_2_1_6_team_business_role.py`与冻结副本逐字节一致。
- 清单5文件变化：task_collaboration、main、review_workflow_api、appraisal_api、report_evidence_api；prisma_task_metrics、workspace_undo_api两文件前后未变。main含此前RoB两hunk，不能把其全HEAD差异都算本批；DIFF_INDEX标注归属。
- **材料覆盖缺口**：`tables_api.py`当前相对HEAD为+32/-26，且报告明确称本批进行了SoF读写语义改造，但start/end哈希、DIFF_INDEX及freeze包没有该文件条目/补丁。主控观察SHA256=`cf2b7e39e74100e1f22f4a86755043919f518ddfe7deef1d9e68b28cf87e49dd`。新会话先补齐这部分审查材料/归属证明，不能仅凭当前freeze包宣布整个HIGH读写面覆盖。appraisal_agreement_api本身没有HEAD差异，其读门通过main注册注入。
- DIFF_INDEX第3行把`cbc8cb5e…`标为main前哈希，但start_hashes实际将该值列给task_collaboration，main前哈希为`13549ef5e23d720c8094e0fe90ba22f7915e1858c5bba000c566a27d81b0fe61`。保留原材料，复核以真实清单/文件字节为准，不在本会话改写作者证据。
- `_coding_authorize`保持布尔以兼容rob_shared的`consensus:read→coding:submit`回退，部分共享端点仍用旧403；作者明确把409角色细分列为待裁。前端合同表格的概括409语义须结合该例外阅读，新会话需验证实际端点合同，不能只依据总表承诺。
- `test_r6_undo_collab_fixes.py`未跑：依赖SafetyR4在途helper，作者按分工未拷。待安全线稳定后仍需真实undo回归，不属于已通过证据。
- revoked事件补canonical task_id尚未实施；新`team-role-changed`事件已报告含该字段。不要把两者混写为SSE全部完成。
- 角色前端文档将管理API标为server模式；desktop team的角色配置/参与设置及后续仲裁路径可达性仍须在集成验收明确核对。

上述是交接证据与待复核清单，不是新增代码审查结论或新派工；按用户指令本会话未回派补材料/复审任务。

## 8. 新会话的剩余执行顺序

本节是待办，不是本会话派工：

1. 先读取三项最终交付和最终只读manifest，确认所有写者停止、原先缺失的报告是否已补齐，识别开发绿与独立评审的差别。
2. 核心修后逐F复核；SafetyR4按A–D真实框架边界复核；角色II-A独立异构评审，特别是撤销/残留choice、单选权限、personal兼容、HIGH读写拆分和隐私。
3. 前述通过后才接团队身份/当前输入装载、逐条终局资格、复筛/编码/评价队列与直接/批量写门、现行PRISMA消费者、两阶段逐条仲裁与desktop合法路径；旧批量paused/离线/历史语义保留。需测试A就绪B未完、B变化不废A、A改票过期、曾个人排除者仲裁纳入后推进。
4. 完成任务移除后的活动名单与当前输入收敛；不得扩成A7物理清理。物理rename失败不能决定列表正确性；falsy revoke不等于可无条件清理。
5. 所有代码停笔、各任务复审通过后，再控制范围做EOL规范（原清点34个build/tests CRLF/mixed + build/script_manifest.cjs；不能碰前端/字节夹具），同步完整共同候选进隔离源码箱。
6. 运行共同的8步完整入口；失败回到对应任务，不加ignore/分段遮蔽。之后做整份候选异构审查，包含扩展后的assemble/publish链、真实门禁接线、数据守卫。
7. 按实际证据更新`out/maintenance216/RESULTS.md`、原plan勾选与BACKLOG，区分完成/待前端/待真机/待账号/待原生三平台，不改写旧发行版。提交/发布仍待用户明确指令。

## 9. 验证环境、统一入口与风险边界

- Python主验证环境：`E:/rf216-scratch/validation-venv312/Scripts/python.exe`（3.12.13，与CI匹配）；旧3.11环境保留在`validation-venv`。
- `build/run_validation.py` 实际是8步：一次完整`pytest build/tests -q`、3 Python validators、3 Node validators、一个含24 Node suites的步骤。`--list --json`导出同一真实argv计划；默认执行完整计划，`--log-dir`保存分步日志；缺Node退出2，不静默skip。
- 将来只在完整隔离源码箱执行：`python build/run_validation.py --list --json`检查计划，再`python build/run_validation.py --log-dir <绝对scratch日志目录>`。这里的python应明确使用上述venv；先确保Node/Bash和测试依赖来源。**本会话收尾阶段没有执行这两个命令或完整门禁。**
- 绝对scratch `COBOOKSHELF_DATA_DIR`、短E TEMP/TMP/RF_R9_TMP/RF_R9_TMP_ROOT；验证真实模块`__file__`、源/箱哈希、箱内default data前后。不能从主仓根运行app/API/pytest，真实data和8613不碰。
- 过去w4→r6探针曾写入主仓默认data的测试用户和临时secret；痕迹已处置，备份`E:/rf216-scratch/incident-backup/`，但没有事故前快照，不能声称绝对逐字节回滚。依据`incident_data_recovery.md`。
- 被隔离的旧根目录Bash残骸：`E:/rf216-scratch/legacy-shell-quarantine-20261114T121137/`，含SHA和RESTORE.md；不自行恢复或删除。
- 不碰`E:/rf215-auto`停泊研究候选、`E:/rf215-final`发布树；含users.db的其他目录只登记，不能归入清理。
- GitNexus改已有符号前必须upstream impact；HIGH/CRITICAL先报告。`--file`/FTS/裸名假hub已出现问题；notfound记UNKNOWN+精确静态消费者，不能装作0风险或重建共享索引。提交前detect_changes；本会话没有提交。
- 模型分工：简单开发Flash/DeepSeek，安全开发GLM-5.3，异构复审DeepSeek/Terra。**GLM-5.3-Flash优先Start Plan**：`account:bigmodel-start-plan/GLM-5.3-Flash`。工具不支持本会话agent逐次选模型时，使用已批准的明确模型根会话方式；优先复用，`sessionNotFound`不无限重试，不以prompt冒充模型路由成功。

## 10. 仍开放的前端、外部与独立候选

`custom_frontend/`全部只读；本线交付后端、测试和逐组件/逐词典键规格，实际UI由既定前端团队实施。RoB八码、角色单选/管理徽标分离、活动名单、队列等待原因、逐条仲裁入口、SSE task_id均须按最终精确接口联动；未实施前不能称用户可见问题已全部解决。旧409 `analysis.rob_ambiguous_scope`当前未注册词典，是明确保留的本地化待办。

真实iPhone Safari/软键盘/安全区仍待真机；不能以桌面窄视口/模拟器或缺viewport-fit的推断替代可见可点击实测。Jev钥匙轮换仍待用户账号操作，仅留日期/失效与功能结果，不收集钥匙。原生三平台Actions/安装包统计、排序、图表导出与AppStream仍须分别留证。

发布资产为3安装包+3manifest+SHA256SUMS共7件；assemble先验证6载荷并生成/核SHA、不把SHA文件自身写入其清单；publish仅push tag，可演练的分支/dispatch不能发布。真实发布前核对同一提交三平台全绿与runner存续。

独立候选不要夹带：`import_sources raw_json='null'`真实可达性尚未证；generic latest/CSV抗幽灵覆盖加固不等于当前资格修复；`_finite_mean`末乘理论溢出未确认为本轮缺陷；其他TEMP残留/NCJI等依原记录另行处理。

## 11. 最终只读清单与收尾结果

三份最新报告均已读取，三项最终操作经WaitSessions确认completed；三位执行者均报告冻结停笔。用户停止派工指令后，本会话没有发送新的Agent/CreateSession/SendToSession开发、修复或复审请求；仅收集、读取、计算哈希和整理文档。

最终快照文件均位于 `out/maintenance216/`：

| 文件 | 用途与范围 |
|---|---|
| `handoff_20261114_manifest.json` | 机器可读清单：41个改动/新增候选source/build文件，合计146个scope内源文件、文档、brief、报告/patch等的SHA256；含三项交付的19条哈希对照与scope说明 |
| `handoff_20261114_snapshot.txt` | 人类可读的branch/HEAD、41个source/build哈希、完整工作树status；status含其他未跟踪材料，不是批量staging清单 |
| `handoff_20261114_tracked.patch` | 候选source/build相对HEAD的tracked改动快照，**含tables_api.py**；不含未跟踪新增源码，后者按manifest精确路径和哈希读取，不可漏掉 |

只读核对实际结果：

- 分支仍是 `maintenance/2.1.6-20261114`，HEAD仍是 `80dd35fd3a8833bb5fa81eb3ae7b85057fd25a22`；没有本轮新提交。
- 19/19交付哈希相符：SafetyR4十项主仓=箱=终态清单；角色II-A七项主仓=冻结清单（含两项未改文件）；核心R1两项主仓=交付终态。角色新增测试与冻结副本逐字节相等。
- staged路径0；`custom_frontend` tracked改动0。未做EOL规范、完整验证、主仓app/API/pytest、提交、推送、打标或发布；真实数据/密钥未在收尾核对中读取。
- `RESULTS.md`不存在；三个修后候选都没有本次新的独立复审结论。源文件哈希相符不是功能验收，三个不同箱的开发绿不是共同候选全绿。
- 角色原freeze包的SoF文件遗漏仍保留在§7；总tracked快照补存了其当前代码差异，但没有伪造作者开工前态/归属证明，也没有据此宣布该面独立审查通过。

这是有明确范围的候选/证据快照，不是整仓所有文件的逐字节备份，也不是自动提交列表；研究数据、凭据、工具残留和其他工作树不在hash范围。为避免自引用，本文和动态progress台账不列入该hash清单，其他捕获材料在生成清单后未再修改。

清单生成第一次尝试因shell路径转义产生SyntaxError，解析阶段即失败、未生成产物；随后将只读脚本保存到 `E:/rf216-scratch/handoff-20261114-inventory.py`，通过venv312的`python -I`成功生成上述三份文件。该失败不涉及产品执行或修改，记录它不代表产品测试曾失败/已补跑。

## 12. 新会话入口

可直接发送：

> 先读 `docs/maintenance/HANDOFF_2.1.6_20261114.md`，再核对其中的最终manifest与三份交付。继续剩余任务：先独立复审，再做后续集成和完整验证；遵守模型分工、前端只读与数据隔离边界。不要把开发方绿灯当作独立验收，也不要从旧plan的早期状态重复派工。

本交接本身不授权commit/push/tag/release；按新会话用户的实际指令推进。
