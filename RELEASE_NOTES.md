# 木笔 ReviewFlow 2.0.5.2

## English

This maintenance release improves protected import review and its onboarding guide, on top of 2.0.5.1.

- Prioritize outstanding duplicate candidates, metadata reviews, blocked records and ready records. Completed records are collapsed by default, with separate pagination.
- Explain source-record, staged, ready, activated, metadata-review and candidate counts. Show filenames and dates; place technical batch IDs under details.
- Compare eight bibliographic fields side by side. Explain Keep/Accept effects and preserve decision-time comparison snapshots after review.
- Display the ready count before activation. Unresolved records remain staged; repeated activation creates no duplicate work.
- Update Guide — Import literature with separate review/activation steps, direct navigation, task-wide counts and accurate completion states.
- Improve keyboard access, tablet layouts, light/dark themes, reduced motion and all ten interface languages. Refresh changed asset versions.
- Preserve article identities, screening decisions, coding, evidence, original records and CSV exports. New backend additions are read-only; no additional database migration beyond 2.0.5.1 is required.

### Validation and downloads

Passed seven JavaScript unit checks, six Python projection checks, 26 existing regression scenarios, additional disposable-data API/activation checks and browser checks covering ten languages, four widths and both themes. No real research decisions were changed. See the [validation record](https://github.com/RUCbookshelf/mubi-reviewflow/blob/v2.0.5.2/docs/IMPORT_REVIEW_PRESENTATION.md).

Windows x64 installer, Linux x86_64 AppImage, macOS DMG and SHA256SUMS.txt are provided. Close ReviewFlow and back up the complete data directory before upgrading. Existing 2.0.5 clients may not detect four-part maintenance releases automatically; download directly from GitHub. macOS native version is 2.0.5, build 2.0.502, with full application version 2.0.5.2.

Build success does not establish every runtime scenario on every OS. E:/ReviewFlow was not replaced.

## 中文

本维护版本在 2.0.5.1 基础上优化受保护的导入核验界面与导入引导。

- 优先展示待核验重复候选、元数据差异、依赖待处理和可激活记录；已完成记录默认折叠，保留独立分页。
- 解释来源原始记录、暂存、可激活、已激活、元数据核验及候选数量。显示文件名与日期，技术批次编号收纳在详情中。
- 并排比较八项书目信息，明确保留现有信息或接受导入信息的效果，保留核验时的比较快照。
- 激活前显示可激活数量，未解决记录继续暂存，重复激活不会产生重复工作。
- 导入引导独立跟踪人工核验与激活，支持定位对应区域、任务整体统计和准确的完成状态。
- 改善键盘操作、平板布局、浅深色主题、减少动态效果设置及十种界面语言，刷新相关资源版本。
- 保留文献标识、筛选决定、编码、证据、原始记录和 CSV 导出。本次后端新增内容为只读展示接口，不增加 2.0.5.1 之外的数据库迁移。

### 验证与下载

通过 7 项 JavaScript 单元检查、6 项 Python 展示逻辑检查、26 项既有回归场景，以及独立虚构数据接口、激活和浏览器检查。浏览器检查覆盖十种语言、四种宽度和浅深色主题，未修改真实研究决定。详见[验证记录](https://github.com/RUCbookshelf/mubi-reviewflow/blob/v2.0.5.2/docs/IMPORT_REVIEW_PRESENTATION.md)。

提供 Windows x64 安装程序、Linux x86_64 AppImage、macOS DMG 和 SHA256SUMS.txt。升级前关闭软件并备份整个数据目录。既有 2.0.5 客户端可能不会自动识别四段维护版本，请直接从 GitHub 下载。macOS 原生版本为 2.0.5、构建号 2.0.502，完整软件版本为 2.0.5.2。

构建成功不代表所有系统的全部运行场景均已验收；E:/ReviewFlow 安装版未替换。
