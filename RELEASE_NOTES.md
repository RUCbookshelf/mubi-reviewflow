# 木笔 ReviewFlow 2.0.5.1

## English

This maintenance release includes the approved fixes on top of 2.0.5.

### Import sources and existing research

- Automatically recognize database names from each RIS record's database metadata, including mixed-database files. Normalize known aliases without treating the export provider as a database.
- Populate Imported sources from actual imports. Remove manual source creation; owners can correct names and confirm database/register/other-source classification.
- Recover reliable legacy provenance while preserving stable source IDs, permitted-source policies, article identities, decisions, coding and evidence. Hide empty Unassigned entries; genuinely unknown provenance appears as **Source not recorded**.
- Distinguish original source-record counts from unique active articles. Additional imports retain protected staging and explicit activation; reimports retain confirmed source corrections.

### Team management and terminology

- Group **Members & progress**, **Sources available by stage**, **Screening decision options**, and **Imported sources** within one Team Management panel.
- Add original SVG role badges for **Task owner / Coder / Adjudicator**, retaining member action menus and authorization identifiers.
- Clarify **Needs review**: initial-screening uncertainty advances to full-text review; full-text uncertainty remains unresolved. Stored decisions and Kappa/adjudication rules are unchanged.
- Update relevant interface/help wording in all ten supported languages.

### PRISMA flow diagrams

- Preserve standalone manual entry and optional selective import from task metrics. Fields the user chooses to retain are not overwritten.
- Import original source counts, confirmed duplicate excess and supported screening/full-text outcomes, explicitly identifying unresolved or unavailable values. Staged imports are excluded.
- Complete existing updated-review and other-source fields while retaining new-review and diagnostic-review modes and existing diagram/export functions.
- Require an independent study count or confirmed study grouping. **One report per study** is an explicit shortcut; study counts are no longer silently equated with report counts.
- Export metric provenance as CSV. Do not infer retrieval failure from an unassessed report or expose another reviewer's private notes.

### Screening controls and version handling

- Keep decision buttons, reason selection, custom reason and reusable-reason Save on one adaptive horizontal row within the reader. Narrow readers use a Save SVG with a translated tooltip and accessible label.
- Refresh affected asset references to avoid stale caches.
- Support four-part hotfix versions in the application version reader and update comparison. The app and release identify as **2.0.5.1**.
- Existing 2.0.5 clients may not show an automatic reminder for this four-part hotfix; download it from this Release. The corrected checker here handles future hotfix versions.
- macOS native metadata uses version **2.0.5**, build **2.0.501**, and complete ReviewFlowVersion **2.0.5.1**, following Apple's [three-component bundle-version format](https://developer.apple.com/documentation/bundleresources/information-property-list/cfbundleshortversionstring).

### Downloads, validation and maintenance

- Windows x64 installer, Linux x86_64 AppImage, macOS DMG, and SHA256SUMS.txt.
- Disposable-data checks cover sources, reimports, staging, authorization, PRISMA metrics, migration preservation and original input modes. See the [implementation and validation record](https://github.com/RUCbookshelf/mubi-reviewflow/blob/v2.0.5.1/docs/TEAM_SOURCES_PRISMA_IMPLEMENTATION.md).
- Source migration v4 is additive and creates a pre-upgrade database backup. Close writers and back up the complete data directory before upgrading; rollback must preserve newer research.
- Successful builds do not establish every runtime scenario on every operating system. No real research decisions were changed during validation. The developer's installed E:/ReviewFlow was not replaced.

## 中文

本维护版本在 2.0.5 基础上，包含已确认的来源、团队管理、PRISMA 与筛选界面修复。

### 导入来源与既有研究保护

- 根据每条 RIS 记录的数据库元数据自动识别来源，支持单文件多个数据库；统一已知别名，不将导出平台直接认定为数据库。
- 来源由实际导入产生，移除手动新增入口；负责人可以校正名称并确认数据库、注册库或其他来源类型。
- 恢复可靠历史来源，保留稳定来源 ID、来源权限、文献标识、决定、编码和证据。隐藏空的“未分配”，真正未知的信息显示为“来源未记录”。
- 区分来源原始记录数与激活后的唯一文献数；追加导入保留暂存与主动激活，重复导入保留已确认的来源校正。

### 团队管理与术语

- 成员与进度、各阶段可用来源、筛选决定选项、已导入来源统一放入团队管理区域。
- 为任务负责人、编码员、仲裁员增加原创 SVG 角色标识，保留成员操作菜单和既有权限标识。
- 澄清“待核查”：初筛待核查进入全文筛选，全文待核查仍待解决。已保存决定与 Kappa、仲裁规则不变。
- 十种受支持语言同步更新相关界面及帮助文字。

### PRISMA 流程图

- 保留独立手动填数与选择性任务数据导入两种模式，用户选择保留的字段不会被覆盖。
- 导入来源原始记录数、确认去重多余数及可靠筛选结果；标明未解决或未知值，暂存导入不计入激活任务指标。
- 补全更新综述与其他来源分支字段，保留新综述、诊断综述模式及原有图形、导出功能。
- 研究数须独立填写或来自确认分组；“每份报告对应一项研究”为主动快捷操作，不再静默令研究数等于报告数。
- 导出逐记录、逐来源指标依据 CSV；不将未评估文献推断为未获取全文，不暴露他人私人笔记。

### 筛选界面与版本

- 决定按钮、原因选择、自定义原因及保存常用原因保持在阅读器内同一水平行，自适应分配空间；窄界面使用保存 SVG，保留翻译提示和无障碍标签。
- 刷新相关资源版本以避免旧缓存。
- 版本读取和更新比较支持四段维护版本，软件与 Release 均为 **2.0.5.1**。
- 既有 2.0.5 客户端可能不会自动提示此四段维护版本，请从本 Release 下载；本次修正后的更新检查支持后续维护版本。
- macOS 原生版本 **2.0.5**、构建号 **2.0.501**，保留完整 ReviewFlowVersion **2.0.5.1**。

### 下载、验证与维护

- 提供 Windows x64 安装程序、Linux x86_64 AppImage、macOS DMG 及 SHA-256 校验文件。
- 使用独立虚构数据验证来源、重复导入、暂存、权限、PRISMA、迁移保留及原有模式，详见[实施与验证记录](https://github.com/RUCbookshelf/mubi-reviewflow/blob/v2.0.5.1/docs/TEAM_SOURCES_PRISMA_IMPLEMENTATION.md)。
- 来源迁移 v4 为增量迁移并创建数据库备份。升级前关闭写入程序，备份整个数据目录；回滚须保留新研究数据。
- 构建成功不代表每个系统上的所有运行场景均已验收。验证未修改真实研究决定，开发者本地 E:/ReviewFlow 安装版未替换。
