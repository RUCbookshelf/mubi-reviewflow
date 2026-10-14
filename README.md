<div align="center">
  <h1>木笔 ReviewFlow</h1>
  <p><img src="custom_frontend/assets/icon_writelab_budnib_20260824.svg" alt="木笔 WriteLab 标识" width="96"></p>
  <p><strong>系统综述与 Meta 分析工作台 · Systematic review and meta-analysis workspace</strong></p>
  <p><a href="#中文">中文</a> · <a href="#english">English</a> · <a href="#français">Français</a> · <a href="#русский">Русский</a> · <a href="#español">Español</a> · <a href="#日本語">日本語</a> · <a href="#português">Português</a> · <a href="#deutsch">Deutsch</a> · <a href="#српски">Српски</a> · <a href="#한국어">한국어</a></p>
  <p><a href="https://github.com/RUCbookshelf/mubi-reviewflow">GitHub</a> · <a href="https://github.com/RUCbookshelf/mubi-reviewflow/releases">Releases</a> · <a href="https://github.com/RUCbookshelf/mubi-reviewflow/issues">Issues</a></p>
</div>

**2.1.3:** [下载 / Downloads](https://github.com/RUCbookshelf/mubi-reviewflow/releases/tag/v2.1.3) · [更新说明 / Release notes](RELEASE_NOTES.md)

2.1.3 使用引导：[导入文献](docs/IMPORT_LITERATURE_USER_GUIDE.zh-CN.md) · [报告关联与证据](docs/REPORT_LINKS_AND_EVIDENCE.zh-CN.md)。机器学习设置及排序更新间隔按成员保存。

## 中文

木笔 ReviewFlow 将文献导入、筛选、编码、数据分析与研究过程记录放在同一个工作台中，避免来回调动软件。
个人可以在本机离线使用；团队可以部署到服务器，共享任务文献和进度，同时独立保存各自的筛选决定。

应用采用 FastAPI 后端、浏览器前端和 Python 计算模块。桌面安装包包含 Python 与运行依赖，通过浏览器打开界面；使用者无需自行配置 Python 或 WSL。您可以直接前往Releases下载Linux、Windows、macOS对应安装包进行体验。

### 主要功能

| 功能 | 内容 |
| --- | --- |
| 文献导入 | RIS 导入、重复记录处理、导入历史与原始数据追溯 |
| 文献筛选 | 标题/摘要初筛、PDF 全文复筛、常用与自定义排除理由 |
| 机器学习排序 | 辅助安排阅读顺序；纳入、需复核和排除由用户决定 |
| 数据提取与编码 | 提取研究信息、记录编码内容与全文依据 |
| 数据分析 | 效应量计算、固定与随机效应合并、异质性、亚组、Meta 回归、敏感性与小研究效应分析 |
| 扩展分析 | 诊断准确性、网络 Meta 分析、剂量反应、IPD、贝叶斯与多层模型等模块；使用前请确认各模块的输入与适用条件 |
| 偏倚风险 | 记录 RoB 2、ROBINS-I、QUADAS-2 等工具的人工判断 |
| 文献计量 | 文献计量分析与证据图探索 |
| 研究轨迹 | 默认开启，可关闭；记录初筛、复筛和编码操作，预览并导出过程材料与多语言图表 |
| 团队协作 | 受邀成员共享任务目录、查看团队进度；支持离线决策导出、合并与仲裁 |
| 研究材料导出 | PRISMA 流程图，以及对应模块提供的 DOCX、PPTX、LaTeX 等材料 |

分析模块的实现不代表所有研究设计均已充分验证。方法前提、验证范围和限制见[分析能力评估](REVIEW_DATA_ANALYSIS_ASSESSMENT.md#zh-cn)。发表时请核对方法来源并引用相应文献；软件结果仍需研究者检查。

### 2.1.2 导入核验改进

- 首批记录就绪后即可核验，其余记录分批在后台加载。
- 紧凑圆环显示真实加载数量；中断后保留已加载记录并支持重试。
- 后台加载保留当前记录、选项、展开内容和滚动位置。
- 紧凑批次与加载状态、折叠来源文件和均分分类条；手机端可直接切换上一条或下一条。
- 确认保存后直接更新受影响记录，下一项可继续操作；辅助统计在后台刷新。
- 支持确认前预览的批量重复核验与批量保留已有信息，并可在启用前整批撤销。

### 2.1.1 导入与导出工作区

- 导入与报告、导入核验和导入历史统一组织；选中文件后明确确认导入。
- 重复候选、信息差异、待启用、已完成和稍后处理使用同一核验区域；处理后保持页面位置，启用前支持受保护的上次核验撤销。
- 悬浮待办可缩为显示当前事项的胶囊，全部完成后自动隐藏。
- 筛选结果、编码数据和全文高亮笔记按用途分组，保留七种原有导出；文件卡片说明格式、内容和下载状态。

### 流程控制与研究依据

- **首页与设置：** 六个标签页集中管理概览、任务与团队、筛选设置、机器学习、数据提取与编码、外观与存储。初筛顺序可选导入、机器学习或随机；随机顺序按用户、任务与来源范围保存。
- **导入来源与去重：** 来源名称直接使用导入文件的完整文件名（例如 `01-Wiley.ris`），不再从 RIS 数据库元数据推断。精确匹配与待人工核验的标题/模糊候选分开处理。按来源筛选或编码时，多来源文献在总体队列中只出现一次。
- **保护既有工作：** 已有保存决定或编码后，追加导入的新记录先暂存，负责人核验重复及元数据差异后激活；保留既有标识、决定与证据。
- **筛选决定与原因：** 初筛、全文筛选分别保存常用排除原因。两阶段分别设置是否提供暂不确定；初筛纳入或暂不确定均进入全文筛选，全文暂不确定仍待解决，不因该决定进入编码。 可选择第 2、3、4 或更多轮初筛，仅复核仍暂不确定的文献；不继续复核时进入全文筛选。
- **数据提取与编码：** 每个维度可添加文字、页码和 PNG/JPEG/WebP 图片证据。负责人可要求指定维度提供证据；不完整内容可保存为草稿，不计为完成。
- **撤销与轨迹回溯：** 初筛、全文筛选、编码支持受保护的撤销。可从轨迹打开文献修改当前结果；只有满足条件的最近保存可直接撤销，不能任意恢复旧快照。
- **团队管理：** 在同一区域管理成员与进度、阶段来源权限、筛选选项及导入来源。任务负责人、筛选与编码员、裁决员有角色标识与成员操作菜单；负责人可为各阶段设置全部来源、指定来源或暂停，成员列表及任务变更自动刷新。
- **PRISMA：** 保留独立手动填数与选择性导入任务指标，支持更新综述和其他来源分支及指标依据 CSV。未知数值需要确认；研究数与报告数独立，“每份报告对应一项研究”须主动选择。

**升级：** 从 [最新 Release](https://github.com/RUCbookshelf/mubi-reviewflow/releases/latest) 获取安装包，升级前备份整个数据目录。2.0.5 客户端可能不自动提示 2.0.5.1，请直接下载该维护版本；新版本支持四段版本比较。

### 快速开始

**使用安装包**

在 [Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases) 查看已发布文件；若尚无适合系统的安装包，请按下方说明构建。Windows 安装后从快捷方式启动，程序会检查依赖；缺失或损坏时先询问是否修复，同意后优先使用离线依赖文件。启动不依赖 VBS 脚本引擎。

**从源码运行（Ubuntu / macOS）**

准备 Python 3.12 和 Git。Linux 的 SVG 渲染需要系统 Cairo 库；Ubuntu 可先安装 `python3-venv`、`libcairo2`。随后运行：

```bash
git clone https://github.com/RUCbookshelf/mubi-reviewflow.git
cd mubi-reviewflow
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r build/requirements-app.txt
python build/launcher.py
```

启动器同时提供页面与 API，并打开浏览器。默认本机端口为 `8613`；端口被占用时尝试其他空闲端口，以实际打开的地址为准。`run_pathB.bat` 是 Windows 上通过 WSL 启动源码的开发入口，安装版不需要它。

### 数据存储与协作

| 运行方式 | 默认数据位置 |
| --- | --- |
| 源码运行 | 仓库内 `data/` |
| Windows 安装版 | `%LOCALAPPDATA%\ReviewFlow` |
| macOS 安装版 | `~/Library/Application Support/ReviewFlow` |
| Linux 安装版 | `~/.local/share/ReviewFlow`，或 `XDG_DATA_HOME` 下的 `ReviewFlow` |

上传的 PDF 位于数据目录中的 `tasks/<任务标识>/pdfs`，占用磁盘空间。安装版可在「首页 & 设置 → 外观与存储 → 数据存放位置」迁移到其他目录，**重启应用后生效**。迁移完成前保留原数据；确认新目录正常后再使用清理功能。卸载保留个人研究数据。请另行备份重要材料。

本机使用的数据保存在本机。启用在线 AI/API 时，相应请求会发往配置的服务。服务器模式将任务数据持久保存到服务器；同一服务器的任务成员能查看彼此进度，但不能修改他人的个人筛选决定。

团队服务器请按[Ubuntu 服务器部署指南](docs/deployment/UBUNTU_SERVER.md#zh-cn)配置。团队协作必须设置 `REVIEWFLOW_MODE=server`；不要用桌面启动器启动团队服务。账户、任务、导入原件、PDF 和决定保存在服务器数据目录，请备份整个目录。协作与离线合并说明见应用左下角「帮助」及[协作指南](docs/handoff/2026-09-30-cloud-collaboration.md#zh-cn)。

### 构建安装包

请在对应系统上构建；构建需要联网下载运行时和依赖。

| 平台 | 操作 | 输出 |
| --- | --- | --- |
| Windows 10/11 x64 | 安装 Inno Setup 6/7 或 NSIS 3，运行 `build\build_windows.bat` | `build/Output/ReviewFlow-Setup-x64.exe` |
| macOS | `bash build/build_macos.sh` | `build/dist/ReviewFlow.dmg` |
| Linux x86_64 | `bash build/build_linux.sh` | `build/dist/ReviewFlow-x86_64.AppImage` |

macOS 的 Intel 与 Apple Silicon 包应分别在对应架构构建。构建脚本存在不代表最新源码已有对应发布包；请检查发布时间与版本。Windows 详细步骤见[构建与验收说明](build/WINDOWS_TRANSFER.md#zh-cn)。

### 常见问题

- **无法连接 8613**：8613 是默认端口，不是错误代码。登录页提供「连接诊断与修复」。Windows 页面打不开时，可从开始菜单运行「检查与修复 ReviewFlow」，或运行安装目录的 `repair_reviewflow.bat`。
- **依赖修复失败**：查看 `%LOCALAPPDATA%\ReviewFlow\startup_diagnostic.log`。若自带 Python 被删除，重新运行安装 EXE。修复不删除研究数据。
- **反馈问题**：请在 [Issues](https://github.com/RUCbookshelf/mubi-reviewflow/issues) 提供系统、应用版本、复现步骤及已去除个人信息的日志。

### 开发与贡献

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

| 目录 | 用途 |
| --- | --- |
| `custom_frontend/` | 页面、交互、多语言与 PDF 阅读器 |
| `custom_backend/` | API、认证、任务与文件处理 |
| `coscreen/` | 文献处理、统计计算与导出 |
| `build/` | 启动器与各平台构建脚本 |
| `tests/` | 自动化测试 |
| `docs/` | 方法、设计与部署说明 |

欢迎通过 Issue 或 Pull Request 提交问题与改进。统计方法变更请附方法来源、适用条件及可复现的参考结果。请勿提交账号、密钥、研究数据库或未获授权的全文文件。

### 许可证与联系

本项目采用 MIT 许可证，详见根目录 [`LICENSE`](LICENSE)。第三方组件遵循各自许可证，见[第三方许可](THIRD_PARTY_NOTICES.md)与[发布准备说明](OPEN_SOURCE_RELEASE.md#zh-cn)。

作者：**Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn)

---

## English

ReviewFlow is a workspace for systematic reviews and meta-analysis, covering reference import, screening, coding, analysis and records of the research process. Individuals can work locally offline. Teams can deploy it on a server to share references and progress while keeping each reviewer's decisions separate.

The application uses FastAPI, a browser interface and Python calculation modules. Desktop packages bundle Python and runtime dependencies; users do not need to install Python or WSL. The [Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases) page lists the current version and available desktop installers.

### Features

- **Reference import:** RIS files, duplicate handling, import history and original import data.
- **Screening:** title/abstract and PDF full-text screening, with standard or custom exclusion reasons.
- **Machine Learning:** assistance with reading order; inclusion, uncertainty and exclusion remain user decisions.
- **Data extraction & coding:** study information extraction and supporting full-text evidence.
- **Analysis:** effect sizes, fixed and random effects, heterogeneity, subgroups, meta-regression, sensitivity and small-study effects.
- **Extended methods:** diagnostic accuracy, network meta-analysis, dose–response, IPD, Bayesian and multilevel modules, each with its own input requirements.
- **Risk of bias:** manual assessments using tools such as RoB 2, ROBINS-I and QUADAS-2.
- **Bibliometrics:** bibliometric analysis and evidence map exploration.
- **Research trace:** enabled by default and optional; screening and coding activity records, previews and multilingual exports.
- **Collaboration:** shared task references and team progress, plus offline decision export, merging and adjudication.
- **Exports:** PRISMA diagrams and module-specific DOCX, PPTX and LaTeX materials.

Implementation does not establish validation for every study design. Consult the [analysis assessment](REVIEW_DATA_ANALYSIS_ASSESSMENT.md#en) for assumptions and validation limits. Check results and cite the original methods when publishing.

### Import review loading in 2.1.2

- Review the first available records while remaining records load in small background batches.
- A compact animated ring shows actual loading counts and supports retry after an interruption.
- Background loading preserves the current record, choices, expanded details and scroll position.

### Import and export workspace in 2.1.1

- Upload and report, review, and history tabs; explicitly confirm selected files before importing.
- One review area for duplicate candidates, information differences, ready, completed and deferred records. Preserve the page position and undo the last eligible review before activation.
- A floating checklist collapses to the current item and hides when complete.
- Screening results, coding data and full-text highlight notes use file cards that explain formats, contents and download status; all seven existing exports remain available.

### Workflow controls and research evidence

- **Home & Settings:** six tabs organize Overview, Tasks & team, Screening settings, Machine Learning, Data extraction & coding, and Appearance & storage. Initial-screening order offers Import / Machine Learning / Random; random order persists per user, task and source scope.
- **Sources and deduplication:** Source names use the complete imported filename (for example, `01-Wiley.ris`), without inferring names from RIS database metadata. Exact matches are separate from title/fuzzy candidates requiring human review. Multi-source articles appear once in the overall queue when screening or coding by source.
- **Protect existing work:** once decisions or coding have been saved, additional new records are staged for owner review of duplicates and metadata differences before activation. Existing identities, decisions and evidence are retained.
- **Decisions and reasons:** save reusable exclusion reasons separately for initial and full-text screening. Not Sure Now is configurable independently for both stages. Initial Include and Not Sure Now advance to full-text screening; full-text Not Sure Now remains unresolved and does not qualify an article for coding. Optional second, third, fourth or further title-and-abstract rounds revisit only remaining Not Sure Now articles; stopping releases them to full-text screening.
- **Data extraction & coding:** add text, page references and PNG/JPEG/WebP evidence per dimension. Owners may require evidence for selected dimensions; incomplete work can be saved as a draft and is not counted as complete.
- **Undo and Trace:** guarded Undo is available in initial screening, full-text screening and coding. Open linked articles from Trace to correct current results; only an eligible latest save can be undone directly, rather than restoring arbitrary historical snapshots.
- **Team management:** one panel contains members/progress, stage source permissions, screening options and imported sources. Task owner / Coder / Adjudicator badges and member action menus identify responsibilities. Owners choose All / Selected / Paused per stage; membership and task lists refresh automatically.
- **PRISMA:** preserve standalone manual entry and selective task-metric import, with updated-review and other-source branches and metric-provenance CSV. Unknown values need confirmation. Study and report counts are independent; one report per study is an explicit shortcut.

**Upgrading:** download from the [latest Release](https://github.com/RUCbookshelf/mubi-reviewflow/releases/latest) and back up the complete data directory first. Existing 2.0.5 clients may not automatically prompt for 2.0.5.1; download this hotfix directly. The corrected checker supports four-part versions.

### Getting started

Check [Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases) for available packages. If no suitable package is available, build one using the instructions below. Windows checks dependencies at startup and asks before repairing missing or damaged dependencies, using bundled offline files where available. VBScript is not required.

For source use on Ubuntu or macOS, install Python 3.12 and Git. Linux SVG rendering requires Cairo; on Ubuntu install `python3-venv` and `libcairo2` as needed.

```bash
git clone https://github.com/RUCbookshelf/mubi-reviewflow.git
cd mubi-reviewflow
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r build/requirements-app.txt
python build/launcher.py
```

The launcher serves the UI and API together and opens a browser. The default local port is `8613`; another available port is used if needed. Use the address opened by the launcher. `run_pathB.bat` is a Windows/WSL development entry, not an installed application requirement.

### Storage and collaboration

Source runs use the repository's `data/` folder. Installed applications default to:

- Windows: `%LOCALAPPDATA%\ReviewFlow`
- macOS: `~/Library/Application Support/ReviewFlow`
- Linux: `~/.local/share/ReviewFlow`, or `ReviewFlow` under `XDG_DATA_HOME`.

Uploaded PDFs are stored under `tasks/<task-id>/pdfs` inside the data folder. In installed applications, use **Home & Settings → Appearance & storage → Data location** to migrate data. **Restart the application to apply the change.** The original data is retained until you confirm the new location and use the cleanup option. Uninstalling retains personal research data; keep separate backups.

Local mode stores research data locally. Online AI/API features send relevant requests to the configured service. Server mode persists task data on the server. Task members can see each other's progress without changing another reviewer's personal decisions.

For team deployment, follow the [Ubuntu server deployment guide](docs/deployment/UBUNTU_SERVER.md#en). Team collaboration requires `REVIEWFLOW_MODE=server`; do not use the desktop launcher for the team service. Accounts, tasks, imported originals, PDFs, and decisions are stored in the server data directory, which should be backed up in full. See **Help** in the sidebar and the [collaboration guide](docs/handoff/2026-09-30-cloud-collaboration.md#en) for collaboration and offline merging.

### Building packages

Build on the target operating system with an internet connection.

| Platform | Command / preparation | Output |
| --- | --- | --- |
| Windows 10/11 x64 | Install Inno Setup 6/7 or NSIS 3; run `build\build_windows.bat` | `build/Output/ReviewFlow-Setup-x64.exe` |
| macOS | `bash build/build_macos.sh` | `build/dist/ReviewFlow.dmg` |
| Linux x86_64 | `bash build/build_linux.sh` | `build/dist/ReviewFlow-x86_64.AppImage` |

Build Intel and Apple Silicon macOS packages separately on the corresponding architecture. Check release dates and versions: a build script does not imply that an up-to-date binary is published. See the [Windows build guide](build/WINDOWS_TRANSFER.md#en).

### Troubleshooting and contributing

`8613` is a port, not an error code. Use **Connection diagnostics and repair** on the login page. If the Windows page cannot load, use **检查与修复 ReviewFlow** in the Start menu or `repair_reviewflow.bat` in the installation folder. Dependency logs are in `%LOCALAPPDATA%\ReviewFlow\startup_diagnostic.log`. Reinstall the application if bundled Python is missing.

Report reproducible problems through [Issues](https://github.com/RUCbookshelf/mubi-reviewflow/issues), including your OS, version and sanitized logs. Pull requests are welcome. Statistical changes should include method sources, assumptions and reproducible reference results. Do not commit credentials, research databases or unauthorized full texts.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

Code is organized into `custom_frontend/` (UI), `custom_backend/` (API), `coscreen/` (research logic), `build/` (launchers and packaging), `tests/` and `docs/`.

### License and contact

This project is licensed under the MIT License; see the root [`LICENSE`](LICENSE). Third-party components retain their respective licenses. See [third-party notices](THIRD_PARTY_NOTICES.md) and [release preparation](OPEN_SOURCE_RELEASE.md#en).

Author: **Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn)

---


## Español

ReviewFlow reúne la importación de referencias, el cribado, la codificación, el análisis y el registro del proceso de revisión sistemática y metaanálisis. Puede utilizarse sin conexión en un ordenador personal o desplegarse en un servidor para colaborar, manteniendo separadas las decisiones de cada revisor.

### Funciones

- Importación RIS, detección y gestión de duplicados, historial y trazabilidad de los archivos originales.
- Cribado de título/resumen y de texto completo en PDF; motivos de exclusión estándar o personalizados.
- Ordenación asistida por aprendizaje automático; las decisiones de inclusión, duda y exclusión siguen correspondiendo al usuario.
- Codificación de estudios con evidencia del texto completo; análisis de tamaños del efecto, heterogeneidad, subgrupos, metarregresión y sensibilidad.
- Evaluación manual del riesgo de sesgo, bibliometría, mapas de evidencia, colaboración y diagramas PRISMA.
- Registro de investigación opcional y exportación de materiales, según el módulo, a DOCX, PPTX o LaTeX.

La disponibilidad de un método no significa que esté validado para todos los diseños. Consulte la [evaluación de análisis](REVIEW_DATA_ANALYSIS_ASSESSMENT.md#es), compruebe los resultados y cite las fuentes metodológicas.

### Controles del flujo de trabajo y evidencia

- **Inicio y configuración:** seis pestañas reúnen Resumen, Tareas y equipo, Cribado, Aprendizaje automático, Extracción y codificación de datos, Apariencia y almacenamiento. Orden inicial: Importación / Aprendizaje automático / Aleatorio; el orden aleatorio persiste por usuario, tarea y conjunto de fuentes.
- **Fuentes y duplicados:** Las fuentes usan el nombre completo del archivo importado (por ejemplo, `01-Wiley.ris`), sin inferirlo de metadatos RIS. Las coincidencias exactas se separan de candidatos por título/similitud para revisión humana. Una referencia de varias fuentes aparece una sola vez en la cola global.
- **Trabajo existente:** tras guardar decisiones o codificación, los nuevos registros de importaciones adicionales quedan pendientes de revisión de duplicados y diferencias de metadatos por el propietario antes de activarse. Se conservan identidades, decisiones y evidencia.
- **Decisiones y motivos:** bibliotecas de motivos reutilizables separadas para ambos cribados. No estoy seguro aún se configura por etapa. Incluir y No estoy seguro aún iniciales avanzan al texto completo; la incertidumbre en texto completo queda sin resolver y no permite pasar a codificación. Las rondas opcionales 2, 3, 4 o posteriores revisan solo registros aún inciertos; al detenerlas pasan al texto completo.
- **Extracción y codificación:** texto, páginas e imágenes PNG/JPEG/WebP por dimensión. El propietario puede exigir evidencia; un borrador incompleto se guarda sin contarse como finalizado.
- **Deshacer y trazabilidad:** deshacer protegido en las tres etapas; abrir un artículo vinculado desde el registro para corregir sus valores actuales. Solo se deshace directamente el último guardado admisible, no cualquier instantánea histórica.
- **Equipo:** un panel integra miembros/progreso, fuentes permitidas por etapa, opciones de cribado y fuentes importadas. Insignias Responsable de la tarea / Revisor y codificador / Adjudicador y menús de miembros indican responsabilidades. El propietario elige Todas / Seleccionadas / En pausa; miembros y tareas se actualizan automáticamente.
- **PRISMA:** entrada manual independiente o importación selectiva de métricas, revisiones actualizadas/otras fuentes y CSV de procedencia. Los valores desconocidos necesitan confirmación; estudios e informes son independientes, con un atajo explícito de un informe por estudio.

**Actualización:** descargue la [última versión](https://github.com/RUCbookshelf/mubi-reviewflow/releases/latest) y respalde toda la carpeta de datos. El cliente 2.0.5 puede no avisar de 2.0.5.1; descargue el parche directamente. El comprobador corregido admite versiones de cuatro componentes.

### Inicio y datos

Descargue el instalador disponible para su sistema desde [Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases). Los instaladores de escritorio incluyen Python y sus dependencias. Para ejecutar el código fuente en Ubuntu o macOS, prepare Python 3.12 y Git; en Linux instale Cairo y `python3-venv` si son necesarios, y siga los comandos de la sección [English](#english).

Los datos se guardan localmente de forma predeterminada: Windows `%LOCALAPPDATA%\ReviewFlow`, macOS `~/Library/Application Support/ReviewFlow` y Linux `~/.local/share/ReviewFlow` o `XDG_DATA_HOME`. Los PDF ocupan espacio dentro de la carpeta de datos. La ubicación puede migrarse desde Inicio y configuración; reinicie la aplicación para aplicar el cambio y mantenga copias de seguridad. El modo servidor conserva los datos en el servidor; use almacenamiento persistente y HTTPS para el acceso externo. Las funciones de IA en línea envían las solicitudes pertinentes al servicio configurado. Para el despliegue de equipos, consulte la [guía de servidor Ubuntu](docs/deployment/UBUNTU_SERVER.md#es).

### Compilación, ayuda y licencia

Las instrucciones para Windows, macOS y Linux están en [Construir paquetes](#building-packages). Consulte la ayuda de la barra lateral y la [guía de colaboración](docs/handoff/2026-09-30-cloud-collaboration.md#es). Para informar un problema, abra un [Issue](https://github.com/RUCbookshelf/mubi-reviewflow/issues) con el sistema, la versión y los pasos para reproducirlo; quite los datos personales de los registros. Las contribuciones pueden enviarse mediante Pull Request. Licencia MIT: [`LICENSE`](LICENSE). Autor: **Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn).

## 日本語

ReviewFlowは、文献の取り込み、スクリーニング、コーディング、分析、研究過程の記録をまとめたシステマティックレビュー／メタ分析用ワークスペースです。個人はローカルでオフライン利用でき、チームはサーバーに配置して進捗を共有しながら、各レビュアーの判断を分けて保存できます。

### 主な機能

- RIS取り込み、重複候補の確認、インポート履歴、元ファイルの追跡。
- タイトル／抄録スクリーニング、PDF全文スクリーニング、標準またはカスタムの除外理由。
- 機械学習による閲覧順の補助。採用・保留・除外の判断はユーザーが行います。
- 全文根拠を伴う研究情報のコーディング、効果量、異質性、サブグループ、メタ回帰、感度分析。
- バイアスリスクの評価、文献計量、エビデンスマップ、チーム共同作業、PRISMAフロー図。
- 研究履歴の記録と、各モジュールに応じたDOCX／PPTX／LaTeX出力。

機能が実装されていることは、すべての研究デザインで妥当性が確認されたことを意味しません。[分析機能の評価](REVIEW_DATA_ANALYSIS_ASSESSMENT.md#ja)を確認し、結果を検証して方法論の原典を引用してください。

### ワークフロー管理と研究の根拠

- **ホームと設定：** 概要、タスクとチーム、スクリーニング、機械学習、データ抽出とコーディング、外観と保存先の6タブ。初期スクリーニング順は取り込み順／機械学習／ランダムで、ランダム順はユーザー・タスク・対象ソースごとに保持します。
- **ソースと重複：** ソース名は取り込んだファイル名全体（例：`01-Wiley.ris`）を使用し、RISのデータベースメタデータから推測しません。完全一致と、手動確認が必要なタイトル・類似候補を区別し、複数ソースの文献は全体キューに一度だけ表示します。
- **既存作業の保護：** 判断やコーディングを保存した後の追加取り込みは、新規記録を一時保留し、所有者が重複・メタデータ差異を確認して有効化します。識別子、判断、根拠は保持します。
- **判断と除外理由：** 初期／全文の理由ライブラリを別々に保存します。今は判断できないは各段階で設定できます。初期の採用・今は判断できないは全文へ進み、全文の今は判断できないは未解決のままでコーディング対象にはなりません。 第2、第3、第4以降の選別を任意で行い、判断できない文献だけを再確認できます。続けない場合は全文選別へ進みます。
- **データ抽出とコーディング：** 各項目に文章、ページ、PNG/JPEG/WebP画像を添付できます。所有者は根拠を必須にでき、不完全な内容は下書きとして保存し、完了には数えません。
- **取り消しと履歴：** 3段階で保護付き取り消しを利用できます。履歴から文献を開いて現在値を修正できますが、直接取り消せるのは条件を満たす直近の保存のみです。
- **チーム：** メンバーと進捗、段階別ソース、判断設定、取り込み済みソースを一つのパネルにまとめます。タスク所有者／スクリーニング・コーディング担当／判定担当者のバッジと操作メニューを表示し、所有者は全ソース／選択／一時停止を設定します。メンバーとタスク一覧は自動更新します。
- **PRISMA：** 独立した手入力と選択的なタスク指標取り込みを維持し、更新レビュー・その他ソースと指標の由来CSVに対応します。不明値は確認が必要です。研究数と報告数は独立し、1報告＝1研究は明示的に選択します。

**更新：** データフォルダー全体をバックアップしてから[最新版](https://github.com/RUCbookshelf/mubi-reviewflow/releases/latest)を取得してください。2.0.5は2.0.5.1を自動通知しない場合があるため、直接ダウンロードしてください。修正版は4要素のバージョンに対応します。

### はじめに・データ保存

対応するデスクトップ版を[Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases)から入手してください。インストーラーにはPythonと実行依存関係が含まれます。Ubuntu／macOSでソースから実行する場合はPython 3.12とGitを用意し、Linuxでは必要に応じてCairoと`python3-venv`をインストールして、[English](#english)の手順を実行してください。

データは既定でローカルに保存されます。Windowsは`%LOCALAPPDATA%\ReviewFlow`、macOSは`~/Library/Application Support/ReviewFlow`、Linuxは`~/.local/share/ReviewFlow`または`XDG_DATA_HOME`配下です。PDFはデータフォルダー内に保存されます。保存先を移行した後はアプリを再起動し、別途バックアップを保管してください。サーバーモードではデータはサーバーに保存されます。外部公開には永続ストレージとHTTPSを使用してください。オンラインAI機能では設定したサービスに該当するリクエストが送信されます。チームサーバーの手順は[Ubuntuサーバー構築ガイド](docs/deployment/UBUNTU_SERVER.md#ja)を参照してください。

### ビルド・サポート・ライセンス

Windows、macOS、Linuxのビルド方法は[パッケージのビルド](#building-packages)を参照してください。サイドバーのヘルプと[共同作業ガイド](docs/handoff/2026-09-30-cloud-collaboration.md#ja)も利用できます。不具合は、OS、バージョン、再現手順を添えて[Issue](https://github.com/RUCbookshelf/mubi-reviewflow/issues)へ報告してください。ログから個人情報を削除してください。ライセンスはMIT（[`LICENSE`](LICENSE)）。作者：**Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn)。

## Português

O ReviewFlow reúne importação de referências, triagem, codificação, análise e registro do processo de revisão sistemática e meta-análise. Pode ser usado offline localmente ou implantado em um servidor para colaboração, mantendo separadas as decisões de cada revisor.

### Recursos

- Importação RIS, identificação e revisão de duplicatas, histórico e rastreabilidade dos arquivos originais.
- Triagem de título/resumo e de texto completo em PDF, com motivos de exclusão padrão ou personalizados.
- Ordenação de leitura assistida por aprendizado de máquina; as decisões de incluir, deixar em dúvida ou excluir pertencem ao usuário.
- Codificação de estudos com evidências do texto completo; tamanhos de efeito, heterogeneidade, subgrupos, meta-regressão e análises de sensibilidade.
- Avaliação manual de risco de viés, bibliometria, mapas de evidências, colaboração e diagramas PRISMA.
- Registro da pesquisa e exportação, conforme o módulo, para DOCX, PPTX ou LaTeX.

A implementação de um método não comprova sua validação para todos os delineamentos. Consulte a [avaliação dos recursos de análise](REVIEW_DATA_ANALYSIS_ASSESSMENT.md#pt), confira os resultados e cite as fontes metodológicas.

### Controles do fluxo de trabalho e evidências

- **Início e configurações:** seis abas organizam Visão geral, Tarefas e equipe, Triagem, Aprendizado de máquina, Extração e codificação de dados, Aparência e armazenamento. Ordem inicial: Importação / Aprendizado de máquina / Aleatória; a ordem aleatória persiste por usuário, tarefa e conjunto de fontes.
- **Fontes e duplicatas:** Os nomes das fontes usam o nome completo do arquivo importado (por exemplo, `01-Wiley.ris`), sem inferência pelos metadados RIS. Correspondências exatas são separadas de candidatos por título/similaridade para revisão humana. Um artigo de várias fontes aparece uma vez na fila global.
- **Trabalho existente:** após salvar decisões ou codificação, novos registros de importações adicionais ficam aguardando revisão de duplicatas e diferenças de metadados pelo proprietário antes da ativação. Identidades, decisões e evidências são mantidas.
- **Decisões e motivos:** bibliotecas reutilizáveis separadas para triagem inicial e texto completo. Ainda não tenho certeza é configurável por etapa. Incluir e Ainda não tenho certeza iniciais avançam ao texto completo; a incerteza no texto completo continua pendente e não autoriza codificação. Rodadas opcionais 2, 3, 4 ou posteriores revisam apenas os registros ainda incertos; ao parar, seguem para o texto completo.
- **Extração e codificação:** texto, páginas e imagens PNG/JPEG/WebP por dimensão. O proprietário pode exigir evidências; rascunhos incompletos são salvos sem contar como concluídos.
- **Desfazer e histórico:** desfazer protegido nas três etapas; abrir artigos vinculados ao histórico para corrigir valores atuais. Apenas o último salvamento elegível pode ser desfeito diretamente, sem restaurar instantâneos históricos arbitrários.
- **Equipe:** um painel reúne membros/progresso, fontes por etapa, opções de triagem e fontes importadas. Insígnias Responsável pela tarefa / Revisor e codificador / Adjudicador e menus de membros indicam responsabilidades. O proprietário escolhe Todas / Selecionadas / Pausada; membros e tarefas atualizam automaticamente.
- **PRISMA:** entrada manual independente ou importação seletiva de métricas, revisões atualizadas/outras fontes e CSV de procedência. Valores desconhecidos exigem confirmação; estudos e relatórios são independentes, com atalho explícito de um relatório por estudo.

**Atualização:** baixe a [versão mais recente](https://github.com/RUCbookshelf/mubi-reviewflow/releases/latest) e faça backup de toda a pasta de dados. O cliente 2.0.5 pode não avisar sobre 2.0.5.1; baixe o corretivo diretamente. A verificação corrigida aceita versões com quatro componentes.

### Como começar e onde ficam os dados

Baixe o instalador disponível para seu sistema em [Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases). Os instaladores incluem Python e as dependências. Para executar o código-fonte no Ubuntu ou macOS, instale Python 3.12 e Git; no Linux, instale Cairo e `python3-venv` se necessário e siga os comandos na seção [English](#english).

Por padrão, os dados ficam no computador: Windows `%LOCALAPPDATA%\ReviewFlow`, macOS `~/Library/Application Support/ReviewFlow` e Linux `~/.local/share/ReviewFlow` ou em `XDG_DATA_HOME`. PDFs ficam na pasta de dados. A migração do local de armazenamento é feita em Início e configurações e requer reiniciar o aplicativo. Mantenha cópias de segurança. No modo servidor, os dados ficam no servidor; use armazenamento persistente e HTTPS para acesso externo. Recursos de IA online enviam as solicitações pertinentes ao serviço configurado. Para implantação em equipe, consulte o [guia de servidor Ubuntu](docs/deployment/UBUNTU_SERVER.md#pt).

### Compilação, suporte e licença

As instruções para Windows, macOS e Linux estão em [Build packages](#building-packages). Consulte a ajuda na barra lateral e o [guia de colaboração](docs/handoff/2026-09-30-cloud-collaboration.md#pt). Para relatar um problema, abra um [Issue](https://github.com/RUCbookshelf/mubi-reviewflow/issues) com sistema, versão e etapas para reproduzi-lo; remova dados pessoais dos registros. Contribuições são bem-vindas por Pull Request. Licença MIT: [`LICENSE`](LICENSE). Autor: **Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn).

## Deutsch

ReviewFlow bündelt Literaturimport, Screening, Kodierung, Analyse und die Dokumentation des Forschungsprozesses für systematische Reviews und Metaanalysen. Einzelpersonen können lokal offline arbeiten. Teams können die Anwendung auf einem Server betreiben und Fortschritte teilen, während die Entscheidungen der einzelnen Reviewer getrennt bleiben.

### Funktionen

- RIS-Import, Erkennung und Prüfung von Duplikaten, Importverlauf und Rückverfolgbarkeit der Originaldateien.
- Titel-/Abstract-Screening und PDF-Volltextscreening mit standardisierten oder eigenen Ausschlussgründen.
- Lesereihenfolge mit maschinellem Lernen; über Einschluss, Unsicherheit und Ausschluss entscheidet weiterhin der Nutzer.
- Studienkodierung mit Volltextbelegen; Effektgrößen, Heterogenität, Subgruppen, Metaregression und Sensitivitätsanalysen.
- Manuelle Bewertung des Verzerrungsrisikos, Bibliometrie, Evidenzkarten, Zusammenarbeit und PRISMA-Flussdiagramme.
- Forschungsprotokoll und je nach Modul Export als DOCX, PPTX oder LaTeX.

Eine implementierte Methode ist nicht automatisch für jedes Studiendesign validiert. Lesen Sie die [Bewertung der Analysefunktionen](REVIEW_DATA_ANALYSIS_ASSESSMENT.md#de), prüfen Sie die Ergebnisse und zitieren Sie die methodischen Originalquellen.

### Workflow-Steuerung und Forschungsbelege

- **Startseite und Einstellungen:** sechs Register für Übersicht, Aufgaben und Team, Screening, Maschinelles Lernen, Datenextraktion und Kodierung, Darstellung und Speicherung. Erstscreening-Reihenfolge: Import / Maschinelles Lernen / Zufällig; die Zufallsreihenfolge bleibt je Nutzer, Aufgabe und Quellenumfang erhalten.
- **Quellen und Duplikate:** Quellennamen entsprechen dem vollständigen importierten Dateinamen (z. B. `01-Wiley.ris`), ohne Ableitung aus RIS-Metadaten. Exakte Treffer werden von manuell zu prüfenden Titel-/Ähnlichkeitskandidaten getrennt. Artikel mit mehreren Quellen erscheinen einmal in der Gesamtwarteschlange.
- **Bestehende Arbeit:** nach gespeicherten Entscheidungen oder Kodierungen bleiben neue zusätzliche Importdatensätze zunächst vorgemerkt. Der Eigentümer prüft Duplikate und Metadatenunterschiede vor der Aktivierung; Identitäten, Entscheidungen und Belege bleiben erhalten.
- **Entscheidungen und Gründe:** getrennte wiederverwendbare Ausschlussgründe für Erst- und Volltextscreening. Im Moment unsicher ist je Phase konfigurierbar. Einschluss und Im Moment unsicher im Erstscreening führen zum Volltext; Im Moment unsicher im Volltext bleibt ungeklärt und berechtigt nicht zur Kodierung. Optionale zweite, dritte, vierte und weitere Runden prüfen nur verbleibende unsichere Datensätze; beim Beenden gehen diese zum Volltext-Screening.
- **Datenextraktion und Kodierung:** Text, Seitenangaben und PNG/JPEG/WebP-Bilder je Dimension. Der Eigentümer kann Belege verlangen; unvollständige Entwürfe sind speicherbar, zählen aber nicht als abgeschlossen.
- **Rückgängig und Protokoll:** geschütztes Rückgängigmachen in allen drei Phasen; verknüpfte Artikel aus dem Protokoll zur Korrektur aktueller Werte öffnen. Nur die letzte zulässige Speicherung lässt sich direkt rückgängig machen, kein beliebiger historischer Stand.
- **Team:** ein Bereich enthält Mitglieder/Fortschritt, Quellen je Phase, Screening-Optionen und importierte Quellen. Aufgabeninhaber / Screening- und Kodierperson / Entscheidungsperson werden durch Symbole und Mitgliedermenüs gekennzeichnet. Der Eigentümer wählt Alle / Ausgewählte / Pausiert; Mitglieder- und Aufgabenlisten aktualisieren sich automatisch.
- **PRISMA:** unabhängige manuelle Eingabe oder selektiver Kennzahlenimport, aktualisierte Reviews/andere Quellen und Herkunfts-CSV. Unbekannte Werte müssen bestätigt werden; Studien und Berichte werden getrennt gezählt, ein Bericht pro Studie ist eine ausdrückliche Auswahl.

**Aktualisierung:** laden Sie die [neueste Version](https://github.com/RUCbookshelf/mubi-reviewflow/releases/latest) und sichern Sie vorher den gesamten Datenordner. Version 2.0.5 meldet 2.0.5.1 möglicherweise nicht automatisch; laden Sie den Hotfix direkt. Die korrigierte Prüfung unterstützt vierteilige Versionen.

### Einstieg und Datenspeicherung

Laden Sie ein verfügbares Installationspaket für Ihr System unter [Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases) herunter. Desktop-Pakete enthalten Python und Laufzeitabhängigkeiten. Für die Ausführung aus dem Quellcode unter Ubuntu oder macOS benötigen Sie Python 3.12 und Git; unter Linux bei Bedarf Cairo und `python3-venv`. Die Befehle stehen im Abschnitt [English](#english).

Standardmäßig werden Daten lokal gespeichert: Windows `%LOCALAPPDATA%\ReviewFlow`, macOS `~/Library/Application Support/ReviewFlow` und Linux `~/.local/share/ReviewFlow` oder unter `XDG_DATA_HOME`. PDFs liegen im Datenordner. Nach einer Änderung des Speicherorts muss die Anwendung neu gestartet werden. Erstellen Sie zusätzliche Sicherungskopien. Im Servermodus liegen Aufgabendaten auf dem Server; für externen Zugriff sind dauerhafter Speicher und HTTPS erforderlich. Online-KI-Funktionen senden die betreffenden Anfragen an den konfigurierten Dienst. Für die Teambereitstellung siehe den [Ubuntu-Serverleitfaden](docs/deployment/UBUNTU_SERVER.md#de).

### Pakete, Hilfe und Lizenz

Die Bauanleitungen für Windows, macOS und Linux stehen unter [Pakete erstellen](#building-packages). Nutzen Sie die Hilfe in der Seitenleiste und den [Leitfaden zur Zusammenarbeit](docs/handoff/2026-09-30-cloud-collaboration.md#de). Melden Sie Probleme unter [Issues](https://github.com/RUCbookshelf/mubi-reviewflow/issues) mit Betriebssystem, Version und Reproduktionsschritten; entfernen Sie persönliche Daten aus Protokollen. Beiträge sind als Pull Request willkommen. MIT-Lizenz: [`LICENSE`](LICENSE). Autor: **Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn).

## Српски

ReviewFlow objedinjuje uvoz literature, skrining, kodiranje, analizu i beleženje istraživačkog procesa za sistematske preglede i meta-analize. Može se koristiti lokalno bez interneta ili postaviti na server radi timskog rada, uz odvojeno čuvanje odluka svakog recenzenta.

### Funkcije

- RIS uvoz, otkrivanje i provera duplikata, istorija uvoza i praćenje izvornih datoteka.
- Skrining naslova/apstrakta i punog teksta PDF-a, uz standardne ili prilagođene razloge za isključivanje.
- Mašinsko učenje za pomoć pri određivanju redosleda čitanja; korisnik donosi odluke o uključivanju, neizvesnosti ili isključivanju.
- Kodiranje studija uz dokaze iz punog teksta; veličine efekta, heterogenost, podgrupe, meta-regresija i analize osetljivosti.
- Ručna procena rizika od pristrasnosti, bibliometrija, mape dokaza, saradnja i PRISMA dijagrami toka.
- Beleženje istraživačkog procesa i izvoz u DOCX, PPTX ili LaTeX, u zavisnosti od modula.

Dostupnost metode ne znači da je ona validirana za svaki dizajn istraživanja. Pogledajte [procenu analitičkih mogućnosti](REVIEW_DATA_ANALYSIS_ASSESSMENT.md#sr), proverite rezultate i citirajte izvorne metodološke radove.

### Kontrole toka rada i istraživački dokazi

- **Početna i podešavanja:** šest kartica — Pregled, Zadaci i tim, Skrining, Mašinsko učenje, Ekstrakcija i kodiranje podataka, Izgled i skladištenje. Početni redosled: Uvoz / Mašinsko učenje / Nasumično; nasumični redosled se čuva po korisniku, zadatku i skupu izvora.
- **Izvori i duplikati:** Izvori koriste puno ime uvezene datoteke (npr. `01-Wiley.ris`), bez zaključivanja iz RIS metapodataka. Tačna podudaranja se odvajaju od kandidata po naslovu/sličnosti za ljudsku proveru. Članak iz više izvora pojavljuje se jednom u ukupnom redu.
- **Postojeći rad:** posle sačuvanih odluka ili kodiranja, novi zapisi dodatnog uvoza čekaju vlasničku proveru duplikata i razlika metapodataka pre aktiviranja. Identiteti, odluke i dokazi se čuvaju.
- **Odluke i razlozi:** odvojene biblioteke čestih razloga za oba skrininga. Trenutno nisam siguran se podešava po fazi. Uključiti i Trenutno nisam siguran u početnoj fazi vode ka punom tekstu; neizvesnost u punom tekstu ostaje nerešena i ne omogućava kodiranje. Opcioni drugi, treći, četvrti i naredni krugovi obrađuju samo preostale neizvesne zapise; prekid vodi na puni tekst.
- **Ekstrakcija i kodiranje:** tekst, stranice i PNG/JPEG/WebP slike po dimenziji. Vlasnik može zahtevati dokaze; nepotpun nacrt se čuva, ali ne računa kao završen.
- **Opoziv i trag:** zaštićeni opoziv u tri faze; otvaranje povezanog članka iz traga radi ispravke trenutnih vrednosti. Direktno se opoziva samo poslednje dozvoljeno čuvanje, ne proizvoljan istorijski snimak.
- **Tim:** jedan panel sadrži članove/napredak, izvore po fazama, opcije skrininga i uvezene izvore. Oznake Vlasnik zadatka / Pregledač i koder / Arbitar i meniji članova prikazuju uloge. Vlasnik bira Svi / Izabrani / Pauzirano; članstvo i liste zadataka se automatski osvežavaju.
- **PRISMA:** samostalan ručni unos ili selektivan uvoz pokazatelja, ažurirani pregledi/drugi izvori i CSV porekla. Nepoznate vrednosti zahtevaju potvrdu; studije i izveštaji se broje odvojeno, jedan izveštaj po studiji je izričit izbor.

**Ažuriranje:** preuzmite [najnovije izdanje](https://github.com/RUCbookshelf/mubi-reviewflow/releases/latest) i prvo kopirajte ceo direktorijum podataka. Klijent 2.0.5 možda neće automatski najaviti 2.0.5.1; preuzmite ispravku direktno. Ispravljena provera podržava četiri dela verzije.

### Početak rada i čuvanje podataka

Preuzmite odgovarajući instalacioni paket sa stranice [Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases). Paketi uključuju Python i zavisnosti. Za pokretanje izvornog koda na Ubuntu ili macOS-u instalirajte Python 3.12 i Git; na Linuxu po potrebi instalirajte Cairo i `python3-venv`, a zatim pratite komande u odeljku [English](#english).

Podaci se podrazumevano čuvaju lokalno: Windows `%LOCALAPPDATA%\ReviewFlow`, macOS `~/Library/Application Support/ReviewFlow`, Linux `~/.local/share/ReviewFlow` ili u okviru `XDG_DATA_HOME`. PDF datoteke su u direktorijumu sa podacima. Posle migracije lokacije ponovo pokrenite aplikaciju i čuvajte zasebne rezervne kopije. U serverskom režimu podaci zadataka ostaju na serveru; za spoljašnji pristup koristite trajno skladište i HTTPS. Mrežne AI funkcije šalju odgovarajuće zahteve podešenom servisu. Za timsku instalaciju pogledajte [Ubuntu vodič za server](docs/deployment/UBUNTU_SERVER.md#sr).

### Paketi, podrška i licenca

Uputstva za Windows, macOS i Linux nalaze se u odeljku [Building packages](#building-packages). Pogledajte pomoć u bočnoj traci i [vodič za saradnju](docs/handoff/2026-09-30-cloud-collaboration.md#sr). Problem prijavite preko [Issues](https://github.com/RUCbookshelf/mubi-reviewflow/issues) uz sistem, verziju i korake za reprodukciju; uklonite lične podatke iz evidencije. Doprinosi su dobrodošli kroz Pull Request. MIT licenca: [`LICENSE`](LICENSE). Autor: **Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn).

## 한국어

ReviewFlow는 체계적 문헌고찰과 메타분석을 위한 문헌 가져오기, 선별, 코딩, 분석 및 연구 과정 기록을 한곳에 제공합니다. 개인은 로컬에서 오프라인으로 사용할 수 있고, 팀은 서버에 배포해 진행 상황을 공유하면서 검토자별 판단을 분리해 보관할 수 있습니다.

### 주요 기능

- RIS 가져오기, 중복 탐지 및 검토, 가져오기 기록과 원본 파일 추적.
- 제목/초록 선별 및 PDF 전문 선별, 기본 또는 사용자 지정 제외 사유.
- 머신러닝 읽기 순서 지원. 포함·보류·제외 결정은 사용자가 내립니다.
- 전문 근거를 포함한 연구 코딩, 효과크기, 이질성, 하위그룹, 메타회귀 및 민감도 분석.
- 비뚤림 위험 수동 평가, 서지계량, 근거 지도, 팀 협업 및 PRISMA 흐름도.
- 연구 과정 기록과 모듈별 DOCX, PPTX, LaTeX 내보내기.

기능이 구현되어 있다고 해서 모든 연구 설계에서 검증되었다는 뜻은 아닙니다. [분석 기능 평가](REVIEW_DATA_ANALYSIS_ASSESSMENT.md#ko)를 확인하고 결과를 검토하며 방법론 원문을 인용하세요.

### 워크플로 제어와 연구 근거

- **홈 및 설정:** 개요, 작업 및 팀, 선별, 머신러닝, 데이터 추출 및 코딩, 화면 및 저장소의 6개 탭으로 구성됩니다. 초기 선별 순서는 가져오기 / 머신러닝 / 무작위이며, 무작위 순서는 사용자·작업·출처 범위별로 유지됩니다.
- **출처와 중복:** 출처 이름은 가져온 파일명 전체(예: `01-Wiley.ris`)를 사용하며 RIS 데이터베이스 메타데이터에서 추론하지 않습니다. 정확한 일치와 사람이 검토할 제목·유사 후보를 구분하며, 여러 출처의 문헌도 전체 대기열에는 한 번만 나타납니다.
- **기존 작업 보호:** 판단이나 코딩을 저장한 뒤의 추가 가져오기는 신규 기록을 임시 보관합니다. 소유자가 중복·메타데이터 차이를 검토한 후 활성화하며, 식별자·판단·근거는 유지됩니다.
- **판단과 사유:** 초기·전문 선별의 재사용 제외 사유를 각각 저장합니다. 아직 확신 없음는 단계별로 설정합니다. 초기 포함·아직 확신 없음는 전문 선별로 진행하며, 전문의 아직 확신 없음는 미해결 상태로 코딩 대상이 되지 않습니다. 선택적으로 2·3·4차 이상의 초기 선별을 진행해 불확실한 문헌만 다시 검토하며, 중단하면 전문 선별로 진행합니다.
- **데이터 추출 및 코딩:** 차원별로 텍스트, 페이지, PNG/JPEG/WebP 이미지를 첨부합니다. 소유자는 근거를 필수로 설정할 수 있고, 불완전한 내용은 초안으로 저장하되 완료로 계산하지 않습니다.
- **실행 취소 및 기록:** 세 단계에 보호된 실행 취소를 제공합니다. 기록에서 연결 문헌을 열어 현재 값을 수정할 수 있지만 직접 취소는 조건을 충족하는 최근 저장만 가능합니다.
- **팀:** 구성원·진행률, 단계별 출처, 선별 옵션, 가져온 출처를 한 패널에 모읍니다. 작업 소유자 / 선별 및 코딩 담당자 / 판정자 표시와 구성원 메뉴로 역할을 구분합니다. 소유자는 전체 / 선택 / 일시 중지를 설정하며 구성원과 작업 목록은 자동 갱신됩니다.
- **PRISMA:** 독립 수동 입력과 선택적 작업 지표 가져오기, 갱신 고찰·기타 출처 분기, 지표 출처 CSV를 지원합니다. 미상 값은 확인해야 합니다. 연구 수와 보고서 수는 별개이며 보고서 1개당 연구 1개는 명시적으로 선택합니다.

**업데이트:** 전체 데이터 폴더를 백업한 뒤 [최신 릴리스](https://github.com/RUCbookshelf/mubi-reviewflow/releases/latest)를 받으세요. 2.0.5는 2.0.5.1을 자동 알리지 않을 수 있으므로 직접 내려받으세요. 수정된 검사기는 네 부분 버전을 지원합니다.

### 시작하기와 데이터 저장

[Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases)에서 운영체제에 맞는 설치 파일을 받으세요. 데스크톱 설치 파일에는 Python과 실행 종속성이 포함됩니다. Ubuntu 또는 macOS에서 소스 코드를 실행하려면 Python 3.12와 Git을 준비하세요. Linux에서는 필요에 따라 Cairo와 `python3-venv`를 설치하고 [English](#english) 안내의 명령을 실행하세요.

데이터는 기본적으로 로컬에 저장됩니다. Windows는 `%LOCALAPPDATA%\ReviewFlow`, macOS는 `~/Library/Application Support/ReviewFlow`, Linux는 `~/.local/share/ReviewFlow` 또는 `XDG_DATA_HOME`을 사용합니다. PDF는 데이터 폴더에 저장됩니다. 저장 위치를 옮긴 뒤에는 앱을 재시작하고 별도 백업을 보관하세요. 서버 모드의 작업 데이터는 서버에 저장됩니다. 외부 접속에는 영구 저장소와 HTTPS를 사용하세요. 온라인 AI 기능은 설정된 서비스로 관련 요청을 전송합니다. 팀 서버 배포는 [Ubuntu 서버 배포 안내](docs/deployment/UBUNTU_SERVER.md#ko)를 참고하세요.

### 빌드, 도움말 및 라이선스

Windows, macOS, Linux 빌드 방법은 [Building packages](#building-packages)를 참고하세요. 사이드바 도움말과 [협업 안내](docs/handoff/2026-09-30-cloud-collaboration.md#ko)도 확인할 수 있습니다. 문제를 보고할 때는 [Issues](https://github.com/RUCbookshelf/mubi-reviewflow/issues)에 운영체제, 버전, 재현 단계를 적고 로그에서 개인정보를 삭제하세요. Pull Request를 통한 기여를 환영합니다. MIT 라이선스: [`LICENSE`](LICENSE). 작성자: **Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn).

## Русский

ReviewFlow — рабочая среда для систематических обзоров и метаанализа: импорт литературы, отбор, кодирование, анализ и регистрация исследовательского процесса. Можно работать локально без интернета или развернуть приложение на сервере для команды. Участники видят общую литературу и прогресс, а решения каждого рецензента сохраняются отдельно.

Приложение использует FastAPI, интерфейс в браузере и вычислительные модули Python. Установочные пакеты содержат Python и зависимости; пользователям не нужно устанавливать Python или WSL. Актуальные версии и установочные пакеты доступны в [Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases/latest).

### Возможности

- Импорт RIS, обработка дубликатов, история импорта и сохранение исходных данных.
- Отбор по заголовку/аннотации и полному тексту PDF; стандартные и собственные причины исключения.
- Машинное обучение для определения порядка чтения; решения принимает пользователь.
- Кодирование характеристик исследований с опорой на полный текст.
- Размеры эффекта, модели фиксированного и случайных эффектов, гетерогенность, подгруппы, метарегрессия, анализ чувствительности и эффектов малых исследований.
- Модули диагностической точности, сетевого метаанализа, зависимости «доза–ответ», IPD, байесовских и многоуровневых моделей.
- Ручная оценка риска систематической ошибки с помощью RoB 2, ROBINS-I, QUADAS-2 и других инструментов.
- Библиометрический анализ и изучение карт доказательств.
- Журнал исследовательского процесса: включён по умолчанию, может быть отключён; записи отбора и кодирования, предварительный просмотр и многоязычный экспорт.
- Командный прогресс, экспорт автономных решений, объединение и разрешение разногласий.
- Диаграммы PRISMA и экспорт DOCX, PPTX, LaTeX в соответствующих модулях.

Наличие метода в программе не означает его полную проверку для всех дизайнов исследований. Условия применения и границы проверки описаны в [оценке аналитических возможностей](REVIEW_DATA_ANALYSIS_ASSESSMENT.md#ru). Проверяйте результаты и цитируйте первоисточники методов при публикации.

### Управление процессом и подтверждающие материалы

- **Главная и настройки:** шесть вкладок — Обзор, Задачи и команда, Отбор, Машинное обучение, Извлечение и кодирование данных, Оформление и хранение. Порядок первичного отбора: Импорт / Машинное обучение / Случайный; случайный порядок сохраняется для пользователя, задачи и набора источников.
- **Источники и дубликаты:** Название источника — полное имя импортированного файла (например, `01-Wiley.ris`), без вывода из метаданных RIS. Точные совпадения отделены от кандидатов по названию/сходству для ручной проверки. Запись из нескольких источников появляется один раз в общей очереди.
- **Сохранность работы:** после сохранения решений или кодирования новые записи дополнительных импортов ожидают проверки владельцем дубликатов и различий метаданных перед активацией. Идентификаторы, решения и материалы сохраняются.
- **Решения и причины:** отдельные библиотеки повторно используемых причин исключения для двух этапов. Пока не уверен включается отдельно для каждого этапа. Включить и Пока не уверен на первом этапе ведут к полному тексту; неопределённость при полном тексте остаётся нерешённой и не допускает запись к кодированию. Дополнительные второй, третий, четвёртый и последующие раунды пересматривают только неопределённые записи; при остановке они переходят к полному тексту.
- **Извлечение и кодирование:** текст, страницы и изображения PNG/JPEG/WebP для каждого поля. Владелец может требовать подтверждение отдельных полей; неполный черновик сохраняется, но не считается завершённым.
- **Отмена и журнал:** защищённая отмена в трёх этапах; открытие связанной записи из журнала для исправления текущих значений. Напрямую отменяется только последнее допустимое сохранение, а не произвольный старый снимок.
- **Команда:** общая панель участников/прогресса, источников по этапам, вариантов решений и импортированных источников. Значки Владелец задачи / Специалист по отбору и кодированию / Арбитр и меню участников показывают роли. Владелец выбирает Все / Выбранные / Приостановлено; состав и списки задач обновляются автоматически.
- **PRISMA:** независимый ручной ввод и выборочный импорт показателей задачи, обновлённые обзоры/другие источники и CSV происхождения показателей. Неизвестные значения требуют подтверждения; исследования и отчёты считаются отдельно, «один отчёт на исследование» — явный выбор.

**Обновление:** скачайте [последний выпуск](https://github.com/RUCbookshelf/mubi-reviewflow/releases/latest), предварительно скопировав весь каталог данных. Клиент 2.0.5 может не сообщить о 2.0.5.1 автоматически; скачайте исправление напрямую. Исправленная проверка поддерживает четыре компонента версии.

### Начало работы

Доступные пакеты смотрите в [Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases). Если подходящего пакета нет, соберите его по инструкции ниже. В Windows при запуске проверяются зависимости; перед восстановлением программа запрашивает согласие и по возможности использует локальные файлы. VBScript не требуется.

Для запуска из исходников в Ubuntu или macOS нужны Python 3.12 и Git. Для SVG в Linux нужна библиотека Cairo; в Ubuntu при необходимости установите `python3-venv` и `libcairo2`.

```bash
git clone https://github.com/RUCbookshelf/mubi-reviewflow.git
cd mubi-reviewflow
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r build/requirements-app.txt
python build/launcher.py
```

Запускатель обслуживает интерфейс и API и открывает браузер. Порт по умолчанию — `8613`; если он занят, выбирается другой свободный порт. Используйте открытый программой адрес. `run_pathB.bat` предназначен для разработки в Windows через WSL и не нужен установленному приложению.

### Хранение данных и совместная работа

При запуске из исходников данные находятся в `data/` внутри репозитория. Для установленного приложения:

- Windows: `%LOCALAPPDATA%\ReviewFlow`
- macOS: `~/Library/Application Support/ReviewFlow`
- Linux: `~/.local/share/ReviewFlow` или каталог `ReviewFlow` в `XDG_DATA_HOME`.

PDF хранятся в `tasks/<task-id>/pdfs` внутри каталога данных. Перенос доступен в Главная и настройки → Оформление и хранение → Расположение данных. **Изменение применяется после перезапуска приложения.** Исходные данные сохраняются до проверки нового каталога и запуска очистки. Удаление приложения сохраняет исследовательские данные; создавайте отдельные резервные копии.

При локальной работе данные остаются на компьютере. Онлайн-функции ИИ/API отправляют соответствующие запросы настроенному сервису. В серверном режиме данные задач постоянно хранятся на сервере. Участники задачи видят прогресс друг друга, но не могут изменять личные решения другого участника.

Для развёртывания команды следуйте [руководству по серверной установке Ubuntu](docs/deployment/UBUNTU_SERVER.md#ru). Для совместной работы требуется `REVIEWFLOW_MODE=server`; не запускайте командный сервис через настольный загрузчик. Аккаунты, задачи, исходные импорты, PDF и решения хранятся в каталоге данных сервера — резервируйте его целиком. Правила сотрудничества и офлайн-объединения описаны в [руководстве](docs/handoff/2026-09-30-cloud-collaboration.md#ru) и в справке боковой панели.

### Сборка установочных пакетов

Сборка выполняется на целевой ОС и требует интернета.

| Платформа | Действие | Результат |
| --- | --- | --- |
| Windows 10/11 x64 | Установить Inno Setup 6/7 или NSIS 3; запустить `build\build_windows.bat` | `build/Output/ReviewFlow-Setup-x64.exe` |
| macOS | `bash build/build_macos.sh` | `build/dist/ReviewFlow.dmg` |
| Linux x86_64 | `bash build/build_linux.sh` | `build/dist/ReviewFlow-x86_64.AppImage` |

Пакеты macOS для Intel и Apple Silicon собираются отдельно на соответствующей архитектуре. Проверяйте дату и версию выпуска: наличие скрипта сборки не означает наличие актуального опубликованного пакета. Подробнее: [сборка для Windows](build/WINDOWS_TRANSFER.md#ru).

### Помощь и участие в разработке

`8613` — номер порта, а не код ошибки. На странице входа есть диагностика подключения и восстановление. Если страница в Windows не открывается, запустите **检查与修复 ReviewFlow** из меню «Пуск» или `repair_reviewflow.bat` из каталога установки. Журнал: `%LOCALAPPDATA%\ReviewFlow\startup_diagnostic.log`. Если встроенный Python удалён, переустановите приложение.

Сообщайте о проблемах в [Issues](https://github.com/RUCbookshelf/mubi-reviewflow/issues): ОС, версия, шаги воспроизведения и журнал без личных данных. Приветствуются Pull Request. Для изменений статистических методов приложите источники, условия применения и воспроизводимые эталонные результаты. Не публикуйте пароли, базы исследований или полные тексты без разрешения.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

Основные каталоги: `custom_frontend/` — интерфейс, `custom_backend/` — API, `coscreen/` — исследовательская логика, `build/` — запуск и сборка, `tests/` — тесты, `docs/` — документация.

### Лицензия и контакты

Проект распространяется по лицензии MIT; см. корневой файл [`LICENSE`](LICENSE). Сторонние компоненты распространяются по собственным лицензиям. См. [уведомления](THIRD_PARTY_NOTICES.md) и [подготовку выпуска](OPEN_SOURCE_RELEASE.md#ru).

Автор: **Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn)

---

## Français

ReviewFlow est un espace de travail pour les revues systématiques et les méta-analyses : importation des références, sélection, codage, analyse et suivi du processus de recherche. Il peut être utilisé localement hors ligne ou déployé sur un serveur pour une équipe. Les membres partagent les références et consultent leur progression, tout en conservant leurs décisions individuelles.

L'application repose sur FastAPI, une interface dans le navigateur et des modules de calcul Python. Les paquets de bureau incluent Python et les dépendances nécessaires ; les utilisateurs n'ont pas besoin d'installer Python ou WSL. Les versions et paquets actuels sont disponibles dans [Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases/latest).

### Fonctionnalités

- Importation RIS, traitement des doublons, historique des imports et données originales.
- Sélection sur titre/résumé et texte intégral PDF, avec motifs d'exclusion prédéfinis ou personnalisés.
- Apprentissage automatique pour organiser la lecture ; les décisions restent celles de l'utilisateur.
- Codage des caractéristiques des études et enregistrement des éléments du texte intégral qui les justifient.
- Tailles d'effet, modèles à effets fixes et aléatoires, hétérogénéité, sous-groupes, méta-régression, analyses de sensibilité et effets liés aux petites études.
- Modules de précision diagnostique, méta-analyse en réseau, dose–réponse, IPD, modèles bayésiens et multiniveaux.
- Évaluation manuelle du risque de biais avec RoB 2, ROBINS-I, QUADAS-2 et d'autres outils.
- Analyse bibliométrique et exploration de cartes des données probantes.
- Traçabilité : activée par défaut et désactivable ; activités de sélection et de codage, aperçu et exports multilingues.
- Progression de l'équipe, export des décisions hors ligne, fusion et arbitrage.
- Diagrammes PRISMA et documents DOCX, PPTX ou LaTeX selon le module.

La présence d'une méthode ne signifie pas qu'elle a été entièrement validée pour tous les plans d'étude. Consultez l'[évaluation des analyses](REVIEW_DATA_ANALYSIS_ASSESSMENT.md#fr) pour les hypothèses et limites de validation. Vérifiez les résultats et citez les sources des méthodes lors de la publication.

### Contrôles du flux de travail et éléments justificatifs

- **Accueil et paramètres :** six onglets regroupent Vue d’ensemble, Tâches et équipe, Sélection, Apprentissage automatique, Extraction et codage des données, Apparence et stockage. L’ordre initial propose Importation / Apprentissage automatique / Aléatoire ; l’ordre aléatoire est conservé par utilisateur, tâche et périmètre de sources.
- **Sources et doublons :** Le nom de source est le nom complet du fichier importé (par exemple `01-Wiley.ris`), sans déduction à partir des métadonnées RIS. Les correspondances exactes sont distinguées des candidats par titre/similarité à vérifier manuellement ; une référence multisource apparaît une seule fois dans la file globale.
- **Travail existant :** après un enregistrement de décisions ou de codage, les nouveaux imports restent en attente. Le propriétaire vérifie doublons et différences de métadonnées avant activation ; identités, décisions et justificatifs sont conservés.
- **Décisions et motifs :** conserver des motifs d’exclusion réutilisables séparés pour les deux étapes. L’option Incertain pour l’instant est configurable par étape. Inclure et Incertain pour l’instant au premier tri conduisent au texte intégral ; Incertain pour l’instant au texte intégral reste non résolu et ne donne pas accès au codage. Les cycles facultatifs 2, 3, 4 et suivants réévaluent seulement les notices encore incertaines ; arrêter les cycles les dirige vers le texte intégral.
- **Extraction et codage :** notes, pages et images PNG/JPEG/WebP par dimension ; le propriétaire peut exiger des justificatifs. Un brouillon incomplet peut être enregistré sans être compté comme terminé.
- **Annulation et traçabilité :** annulation protégée dans les trois étapes ; ouvrir une référence liée au journal pour corriger sa valeur actuelle. Seul le dernier enregistrement admissible peut être annulé directement, sans restauration arbitraire d’anciens instantanés.
- **Équipe :** un panneau réunit membres/progression, sources autorisées par étape, options de sélection et sources importées. Les badges Responsable de la tâche / Responsable du dépistage et du codage / Arbitre et menus des membres précisent les rôles. Le propriétaire choisit Toutes / Sélectionnées / En pause ; listes et adhésions sont actualisées automatiquement.
- **PRISMA :** saisie manuelle indépendante ou import sélectif des indicateurs d’une tâche, branches de revue actualisée/autres sources et CSV de provenance. Les valeurs inconnues doivent être confirmées ; études et rapports sont distincts, avec un raccourci explicite « un rapport par étude ».

**Mise à niveau :** utilisez la [dernière version](https://github.com/RUCbookshelf/mubi-reviewflow/releases/latest) et sauvegardez tout le dossier de données. Le client 2.0.5 peut ne pas signaler automatiquement 2.0.5.1 ; téléchargez ce correctif directement. La vérification corrigée prend en charge quatre composantes.

### Prise en main

Consultez les paquets disponibles dans [Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases). En l'absence de paquet adapté, utilisez les instructions de compilation ci-dessous. Sous Windows, les dépendances sont vérifiées au démarrage ; une réparation nécessite votre accord et utilise de préférence les fichiers fournis hors ligne. VBScript n'est pas nécessaire.

Pour utiliser les sources sous Ubuntu ou macOS, installez Python 3.12 et Git. Le rendu SVG sous Linux nécessite Cairo ; sous Ubuntu, installez `python3-venv` et `libcairo2` si nécessaire.

```bash
git clone https://github.com/RUCbookshelf/mubi-reviewflow.git
cd mubi-reviewflow
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r build/requirements-app.txt
python build/launcher.py
```

Le lanceur sert l'interface et l'API et ouvre le navigateur. Le port local par défaut est `8613` ; un autre port libre est choisi si nécessaire. Utilisez l'adresse ouverte par le lanceur. `run_pathB.bat` est une entrée de développement Windows via WSL, inutile pour l'application installée.

### Stockage et collaboration

Depuis les sources, les données sont dans `data/` à la racine du dépôt. Pour l'application installée :

- Windows : `%LOCALAPPDATA%\ReviewFlow`
- macOS : `~/Library/Application Support/ReviewFlow`
- Linux : `~/.local/share/ReviewFlow`, ou `ReviewFlow` dans `XDG_DATA_HOME`.

Les PDF sont stockés dans `tasks/<task-id>/pdfs` sous le dossier de données. Dans Accueil et paramètres → Apparence et stockage → Emplacement des données, vous pouvez migrer ce dossier. **Redémarrez l'application pour appliquer le changement.** Les données originales restent disponibles jusqu'à la vérification du nouveau dossier et l'utilisation de l'option de nettoyage. La désinstallation conserve les données de recherche ; prévoyez des sauvegardes séparées.

En mode local, les données restent sur l'ordinateur. Les fonctions IA/API en ligne envoient les requêtes correspondantes au service configuré. En mode serveur, les données des tâches sont conservées sur le serveur. Les membres voient leur progression respective sans pouvoir modifier les décisions personnelles d'un autre membre.

Pour déployer l’équipe, suivez le [guide de déploiement serveur Ubuntu](docs/deployment/UBUNTU_SERVER.md#fr). La collaboration exige `REVIEWFLOW_MODE=server` ; n’utilisez pas le lanceur de bureau pour le service d’équipe. Les comptes, tâches, imports originaux, PDF et décisions sont conservés dans le répertoire serveur, à sauvegarder intégralement. Consultez aussi l’aide latérale et le [guide de collaboration](docs/handoff/2026-09-30-cloud-collaboration.md#fr) pour le travail hors ligne et la fusion.

### Création des paquets

Compilez sur le système cible avec une connexion Internet.

| Plateforme | Commande / préparation | Résultat |
| --- | --- | --- |
| Windows 10/11 x64 | Installer Inno Setup 6/7 ou NSIS 3 ; lancer `build\build_windows.bat` | `build/Output/ReviewFlow-Setup-x64.exe` |
| macOS | `bash build/build_macos.sh` | `build/dist/ReviewFlow.dmg` |
| Linux x86_64 | `bash build/build_linux.sh` | `build/dist/ReviewFlow-x86_64.AppImage` |

Les paquets macOS Intel et Apple Silicon doivent être créés séparément sur l'architecture correspondante. Vérifiez la date et la version : un script de compilation ne garantit pas l'existence d'un paquet publié à jour. Voir le [guide Windows](build/WINDOWS_TRANSFER.md#fr).

### Dépannage et contributions

`8613` est un port, pas un code d'erreur. Utilisez le diagnostic et la réparation sur la page de connexion. Si la page Windows ne s'ouvre pas, lancez **检查与修复 ReviewFlow** depuis le menu Démarrer ou `repair_reviewflow.bat` depuis le dossier d'installation. Journal : `%LOCALAPPDATA%\ReviewFlow\startup_diagnostic.log`. Réinstallez l'application si Python intégré a été supprimé.

Signalez les problèmes dans [Issues](https://github.com/RUCbookshelf/mubi-reviewflow/issues), avec le système, la version, les étapes de reproduction et des journaux sans données personnelles. Les Pull Request sont bienvenues. Toute modification statistique doit préciser les sources, les conditions d'application et des résultats de référence reproductibles. N'ajoutez pas d'identifiants, de bases de recherche ou de textes intégraux non autorisés.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

Organisation : `custom_frontend/` pour l'interface, `custom_backend/` pour l'API, `coscreen/` pour les traitements scientifiques, `build/` pour le lancement et les paquets, `tests/` et `docs/`.

### Licence et contact

Ce projet est distribué sous licence MIT ; voir le fichier [`LICENSE`](LICENSE) à la racine. Les composants tiers conservent leurs licences respectives. Voir les [mentions tierces](THIRD_PARTY_NOTICES.md) et la [préparation de publication](OPEN_SOURCE_RELEASE.md#fr).

Auteur : **Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn)

## Mobile interface · 2.1.0

Mobile presentation now follows the approved concept: compact navigation, separate language and theme controls, animated search, sheets and disclosures. Original desktop controls, research logic, permissions and data remain shared. Trace search accepts a partial article title or identifier across matching history, with date/stage/action filters and reviewer isolation. Redundant headings were removed; disclosure indicators now use SVG.

移动端按已确认概念重新实现：紧凑导航、独立语言与主题按钮、搜索框动画、底部面板和详情展开；复用电脑端控件、研究逻辑、权限和数据。 轨迹支持按文献标题片段或标识检索符合筛选条件的历史记录，保留日期、阶段、操作筛选和个人记录隔离。删除重复标题，展开图标统一使用 SVG。

Responsive layouts are available at widths up to 760 CSS pixels. Desktop layouts remain unchanged. Language and theme controls stay separate; all ten interface languages and the desktop light/dark palettes are supported. Phone use requires access to a configured server; this update does not add remote pairing or tunneling. [Mobile interface details](docs/MOBILE_INTERFACE.md).

移动端适配支持十种界面语言，语言与浅色/深色按钮独立，沿用电脑端配色，电脑布局保持不变。手机使用需连接已配置的服务器，本次不新增远程控制或内网穿透。

### Español · 2.1.0

La interfaz móvil sigue el concepto aprobado: navegación compacta, idioma y tema separados, búsqueda animada, paneles y detalles desplegables. Comparte controles, lógica de investigación, permisos y datos con el escritorio. Trace busca por parte del título o identificador, manteniendo filtros de fecha, etapa y acción y el aislamiento entre revisores. Se eliminaron títulos redundantes y los indicadores usan SVG.

- Interfaz móvil en todas las páginas; diseño de escritorio conservado, diez idiomas y controles separados de idioma y tema.
- Lectura, formularios, paneles y controles táctiles mejorados. Corrección de la carga de colas vacías.
- Los CSV de ambas fases de cribado añaden DOI, Journal y Author(s). Sin nueva migración ni acceso remoto automático. Haga una copia de seguridad antes de actualizar.

### 日本語 · 2.1.0

承認済みのモバイル案に合わせ、コンパクトなナビゲーション、独立した言語・テーマ操作、検索アニメーション、パネルと詳細展開を実装。デスクトップと同じ操作、研究処理、権限、データを使用します。 Trace は文献タイトルの一部または識別子で履歴を検索し、日付・段階・操作の条件とレビュアー間の記録分離を維持します。重複見出しを削除し、展開アイコンを SVG に統一しました。

- 全ページのモバイル表示を改善。デスクトップ表示、10言語、独立した言語・テーマ操作を維持。
- 文献閲覧、フォーム、タッチ操作を改善し、空のキューでの読み込みエラーを修正。
- 両スクリーニング段階のCSVにDOI、Journal、Author(s)を追加。新しいデータ移行や自動リモートアクセスはありません。更新前にバックアップしてください。

### Français · 2.1.0

La présentation mobile suit le concept approuvé : navigation compacte, langue et thème séparés, recherche animée, panneaux et détails dépliables. Les commandes, la logique de recherche, les permissions et les données restent communes au bureau. La recherche dans Trace accepte une partie du titre ou de l’identifiant, avec les filtres de date, d’étape et d’action et l’isolation des évaluateurs. Les titres redondants sont supprimés et les indicateurs utilisent SVG.

- Présentation mobile sur toutes les pages, avec le bureau préservé, dix langues et des commandes distinctes pour la langue et le thème.
- Lecture, formulaires et commandes tactiles améliorés; correction du chargement des files vides.
- Ajout de DOI, Journal et Author(s) aux deux exports CSV de sélection. Aucune nouvelle migration ni connexion distante automatique. Sauvegardez avant la mise à jour.

### Русский · 2.1.0

Мобильный интерфейс следует утверждённому макету: компактная навигация, отдельные кнопки языка и темы, анимированный поиск, панели и раскрытие деталей. Элементы управления, исследовательская логика, права и данные общие с настольной версией. Поиск в Trace поддерживает часть названия или идентификатора статьи с фильтрами даты, этапа и действия и изоляцией записей рецензентов. Удалены повторяющиеся заголовки; индикаторы раскрытия используют SVG.

- Мобильный интерфейс для всех страниц; сохранены настольный интерфейс, десять языков и отдельные кнопки языка и темы.
- Улучшены чтение, формы и сенсорные элементы; исправлена загрузка пустой очереди.
- В CSV обоих этапов отбора добавлены DOI, Journal и Author(s). Новых миграций и автоматического удалённого доступа нет. Перед обновлением создайте резервную копию.

### Português · 2.1.0

A interface móvel segue o conceito aprovado: navegação compacta, idioma e tema separados, busca animada, painéis e detalhes expansíveis. Compartilha controles, lógica de pesquisa, permissões e dados com o computador. A busca no Trace aceita parte do título ou identificador, mantendo filtros de data, etapa e ação e o isolamento entre revisores. Títulos redundantes foram removidos e indicadores usam SVG.

- Interface móvel em todas as páginas, mantendo o layout de computador, dez idiomas e controles separados de idioma e tema.
- Melhorias na leitura, nos formulários e nos controles de toque; correção do carregamento de filas vazias.
- Os CSV das duas etapas de triagem incluem DOI, Journal e Author(s). Sem nova migração ou acesso remoto automático. Faça backup antes de atualizar.

### Deutsch · 2.1.0

Die mobile Oberfläche folgt dem freigegebenen Entwurf: kompakte Navigation, getrennte Sprach- und Themensteuerung, animierte Suche, Panels und aufklappbare Details. Bedienelemente, Forschungslogik, Rechte und Daten werden mit dem Desktop geteilt. Die Trace-Suche unterstützt Teile des Artikeltitels oder der Kennung mit Datums-, Phasen- und Aktionsfiltern sowie getrennten Gutachterdaten. Doppelte Überschriften wurden entfernt; Aufklappindikatoren verwenden SVG.

- Mobile Darstellung für alle Seiten; Desktoplayout, zehn Sprachen und getrennte Sprach- und Themensteuerung bleiben erhalten.
- Verbesserte Leseflächen, Formulare und Touchbedienung; Fehler beim Laden leerer Warteschlangen behoben.
- Beide Screening-CSV-Exporte enthalten zusätzlich DOI, Journal und Author(s). Keine neue Migration und kein automatischer Fernzugriff. Vor dem Update eine Sicherung erstellen.

### Српски · 2.1.0

Мобилни интерфејс прати одобрени концепт: компактна навигација, одвојене контроле језика и теме, анимирана претрага, панели и прошириви детаљи. Контроле, истраживачка логика, дозволе и подаци заједнички су са приказом на рачунару. Trace претрага прихвата део наслова или идентификатора, уз филтере датума, фазе и операције и изолацију записа рецензената. Уклоњени су поновљени наслови; индикатори користе SVG.

- Мобилни приказ свих страница; задржани су приказ на рачунару, десет језика и одвојене контроле језика и теме.
- Побољшани су читање, обрасци и додирне контроле; исправљено је учитавање празног реда.
- CSV извози обе фазе селекције додају DOI, Journal и Author(s). Нема нове миграције ни аутоматског удаљеног приступа. Направите резервну копију пре ажурирања.

### 한국어 · 2.1.0

승인된 모바일 콘셉트에 맞춰 간결한 탐색, 별도 언어 및 테마 버튼, 검색 애니메이션, 패널과 상세 펼치기를 구현했습니다. 데스크톱과 같은 조작, 연구 로직, 권한 및 데이터를 사용합니다. Trace는 문헌 제목 일부나 식별자로 이력을 검색하며 날짜·단계·작업 필터와 검토자별 기록 분리를 유지합니다. 중복 제목을 제거하고 펼치기 아이콘을 SVG로 통일했습니다.

- 모든 페이지의 모바일 화면을 개선했습니다. 데스크톱 레이아웃, 10개 언어, 별도의 언어 및 테마 버튼을 유지합니다.
- 문헌 읽기, 입력 양식, 터치 조작을 개선하고 빈 대기열의 로딩 오류를 수정했습니다.
- 두 선별 단계의 CSV에 DOI, Journal, Author(s) 열을 추가했습니다. 새로운 데이터베이스 마이그레이션이나 자동 원격 접속은 없습니다. 업데이트 전에 전체 데이터를 백업하세요.

Screening CSV exports also include DOI, Journal, Author(s), and **Imported source file(s)**. Multiple activated source filenames are retained in one cell; unknown legacy provenance is left blank.
