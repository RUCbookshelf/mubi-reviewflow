# r6 复审修复指示 —— PRISMA 表单（coscreen/stats + prisma_task_metrics）

> 基线：集成轮确认 fix/2.1.4 上 PRISMA 整单元修复从未落地（`git log --all --
> coscreen/stats/prisma.py` 仅 526a6f4 初始版；fix/2.1.4-r5-prisma 相对
> fix/2.1.4 零独有提交、工作树干净）。已修两条：【已修·集成轮】标注。
> U6-P4 的修复只在已合并的 r5-ingest（174186b）内，现已在场——需复核确认。

## R6-F1（high）U6-P1：other 列缺 R4 勾稽（sought−notretrieved=assessed）

- 位置：`coscreen/stats/prisma_form.py:517-528 (R4 仅 db 列), :558-564 (R6 warning), :1050-1084 (other 列 DOT)`。
- 复现要点（复审实测）：sources_scope='db_reg_other'、other sought=10/not_retrieved=0/assessed=5/理由=2 → ok=True、errors=[]、仅 R6 warning；DOT 缺 notret_other 框，缺口被 R6/R12 掩盖。
- 修复指示：v2（db_reg_other）时补 other 列 R4 为 error：`reports_sought_other − reports_not_retrieved_other == reports_assessed_other`；R6 warning 语义保留为「未启用 other 列」提示。DOT 补 notret_other 框（复审指出缺失）。
- 验收标准：复审表单 → ok=False 且 errors 含 other 列 R4；DOT 呈现完整 other 链；补回归。

## R6-F2（high）U6-P7/U6-P3：空决策判 agree 并计入 screened

- 位置：`coscreen/stats/merge.py:87-93,96-114`（''=='' 判 agree）；`coscreen/stats/prisma.py:81,86-91,133`（screened 按 agree+conflict+A_only+B_only 求和；multi 用 zotero_key.nunique）。
- 复现要点（复审实测）：k1 双方 decision=''、k2 双方 'include' → 状态 ['agree','agree']；prisma_from_merge → screened=2、included=1；multi 路径 k1 n_screened=0/incomplete 仍 screened=2。
- 修复指示：`merge_decisions` 空/空 → status 独立（如 'unscreened'，不计 agree）；`prisma_from_merge(_multi)` 的 screened 仅计至少一名筛选员有实际决策的记录（multi：n_screened>0）；included/excluded 口径随之复核。
- 验收标准：复审小例 → screened=1（仅 k2）；补 blank/blank 与 multi 回归；与 R6-F9 口径统一联测。

## R6-F3（high）【已修·集成轮】U6-P8：_esc 不转义 CR/LF

- 集成轮已把 `_esc` 改为 CR 归一 + LF 转义 graphviz `\n`（commit fa12f16），测试按复审同法扫描 DOT 每行引号闭合。泳道待办：本机无 dot 二进制，复审未做渲染级验证——有 graphviz 环境时补一次渲染级冒烟（`dot -Tsvg` 对含换行 merge_note/removed_other_note/行名的表单不报错）。

## R6-F4（medium）U6-P2 及其余未修条目逐条收口

- 复审同法复现清单（位置与实测值均复审给出）：
  - U6-P2：new 模式 studies_included=5、reports=0 → ok=True、DOT「报告（n=0）」；updated 模式 new_studies=0/new_reports=8 → ok=True。修复：final/new box 勾稽规则（至少一项 >0 且与总数链一致），error 或至少 warning 阻断。
  - U6-P4：两条同 DOI+同键 → n_duplicates=2、identified=2/duplicates_removed=2/screened=1（identified−duplicates=0≠screened=1）。注：该修复已在 r5-ingest 174186b（现已在基线）——先复核确认，再决定是否还需处理 report 侧。
  - U6-P5：screened=4、included+excluded=2、DOT 无 pending 节点。修复：included+excluded<screened 时画 pending/未决节点（或 warning）。
  - U6-P9：官方模板表单 ok=False、唯一 error 为 R13。修复：官方模板各列合法组合必须 ok=True。
  - U6-P10：other 列名称空 + notretrieved_other=-2 → s1-s4 全 ok=True、n_global=2。修复：名称必填（error）、负数拒绝（error）。
  - U6-P11：resolved 键域外（幽灵键计入 included）；report total=10/unique=8/2 pairs → identified=10、duplicates_removed=2、screened=5，勾稽 8≠5 无提示。修复：resolved ⊆ merged 键域校验 + 去重；report 侧勾稽校验或告警。
  - U6-P12：v2 输出 9 条 rank=same（ident_db/removed_db/sought_db/assessed_db/excl_ft_db 各重复 2 次）。修复：issues 去重（rule+field 维度）。
- 验收标准：首审 U6 12 条逐条「已修复+测试钉」或「不复现+证据」；不能部分收口（复审原话）。

## R6-F5（medium）prisma_two_stage 不校验 stage2 ⊆ stage1 纳入集（+multi/resolved 同族）

- 对应复审条目：两条新发现（prisma.py:157-185 two_stage：stage1 纳入 1 条、stage2 exclude 5 条 → included+excluded=5>screened=1；prisma.py:134-137 multi resolved 分支：幽灵键与重复键各计一次，included=3>screened=2）。
- 修复指示：three 处统一校验——resolved/stage2 键域 ⊆ 上一级纳入键域，重复键去重，违反时抛 ValueError 或返回带错误的结构；two_stage 增加 report 参数或显式 deprecate（复审另证其 identified=screened、duplicates_removed=0 硬编码且全仓无生产调用方）。
- 验收标准：复审小例 → 报错或钳制后 included+excluded<=screened 恒成立（docstring prisma.py:29 承诺）。

## R6-F6（medium）R10 人工/自动化拆分与 merge_note 无产品输入（不可达功能）

- 对应复审条目：『human_excluded_n/automation_excluded_n 前端无任何命中，后端恒收 None；R10 error 分支不可达却要求补填不存在的输入；prisma_form_dot 的 merge_note 参数全仓无调用方/无 API 字段』（index.html:7414-7433、prisma_task_ui.js:87-89、main.py:853-854,2413-2414、prisma_form.py:645-665,1016-1019）。
- 修复指示（二选一，产品定夺）：
  - 方案 A：PRISMA 页补两个人数输入（automation_ineligible 已有联动）并随 form 提交；后端 PrismaFormIn 补 merge_note 字段并在页面提供输入。
  - 方案 B：删除/降级 R10 的 error 分支为 warning（文案改为「可在备注中说明」），DOT 不再要求该拆分行；merge_note 参数移除或标注内部使用。
- 验收标准：R10 行为在产品页可达且自洽（或按 B 移除）；UI 回归测试覆盖。

## R6-F7（low）agree-maybe 行在图中静默消失

- 对应复审条目：2 行完全一致 agree（1 maybe + 1 include）→ screened=2、included=1、excluded=0、无 pending 节点/警示。prisma_from_merge docstring 未声明 agree-maybe 丢弃。
- 修复指示：与 U6-P5 的 pending 节点一并处理（included+excluded<screened → pending），或 docstring 明示口径并加 warning。

## R6-F8（low）【已修·集成轮】/api/prisma/generate 契约缺口

- 集成轮已改为 ok=False 时 dot/json 返回 None（validate docstring 契约），前端 prRenderResult 仅在 ok 分支消费导出物（commit fa12f16；回归测试在 test_r6_integration_regressions.py）。

## R6-F9（low）screened 口径分裂：任务页 /prisma-metrics vs CLI prisma

- 对应复审条目：`custom_backend/prisma_task_metrics.py:112-127`（只计有决策记录）vs `coscreen/cli.py:239,263`（把从未筛选的记录也计入，U6-P7 未修所致）——团队任务页与 CLI 导出图无法勾稽。
- 修复指示：随 R6-F2 统一：两处 screened 均只计至少一名筛选员有实际决策的记录；变更在 changelog 中说明口径。
- 验收标准：同一任务库，任务页 metrics 与 CLI stats.json 的 screened 相等。
