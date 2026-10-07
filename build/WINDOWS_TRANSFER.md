# Windows 构建与验收说明

本文面向需要从源码构建或核验 Windows 安装包的维护者。普通用户应优先从 [GitHub Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases) 下载已发布版本，不需要自行安装 Python、WSL 或构建工具。

## 构建条件

- Windows 10/11，64 位。
- 可访问网络：脚本会下载 Python 3.12 嵌入式运行时、`get-pip.py`，并从 `build/requirements-app.txt` 安装运行依赖。
- Inno Setup 6/7 或 NSIS 3；脚本会检测可用的安装器编译器。若两者都未安装，构建会失败并提示安装编译器。
- 足够空间用于运行时、依赖 wheel、构建暂存目录和安装包。不要把 `.venv` 或其他平台的 Python 环境复制进安装包。

## 构建步骤

1. 使用干净的仓库检出，并确认根目录 `VERSION` 是准备发布的版本号。
2. 在命令提示符或 PowerShell 中进入仓库根目录。
3. 运行：

   ```bat
   build\build_windows.bat
   ```

4. 等待脚本完成嵌入式 Python 准备、依赖安装、应用文件复制、依赖检查、运行时 smoke 检查和安装器编译。
5. 成功时应生成：`build/Output/ReviewFlow-Setup-x64.exe`。

构建日志位于 `build/build_windows.log`，运行时 smoke 输出位于 `build/build_windows_smoke.log`。失败时先检查日志中的第一个失败步骤，不要把未通过 smoke 的安装包发布。

从 WSL/UNC 路径调用时，`cmd.exe` 不能将 UNC 路径设为工作目录。构建脚本会将源码复制到 `%LOCALAPPDATA%\ReviewFlow-build` 后再编译，并把安装包复制回原输出目录。这样可以避免在网络路径上运行 Windows 安装器编译工具。

## 本地验收

构建脚本中的 `windows_smoke.py` 检查安装运行时依赖、scikit-learn 基本路径、SVG/PNG 导出和应用启动入口。它是构建门槛，不等于完整的安装后人工验收。

发布候选包还应在 Windows 10/11 x64 环境中手动核对：

- 安装器能正常完成安装，开始菜单快捷方式可启动应用；可选桌面快捷方式行为正常。
- 登录页可打开，安装目录中的本机服务只监听本机地址；若端口被占用，启动器能使用另一个空闲端口。
- 通过界面执行一个小型、可丢弃的数据流程，检查 RIS 导入、筛选进度、导出和主要导航。
- 检查卸载行为：程序文件和快捷方式按预期移除，位于 `%LOCALAPPDATA%\ReviewFlow` 的研究数据保留。
- 若验证重启/关闭、升级或数据迁移，应使用专门的测试账户和可恢复的测试数据；确认数据目录没有被安装或卸载步骤覆盖。

不要在用户的唯一研究数据库上做验收。手工验收完成后，删除测试任务应使用应用自身提供的正常删除流程。

## 发布

仓库的 `.github/workflows/release.yml` 在相应发布分支推送或手动触发后，为 Windows、Linux 和 macOS 分别构建，并在三项构建成功后上传安装包及 SHA-256 校验文件。发布前核对 `VERSION`、`RELEASE_NOTES.md`、标签和工作流目标提交；发布后下载 Windows 文件并核对名称、文件大小和校验值。
