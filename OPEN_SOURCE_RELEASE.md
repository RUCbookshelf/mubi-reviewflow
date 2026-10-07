# Open-Source Release Preparation

[中文](#zh-cn) · [English](#en) · [Français](#fr) · [Русский](#ru) · [Español](#es) · [日本語](#ja) · [Português](#pt) · [Deutsch](#de) · [Српски](#sr) · [한국어](#ko)

<a id="zh-cn"></a>
## 中文

这份清单用于发布 ReviewFlow 源码和桌面安装包前检查仓库内容。它是维护流程文档，不构成法律意见或第三方许可的替代文本。

## 仓库许可证

根目录 `LICENSE` 为 MIT License。发布时保留许可证原文和版权声明。第三方依赖、字体、PDF.js、图表引用样式、图标和随包源码可能适用不同许可证；请同时检查根目录 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)、依赖清单及其随包许可证，不要把项目主许可证表述为覆盖全部第三方组件。

## 发布前检查

- 确认提交不含密码、API 密钥、访问令牌、个人数据、研究数据库、未经授权的全文或本机配置。
- 检查 `.gitignore`，避免提交 `data/`、日志、缓存、测试数据库、临时导入文件、构建产物和本地配置。
- 核对 `VERSION`、`RELEASE_NOTES.md`、安装包显示版本、Git 标签目标提交和 Release 说明一致。
- 确认 README 中引用的仓库文件存在，下载链接指向本仓库正式 Release 或清晰标注的上游来源。
- 更新运行时依赖及许可清单时，核对各平台实际打包的版本。Windows 运行时锁定信息以随包生成的 `runtime-lock.txt` 和嵌入式发行元数据为准。
- 确认构建工作流权限仅满足发布所需；检查每个平台构建结果、生成的 SHA-256 文件及最终上传的 Release 资产。
- 检查安装、升级、卸载和数据保留说明是否与当前启动器和安装脚本一致。

## GitHub Release 流程

仓库中的 `.github/workflows/release.yml` 为 Windows、macOS 和 Linux 分别构建桌面安装包。它读取根目录版本号；三个构建作业全部成功后，发布版本化 GitHub Release、安装包和 SHA-256 校验文件。普通提交到 `main` 不会自动发布桌面包；请查看工作流触发条件，并按仓库当前流程手动触发或推送至配置的发布分支。

发布后请确认：

1. Release 标签指向预期提交，Release 不是误设为草稿或预发布。
2. `.exe`、`.dmg`、`.AppImage` 和 `SHA256SUMS.txt` 按工作流预期存在。
3. 更新说明描述实际变更，未承诺未实现的差分更新、自动迁移或跨平台验收。
4. 下载至少一个代表性产物并核对 SHA-256；平台包应由相应 CI runner 构建。
5. 安装包升级保留用户数据的表述有当前安装/卸载实现支持。

## 问题报告与安全

请在 Issue 中提供操作系统、应用版本、最小复现步骤和已去除个人信息的日志。不要在公开 Issue、提交或附件中发布账户密码、密钥、研究数据或未经授权的论文全文。发现可能影响用户数据或认证的安全问题时，请先通过 [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn) 私下联系维护者，不要公开发布可直接利用的细节。

<a id="en"></a>
## English

This checklist helps maintainers review the repository before publishing ReviewFlow source code or desktop installers. It is a maintenance guide, not legal advice or a substitute for third-party license texts.

### Project license
The root `LICENSE` is the MIT License. Retain its text and copyright notices. Dependencies, fonts, PDF.js, chart styles, icons, and bundled source may use other licenses. Review [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md), dependency manifests, and licenses shipped with the app; do not imply the project license covers every third-party component.

### Before release
- Check that commits contain no passwords, API keys, tokens, personal data, research databases, unauthorized full text, or machine-specific settings.
- Check `.gitignore` for `data/`, logs, caches, test databases, temporary imports, build output, and local configuration.
- Align `VERSION`, `RELEASE_NOTES.md`, installer version, tag target, and release description.
- Confirm README links resolve and downloads point to an official project Release or clearly named upstream source.
- When updating runtime dependencies or notices, verify the versions actually packaged on each platform. Windows runtime locks are recorded in generated `runtime-lock.txt` and embedded distribution metadata.
- Ensure workflow permissions are limited to release needs; inspect all platform jobs, SHA-256 files, and uploaded assets.
- Ensure install, upgrade, uninstall, and data-retention statements match the current launcher and installer scripts.

### GitHub Release process
`.github/workflows/release.yml` builds Windows, macOS, and Linux installers. It reads the root version, then publishes a versioned GitHub Release, packages, and SHA-256 files after all three jobs succeed. Ordinary pushes to `main` do not publish desktop packages automatically. Check workflow triggers and follow the current manual or configured release-branch process.

After release, verify that the tag points to the intended commit and is not accidentally draft/prerelease; expected `.exe`, `.dmg`, `.AppImage`, and `SHA256SUMS.txt` assets exist; notes describe actual changes and make no unsupported promises; at least one package checksum matches; and data-retention claims are supported by current installer behavior.

### Reports and security
For issues, include OS, app version, minimal reproduction steps, and logs with personal information removed. Do not publish credentials, research data, or unauthorized full text. For security issues that may affect user data or authentication, contact maintainers privately at [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn) instead of posting exploitable details publicly.

<a id="fr"></a>
## Français

Cette liste aide les mainteneurs à vérifier le dépôt avant de publier le code source ou les installateurs ReviewFlow. Elle décrit le processus de maintenance et ne constitue ni un avis juridique ni un remplacement des licences tierces.

### Licence et contrôles avant publication
La licence racine `LICENSE` est MIT : conservez son texte et les mentions de copyright. Les dépendances, polices, PDF.js, styles de graphiques, icônes et sources intégrées peuvent avoir d’autres licences. Consultez `THIRD_PARTY_NOTICES.md`, les manifestes et les licences embarquées ; ne présentez pas MIT comme couvrant tous les composants.

Avant publication, vérifiez l’absence de secrets, données personnelles, bases de recherche, textes non autorisés et configuration locale ; l’exclusion des données, journaux, caches et artefacts dans `.gitignore` ; la cohérence de `VERSION`, `RELEASE_NOTES.md`, des installateurs, du tag et des notes ; les liens README ; les versions réellement empaquetées et les avis de licence ; les permissions et résultats de chaque tâche CI, fichiers SHA-256 et actifs téléversés ; ainsi que la conformité des descriptions d’installation, mise à niveau, désinstallation et conservation des données au code actuel.

### Publication GitHub et sécurité
`release.yml` construit les paquets Windows, macOS et Linux, puis publie Release, paquets et SHA-256 après réussite des trois tâches. Un push ordinaire sur `main` ne publie pas automatiquement les paquets. Après publication, vérifiez tag, commit, statut, fichiers `.exe`/`.dmg`/`.AppImage`, sommes, notes et promesses effectivement prises en charge. Pour un problème, fournissez OS, version, étapes minimales et journaux expurgés. Pour une vulnérabilité liée aux données ou à l’authentification, contactez en privé [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn).

<a id="ru"></a>
## Русский

Этот список помогает сопровождающим проверить репозиторий перед публикацией кода ReviewFlow или установщиков. Это руководство по обслуживанию, а не юридическая консультация и не замена лицензий сторонних компонентов.

### Лицензия и проверки перед выпуском
Корневой `LICENSE` содержит MIT License. Сохраняйте текст и авторские уведомления. Зависимости, шрифты, PDF.js, стили графиков, значки и встроенный исходный код могут иметь другие лицензии. Проверяйте `THIRD_PARTY_NOTICES.md`, манифесты и лицензии пакета; не утверждайте, что MIT распространяется на все компоненты.

Перед выпуском убедитесь, что в коммитах нет паролей, ключей, токенов, персональных данных, исследовательских баз и неразрешённых полных текстов; `.gitignore` исключает данные, логи, кэш, тестовые БД, временные файлы и сборки; `VERSION`, `RELEASE_NOTES.md`, пакет, тег и описание согласованы; ссылки README существуют; проверены фактические платформенные зависимости и лицензии; права CI минимальны, а сборки, SHA-256 и активы проверены; инструкции установки/обновления/удаления соответствуют текущему коду.

### GitHub Release и безопасность
`release.yml` собирает Windows/macOS/Linux и публикует версию, пакеты и SHA-256 после успешных трёх задач. Обычный push в `main` не публикует пакеты автоматически. После выпуска проверьте тег и commit, статус выпуска, `.exe`/`.dmg`/`.AppImage`, суммы, заметки и подтверждённые обещания. В отчёте об ошибке укажите ОС, версию, краткие шаги и очищенные логи. О проблемах безопасности данных или входа сообщайте приватно на [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn).

<a id="es"></a>
## Español

Esta lista ayuda a revisar el repositorio antes de publicar el código fuente o los instaladores de ReviewFlow. Es una guía de mantenimiento, no asesoramiento jurídico ni sustituto de las licencias de terceros.

### Licencia y preparación
El `LICENSE` raíz es MIT. Conserve el texto y los avisos de autoría. Dependencias, fuentes, PDF.js, estilos de gráficos, iconos y código incluido pueden tener otras licencias. Revise `THIRD_PARTY_NOTICES.md`, manifiestos y licencias empaquetadas; no afirme que MIT cubre todos los componentes.

Antes de publicar, compruebe que no haya secretos, datos personales, bases de investigación ni textos no autorizados; que `.gitignore` excluya datos, registros, cachés, bases de prueba, importaciones temporales y artefactos; que `VERSION`, `RELEASE_NOTES.md`, instalador, etiqueta y descripción coincidan; que los enlaces del README existan; que dependencias y licencias correspondan a cada paquete; que los permisos y resultados de CI, sumas SHA-256 y activos estén revisados; y que las instrucciones de instalación, actualización, desinstalación y conservación coincidan con el código.

### Release y seguridad
`release.yml` construye Windows, macOS y Linux, y publica Release, paquetes y SHA-256 tras el éxito de las tres tareas. Un push normal a `main` no publica instaladores. Después, revise etiqueta, commit, estado, activos `.exe`/`.dmg`/`.AppImage`, hashes, notas y afirmaciones. Para incidencias, incluya SO, versión, pasos mínimos y registros sin datos personales. Informe en privado de problemas de seguridad relacionados con datos o autenticación a [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn).

<a id="ja"></a>
## 日本語

このチェックリストは、ReviewFlow のソースやデスクトップインストーラー公開前にリポジトリを確認する保守担当者向けです。保守手順の案内であり、法律上の助言や第三者ライセンスの代替ではありません。

### ライセンスと公開前確認
ルートの `LICENSE` は MIT License です。本文と著作権表示を保持してください。依存関係、フォント、PDF.js、グラフスタイル、アイコン、同梱ソースには別のライセンスが適用される場合があります。`THIRD_PARTY_NOTICES.md`、依存一覧、同梱ライセンスを確認し、MIT が全コンポーネントを対象と誤解させないでください。

公開前に、秘密情報・個人情報・研究 DB・未許諾全文がないこと、`.gitignore` がデータやログ、キャッシュ、テスト DB、一時ファイル、生成物を除外すること、`VERSION`・`RELEASE_NOTES.md`・インストーラー・タグ・説明が一致すること、README リンクが存在すること、各 OS の実際の依存版とライセンス、CI 権限・各ジョブ・SHA-256・アップロード資産、インストール/更新/削除/データ保持の説明が現行コードと一致することを確認します。

### GitHub Release とセキュリティ
`release.yml` は Windows/macOS/Linux をビルドし、3 ジョブ成功後に Release、パッケージ、SHA-256 を公開します。`main` への通常の push では自動公開されません。公開後はタグ、コミット、状態、`.exe`/`.dmg`/`.AppImage`、チェックサム、説明を確認します。問題報告には OS、版、最小手順、個人情報を除いたログを含めてください。データや認証に影響するセキュリティ問題は [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn) に非公開で連絡してください。

<a id="pt"></a>
## Português

Esta lista ajuda a revisar o repositório antes de publicar o código-fonte ou os instaladores do ReviewFlow. É um guia de manutenção, não aconselhamento jurídico nem substituto das licenças de terceiros.

### Licença e preparação
O `LICENSE` raiz contém a licença MIT. Preserve o texto e os avisos de direitos autorais. Dependências, fontes, PDF.js, estilos de gráficos, ícones e código incluído podem ter licenças diferentes. Consulte `THIRD_PARTY_NOTICES.md`, manifestos e licenças distribuídas; não declare que a MIT cobre todos os componentes.

Antes do lançamento, verifique a ausência de segredos, dados pessoais, bases de pesquisa e textos sem autorização; confira se `.gitignore` exclui dados, logs, cache, bases de teste, arquivos temporários e artefatos; alinhe `VERSION`, `RELEASE_NOTES.md`, instalador, tag e descrição; valide links do README, dependências e licenças de cada plataforma; revise permissões e resultados da CI, arquivos SHA-256 e ativos; e confirme que as instruções de instalação, atualização, remoção e retenção correspondem ao código atual.

### Release e segurança
`release.yml` compila Windows/macOS/Linux e publica Release, pacotes e SHA-256 quando os três trabalhos terminam com sucesso. Um push comum para `main` não publica automaticamente. Após o lançamento, confira tag, commit, estado, ativos `.exe`/`.dmg`/`.AppImage`, hashes e notas. Relatos devem incluir SO, versão, passos mínimos e logs sem dados pessoais. Problemas de segurança que possam afetar dados ou autenticação devem ser comunicados em privado para [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn).

<a id="de"></a>
## Deutsch

Diese Checkliste unterstützt Maintainer bei der Prüfung des Repositorys vor der Veröffentlichung des ReviewFlow-Quellcodes oder der Desktop-Installer. Sie ist eine Wartungsanleitung, keine Rechtsberatung und kein Ersatz für Drittanbieter-Lizenzen.

### Lizenz und Vorbereitung
Die Datei `LICENSE` im Stammverzeichnis enthält die MIT-Lizenz. Text und Copyright-Hinweise müssen erhalten bleiben. Abhängigkeiten, Schriftarten, PDF.js, Diagrammstile, Symbole und gebündelter Quellcode können anderen Lizenzen unterliegen. `THIRD_PARTY_NOTICES.md`, Abhängigkeitslisten und mitgelieferte Lizenzen prüfen; nicht behaupten, MIT decke alle Komponenten ab.

Vor der Veröffentlichung sicherstellen: keine Geheimnisse, personenbezogenen Daten, Forschungsdatenbanken oder nicht autorisierten Volltexte; `.gitignore` schließt Daten, Logs, Cache, Testdatenbanken, temporäre Importe und Build-Ausgaben aus; `VERSION`, `RELEASE_NOTES.md`, Installer, Tag und Beschreibung stimmen überein; README-Links funktionieren; tatsächlich gebündelte Abhängigkeiten und Lizenzen sind geprüft; CI-Berechtigungen, Plattformläufe, SHA-256-Dateien und Assets sind geprüft; Installations-, Upgrade-, Deinstallations- und Datenerhalt-Aussagen passen zum aktuellen Code.

### GitHub Release und Sicherheit
`release.yml` baut Windows/macOS/Linux und veröffentlicht Release, Pakete und SHA-256 nach Erfolg aller drei Jobs. Ein normaler Push auf `main` veröffentlicht keine Pakete. Danach Tag, Commit, Status, `.exe`/`.dmg`/`.AppImage`, Prüfsummen und Hinweise prüfen. Fehlerberichte sollen Betriebssystem, Version, knappe Schritte und bereinigte Logs enthalten. Sicherheitsprobleme zu Daten oder Anmeldung privat an [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn) melden.

<a id="sr"></a>
## Српски

Ova lista pomaže održavaocima da pregledaju repozitorijum pre objave ReviewFlow izvornog koda ili instalacionih paketa. To je uputstvo za održavanje, a ne pravni savet niti zamena za licence trećih strana.

### Licenca i priprema
Korenski `LICENSE` sadrži MIT licencu. Sačuvajte tekst i obaveštenja o autorskim pravima. Zavisnosti, fontovi, PDF.js, stilovi grafikona, ikone i uključeni izvorni kod mogu imati druge licence. Proverite `THIRD_PARTY_NOTICES.md`, manifeste i licence u paketu; nemojte tvrditi da MIT pokriva sve komponente.

Pre objave proverite da nema tajni, ličnih podataka, istraživačkih baza ni neovlašćenih punih tekstova; da `.gitignore` isključuje podatke, evidencije, keš, test baze, privremene uvoze i artefakte; da su `VERSION`, `RELEASE_NOTES.md`, instalater, oznaka i opis usklađeni; da README veze postoje; da su proverene stvarno upakovane zavisnosti i licence; da su dozvole CI-ja, rezultati platformi, SHA-256 i objavljeni fajlovi pregledani; i da uputstva odgovaraju aktuelnom kodu.

### GitHub izdanje i bezbednost
`release.yml` pravi Windows/macOS/Linux pakete i objavljuje izdanje i SHA-256 nakon uspeha sva tri posla. Običan push na `main` ne objavljuje pakete. Posle objave proverite oznaku, commit, status, `.exe`/`.dmg`/`.AppImage`, kontrolne zbirove i beleške. Prijava problema treba da sadrži OS, verziju, kratke korake i očišćene evidencije. Bezbednosne probleme koji mogu uticati na podatke ili prijavu prijavite privatno na [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn).

<a id="ko"></a>
## 한국어

이 체크리스트는 ReviewFlow 소스 코드나 데스크톱 설치 파일을 공개하기 전에 저장소를 점검하는 유지관리자용입니다. 유지관리 안내이며 법률 자문이나 제3자 라이선스 문서를 대체하지 않습니다.

### 라이선스 및 공개 전 점검
루트 `LICENSE`는 MIT License입니다. 원문과 저작권 고지를 유지하세요. 의존성, 글꼴, PDF.js, 차트 스타일, 아이콘 및 포함된 소스에는 다른 라이선스가 적용될 수 있습니다. `THIRD_PARTY_NOTICES.md`, 의존성 목록, 패키지 내 라이선스를 확인하고 MIT가 모든 구성요소에 적용된다고 표현하지 마세요.

공개 전에 비밀번호·API 키·토큰·개인정보·연구 DB·무단 원문이 없는지, `.gitignore`가 데이터·로그·캐시·테스트 DB·임시 파일·빌드 산출물을 제외하는지, `VERSION`·`RELEASE_NOTES.md`·설치 파일·태그·설명이 일치하는지, README 링크가 유효한지 확인하세요. 플랫폼별 실제 패키지 의존성과 라이선스, CI 권한과 결과, SHA-256 및 업로드 파일, 설치·업데이트·제거·데이터 보존 설명도 현재 코드와 대조하세요.

### GitHub Release 및 보안
`release.yml`은 Windows/macOS/Linux를 빌드하고 세 작업이 모두 성공하면 Release, 패키지, SHA-256을 게시합니다. `main`의 일반 push는 자동으로 데스크톱 패키지를 게시하지 않습니다. 게시 후 태그, 커밋, 상태, `.exe`/`.dmg`/`.AppImage`, 체크섬 및 설명을 확인하세요. 버그 보고에는 OS, 앱 버전, 최소 재현 단계와 개인정보를 제거한 로그를 포함하세요. 데이터 또는 인증에 영향을 줄 수 있는 보안 문제는 [bookshelf@ruc.edu.cn](mailto:bookshelf@ruc.edu.cn)으로 비공개 연락하세요.
