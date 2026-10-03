# Windows 构建与安装

## 在你的 Windows 电脑上构建

1. 将整个文件夹复制或解压到本地磁盘，例如 `D:\ReviewFlow-Windows`。请先解压，不要直接在 ZIP 中运行。
2. 安装 Inno Setup 6/7（也支持 NSIS 3），保持网络连接。
3. 双击 `build\build_windows.bat`。构建会下载内嵌 Python、安装依赖，并生成离线修复文件。
4. 等待出现 `BUILD OK`。安装包位于 `build\Output\ReviewFlow-Setup-x64.exe`。
5. 将这个 EXE 发给同学。他们不需要复制源码，也不需要安装 Python、pip、Inno Setup 或后端环境。

请使用 Windows 10/11 的 x64 电脑构建和测试；此包不支持 Windows ARM 原生构建或32位Windows。
本目录是构建材料，不是已生成的安装 EXE。Ubuntu 无法直接验证 Windows 安装过程。

## 新用户启动

- 快捷方式直接使用安装包自带的 Python，不使用 VBS/wscript。
- 首次启动会检查运行环境。完整安装通常显示检查通过，然后打开浏览器中的应用。
- 依赖缺失或损坏时，会列出问题并询问是否修复。选择“否”会退出，不自动改动电脑。
- 同意后优先使用安装包的离线依赖文件，安装到当前用户的 `%LOCALAPPDATA%\ReviewFlow\runtime-deps`。不修改系统 Python，不要求管理员权限。
- 若离线依赖文件也被删除，修复需要联网。若 Python 本身被删除或损坏，重新运行安装 EXE 修复安装。
- 正常启动和使用不需要联网；在线AI/API功能仍按其设置需要网络。

依赖检测和修复日志：`%LOCALAPPDATA%\ReviewFlow\startup_diagnostic.log`。
应用日志与个人数据：`%LOCALAPPDATA%\ReviewFlow`。卸载不会删除个人研究数据。

## 发布前的 Windows 验收

请在一台没有安装 Python、没有启用 VBScript 的 Windows 电脑或干净虚拟机上检查：

- 安装到带空格或中文的路径，从桌面快捷方式启动。
- 注册/登录、RIS 导入、初筛/复筛/编码、AI 排序。
- 轨迹 ZIP（包括中文 PNG）、DOCX/PPTX 导出。
- 关闭程序后断网重启。
- 在测试机中移走安装目录的一个依赖包，确认缺失提示、拒绝退出和同意离线修复；不要对正式研究数据做破坏性测试。

构建脚本会先运行依赖一致性检查、聚类和中文PNG导出检查，再生成 EXE；失败时不要发布，查看 `build\build_windows.log` 和 `build\build_windows_smoke.log`。

## 文件范围

只包含 PathB 的前后端、运行模块、字体/图标和 Windows 构建脚本。未复制研究数据库、上传文献、账号数据、Ubuntu虚拟环境、书籍、旧 AppImage、测试缓存或开发日志。
依赖实际版本会写入安装目录的 `runtime-lock.txt`，离线修复 wheel 保存在 `wheelhouse`；完整第三方许可保留在依赖发行包中，另见 `THIRD_PARTY_NOTICES.md`。

## 更换研究数据目录

安装目录与研究数据目录分别设置。Windows 默认把研究数据保存到 `%LOCALAPPDATA%\ReviewFlow`；复筛 PDF 位于 `tasks\<任务标识>\pdfs` 中，会占用该磁盘的存储空间。

在「首页 & 设置 → 数据存放位置」选择 D/E 盘的空文件夹，点击「迁移到新目录」。退出应用并重新启动后，程序复制、校验数据，并更新 PDF 与编码图片的文件路径。Windows 用户可重启电脑以确保原后台进程已关闭。迁移失败时继续使用原目录，不会删除原数据。

确认新目录中的任务、PDF、编码和轨迹均可使用后，可点击「清理旧数据」释放原磁盘空间。清理只针对已迁移且未更改的旧数据；请勿直接删除整个用户配置目录。迁移对本机所有账号和任务生效，云端部署不提供客户端更改服务器目录的入口。

迁移后的后端日志 `reviewflow.log` 位于新数据目录；启动依赖检测日志仍位于 `%LOCALAPPDATA%\ReviewFlow\startup_diagnostic.log`。少量启动配置和依赖修复文件仍保存在系统用户目录。
