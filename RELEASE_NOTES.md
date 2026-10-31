# Mubi·ReviewFlow 2.1.4

## 2.1.4 变更摘要

本版为安全加固与多轮评审修复版本（要点归纳自 2.1.3 以来的修复提交）：

- 上传与路径安全：任务锁规范化（URL 别名不再派生第二把锁）、上传体积与文件部件数预检、限流按直连来源防伪造、上传端点鉴权前置；SSE 进度流改用短期票据，长期令牌不再出现在 URL／反代日志／浏览器历史中。
- 认证与账户安全：注册／登录请求体上限与 422 回显收敛、bcrypt 口令长度上限显式校验、登录限流只计失败尝试；CSV 导出补公式注入防护。
- 协作权限与受保护撤销：属主解析失败的重试语义、legacy 任务统一口径、保留名／幽灵任务收口；撤销守卫幂等化并核验证据文件存在性。
- 受保护撤销的证据摘要与升级迁移（进阶说明，需运维知悉）：证据摘要不再把备注时间戳纳入，因此「同值重存刷新时间戳」不再误判为下游变更（修复前这类条目会被拒绝撤销）。升级时会迁移**摘要形态**（2.1.4 早期构建）的存量撤销条目，但只迁移可证明「内容未变、仅摘要公式变更」的条目（存量摘要恰等于当前内容按旧公式重算）；无法证明的条目（内容在此前真的改过，或无法判定）**保留旧摘要、继续按守卫失配阻断**——这些条目在升级后仍可能提示「已保存结果或下游工作已变更」，属有意的保守取舍（撤销不得静默覆盖较新的下游工作），可用「重建任务协作／在副本上人工核对」处理。全文快照形态（2.1.3 及更早）的条目不受影响，历史审计快照原样保留。
- 去重引擎保守化：自动合并仅保留可核验证据，模糊／键冲突改入人工候选；CJK 尾标点不再破坏 DOI 相等；消歧键拼接前安全截断，杜绝死锁文献。
- 文献解析器：RIS 标签映射与键消歧补齐、编码双候选择优与 GBK 回退、缺 ER 不再整文件拒绝。
- PRISMA 表单与流程图：数字勾稽补全（other 列、理由余量、研究≤报告）、DOT 标签转义、空决策不计入分母。
- AI 抽取建议（自带钥匙，BYOK）：个人／任务钥匙保险柜、逐条确认留痕、引文核验门槛分级。
- 轨迹导出与迁移：origin 结构化脱敏、events 分卷与存量迁移增量修复。
- 前端：导入复核页签／按钮／实际可启用三口径计数一致；401 强制登出与缓存令牌-内容绑定；后台分页在启用失败后不再停滞；加载动画、轮询与十语言词条多项修复。
- 发布链：release workflow 最小权限（构建作业只读且不持久化凭据）、appimagetool 固定版本与 sha256 校验、CI 内先跑测试套件再跑静态校验器。

### English summary

- Security-hardening release: upload and path safety (canonical task locks, size and part-count prechecks, direct-peer rate limiting, auth before locks), short-lived SSE tickets replacing long-lived tokens in URLs.
- Account security: register/login body limits, bcrypt length validation, failure-only login throttling, CSV formula-injection protection.
- Collaboration and protected undo: owner-resolution retry semantics, unified legacy-task handling, idempotent undo guards with evidence-file checks.
- Undo evidence digests and upgrade migration (advanced note for operators): evidence digests no longer include note timestamps, so a same-value re-save no longer looks like downstream work (such entries were previously refused). Upgrading migrates **digest-form** journal entries (early 2.1.4 builds) only where it can prove the content is unchanged and only the digest formula differs (the stored digest equals the current content hashed with the old formula); entries that cannot be proven (content actually changed, or indeterminate) **keep the old digest and stay blocked** by the guard. Those entries may still report “saved result or downstream work has changed” after upgrading — a deliberate conservative trade-off (Undo must never silently overwrite newer downstream work); recover them by recreating task collaboration or by reconciling on a copy. Full-snapshot entries (2.1.3 and earlier) are untouched and keep their audit text.
- Deduplication made conservative: auto-merge only with verifiable evidence; ambiguous cases become manual candidates; DOI equality no longer broken by CJK punctuation.
- RIS parser: tag mapping, key disambiguation, dual-candidate encoding selection with GBK fallback.
- PRISMA forms and diagrams: numeric cross-checks, DOT label escaping, empty decisions excluded from denominators.
- Optional AI extraction suggestions (bring-your-own key) with a key vault and per-item confirmation trails.
- Import review: counts consistent across tabs, buttons and activation; background paging survives activation failures.
- Release chain least privilege: read-only build jobs without persisted credentials, pinned appimagetool with sha256, tests and static validators in CI.

## 2.1.3

（以下为 2.1.3 原文正文，未改写。）

## Added

- Report and sample relationships: save personal proposals, confirm relationships through adjudication, revise or unlink them, and undo the latest changes while retaining original reports.
- Qualitative evidence drafts with findings, supporting quotations, page references, context and reviewer interpretations; export CSV and JSON with original source filenames.
- Optional workspaces for JBI meta-aggregation, thematic synthesis, framework synthesis and meta-ethnography, plus mixed-method integration notes, JBI/CASP appraisal and CERQual/ConQual confidence drafts.
- A theme–evidence matrix and human review of shared original evidence across reports.

## Improved

- Machine Learning ranking refreshes after 20 changed article decisions by default, with a personal interval from 1 to 1000. Decisions are saved immediately between ranking updates.
- Machine Learning settings and model selections are personal in team tasks, preserving independent screening.
- Clearer import-review wording and more compact information, retaining the familiar Todo card and existing desktop/mobile layouts.
- Reviewed, eligible records can be activated while other records continue loading in the background.

## Fixed

- Repeated loading caused by an outdated import-review refresh baseline, overlapping refresh checks and stale background reads during activation.
- Unnecessary save or policy-review prompts after reverting an edit or receiving count-only updates.
- Save dialogs that requested corrections to hidden fields.
- Confusing manual training-minimum input: the application now uses its existing system default.

The new evidence and appraisal tools record researchers’ judgments. They do not automatically identify shared samples, exclude studies, synthesize qualitative findings or change existing statistical models.

Download the Windows installer, macOS disk image or Linux AppImage below. Existing research data is retained when the application is upgraded.

## 中文

### Added

- 报告与样本关联：个人线索、团队裁决、修改、解除及撤销，保留原始报告。
- 定性证据草稿与主题—证据矩阵，可记录多条发现、引文、出处、情境和研究者解释，导出带原始来源文件名的 CSV／JSON。
- JBI、主题综合、框架综合、元民族志及混合方法工作区；JBI／CASP 方法学评价和 CERQual／ConQual 置信度草稿。

### Improved

- 机器学习默认每 20 篇发生变化的决定更新排序，可按个人设置 1–1000 篇；每篇决定仍立即保存。
- 模型、策略与更新间隔按成员保存，独立阶段使用各自标签。
- 导入说明和按钮更清楚，减少重复文字，保留原有待办卡片与桌面／移动布局。
- 后台加载期间也可启用已核验且具备条件的记录。

### Fixed

- 修复已完成核验列表因刷新版本基线过旧而反复加载，以及启用时旧后台响应覆盖状态的问题。
- 修复恢复原值、仅统计变化时误报未保存或策略冲突的问题。
- 保存窗口显示需要纠正的实际字段；训练启动门槛由系统使用现有默认值管理。

新增综合和评价工作区用于记录人工判断，不自动生成综合结论、质量总分或新的统计模型。
