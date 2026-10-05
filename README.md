<div align="center">
  <h1>木笔 ReviewFlow</h1>
  <p><img src="custom_frontend/assets/icon_writelab_budnib_20260824.svg" alt="木笔 WriteLab 标识" width="96"></p>
  <p><strong>系统综述与 Meta 分析工作台 · Systematic review and meta-analysis workspace</strong></p>
  <p><a href="#中文">中文</a> · <a href="#english">English</a> · <a href="#français">Français</a> · <a href="#русский">Русский</a> · <a href="#español">Español</a> · <a href="#日本語">日本語</a> · <a href="#português">Português</a> · <a href="#deutsch">Deutsch</a> · <a href="#српски">Српски</a> · <a href="#한국어">한국어</a></p>
  <p><a href="https://github.com/RUCbookshelf/mubi-reviewflow">GitHub</a> · <a href="https://github.com/RUCbookshelf/mubi-reviewflow/releases">Releases</a> · <a href="https://github.com/RUCbookshelf/mubi-reviewflow/issues">Issues</a></p>
</div>

## 中文

木笔 ReviewFlow 将文献导入、筛选、编码、数据分析与研究过程记录放在同一个工作台中，避免来回调动软件。
个人可以在本机离线使用；团队可以部署到服务器，共享任务文献和进度，同时独立保存各自的筛选决定。

应用采用 FastAPI 后端、浏览器前端和 Python 计算模块。桌面安装包包含 Python 与运行依赖，通过浏览器打开界面；使用者无需自行配置 Python 或 WSL。您可以直接前往Releases下载Linux、Windows、macOS对应安装包进行体验。

### 主要功能

| 功能 | 内容 |
| --- | --- |
| 文献导入 | RIS 导入、重复记录处理、导入历史与原始数据追溯 |
| 文献筛选 | 标题/摘要初筛、PDF 全文复筛、常用与自定义排除理由 |
| AI 排序 | 辅助安排阅读顺序；纳入、待定和排除由用户决定 |
| 文献编码 | 提取研究信息、记录编码内容与全文依据 |
| 数据分析 | 效应量计算、固定与随机效应合并、异质性、亚组、Meta 回归、敏感性与小研究效应分析 |
| 扩展分析 | 诊断准确性、网络 Meta 分析、剂量反应、IPD、贝叶斯与多层模型等模块；使用前请确认各模块的输入与适用条件 |
| 偏倚风险 | 记录 RoB 2、ROBINS-I、QUADAS-2 等工具的人工判断 |
| 文献计量 | 文献计量分析与证据图探索 |
| 研究轨迹 | 默认开启，可关闭；记录初筛、复筛和编码操作，预览并导出过程材料与多语言图表 |
| 团队协作 | 受邀成员共享任务目录、查看团队进度；支持离线决策导出、合并与仲裁 |
| 研究材料导出 | PRISMA 流程图，以及对应模块提供的 DOCX、PPTX、LaTeX 等材料 |

分析模块的实现不代表所有研究设计均已充分验证。方法前提、验证范围和限制见[分析能力评估](REVIEW_DATA_ANALYSIS_ASSESSMENT.md)。发表时请核对方法来源并引用相应文献；软件结果仍需研究者检查。

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
python -m pip install -r requirements.txt
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

上传的 PDF 位于数据目录中的 `tasks/<任务标识>/pdfs`，占用磁盘空间。安装版可在「首页 & 设置 → 数据存放位置」迁移到其他目录，**重启应用后生效**。迁移完成前保留原数据；确认新目录正常后再使用清理功能。卸载保留个人研究数据。请另行备份重要材料。

本机使用的数据保存在本机。启用在线 AI/API 时，相应请求会发往配置的服务。服务器模式将任务数据持久保存到服务器；同一服务器的任务成员能查看彼此进度，但不能修改他人的个人筛选决定。

完成上述依赖安装后，服务器部署可使用：

```bash
COBOOKSHELF_DATA_DIR=/srv/reviewflow-data \
REVIEWFLOW_HOST=127.0.0.1 bash build/start_server.sh
```

请将示例目录替换为有写入权限的持久目录，外部访问使用 HTTPS 反向代理。协作和离线合并步骤见应用左下角「帮助」及[协作说明](docs/handoff/2026-09-30-cloud-collaboration.md)。

### 构建安装包

请在对应系统上构建；构建需要联网下载运行时和依赖。

| 平台 | 操作 | 输出 |
| --- | --- | --- |
| Windows 10/11 x64 | 安装 Inno Setup 6/7 或 NSIS 3，运行 `build\build_windows.bat` | `build/Output/ReviewFlow-Setup-x64.exe` |
| macOS | `bash build/build_macos.sh` | `build/dist/ReviewFlow.dmg` |
| Linux x86_64 | `bash build/build_linux.sh` | `build/ReviewFlow-x86_64.AppImage` |

macOS 的 Intel 与 Apple Silicon 包应分别在对应架构构建。构建脚本存在不代表最新源码已有对应发布包；请检查发布时间与版本。Windows 详细步骤见[构建与验收说明](build/WINDOWS_TRANSFER.md)。

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

本项目采用 MIT 许可证，详见根目录 [`LICENSE`](LICENSE)。第三方组件遵循各自许可证，见[第三方许可](THIRD_PARTY_NOTICES.md)与[发布准备说明](OPEN_SOURCE_RELEASE.md)。

作者：**Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn)

---

## English

ReviewFlow is a workspace for systematic reviews and meta-analysis, covering reference import, screening, coding, analysis and records of the research process. Individuals can work locally offline. Teams can deploy it on a server to share references and progress while keeping each reviewer's decisions separate.

The application uses FastAPI, a browser interface and Python calculation modules. Desktop packages bundle Python and runtime dependencies; users do not need to install Python or WSL. The [Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases) page lists the current version and available desktop installers.

### Features

- **Reference import:** RIS files, duplicate handling, import history and original import data.
- **Screening:** title/abstract and PDF full-text screening, with standard or custom exclusion reasons.
- **AI ranking:** assistance with reading order; inclusion, uncertainty and exclusion remain user decisions.
- **Coding:** study information extraction and supporting full-text evidence.
- **Analysis:** effect sizes, fixed and random effects, heterogeneity, subgroups, meta-regression, sensitivity and small-study effects.
- **Extended methods:** diagnostic accuracy, network meta-analysis, dose–response, IPD, Bayesian and multilevel modules, each with its own input requirements.
- **Risk of bias:** manual assessments using tools such as RoB 2, ROBINS-I and QUADAS-2.
- **Bibliometrics:** bibliometric analysis and evidence map exploration.
- **Research trace:** enabled by default and optional; screening and coding activity records, previews and multilingual exports.
- **Collaboration:** shared task references and team progress, plus offline decision export, merging and adjudication.
- **Exports:** PRISMA diagrams and module-specific DOCX, PPTX and LaTeX materials.

Implementation does not establish validation for every study design. Consult the [analysis assessment](REVIEW_DATA_ANALYSIS_ASSESSMENT.md) for assumptions and validation limits. Check results and cite the original methods when publishing.

### Getting started

Check [Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases) for available packages. If no suitable package is available, build one using the instructions below. Windows checks dependencies at startup and asks before repairing missing or damaged dependencies, using bundled offline files where available. VBScript is not required.

For source use on Ubuntu or macOS, install Python 3.12 and Git. Linux SVG rendering requires Cairo; on Ubuntu install `python3-venv` and `libcairo2` as needed.

```bash
git clone https://github.com/RUCbookshelf/mubi-reviewflow.git
cd mubi-reviewflow
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python build/launcher.py
```

The launcher serves the UI and API together and opens a browser. The default local port is `8613`; another available port is used if needed. Use the address opened by the launcher. `run_pathB.bat` is a Windows/WSL development entry, not an installed application requirement.

### Storage and collaboration

Source runs use the repository's `data/` folder. Installed applications default to:

- Windows: `%LOCALAPPDATA%\ReviewFlow`
- macOS: `~/Library/Application Support/ReviewFlow`
- Linux: `~/.local/share/ReviewFlow`, or `ReviewFlow` under `XDG_DATA_HOME`.

Uploaded PDFs are stored under `tasks/<task-id>/pdfs` inside the data folder. In installed applications, use **Home & Settings → Data location** to migrate data. **Restart the application to apply the change.** The original data is retained until you confirm the new location and use the cleanup option. Uninstalling retains personal research data; keep separate backups.

Local mode stores research data locally. Online AI/API features send relevant requests to the configured service. Server mode persists task data on the server. Task members can see each other's progress without changing another reviewer's personal decisions.

After installing dependencies, start a team server with:

```bash
COBOOKSHELF_DATA_DIR=/srv/reviewflow-data \
REVIEWFLOW_HOST=127.0.0.1 bash build/start_server.sh
```

Use a writable persistent directory and an HTTPS reverse proxy for external access. See **Help** in the sidebar and the [collaboration guide](docs/handoff/2026-09-30-cloud-collaboration.md).

### Building packages

Build on the target operating system with an internet connection.

| Platform | Command / preparation | Output |
| --- | --- | --- |
| Windows 10/11 x64 | Install Inno Setup 6/7 or NSIS 3; run `build\build_windows.bat` | `build/Output/ReviewFlow-Setup-x64.exe` |
| macOS | `bash build/build_macos.sh` | `build/dist/ReviewFlow.dmg` |
| Linux x86_64 | `bash build/build_linux.sh` | `build/ReviewFlow-x86_64.AppImage` |

Build Intel and Apple Silicon macOS packages separately on the corresponding architecture. Check release dates and versions: a build script does not imply that an up-to-date binary is published. See the [Windows build guide](build/WINDOWS_TRANSFER.md).

### Troubleshooting and contributing

`8613` is a port, not an error code. Use **Connection diagnostics and repair** on the login page. If the Windows page cannot load, use **检查与修复 ReviewFlow** in the Start menu or `repair_reviewflow.bat` in the installation folder. Dependency logs are in `%LOCALAPPDATA%\ReviewFlow\startup_diagnostic.log`. Reinstall the application if bundled Python is missing.

Report reproducible problems through [Issues](https://github.com/RUCbookshelf/mubi-reviewflow/issues), including your OS, version and sanitized logs. Pull requests are welcome. Statistical changes should include method sources, assumptions and reproducible reference results. Do not commit credentials, research databases or unauthorized full texts.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

Code is organized into `custom_frontend/` (UI), `custom_backend/` (API), `coscreen/` (research logic), `build/` (launchers and packaging), `tests/` and `docs/`.

### License and contact

This project is licensed under the MIT License; see the root [`LICENSE`](LICENSE). Third-party components retain their respective licenses. See [third-party notices](THIRD_PARTY_NOTICES.md) and [release preparation](OPEN_SOURCE_RELEASE.md).

Author: **Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn)

---


## Español

ReviewFlow reúne la importación de referencias, el cribado, la codificación, el análisis y el registro del proceso de revisión sistemática y metaanálisis. Puede utilizarse sin conexión en un ordenador personal o desplegarse en un servidor para colaborar, manteniendo separadas las decisiones de cada revisor.

### Funciones

- Importación RIS, detección y gestión de duplicados, historial y trazabilidad de los archivos originales.
- Cribado de título/resumen y de texto completo en PDF; motivos de exclusión estándar o personalizados.
- Ordenación asistida por IA; las decisiones de inclusión, duda y exclusión siguen correspondiendo al usuario.
- Codificación de estudios con evidencia del texto completo; análisis de tamaños del efecto, heterogeneidad, subgrupos, metarregresión y sensibilidad.
- Evaluación manual del riesgo de sesgo, bibliometría, mapas de evidencia, colaboración y diagramas PRISMA.
- Registro de investigación opcional y exportación de materiales, según el módulo, a DOCX, PPTX o LaTeX.

La disponibilidad de un método no significa que esté validado para todos los diseños. Consulte la [evaluación de análisis](REVIEW_DATA_ANALYSIS_ASSESSMENT.md), compruebe los resultados y cite las fuentes metodológicas.

### Inicio y datos

Descargue el instalador disponible para su sistema desde [Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases). Los instaladores de escritorio incluyen Python y sus dependencias. Para ejecutar el código fuente en Ubuntu o macOS, prepare Python 3.12 y Git; en Linux instale Cairo y `python3-venv` si son necesarios, y siga los comandos de la sección [English](#english).

Los datos se guardan localmente de forma predeterminada: Windows `%LOCALAPPDATA%\ReviewFlow`, macOS `~/Library/Application Support/ReviewFlow` y Linux `~/.local/share/ReviewFlow` o `XDG_DATA_HOME`. Los PDF ocupan espacio dentro de la carpeta de datos. La ubicación puede migrarse desde Inicio y configuración; reinicie la aplicación para aplicar el cambio y mantenga copias de seguridad. El modo servidor conserva los datos en el servidor; use almacenamiento persistente y HTTPS para el acceso externo. Las funciones de IA en línea envían las solicitudes pertinentes al servicio configurado.

### Compilación, ayuda y licencia

Las instrucciones para Windows, macOS y Linux están en [Construir paquetes](#building-packages). Consulte la ayuda de la barra lateral y la [guía de colaboración](docs/handoff/2026-09-30-cloud-collaboration.md). Para informar un problema, abra un [Issue](https://github.com/RUCbookshelf/mubi-reviewflow/issues) con el sistema, la versión y los pasos para reproducirlo; quite los datos personales de los registros. Las contribuciones pueden enviarse mediante Pull Request. Licencia MIT: [`LICENSE`](LICENSE). Autor: **Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn).

## 日本語

ReviewFlowは、文献の取り込み、スクリーニング、コーディング、分析、研究過程の記録をまとめたシステマティックレビュー／メタ分析用ワークスペースです。個人はローカルでオフライン利用でき、チームはサーバーに配置して進捗を共有しながら、各レビュアーの判断を分けて保存できます。

### 主な機能

- RIS取り込み、重複候補の確認、インポート履歴、元ファイルの追跡。
- タイトル／抄録スクリーニング、PDF全文スクリーニング、標準またはカスタムの除外理由。
- AIによる閲覧順の補助。採用・保留・除外の判断はユーザーが行います。
- 全文根拠を伴う研究情報のコーディング、効果量、異質性、サブグループ、メタ回帰、感度分析。
- バイアスリスクの評価、文献計量、エビデンスマップ、チーム共同作業、PRISMAフロー図。
- 研究履歴の記録と、各モジュールに応じたDOCX／PPTX／LaTeX出力。

機能が実装されていることは、すべての研究デザインで妥当性が確認されたことを意味しません。[分析機能の評価](REVIEW_DATA_ANALYSIS_ASSESSMENT.md)を確認し、結果を検証して方法論の原典を引用してください。

### はじめに・データ保存

対応するデスクトップ版を[Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases)から入手してください。インストーラーにはPythonと実行依存関係が含まれます。Ubuntu／macOSでソースから実行する場合はPython 3.12とGitを用意し、Linuxでは必要に応じてCairoと`python3-venv`をインストールして、[English](#english)の手順を実行してください。

データは既定でローカルに保存されます。Windowsは`%LOCALAPPDATA%\ReviewFlow`、macOSは`~/Library/Application Support/ReviewFlow`、Linuxは`~/.local/share/ReviewFlow`または`XDG_DATA_HOME`配下です。PDFはデータフォルダー内に保存されます。保存先を移行した後はアプリを再起動し、別途バックアップを保管してください。サーバーモードではデータはサーバーに保存されます。外部公開には永続ストレージとHTTPSを使用してください。オンラインAI機能では設定したサービスに該当するリクエストが送信されます。

### ビルド・サポート・ライセンス

Windows、macOS、Linuxのビルド方法は[パッケージのビルド](#building-packages)を参照してください。サイドバーのヘルプと[共同作業ガイド](docs/handoff/2026-09-30-cloud-collaboration.md)も利用できます。不具合は、OS、バージョン、再現手順を添えて[Issue](https://github.com/RUCbookshelf/mubi-reviewflow/issues)へ報告してください。ログから個人情報を削除してください。ライセンスはMIT（[`LICENSE`](LICENSE)）。作者：**Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn)。

## Português

O ReviewFlow reúne importação de referências, triagem, codificação, análise e registro do processo de revisão sistemática e meta-análise. Pode ser usado offline localmente ou implantado em um servidor para colaboração, mantendo separadas as decisões de cada revisor.

### Recursos

- Importação RIS, identificação e revisão de duplicatas, histórico e rastreabilidade dos arquivos originais.
- Triagem de título/resumo e de texto completo em PDF, com motivos de exclusão padrão ou personalizados.
- Ordenação de leitura assistida por IA; as decisões de incluir, deixar em dúvida ou excluir pertencem ao usuário.
- Codificação de estudos com evidências do texto completo; tamanhos de efeito, heterogeneidade, subgrupos, meta-regressão e análises de sensibilidade.
- Avaliação manual de risco de viés, bibliometria, mapas de evidências, colaboração e diagramas PRISMA.
- Registro da pesquisa e exportação, conforme o módulo, para DOCX, PPTX ou LaTeX.

A implementação de um método não comprova sua validação para todos os delineamentos. Consulte a [avaliação dos recursos de análise](REVIEW_DATA_ANALYSIS_ASSESSMENT.md), confira os resultados e cite as fontes metodológicas.

### Como começar e onde ficam os dados

Baixe o instalador disponível para seu sistema em [Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases). Os instaladores incluem Python e as dependências. Para executar o código-fonte no Ubuntu ou macOS, instale Python 3.12 e Git; no Linux, instale Cairo e `python3-venv` se necessário e siga os comandos na seção [English](#english).

Por padrão, os dados ficam no computador: Windows `%LOCALAPPDATA%\ReviewFlow`, macOS `~/Library/Application Support/ReviewFlow` e Linux `~/.local/share/ReviewFlow` ou em `XDG_DATA_HOME`. PDFs ficam na pasta de dados. A migração do local de armazenamento é feita em Início e configurações e requer reiniciar o aplicativo. Mantenha cópias de segurança. No modo servidor, os dados ficam no servidor; use armazenamento persistente e HTTPS para acesso externo. Recursos de IA online enviam as solicitações pertinentes ao serviço configurado.

### Compilação, suporte e licença

As instruções para Windows, macOS e Linux estão em [Build packages](#building-packages). Consulte a ajuda na barra lateral e o [guia de colaboração](docs/handoff/2026-09-30-cloud-collaboration.md). Para relatar um problema, abra um [Issue](https://github.com/RUCbookshelf/mubi-reviewflow/issues) com sistema, versão e etapas para reproduzi-lo; remova dados pessoais dos registros. Contribuições são bem-vindas por Pull Request. Licença MIT: [`LICENSE`](LICENSE). Autor: **Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn).

## Deutsch

ReviewFlow bündelt Literaturimport, Screening, Kodierung, Analyse und die Dokumentation des Forschungsprozesses für systematische Reviews und Metaanalysen. Einzelpersonen können lokal offline arbeiten. Teams können die Anwendung auf einem Server betreiben und Fortschritte teilen, während die Entscheidungen der einzelnen Reviewer getrennt bleiben.

### Funktionen

- RIS-Import, Erkennung und Prüfung von Duplikaten, Importverlauf und Rückverfolgbarkeit der Originaldateien.
- Titel-/Abstract-Screening und PDF-Volltextscreening mit standardisierten oder eigenen Ausschlussgründen.
- KI-gestützte Lesereihenfolge; über Einschluss, Unsicherheit und Ausschluss entscheidet weiterhin der Nutzer.
- Studienkodierung mit Volltextbelegen; Effektgrößen, Heterogenität, Subgruppen, Metaregression und Sensitivitätsanalysen.
- Manuelle Bewertung des Verzerrungsrisikos, Bibliometrie, Evidenzkarten, Zusammenarbeit und PRISMA-Flussdiagramme.
- Forschungsprotokoll und je nach Modul Export als DOCX, PPTX oder LaTeX.

Eine implementierte Methode ist nicht automatisch für jedes Studiendesign validiert. Lesen Sie die [Bewertung der Analysefunktionen](REVIEW_DATA_ANALYSIS_ASSESSMENT.md), prüfen Sie die Ergebnisse und zitieren Sie die methodischen Originalquellen.

### Einstieg und Datenspeicherung

Laden Sie ein verfügbares Installationspaket für Ihr System unter [Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases) herunter. Desktop-Pakete enthalten Python und Laufzeitabhängigkeiten. Für die Ausführung aus dem Quellcode unter Ubuntu oder macOS benötigen Sie Python 3.12 und Git; unter Linux bei Bedarf Cairo und `python3-venv`. Die Befehle stehen im Abschnitt [English](#english).

Standardmäßig werden Daten lokal gespeichert: Windows `%LOCALAPPDATA%\ReviewFlow`, macOS `~/Library/Application Support/ReviewFlow` und Linux `~/.local/share/ReviewFlow` oder unter `XDG_DATA_HOME`. PDFs liegen im Datenordner. Nach einer Änderung des Speicherorts muss die Anwendung neu gestartet werden. Erstellen Sie zusätzliche Sicherungskopien. Im Servermodus liegen Aufgabendaten auf dem Server; für externen Zugriff sind dauerhafter Speicher und HTTPS erforderlich. Online-KI-Funktionen senden die betreffenden Anfragen an den konfigurierten Dienst.

### Pakete, Hilfe und Lizenz

Die Bauanleitungen für Windows, macOS und Linux stehen unter [Pakete erstellen](#building-packages). Nutzen Sie die Hilfe in der Seitenleiste und den [Leitfaden zur Zusammenarbeit](docs/handoff/2026-09-30-cloud-collaboration.md). Melden Sie Probleme unter [Issues](https://github.com/RUCbookshelf/mubi-reviewflow/issues) mit Betriebssystem, Version und Reproduktionsschritten; entfernen Sie persönliche Daten aus Protokollen. Beiträge sind als Pull Request willkommen. MIT-Lizenz: [`LICENSE`](LICENSE). Autor: **Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn).

## Српски

ReviewFlow objedinjuje uvoz literature, skrining, kodiranje, analizu i beleženje istraživačkog procesa za sistematske preglede i meta-analize. Može se koristiti lokalno bez interneta ili postaviti na server radi timskog rada, uz odvojeno čuvanje odluka svakog recenzenta.

### Funkcije

- RIS uvoz, otkrivanje i provera duplikata, istorija uvoza i praćenje izvornih datoteka.
- Skrining naslova/apstrakta i punog teksta PDF-a, uz standardne ili prilagođene razloge za isključivanje.
- AI pomoć pri određivanju redosleda čitanja; korisnik donosi odluke o uključivanju, neizvesnosti ili isključivanju.
- Kodiranje studija uz dokaze iz punog teksta; veličine efekta, heterogenost, podgrupe, meta-regresija i analize osetljivosti.
- Ručna procena rizika od pristrasnosti, bibliometrija, mape dokaza, saradnja i PRISMA dijagrami toka.
- Beleženje istraživačkog procesa i izvoz u DOCX, PPTX ili LaTeX, u zavisnosti od modula.

Dostupnost metode ne znači da je ona validirana za svaki dizajn istraživanja. Pogledajte [procenu analitičkih mogućnosti](REVIEW_DATA_ANALYSIS_ASSESSMENT.md), proverite rezultate i citirajte izvorne metodološke radove.

### Početak rada i čuvanje podataka

Preuzmite odgovarajući instalacioni paket sa stranice [Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases). Paketi uključuju Python i zavisnosti. Za pokretanje izvornog koda na Ubuntu ili macOS-u instalirajte Python 3.12 i Git; na Linuxu po potrebi instalirajte Cairo i `python3-venv`, a zatim pratite komande u odeljku [English](#english).

Podaci se podrazumevano čuvaju lokalno: Windows `%LOCALAPPDATA%\ReviewFlow`, macOS `~/Library/Application Support/ReviewFlow`, Linux `~/.local/share/ReviewFlow` ili u okviru `XDG_DATA_HOME`. PDF datoteke su u direktorijumu sa podacima. Posle migracije lokacije ponovo pokrenite aplikaciju i čuvajte zasebne rezervne kopije. U serverskom režimu podaci zadataka ostaju na serveru; za spoljašnji pristup koristite trajno skladište i HTTPS. Mrežne AI funkcije šalju odgovarajuće zahteve podešenom servisu.

### Paketi, podrška i licenca

Uputstva za Windows, macOS i Linux nalaze se u odeljku [Building packages](#building-packages). Pogledajte pomoć u bočnoj traci i [vodič za saradnju](docs/handoff/2026-09-30-cloud-collaboration.md). Problem prijavite preko [Issues](https://github.com/RUCbookshelf/mubi-reviewflow/issues) uz sistem, verziju i korake za reprodukciju; uklonite lične podatke iz evidencije. Doprinosi su dobrodošli kroz Pull Request. MIT licenca: [`LICENSE`](LICENSE). Autor: **Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn).

## 한국어

ReviewFlow는 체계적 문헌고찰과 메타분석을 위한 문헌 가져오기, 선별, 코딩, 분석 및 연구 과정 기록을 한곳에 제공합니다. 개인은 로컬에서 오프라인으로 사용할 수 있고, 팀은 서버에 배포해 진행 상황을 공유하면서 검토자별 판단을 분리해 보관할 수 있습니다.

### 주요 기능

- RIS 가져오기, 중복 탐지 및 검토, 가져오기 기록과 원본 파일 추적.
- 제목/초록 선별 및 PDF 전문 선별, 기본 또는 사용자 지정 제외 사유.
- AI 읽기 순서 지원. 포함·보류·제외 결정은 사용자가 내립니다.
- 전문 근거를 포함한 연구 코딩, 효과크기, 이질성, 하위그룹, 메타회귀 및 민감도 분석.
- 비뚤림 위험 수동 평가, 서지계량, 근거 지도, 팀 협업 및 PRISMA 흐름도.
- 연구 과정 기록과 모듈별 DOCX, PPTX, LaTeX 내보내기.

기능이 구현되어 있다고 해서 모든 연구 설계에서 검증되었다는 뜻은 아닙니다. [분석 기능 평가](REVIEW_DATA_ANALYSIS_ASSESSMENT.md)를 확인하고 결과를 검토하며 방법론 원문을 인용하세요.

### 시작하기와 데이터 저장

[Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases)에서 운영체제에 맞는 설치 파일을 받으세요. 데스크톱 설치 파일에는 Python과 실행 종속성이 포함됩니다. Ubuntu 또는 macOS에서 소스 코드를 실행하려면 Python 3.12와 Git을 준비하세요. Linux에서는 필요에 따라 Cairo와 `python3-venv`를 설치하고 [English](#english) 안내의 명령을 실행하세요.

데이터는 기본적으로 로컬에 저장됩니다. Windows는 `%LOCALAPPDATA%\ReviewFlow`, macOS는 `~/Library/Application Support/ReviewFlow`, Linux는 `~/.local/share/ReviewFlow` 또는 `XDG_DATA_HOME`을 사용합니다. PDF는 데이터 폴더에 저장됩니다. 저장 위치를 옮긴 뒤에는 앱을 재시작하고 별도 백업을 보관하세요. 서버 모드의 작업 데이터는 서버에 저장됩니다. 외부 접속에는 영구 저장소와 HTTPS를 사용하세요. 온라인 AI 기능은 설정된 서비스로 관련 요청을 전송합니다.

### 빌드, 도움말 및 라이선스

Windows, macOS, Linux 빌드 방법은 [Building packages](#building-packages)를 참고하세요. 사이드바 도움말과 [협업 안내](docs/handoff/2026-09-30-cloud-collaboration.md)도 확인할 수 있습니다. 문제를 보고할 때는 [Issues](https://github.com/RUCbookshelf/mubi-reviewflow/issues)에 운영체제, 버전, 재현 단계를 적고 로그에서 개인정보를 삭제하세요. Pull Request를 통한 기여를 환영합니다. MIT 라이선스: [`LICENSE`](LICENSE). 작성자: **Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn).

## Русский

ReviewFlow — рабочая среда для систематических обзоров и метаанализа: импорт литературы, отбор, кодирование, анализ и регистрация исследовательского процесса. Можно работать локально без интернета или развернуть приложение на сервере для команды. Участники видят общую литературу и прогресс, а решения каждого рецензента сохраняются отдельно.

Приложение использует FastAPI, интерфейс в браузере и вычислительные модули Python. Установочные пакеты содержат Python и зависимости; пользователям не нужно устанавливать Python или WSL. Текущая версия установщика Windows — **2.0**.

### Возможности

- Импорт RIS, обработка дубликатов, история импорта и сохранение исходных данных.
- Отбор по заголовку/аннотации и полному тексту PDF; стандартные и собственные причины исключения.
- Ранжирование с помощью ИИ для определения порядка чтения; решения принимает пользователь.
- Кодирование характеристик исследований с опорой на полный текст.
- Размеры эффекта, модели фиксированного и случайных эффектов, гетерогенность, подгруппы, метарегрессия, анализ чувствительности и эффектов малых исследований.
- Модули диагностической точности, сетевого метаанализа, зависимости «доза–ответ», IPD, байесовских и многоуровневых моделей.
- Ручная оценка риска систематической ошибки с помощью RoB 2, ROBINS-I, QUADAS-2 и других инструментов.
- Библиометрический анализ и изучение карт доказательств.
- Журнал исследовательского процесса: включён по умолчанию, может быть отключён; записи отбора и кодирования, предварительный просмотр и многоязычный экспорт.
- Командный прогресс, экспорт автономных решений, объединение и разрешение разногласий.
- Диаграммы PRISMA и экспорт DOCX, PPTX, LaTeX в соответствующих модулях.

Наличие метода в программе не означает его полную проверку для всех дизайнов исследований. Условия применения и границы проверки описаны в [оценке аналитических возможностей](REVIEW_DATA_ANALYSIS_ASSESSMENT.md). Проверяйте результаты и цитируйте первоисточники методов при публикации.

### Начало работы

Доступные пакеты смотрите в [Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases). Если подходящего пакета нет, соберите его по инструкции ниже. В Windows при запуске проверяются зависимости; перед восстановлением программа запрашивает согласие и по возможности использует локальные файлы. VBScript не требуется.

Для запуска из исходников в Ubuntu или macOS нужны Python 3.12 и Git. Для SVG в Linux нужна библиотека Cairo; в Ubuntu при необходимости установите `python3-venv` и `libcairo2`.

```bash
git clone https://github.com/RUCbookshelf/mubi-reviewflow.git
cd mubi-reviewflow
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python build/launcher.py
```

Запускатель обслуживает интерфейс и API и открывает браузер. Порт по умолчанию — `8613`; если он занят, выбирается другой свободный порт. Используйте открытый программой адрес. `run_pathB.bat` предназначен для разработки в Windows через WSL и не нужен установленному приложению.

### Хранение данных и совместная работа

При запуске из исходников данные находятся в `data/` внутри репозитория. Для установленного приложения:

- Windows: `%LOCALAPPDATA%\ReviewFlow`
- macOS: `~/Library/Application Support/ReviewFlow`
- Linux: `~/.local/share/ReviewFlow` или каталог `ReviewFlow` в `XDG_DATA_HOME`.

PDF хранятся в `tasks/<task-id>/pdfs` внутри каталога данных. Перенос можно настроить на главной странице в разделе настроек хранения. **Изменение применяется после перезапуска приложения.** Исходные данные сохраняются до проверки нового каталога и запуска очистки. Удаление приложения сохраняет исследовательские данные; создавайте отдельные резервные копии.

При локальной работе данные остаются на компьютере. Онлайн-функции ИИ/API отправляют соответствующие запросы настроенному сервису. В серверном режиме данные задач постоянно хранятся на сервере. Участники задачи видят прогресс друг друга, но не могут изменять личные решения другого участника.

После установки зависимостей сервер запускается так:

```bash
COBOOKSHELF_DATA_DIR=/srv/reviewflow-data \
REVIEWFLOW_HOST=127.0.0.1 bash build/start_server.sh
```

Укажите постоянный каталог с правом записи; для внешнего доступа используйте обратный прокси с HTTPS. Инструкции доступны в разделе помощи боковой панели и в [руководстве по совместной работе](docs/handoff/2026-09-30-cloud-collaboration.md).

### Сборка установочных пакетов

Сборка выполняется на целевой ОС и требует интернета.

| Платформа | Действие | Результат |
| --- | --- | --- |
| Windows 10/11 x64 | Установить Inno Setup 6/7 или NSIS 3; запустить `build\build_windows.bat` | `build/Output/ReviewFlow-Setup-x64.exe` |
| macOS | `bash build/build_macos.sh` | `build/dist/ReviewFlow.dmg` |
| Linux x86_64 | `bash build/build_linux.sh` | `build/ReviewFlow-x86_64.AppImage` |

Пакеты macOS для Intel и Apple Silicon собираются отдельно на соответствующей архитектуре. Проверяйте дату и версию выпуска: наличие скрипта сборки не означает наличие актуального опубликованного пакета. Подробнее: [сборка для Windows](build/WINDOWS_TRANSFER.md).

### Помощь и участие в разработке

`8613` — номер порта, а не код ошибки. На странице входа есть диагностика подключения и восстановление. Если страница в Windows не открывается, запустите **检查与修复 ReviewFlow** из меню «Пуск» или `repair_reviewflow.bat` из каталога установки. Журнал: `%LOCALAPPDATA%\ReviewFlow\startup_diagnostic.log`. Если встроенный Python удалён, переустановите приложение.

Сообщайте о проблемах в [Issues](https://github.com/RUCbookshelf/mubi-reviewflow/issues): ОС, версия, шаги воспроизведения и журнал без личных данных. Приветствуются Pull Request. Для изменений статистических методов приложите источники, условия применения и воспроизводимые эталонные результаты. Не публикуйте пароли, базы исследований или полные тексты без разрешения.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

Основные каталоги: `custom_frontend/` — интерфейс, `custom_backend/` — API, `coscreen/` — исследовательская логика, `build/` — запуск и сборка, `tests/` — тесты, `docs/` — документация.

### Лицензия и контакты

Проект распространяется по лицензии MIT; см. корневой файл [`LICENSE`](LICENSE). Сторонние компоненты распространяются по собственным лицензиям. См. [уведомления](THIRD_PARTY_NOTICES.md) и [подготовку выпуска](OPEN_SOURCE_RELEASE.md).

Автор: **Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn)

---

## Français

ReviewFlow est un espace de travail pour les revues systématiques et les méta-analyses : importation des références, sélection, codage, analyse et suivi du processus de recherche. Il peut être utilisé localement hors ligne ou déployé sur un serveur pour une équipe. Les membres partagent les références et consultent leur progression, tout en conservant leurs décisions individuelles.

L'application repose sur FastAPI, une interface dans le navigateur et des modules de calcul Python. Les paquets de bureau incluent Python et les dépendances nécessaires ; les utilisateurs n'ont pas besoin d'installer Python ou WSL. La version actuelle de l'installateur Windows est **2.0**.

### Fonctionnalités

- Importation RIS, traitement des doublons, historique des imports et données originales.
- Sélection sur titre/résumé et texte intégral PDF, avec motifs d'exclusion prédéfinis ou personnalisés.
- Classement assisté par IA pour organiser la lecture ; les décisions restent celles de l'utilisateur.
- Codage des caractéristiques des études et enregistrement des éléments du texte intégral qui les justifient.
- Tailles d'effet, modèles à effets fixes et aléatoires, hétérogénéité, sous-groupes, méta-régression, analyses de sensibilité et effets liés aux petites études.
- Modules de précision diagnostique, méta-analyse en réseau, dose–réponse, IPD, modèles bayésiens et multiniveaux.
- Évaluation manuelle du risque de biais avec RoB 2, ROBINS-I, QUADAS-2 et d'autres outils.
- Analyse bibliométrique et exploration de cartes des données probantes.
- Traçabilité : activée par défaut et désactivable ; activités de sélection et de codage, aperçu et exports multilingues.
- Progression de l'équipe, export des décisions hors ligne, fusion et arbitrage.
- Diagrammes PRISMA et documents DOCX, PPTX ou LaTeX selon le module.

La présence d'une méthode ne signifie pas qu'elle a été entièrement validée pour tous les plans d'étude. Consultez l'[évaluation des analyses](REVIEW_DATA_ANALYSIS_ASSESSMENT.md) pour les hypothèses et limites de validation. Vérifiez les résultats et citez les sources des méthodes lors de la publication.

### Prise en main

Consultez les paquets disponibles dans [Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases). En l'absence de paquet adapté, utilisez les instructions de compilation ci-dessous. Sous Windows, les dépendances sont vérifiées au démarrage ; une réparation nécessite votre accord et utilise de préférence les fichiers fournis hors ligne. VBScript n'est pas nécessaire.

Pour utiliser les sources sous Ubuntu ou macOS, installez Python 3.12 et Git. Le rendu SVG sous Linux nécessite Cairo ; sous Ubuntu, installez `python3-venv` et `libcairo2` si nécessaire.

```bash
git clone https://github.com/RUCbookshelf/mubi-reviewflow.git
cd mubi-reviewflow
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python build/launcher.py
```

Le lanceur sert l'interface et l'API et ouvre le navigateur. Le port local par défaut est `8613` ; un autre port libre est choisi si nécessaire. Utilisez l'adresse ouverte par le lanceur. `run_pathB.bat` est une entrée de développement Windows via WSL, inutile pour l'application installée.

### Stockage et collaboration

Depuis les sources, les données sont dans `data/` à la racine du dépôt. Pour l'application installée :

- Windows : `%LOCALAPPDATA%\ReviewFlow`
- macOS : `~/Library/Application Support/ReviewFlow`
- Linux : `~/.local/share/ReviewFlow`, ou `ReviewFlow` dans `XDG_DATA_HOME`.

Les PDF sont stockés dans `tasks/<task-id>/pdfs` sous le dossier de données. La page d'accueil et les paramètres permettent de migrer ce dossier. **Redémarrez l'application pour appliquer le changement.** Les données originales restent disponibles jusqu'à la vérification du nouveau dossier et l'utilisation de l'option de nettoyage. La désinstallation conserve les données de recherche ; prévoyez des sauvegardes séparées.

En mode local, les données restent sur l'ordinateur. Les fonctions IA/API en ligne envoient les requêtes correspondantes au service configuré. En mode serveur, les données des tâches sont conservées sur le serveur. Les membres voient leur progression respective sans pouvoir modifier les décisions personnelles d'un autre membre.

Après installation des dépendances, démarrez le serveur avec :

```bash
COBOOKSHELF_DATA_DIR=/srv/reviewflow-data \
REVIEWFLOW_HOST=127.0.0.1 bash build/start_server.sh
```

Choisissez un dossier persistant accessible en écriture et un proxy inverse HTTPS pour l'accès externe. Consultez l'aide de la barre latérale et le [guide de collaboration](docs/handoff/2026-09-30-cloud-collaboration.md).

### Création des paquets

Compilez sur le système cible avec une connexion Internet.

| Plateforme | Commande / préparation | Résultat |
| --- | --- | --- |
| Windows 10/11 x64 | Installer Inno Setup 6/7 ou NSIS 3 ; lancer `build\build_windows.bat` | `build/Output/ReviewFlow-Setup-x64.exe` |
| macOS | `bash build/build_macos.sh` | `build/dist/ReviewFlow.dmg` |
| Linux x86_64 | `bash build/build_linux.sh` | `build/ReviewFlow-x86_64.AppImage` |

Les paquets macOS Intel et Apple Silicon doivent être créés séparément sur l'architecture correspondante. Vérifiez la date et la version : un script de compilation ne garantit pas l'existence d'un paquet publié à jour. Voir le [guide Windows](build/WINDOWS_TRANSFER.md).

### Dépannage et contributions

`8613` est un port, pas un code d'erreur. Utilisez le diagnostic et la réparation sur la page de connexion. Si la page Windows ne s'ouvre pas, lancez **检查与修复 ReviewFlow** depuis le menu Démarrer ou `repair_reviewflow.bat` depuis le dossier d'installation. Journal : `%LOCALAPPDATA%\ReviewFlow\startup_diagnostic.log`. Réinstallez l'application si Python intégré a été supprimé.

Signalez les problèmes dans [Issues](https://github.com/RUCbookshelf/mubi-reviewflow/issues), avec le système, la version, les étapes de reproduction et des journaux sans données personnelles. Les Pull Request sont bienvenues. Toute modification statistique doit préciser les sources, les conditions d'application et des résultats de référence reproductibles. N'ajoutez pas d'identifiants, de bases de recherche ou de textes intégraux non autorisés.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

Organisation : `custom_frontend/` pour l'interface, `custom_backend/` pour l'API, `coscreen/` pour les traitements scientifiques, `build/` pour le lancement et les paquets, `tests/` et `docs/`.

### Licence et contact

Ce projet est distribué sous licence MIT ; voir le fichier [`LICENSE`](LICENSE) à la racine. Les composants tiers conservent leurs licences respectives. Voir les [mentions tierces](THIRD_PARTY_NOTICES.md) et la [préparation de publication](OPEN_SOURCE_RELEASE.md).

Auteur : **Kang Tairong** · [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn)
