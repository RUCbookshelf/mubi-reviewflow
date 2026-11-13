# 已批准界面迁入记录 — 2026-11-13

用户完成 preview19 目检并明确要求替换正式产品。本次将通过目检的界面接入 `E:/rf215-authfe`，分支 `feat/2.1.5-ui-polish`，迁入前基线为 `6c14965d8021b164435996a8b6964f0b3f58102a`。当前为源码集成，未提交、推送或重新打包发布；历史 v2.1.3 发布任务已完成，与本次迁入无关。

## 接入内容

- 筛选训练、文献初筛、文献复筛、编码的紧凑功能菜单、对应 SVG 图标、阅读优先布局与弹窗居中。
- 首页与设置分类布局、独立模型设置页、合并后的界面偏好／主题／字体设置。
- 关键词词组左右分栏，保留默认词组。研究设计识别默认关闭，用户从阅读辅助开启；未识别时不占位。
- 高亮块批注编辑、保存、刷新读回和并发冲突草稿保护。
- 七套社区主题风格适配加原默认，全部支持浅色和深色；保留主题来源链接，按钮、条栏、概览和常驻侧栏按钮随主题切换。
- 移动端底部导航、更多菜单及阅读工具；六种实际开源字体与本地字体导入。界面字体不改变 PDF 自身字体和排版。

生产入口未引入预览工具栏、布局对比按钮、自动登录、预览会话、合成数据或预览服务器。部分内部类名和 API 保留 `Preview` 字样以保持现有依赖稳定，这些名称不向用户显示。

## 初始化与缓存

新增界面资源统一使用查询版本 `20261113-ui-approved2`。

1. `workflow-layout.js` 通过原 `application/x-reviewflow-workspace` 模块加载器加载，位于 `study_design_badge.js` 前，先建立默认关闭识别的适配层。
2. evidence、keyword、home、annotation、mobile、workspace-presentation 通过同一原加载器，在 `topic_cluster.js` 后按序加载。保留原认证、路由和任务选择入口。
3. theme-suites 与 typography-settings 在 `offline_banner.js` 之前即时加载，以便登录页恢复当前浏览器保存的外观偏好。这两个模块不读取任务 API。
4. 原 Service Worker 注册脚本仍为最后一个脚本；`sw.js` 字节、版本和策略均保持原样。HTML 继续 network-first，新版本 URL 避免复用旧的新增 CSS/JS 缓存。
5. 登出时隐藏移动端外壳并关闭相关弹层。移动端复用原节点，桌面还原时保留节点身份及事件。正式桌面运行时特有的关闭／重启按钮纳入移动端更多菜单，避免遮挡返回按钮。

字体通过应用同源静态目录按需加载，无外部字体服务。默认字体不会预下载六种字体。本地文件支持 TTF、OTF、WOFF、WOFF2，最多 32 MiB，保存在当前浏览器 IndexedDB，可移除，不上传、不安装到操作系统。Service Worker 不缓存 API 或字体；桌面本地服务器提供字体，不等于浏览器断开服务器后仍可离线读取全部字体。

## 批注保存 API

新增 `PATCH /api/tasks/{task_id}/highlights/{highlight_id}`，仅增加 `HighlightNoteUpdateIn` 和 `update_highlight_note_ep` 两个顶层符号。

请求字段为 `zotero_key`、`note`、`expected_note`，以及可选 `expected_policy_version`；拒绝额外字段，批注字段上限 4,000 字符，空字符串可清除批注。复用原 `require_user`、任务成员校验、筛选参与权限及全文工作流写入检查，仅使用已核实的当前用户数据库。

在 `BEGIN IMMEDIATE` 事务中同时校验高亮 ID、文献键和预期原批注，只更新 `note`。缺失或文献键不匹配返回 404，旧内容或并发写入冲突返回 409；前端保留用户草稿，允许查看最新内容后重新保存。不修改高亮坐标、颜色、文献归属或其他批注，也不增加数据库迁移。

## 主题来源

主题界面为 ReviewFlow 独立编写的风格适配，未分发上游主题 CSS、脚本或图片。产品主题卡片保留下列来源链接。

| 产品名称 | 原主题 | 来源 |
| --- | --- | --- |
| 玉青 | ReviewFlow 原默认 | 本项目原有界面 |
| 素简 | Minimal | https://github.com/kepano/obsidian-minimal |
| 澄蓝 | Things | https://github.com/colineckert/obsidian-things |
| 柔彩 | AnuPpuccin | https://github.com/anubisnekhet/AnuPpuccin |
| 蓝晶 | Blue Topaz | https://github.com/pkm-er/Blue-Topaz_Obsidian-css |
| 霜境 | Obsidian Nord | https://github.com/insanum/obsidian_nord |
| 墨夜 | Obsidianite | https://github.com/bennyxguo/Obsidian-Obsidianite |
| 石墨 | Atom | https://github.com/kognise/obsidian-atom |

字体为 Noto Sans SC、Noto Serif SC、LXGW WenKai Lite、Inter、Source Serif 4、JetBrains Mono。完整来源 URL、SHA-256、字重和许可证信息见 `custom_frontend/font-assets/manifest.json`，各原始 OFL 文本随字体分发；总许可入口为 `THIRD_PARTY_NOTICES.md`。

## 验证与限制

正式源码通过原 `build.launcher.build_app` 和真实后端在 `127.0.0.1:18943` 运行；使用独立合成数据目录 `E:/mubi-reviewflow/out/qa18943`。未使用预览服务、API mock 或登录令牌注入。完整验收报告和截图位于 `E:/mubi-reviewflow/out/frontend_product_integration/20261113-preview19`。

- Node 原有模块加载、认证存储和 PWA 回归：32 通过。
- 安装后的 Python 批注权限／并发及 PWA 静态回归：29 通过、2 个可选 Playwright 项跳过；存在一条原有 Starlette/httpx 弃用提醒。跳过项不计为通过，真实浏览器流程另行执行。
- 真实浏览器 28 项检查通过，覆盖冷启动登录边界、注册／登录／登出、模块仅初始化一次、1440／390／320 下界面偏好、实际字体字形渲染、本地文件导入／刷新／删除、缓存和同源请求。
- 合成任务的首页、初筛、复筛、编码、训练在 390 浅色和 320 深色下通过；无横向溢出，无默认研究设计请求；字体切换前后 PDF canvas 与文字位置一致。
- 两个真实登录窗口批注写入结果为 200 → 409 → 200，冲突草稿保留，刷新及重新打开后读回成功；其他高亮不变。
- 移动端关闭／重启按钮修复通过正常点击、320／390 宽度和 1440 原节点还原检查。
- 正式页面主题切换 32 个状态通过（8 套 × 浅／深 × 1440／390）：主题变量、概览底色、按钮、侧栏与移动底栏一致，无横向溢出、脚本错误或业务写请求。墨夜浅色桌面和手机截图已目检。默认浅色手机保留原移动端配色，按原移动端样式的预期值核验。

GitNexus 已运行影响检查及 `detect_changes`：本次未提交改动返回 low；与 main 比较返回 critical（196 个文件、709 个符号、29 个流程），包括当前分支既有历史变化，不能归因于本次界面迁入。已向用户说明该风险。由于 MCP 缓存、HTML 大小上限和未跟踪新文件，图谱结果不能作为完整覆盖证明；以迁入前基线、源文件哈希、既有后端 AST 比较和真实运行回归补充。既有高扇出认证函数未修改。

浏览器验证使用已有 Playwright Chromium，未重建或安装 Windows/macOS/Linux 安装包；iOS Safari、真实软键盘、安全区及系统文件选择器仍需对应真机验证。现有打包脚本递归包含 `custom_frontend`，已检查字体子目录位于该边界内。

## 备份与恢复

迁入前备份 192 个文件（190 个前端文件、后端 main.py、许可说明），原哈希见交付目录 `baseline.json`；新文件及哈希见 `installed-manifest.json`。既有未跟踪验收输出未删除。未改动 `E:/ReviewFlow` 或真实研究数据。

若需回退，先核对安装清单与当前文件哈希，保留后续用户修改，然后仅恢复本次修改的原文件并按清单移走本次新增文件。不要对工作树执行 reset/clean，也不要覆盖或删除任何任务数据库。
