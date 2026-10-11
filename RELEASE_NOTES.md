## 木笔 ReviewFlow 2.0.4

### English

#### Updated 2.0.4 build — reader and workspace layouts

- The sidebar is now a shared floating button that expands into a navigation sheet on every workspace. The same container grows over 360 ms, options appear in staggered groups, and closing reverses the motion. The original product logo remains beside 木笔 ReviewFlow. Keyboard navigation, small screens and reduced-motion preferences are supported.
- Title/abstract screening prioritizes the article reader with a compact toolbar, a details panel, full-width decision controls and a Focus mode. Stopping guidance opens as a compact floating card with Pearl Companion, freeing the space below the article.
- Full-text screening uses a compact document toolbar, Focus mode and an annotation panel docked beside the PDF. Existing PDF navigation, highlights, uploads and screening decisions are retained.
- **Data extraction & coding** replaces the inconsistent Literature coding / Reference coding labels across all ten interface languages. The PDF/form split is adjustable by dragging or arrow keys, coding dimensions can be folded individually, and the existing Save/Next controls sit beside the form. The structured extraction panel remains available.
- PRISMA inputs are arranged in collapsible sections beside a larger diagram preview. Import, validation, citation selection and all export formats are retained. The existing PRISMA engine, reporting fields, template structures and consistency checks are unchanged; concept sample content is not included in the application.
- These layout changes do not change the backend, database schema or saved research decisions. Version remains **2.0.4**; the new installers replace the earlier assets under the same release tag.

#### Team collaboration

- Server task lists refresh through real-time SSE notifications after invitations and role changes, with polling every 30 seconds while the page is visible as a fallback.
- Owners can manage non-owner members through a per-member **···** menu: change the role to **Coder / Reconciler**, or remove the member with confirmation. Team management stays expanded.
- Member-management controls and notifications support all ten interface languages; role names are unified as Coder / Reconciler.
- Entry pages require cache validation; other static assets use a one-day cache. Updated language-pack references load the latest translations.

#### Screening and interface

- Decision history stays compact: numbered entries and Included / Pending / Excluded counts leave room for the article content.
- Long titles and metadata are truncated in the screening layout. Hovering over or focusing a truncated title reveals the full title.
- The original **Pearl Companion** SVG replaces the AI ranking ball, with calm, happy, winking and curious expressions, gentle breathing, blinks and pastel cheek accents.
- The import guide and its collapsed capsule display a still Pearl Companion. The avatar works in light and dark themes and respects reduced-motion preferences.
- Import order uses an SVG icon.
- Trace status appears beside the current task: green for recording and grey for inactive. Clicking the status opens Trace; redundant workspace shortcuts are removed.

#### Documentation and downloads

- README introductions and linked guides cover the supported languages, with restored link targets and a formatted Ubuntu server deployment guide.
- Downloads: Windows installer, Linux AppImage and macOS DMG, plus SHA-256 checksums.
- This release does not include Remote Operation or differential updates. The existing update entry downloads an installer. Keep and back up the existing data directory when upgrading.

### 中文

#### 2.0.4 更新构建 — 阅读与工作区布局

- 所有工作区使用统一的悬浮侧边栏：同一容器在 360 毫秒内从按钮展开为导航面板，选项分组依次出现，关闭时沿原路径逆向收回；「木笔 ReviewFlow」前保留产品原有 logo。支持键盘操作、小屏幕及系统减少动态效果的偏好。
- 文献初筛以阅读区为中心，工具栏更紧凑，详情可按需展开，决策按钮占满整行，并提供专注阅读。停止建议改为带 Pearl Companion 的悬浮卡片，不再占用文献下方空间。
- 文献复筛采用紧凑的文献工具栏和专注阅读；批注面板在开启时停靠 PDF 旁。原有 PDF 导航、高亮、上传及筛选决策功能保留。
- 文献编码名称统一为 **数据提取与编码**，同步十种界面语言。PDF 与表单的宽度可通过拖动或方向键调整，各编码维度可独立折叠，原有保存及下一篇按钮移至表单旁；结构化数据提取区域保留。
- PRISMA 输入区改为可折叠分区，与更大的流程图预览并排显示。保留导入、校验、引文格式及全部导出格式；现有 PRISMA 引擎、报告字段、模板结构和数量一致性校验不变，概念稿的示例内容未写入产品。
- 本次布局更新不修改后端、数据库结构或已保存的研究决策。版本号仍为 **2.0.4**，新安装包覆盖同一发布标签下的原有文件。

#### 团队协作

- 服务器模式通过 SSE 实时通知更新邀请及权限变更后的任务列表；页面可见时每 30 秒轮询作为补充。
- 任务所有者可通过成员右侧的 **···** 菜单，将非所有者成员的角色改为 **Coder / Reconciler**，或确认后移除成员；团队管理区域保持展开。
- 成员管理操作及提示支持十种界面语言，角色名称统一为 Coder / Reconciler。
- 入口页面每次使用前进行缓存校验，其他静态资源缓存一天；语言包引用版本同步更新。

#### 筛选与界面

- 决策历史使用紧凑编号及纳入、待定、排除统计，保留文献阅读空间。
- 过长标题及元数据在筛选界面截断；悬停或键盘聚焦被截断的标题可查看完整标题。
- AI 排序图标改为原创 **Pearl Companion** SVG，包含平静、开心、眨眼及好奇表情，以及轻微呼吸、眨眼和柔和色彩的脸颊变化。
- 导入引导卡片及收起后的胶囊增加静态 Pearl Companion；支持浅深色主题，并遵循减少动态效果的系统偏好。
- 导入顺序改用 SVG 图标。
- 轨迹状态移至当前任务旁，绿色表示记录中，灰色表示未记录；点击状态即可查看轨迹，移除工作区内重复入口。

#### 文档与安装包

- README 介绍及关联指南支持产品对应语言，补全失效链接，并整理 Ubuntu 服务器部署指南。
- 提供 Windows 安装程序、Linux AppImage、macOS DMG 及 SHA-256 校验文件。
- 本版本不包含远程控制或差分更新；当前更新入口仍下载安装包。升级时保留并备份现有数据目录。
