## 木笔 ReviewFlow 2.0.4

### English

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
