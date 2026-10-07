# Windows Build and Acceptance Guide

[中文](#zh-cn) · [English](#en) · [Français](#fr) · [Русский](#ru) · [Español](#es) · [日本語](#ja) · [Português](#pt) · [Deutsch](#de) · [Српски](#sr) · [한국어](#ko)

<a id="zh-cn"></a>
## 中文

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

<a id="en"></a>
## English

This guide is for maintainers building or checking the Windows installer from source. Most users should download a published version from [GitHub Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases); they do not need Python, WSL, or build tools.

### Requirements
- 64-bit Windows 10/11 and network access. The script downloads the Python 3.12 embeddable runtime and `get-pip.py`, then installs dependencies from `build/requirements-app.txt`.
- Inno Setup 6/7 or NSIS 3. The script detects an installer compiler and fails with guidance if neither is available.
- Enough disk space for the runtime, wheels, staging files, and installer. Do not copy `.venv` or another platform's Python environment into the package.

### Build
1. Use a clean checkout and confirm the root `VERSION` is the intended release version.
2. Open Command Prompt or PowerShell in the repository root and run `build\build_windows.bat`.
3. Wait for runtime setup, dependency installation, file copying, dependency checks, runtime smoke checks, and installer compilation.
4. The expected output is `build/Output/ReviewFlow-Setup-x64.exe`.

Logs are `build/build_windows.log` and `build/build_windows_smoke.log`. On failure, inspect the first failing step; do not publish an installer that failed smoke checks. When invoked from a WSL/UNC path, the script stages the source under `%LOCALAPPDATA%\ReviewFlow-build` because `cmd.exe` cannot use a UNC path as its working directory, then copies the installer back.

### Local acceptance
`windows_smoke.py` checks runtime dependencies, a basic scikit-learn path, SVG/PNG export, and the app entry point. It is a build gate, not complete manual acceptance. On Windows 10/11 x64, also verify installation and shortcuts, login and local-only service binding, alternate-port behavior, a small disposable RIS/import/screen/export workflow, and that uninstall removes program files while retaining research data in `%LOCALAPPDATA%\ReviewFlow`. Use a test account and recoverable data for restart, shutdown, upgrade, or migration checks. Never test on a user's only research database.

### Release
`.github/workflows/release.yml` builds Windows, Linux, and macOS packages on its configured release triggers and uploads packages with SHA-256 files after all builds succeed. Check `VERSION`, `RELEASE_NOTES.md`, tag, and workflow commit before release; afterward download the Windows package and verify its name, size, and checksum.

<a id="fr"></a>
## Français

Ce guide s’adresse aux mainteneurs qui construisent ou vérifient l’installateur Windows depuis les sources. Les utilisateurs peuvent télécharger une version publiée depuis [GitHub Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases), sans installer Python, WSL ni outils de compilation.

### Prérequis et construction
Utilisez Windows 10/11 64 bits, une connexion réseau, Inno Setup 6/7 ou NSIS 3 et suffisamment d’espace disque. Le script télécharge Python 3.12 embarqué et `get-pip.py`, puis installe les dépendances de `build/requirements-app.txt`. Depuis une copie propre, vérifiez `VERSION`, ouvrez un terminal à la racine et lancez `build\build_windows.bat`. L’installateur attendu est `build/Output/ReviewFlow-Setup-x64.exe`.

Le script effectue des contrôles de dépendances, un smoke test et compile l’installateur. Les journaux se trouvent dans `build/build_windows.log` et `build/build_windows_smoke.log`. En cas d’échec, consultez la première étape en erreur et ne publiez pas le paquet. Depuis WSL/UNC, le script copie temporairement le dépôt dans `%LOCALAPPDATA%\ReviewFlow-build`, puis recopie le résultat.

### Acceptation et publication
Le smoke test vérifie les dépendances, un parcours scikit-learn simple, l’export SVG/PNG et le point d’entrée. Il ne remplace pas les tests manuels : vérifiez installation, raccourcis, connexion, écoute locale, port alternatif, petit flux RIS jetable, export et conservation des données dans `%LOCALAPPDATA%\ReviewFlow` après désinstallation. N’utilisez jamais la base de recherche unique d’un utilisateur. Le workflow `release.yml` construit les paquets Windows/Linux/macOS et leurs sommes SHA-256 selon ses déclencheurs configurés. Avant et après publication, contrôlez version, notes, étiquette, commit, fichier Windows, taille et somme.

<a id="ru"></a>
## Русский

Руководство предназначено для сопровождающих, которые собирают или проверяют установщик Windows из исходного кода. Пользователям рекомендуется загрузить выпуск из [GitHub Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases); устанавливать Python, WSL и инструменты сборки не требуется.

### Требования и сборка
Нужны Windows 10/11 x64, доступ к сети, Inno Setup 6/7 или NSIS 3 и свободное место. Скрипт загружает встраиваемую среду Python 3.12 и `get-pip.py`, затем устанавливает зависимости из `build/requirements-app.txt`. В чистой копии проверьте `VERSION`, откройте терминал в корне и выполните `build\build_windows.bat`. Результат: `build/Output/ReviewFlow-Setup-x64.exe`.

Журналы: `build/build_windows.log` и `build/build_windows_smoke.log`. При ошибке сначала изучите первый неудачный шаг; не публикуйте пакет, не прошедший smoke-проверку. Для пути WSL/UNC скрипт использует временную копию `%LOCALAPPDATA%\ReviewFlow-build` и возвращает результат в исходный каталог.

### Приёмка и выпуск
`windows_smoke.py` проверяет зависимости, базовый сценарий scikit-learn, экспорт SVG/PNG и точку запуска; это не заменяет ручную приёмку. На Windows 10/11 x64 проверьте установку, ярлыки, вход, привязку службы только к локальному адресу, альтернативный порт, небольшой тестовый процесс RIS и экспорт. Убедитесь, что удаление программы сохраняет данные `%LOCALAPPDATA%\ReviewFlow`. Используйте только тестовую учётную запись и восстанавливаемые данные. `release.yml` собирает пакеты Windows/Linux/macOS и SHA-256 по заданным триггерам; проверьте версию, заметки, тег, commit, имя и контрольную сумму пакета.

<a id="es"></a>
## Español

Esta guía es para mantenedores que compilan o verifican el instalador de Windows desde el código fuente. Los usuarios pueden descargar la versión publicada en [GitHub Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases), sin instalar Python, WSL ni herramientas de compilación.

### Requisitos y compilación
Se necesita Windows 10/11 de 64 bits, conexión de red, Inno Setup 6/7 o NSIS 3 y espacio suficiente. El script descarga Python 3.12 embebido y `get-pip.py`, e instala dependencias de `build/requirements-app.txt`. Desde una copia limpia, compruebe `VERSION`, abra la terminal en la raíz y ejecute `build\build_windows.bat`. El resultado esperado es `build/Output/ReviewFlow-Setup-x64.exe`.

Los registros están en `build/build_windows.log` y `build/build_windows_smoke.log`. Si falla, revise el primer paso fallido y no publique el paquete. Para rutas WSL/UNC, el script usa `%LOCALAPPDATA%\ReviewFlow-build` como área temporal. La prueba `windows_smoke.py` cubre dependencias, una ruta básica de scikit-learn, exportación SVG/PNG y el punto de inicio; no sustituye la aceptación manual.

### Aceptación y publicación
En Windows 10/11 x64, compruebe instalación, accesos directos, inicio de sesión, escucha local, puerto alternativo, un flujo RIS pequeño con datos desechables, exportación y conservación de `%LOCALAPPDATA%\ReviewFlow` al desinstalar. No pruebe con la única base de investigación del usuario. `release.yml` compila Windows, Linux y macOS con sus sumas SHA-256 según los activadores configurados. Verifique versión, notas, etiqueta, commit, nombre, tamaño y suma del paquete.

<a id="ja"></a>
## 日本語

このガイドは、ソースから Windows インストーラーをビルドまたは確認する保守担当者向けです。一般ユーザーは [GitHub Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases) から公開版を取得でき、Python、WSL、ビルドツールは不要です。

### 要件とビルド
Windows 10/11 64 ビット、ネットワーク、Inno Setup 6/7 または NSIS 3、十分な空き容量が必要です。スクリプトは Python 3.12 埋め込み版と `get-pip.py` を取得し、`build/requirements-app.txt` の依存関係を導入します。クリーンなチェックアウトで `VERSION` を確認し、リポジトリルートから `build\build_windows.bat` を実行します。出力先は `build/Output/ReviewFlow-Setup-x64.exe` です。

ログは `build/build_windows.log` と `build/build_windows_smoke.log` です。最初の失敗箇所を確認し、smoke チェックに失敗したパッケージは公開しないでください。WSL/UNC からの実行では `%LOCALAPPDATA%\ReviewFlow-build` に一時コピーします。

### 受け入れ確認とリリース
`windows_smoke.py` は依存関係、基本的な scikit-learn 処理、SVG/PNG 出力、起動入口を確認しますが、手動確認の代わりにはなりません。Windows 10/11 x64 でインストール、ショートカット、ログイン、ローカル限定の待受、代替ポート、使い捨てデータによる小規模 RIS フロー、エクスポートを確認してください。アンインストール後も `%LOCALAPPDATA%\ReviewFlow` の研究データが残ることを確認します。ユーザー唯一の研究 DB では試験しないでください。`release.yml` は設定された条件で三 OS のパッケージと SHA-256 を作成します。版、ノート、タグ、コミット、ファイル名、サイズ、チェックサムを確認します。

<a id="pt"></a>
## Português

Este guia destina-se aos mantenedores que compilam ou verificam o instalador Windows a partir do código-fonte. Os usuários podem baixar a versão publicada em [GitHub Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases), sem instalar Python, WSL ou ferramentas de compilação.

### Requisitos e compilação
É necessário Windows 10/11 de 64 bits, acesso à rede, Inno Setup 6/7 ou NSIS 3 e espaço suficiente. O script baixa o Python 3.12 embutido e `get-pip.py` e instala as dependências de `build/requirements-app.txt`. Em um checkout limpo, confira `VERSION`, abra um terminal na raiz e execute `build\build_windows.bat`. O resultado esperado é `build/Output/ReviewFlow-Setup-x64.exe`.

Os logs ficam em `build/build_windows.log` e `build/build_windows_smoke.log`. Em caso de falha, examine a primeira etapa com erro e não publique o pacote. Em caminhos WSL/UNC, o script usa `%LOCALAPPDATA%\ReviewFlow-build` como área temporária. O `windows_smoke.py` verifica dependências, um fluxo básico do scikit-learn, exportação SVG/PNG e o ponto de entrada; não substitui a aceitação manual.

### Aceitação e lançamento
No Windows 10/11 x64, verifique instalação, atalhos, login, serviço limitado ao endereço local, porta alternativa, pequeno fluxo RIS com dados descartáveis, exportação e preservação de `%LOCALAPPDATA%\ReviewFlow` após desinstalar. Nunca use a única base de pesquisa do usuário. `release.yml` cria pacotes Windows/Linux/macOS e arquivos SHA-256 conforme os gatilhos configurados. Confira versão, notas, tag, commit, nome, tamanho e hash.

<a id="de"></a>
## Deutsch

Diese Anleitung richtet sich an Maintainer, die den Windows-Installer aus dem Quellcode erstellen oder prüfen. Nutzer können eine veröffentlichte Version von [GitHub Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases) laden und benötigen dafür weder Python noch WSL oder Build-Werkzeuge.

### Voraussetzungen und Build
Erforderlich sind Windows 10/11 x64, Netzwerkzugriff, Inno Setup 6/7 oder NSIS 3 und genügend Speicherplatz. Das Skript lädt die eingebettete Python-3.12-Laufzeit und `get-pip.py` herunter und installiert Abhängigkeiten aus `build/requirements-app.txt`. In einem sauberen Checkout `VERSION` prüfen, ein Terminal im Repository-Stamm öffnen und `build\build_windows.bat` ausführen. Erwartete Ausgabe: `build/Output/ReviewFlow-Setup-x64.exe`.

Protokolle: `build/build_windows.log` und `build/build_windows_smoke.log`. Bei Fehlern zuerst den ersten fehlgeschlagenen Schritt prüfen; ein fehlgeschlagenes Paket nicht veröffentlichen. Bei WSL/UNC-Pfaden nutzt das Skript `%LOCALAPPDATA%\ReviewFlow-build` als Zwischenverzeichnis. `windows_smoke.py` prüft Abhängigkeiten, einen einfachen scikit-learn-Pfad, SVG/PNG-Export und den App-Einstiegspunkt, ersetzt aber keine manuelle Abnahme.

### Abnahme und Veröffentlichung
Unter Windows 10/11 x64 Installation, Verknüpfungen, Anmeldung, lokale Bindung, Ausweichport, kleinen RIS-Test mit entbehrlichen Daten, Export und Erhalt von `%LOCALAPPDATA%\ReviewFlow` nach der Deinstallation prüfen. Niemals die einzige Forschungsdatenbank eines Nutzers verwenden. `release.yml` baut bei den konfigurierten Auslösern Pakete für Windows/Linux/macOS samt SHA-256. Version, Notizen, Tag, Commit, Dateiname, Größe und Prüfsumme kontrollieren.

<a id="sr"></a>
## Српски

Ovo uputstvo je namenjeno održavaocima koji prave ili proveravaju Windows instalacioni paket iz izvornog koda. Korisnici mogu preuzeti objavljeno izdanje sa [GitHub Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases), bez instaliranja Pythona, WSL-a ili alata za izgradnju.

### Uslovi i izgradnja
Potrebni su Windows 10/11 x64, mrežna veza, Inno Setup 6/7 ili NSIS 3 i dovoljno prostora. Skripta preuzima ugrađeni Python 3.12 i `get-pip.py`, zatim instalira zavisnosti iz `build/requirements-app.txt`. U čistom klonu proverite `VERSION`, otvorite terminal u korenu i pokrenite `build\build_windows.bat`. Očekivani rezultat je `build/Output/ReviewFlow-Setup-x64.exe`.

Evidencije su `build/build_windows.log` i `build/build_windows_smoke.log`. Kod greške proverite prvi neuspešan korak i ne objavljujte paket. Za WSL/UNC putanju skripta koristi privremeni `%LOCALAPPDATA%\ReviewFlow-build`. `windows_smoke.py` proverava zavisnosti, osnovni scikit-learn tok, SVG/PNG izvoz i ulaznu tačku aplikacije, ali ne zamenjuje ručnu proveru.

### Prihvat i izdanje
Na Windows 10/11 x64 proverite instalaciju, prečice, prijavu, lokalno slušanje servisa, alternativni port, mali RIS tok sa probnim podacima, izvoz i očuvanje `%LOCALAPPDATA%\ReviewFlow` nakon deinstalacije. Ne testirajte na jedinoj korisnikovoj bazi. `release.yml` pravi pakete za Windows/Linux/macOS i SHA-256 datoteke prema podešenim okidačima. Proverite verziju, beleške, oznaku, commit, naziv, veličinu i kontrolni zbir.

<a id="ko"></a>
## 한국어

이 문서는 소스 코드에서 Windows 설치 파일을 빌드하거나 확인하는 유지관리자용입니다. 일반 사용자는 [GitHub Releases](https://github.com/RUCbookshelf/mubi-reviewflow/releases)에서 배포판을 받으면 되며 Python, WSL, 빌드 도구를 설치할 필요가 없습니다.

### 요구 사항 및 빌드
Windows 10/11 64비트, 네트워크 연결, Inno Setup 6/7 또는 NSIS 3, 충분한 디스크 공간이 필요합니다. 스크립트는 Python 3.12 임베더블 런타임과 `get-pip.py`를 내려받고 `build/requirements-app.txt`의 의존성을 설치합니다. 깨끗한 체크아웃에서 `VERSION`을 확인하고 저장소 루트의 터미널에서 `build\build_windows.bat`를 실행하세요. 결과물은 `build/Output/ReviewFlow-Setup-x64.exe`입니다.

로그는 `build/build_windows.log`, `build/build_windows_smoke.log`에 저장됩니다. 실패하면 최초 실패 단계를 확인하고 smoke 검사에 실패한 패키지를 배포하지 마세요. WSL/UNC 경로에서는 `%LOCALAPPDATA%\ReviewFlow-build`에 임시 복사합니다. `windows_smoke.py`는 의존성, 기본 scikit-learn 경로, SVG/PNG 내보내기와 앱 진입점을 점검하지만 수동 검수를 대체하지 않습니다.

### 검수 및 릴리스
Windows 10/11 x64에서 설치, 바로가기, 로그인, 로컬 주소 바인딩, 대체 포트, 폐기 가능한 자료를 사용한 소규모 RIS 흐름, 내보내기를 확인하세요. 제거 후 `%LOCALAPPDATA%\ReviewFlow`의 연구 자료가 보존되는지도 확인합니다. 사용자의 유일한 연구 DB로 시험하지 마세요. `release.yml`은 설정된 조건에서 Windows/Linux/macOS 패키지와 SHA-256 파일을 만듭니다. 버전, 릴리스 노트, 태그, 커밋, 파일명, 크기와 체크섬을 확인하세요.
