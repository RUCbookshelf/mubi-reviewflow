# 木笔 ReviewFlow 2.0.5

## English

### Workflow settings and screening

- Home & Settings groups controls into six tabs with an elastic indicator: Overview, Tasks & team, Screening settings, Machine Learning, Data extraction & coding, and Appearance & storage. Keyboard navigation and reduced motion are supported.
- Save reusable exclusion reasons separately for title-and-abstract and full-text screening. Personal tasks use account-owned reasons; team tasks use owner-managed reasons. Library edits preserve earlier decision text.
- Configure Maybe independently for both screening stages. The owner controls team settings, enforced by the backend. Disabling Maybe preserves historical decisions.
- Initial Include and Maybe both enter full-text screening without converting the initial decision. Full-text Maybe remains unresolved and does not, by itself, qualify a report for coding. Progress counts saved decisions, including Maybe, separately from undecided work.
- Choose **Import / Machine Learning / Random** screening order. Random order persists per reviewer, task and source scope. Newly activated work appends without reshuffling; an explicit action reshuffles remaining eligible records.

### Import sources and protection of existing work

- Filter screening and coding by explicitly assigned, stable import sources. Sources are not inferred from filenames. Multi-source articles appear once overall and retain the same saved result across source views.
- Owners permit All sources, Selected sources, or pause each stage. Members work within that scope. Paused stages remain readable; scoped and overall progress are separate.
- Additional imports made after saved research work enter protected staging. Review duplicate candidates and metadata differences, then activate resolved records. Existing article identities, decisions, coding and evidence are retained.
- Original import receipts and provenance are preserved. Reimporting the same file within the same source does not create duplicate work.
- Living Review incremental import actions lead to protected import review. Export or select the original source file, review it and activate resolved records; run analysis separately afterward.

### Coding evidence and Trace backtracking

- Each dimension and its Notes & evidence form one visual unit, with dividers between units.
- Add text, quotations, page references, and PNG/JPEG/WebP images through file selection or screenshot paste. Image content/type and the 10 MB per-image limit are validated.
- Owners can require evidence for selected dimensions. Nonblank notes or an image satisfy the requirement. Incomplete work can be saved as a draft and is not counted as complete.
- Values, notes and attachment changes save atomically and participate in the same guarded Undo operation. Unsaved forms offer Save / Discard / Stay before navigation.
- Applicable Trace records provide **View change**, **Open article to correct**, and **Undo this save** when linked to the actor's latest eligible save. Reopening loads current saved values; historical records are retained. Out-of-scope articles open read-only.
- Policy and source changes use live notifications with visible-page polling as a fallback. Dirty forms are retained when policies change.

### Interface and terminology

- Screening order has an explicit label and Random uses an SVG icon.
- Full-text queue positions use **Full-text report**. Decision history distinguishes saved **Maybe** from undecided work.
- Home labels and explanations are clarified in all ten supported languages, including evidence requirements, source permissions, browser preferences and separate Save actions.
- Model presets use neutral configuration names and descriptions. **Hide Machine Learning scores** accurately names the display preference. Model parameters and selected identifiers are preserved.

### Downloads and upgrade

- Windows x64 installer, Linux x86_64 AppImage, macOS DMG, and SHA-256 checksums. Application version is **2.0.5**; macOS bundle metadata also follows VERSION.
- This update adds workflow storage migrations. Close writers and back up the entire existing data directory before upgrading, including databases, PDFs, imports, images, task metadata and configuration. Automatic database snapshots do not replace a complete backup.
- Preserve newer research when rolling back; never overwrite it with an old snapshot. See [workflow storage and maintenance](https://github.com/RUCbookshelf/mubi-reviewflow/blob/v2.0.5/docs/WORKFLOW_CONTROLS.md) and [validation record](https://github.com/RUCbookshelf/mubi-reviewflow/blob/v2.0.5/docs/WORKFLOW_VALIDATION.md).
- Source syntax checks and bounded fictional-data preview checks are recorded. A successful package build does not establish every runtime acceptance scenario or independent native-speaker review of every translation.

## 中文

### 流程设置与筛选

- 首页整理为六个标签页：概览、任务与团队、筛选设置、机器学习、数据提取与编码、外观与存储。弹性指示器支持键盘操作与减少动态效果偏好。
- 初筛与全文筛选分别保存常用排除原因。个人任务使用账户原因库；团队任务使用负责人管理的原因库。修改原因库不改写历史决定文本。
- 两个筛选阶段分别设置是否提供待定，团队由负责人管理并由后端限制；关闭后保留历史待定决定。
- 初筛纳入与待定均进入全文筛选，原决定保留。全文待定仍需后续处理，其本身不使文献进入编码。进度统计已保存决定（包含待定），与尚未决定的工作区分。
- 筛选顺序支持 **导入 / 机器学习 / 随机**。随机顺序按筛选员、任务与来源范围保存；新增激活记录追加，不打乱剩余顺序；可主动重新随机排列。

### 来源与既有研究保护

- 初筛、全文筛选、编码支持按明确指定的稳定来源筛选，不依据文件名推断来源。多来源文献在总体队列中只出现一次，各来源视图使用同一保存结果。
- 负责人可为各阶段设置全部来源、指定来源或暂停；成员在允许范围内工作，暂停后仍可阅读。当前范围进度与总体进度分别显示。
- 已有保存的研究工作后，追加导入先进入暂存区。核验重复候选与元数据差异后激活已解决记录，保留既有文献标识、决定、编码及证据。
- 保留导入原始记录与来源；同一文件在同一来源下重新导入，不产生重复工作。
- Living Review 增量导入入口引导至受保护的导入核验：导出或选择原始文件，核验并激活已解决记录，之后单独运行分析。

### 编码证据与轨迹回溯

- 每个维度与笔记、证据作为一个界面单元，单元之间增加分隔。
- 支持文字、引文、页码，以及选择文件或粘贴截图添加 PNG/JPEG/WebP 图片；校验图片内容、格式及每张 10 MB 上限。
- 负责人可指定需要证据的维度，非空笔记或一张图片可满足要求。缺少证据可保存为草稿，不计为完成。
- 编码值、笔记和附件变更原子保存，由同一次受保护的撤销恢复。未保存内容在导航前提供保存、丢弃、停留选项。
- 可关联的轨迹记录提供查看变更、打开文献修改，以及满足最近保存条件时撤销此保存。打开时载入当前结果，历史轨迹保留；范围外的文献只读打开。
- 流程与来源变更通过实时通知及页面可见时的轮询同步，策略变更时保留未保存表单。

### 界面与术语

- 筛选顺序增加明确标签，随机排序使用 SVG 图标。
- 全文队列位置使用全文文献；决策历史区分已保存待定与尚未决定的工作。
- 十种语言同步澄清首页标签及说明，包括证据要求、来源权限、浏览器偏好和保存按钮作用。
- 模型预设使用中性的配置名称与说明；隐藏机器学习分数准确表达显示偏好的用途。模型参数与选择标识保持不变。

### 下载与升级

- 提供 Windows x64 安装程序、Linux x86_64 AppImage、macOS DMG 及 SHA-256 校验文件。版本为 **2.0.5**，macOS 应用包版本同步读取 VERSION。
- 本次增加流程存储迁移。升级既有数据前，关闭写入程序并备份整个目录，包含数据库、PDF、导入原件、图片、任务元数据及配置。自动数据库快照不能替代完整备份。
- 回滚须保留更新后研究数据，不以旧快照覆盖。参见[流程存储与维护说明](https://github.com/RUCbookshelf/mubi-reviewflow/blob/v2.0.5/docs/WORKFLOW_CONTROLS.md)和[验证记录](https://github.com/RUCbookshelf/mubi-reviewflow/blob/v2.0.5/docs/WORKFLOW_VALIDATION.md)。
- 已记录源码语法检查与虚构数据的有限预览检查；构建成功不代表所有运行场景均已验收，也不代表每项翻译均经母语审校。
