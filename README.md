<div align="center">
  <h1>木笔 ReviewFlow</h1>
  <p><img src="custom_frontend/assets/icon_writelab_budnib_20260824.svg" alt="木笔 WriteLab 标识" width="96"></p>
  <p><strong>系统综述与 Meta 分析工作台 · Systematic review and meta-analysis workspace</strong></p>
  <p><a href="#中文">中文</a> · <a href="#english">English</a> · <a href="#русский">Русский</a> · <a href="#français">Français</a></p>
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

The application uses FastAPI, a browser interface and Python calculation modules. Desktop packages bundle Python and runtime dependencies; users do not need to install Python or WSL. The current Windows installer version is **2.0**.

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
