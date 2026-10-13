# 木笔 ReviewFlow 2.0.5.3

## English

- Apply the approved mobile presentation across login, Home & Settings, import, title-and-abstract screening, full-text screening, coding, team management, reconciliation, exports, PRISMA, analysis, Trace and help.
- Retain the desktop layout above 760 CSS pixels. Mobile uses the desktop light/dark palettes and all ten existing interface languages, with separate language and theme controls.
- Improve touch targets, reader space, bounded decision/reason rows, vertically stacked PDF/evidence panels, settings tabs, local table scrolling and sheet-style details. Preserve existing save, Undo, authorization and confirmation handlers.
- Fix an empty-queue frontend error that could leave workflow loading unfinished.
- Append **DOI**, **Journal** and **Author(s)** and **Imported source file(s)** to both screening-result CSV exports. Existing decision columns and research records are preserved.
- No new database migration or Remote Operation feature. Phone access still requires a reachable, configured server.

Windows x64 installer, Linux x86_64 AppImage, macOS DMG and SHA256SUMS.txt are built by GitHub Actions. Back up the full data directory and close ReviewFlow before upgrading. Full application version: 2.0.5.3; macOS native version: 2.0.5, build 2.0.503.

See [mobile interface details](https://github.com/RUCbookshelf/mubi-reviewflow/blob/v2.0.5.3/docs/MOBILE_INTERFACE.md). The installed copy at E:/ReviewFlow was not replaced.

## 中文

- 筛选 CSV 新增 Imported source file(s) 列，记录原始 RIS 文件名；多来源文献保留全部已激活文件名。

- 全页面增加移动端适配，保留电脑布局、十种语言和电脑端浅色/深色配色；语言与主题按钮保持独立。
- 优化触控尺寸、文献阅读、决策与排除理由、PDF 与编码依据布局、设置标签页、局部表格滚动和详情面板。
- 修复空队列可能导致加载停滞的前端错误；初筛和复筛 CSV 新增 DOI、Journal、Author(s) 列。
- 无新增数据库迁移，不包含远程控制或内网穿透。升级前关闭程序并备份完整数据目录。

## Español

- La columna Imported source file(s) conserva los nombres originales de todos los archivos RIS activados asociados al artículo.

- Interfaz móvil en todas las páginas; diseño de escritorio conservado, diez idiomas y controles separados de idioma y tema.
- Lectura, formularios, paneles y controles táctiles mejorados. Corrección de la carga de colas vacías.
- Los CSV de ambas fases de cribado añaden DOI, Journal y Author(s). Sin nueva migración ni acceso remoto automático. Haga una copia de seguridad antes de actualizar.

## 日本語

- Imported source file(s) 列には、文献に関連付けられた有効なRISファイルの元の名前をすべて記録します。

- 全ページのモバイル表示を改善。デスクトップ表示、10言語、独立した言語・テーマ操作を維持。
- 文献閲覧、フォーム、タッチ操作を改善し、空のキューでの読み込みエラーを修正。
- 両スクリーニング段階のCSVにDOI、Journal、Author(s)を追加。新しいデータ移行や自動リモートアクセスはありません。更新前にバックアップしてください。

## Français

- La colonne Imported source file(s) conserve les noms originaux de tous les fichiers RIS activés associés à l’article.

- Présentation mobile sur toutes les pages, avec le bureau préservé, dix langues et des commandes distinctes pour la langue et le thème.
- Lecture, formulaires et commandes tactiles améliorés; correction du chargement des files vides.
- Ajout de DOI, Journal et Author(s) aux deux exports CSV de sélection. Aucune nouvelle migration ni connexion distante automatique. Sauvegardez avant la mise à jour.

## Русский

- Столбец Imported source file(s) содержит исходные имена всех активированных RIS-файлов, связанных со статьёй.

- Мобильный интерфейс для всех страниц; сохранены настольный интерфейс, десять языков и отдельные кнопки языка и темы.
- Улучшены чтение, формы и сенсорные элементы; исправлена загрузка пустой очереди.
- В CSV обоих этапов отбора добавлены DOI, Journal и Author(s). Новых миграций и автоматического удалённого доступа нет. Перед обновлением создайте резервную копию.

## Português

- A coluna Imported source file(s) preserva os nomes originais de todos os arquivos RIS ativados associados ao artigo.

- Interface móvel em todas as páginas, mantendo o layout de computador, dez idiomas e controles separados de idioma e tema.
- Melhorias na leitura, nos formulários e nos controles de toque; correção do carregamento de filas vazias.
- Os CSV das duas etapas de triagem incluem DOI, Journal e Author(s). Sem nova migração ou acesso remoto automático. Faça backup antes de atualizar.

## Deutsch

- Die Spalte Imported source file(s) enthält die ursprünglichen Namen aller aktivierten RIS-Dateien des Artikels.

- Mobile Darstellung für alle Seiten; Desktoplayout, zehn Sprachen und getrennte Sprach- und Themensteuerung bleiben erhalten.
- Verbesserte Leseflächen, Formulare und Touchbedienung; Fehler beim Laden leerer Warteschlangen behoben.
- Beide Screening-CSV-Exporte enthalten zusätzlich DOI, Journal und Author(s). Keine neue Migration und kein automatischer Fernzugriff. Vor dem Update eine Sicherung erstellen.

## Српски

- Колона Imported source file(s) чува оригинална имена свих активираних RIS датотека повезаних са чланком.

- Мобилни приказ свих страница; задржани су приказ на рачунару, десет језика и одвојене контроле језика и теме.
- Побољшани су читање, обрасци и додирне контроле; исправљено је учитавање празног реда.
- CSV извози обе фазе селекције додају DOI, Journal и Author(s). Нема нове миграције ни аутоматског удаљеног приступа. Направите резервну копију пре ажурирања.

## 한국어

- Imported source file(s) 열에는 문헌과 연결된 활성 RIS 파일의 원래 파일명을 모두 기록합니다.

- 모든 페이지의 모바일 화면을 개선했습니다. 데스크톱 레이아웃, 10개 언어, 별도의 언어 및 테마 버튼을 유지합니다.
- 문헌 읽기, 입력 양식, 터치 조작을 개선하고 빈 대기열의 로딩 오류를 수정했습니다.
- 두 선별 단계의 CSV에 DOI, Journal, Author(s) 열을 추가했습니다. 새로운 데이터베이스 마이그레이션이나 자동 원격 접속은 없습니다. 업데이트 전에 전체 데이터를 백업하세요.
