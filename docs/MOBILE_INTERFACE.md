# Mobile interface — 2.0.5.3

ReviewFlow uses the same application, account, permissions and database on small screens. The mobile presentation is enabled at viewport widths of 760 CSS pixels or less. Wider desktop layouts remain unchanged.

## Layout

- Separate language and light/dark controls; both use the existing ten-language interface and desktop theme tokens.
- Touch-sized navigation, settings tabs and controls. Existing keyboard and reduced-motion behavior remains available.
- Title-and-abstract screening prioritizes the article reader. Decisions share a row; exclusion reason and custom text use full-width rows.
- Full-text reading, annotations and coding evidence stack vertically. Existing PDF zoom and save/Undo actions are retained.
- Import review, team management, exports, coding dimensions, analysis, Trace, help and PRISMA use bounded panels. Wide tables and PRISMA diagrams scroll within their panels.
- Stopping guidance and Trace details appear as compact sheets. Existing permissions and confirmation handlers are reused.

## Access

This release improves small-screen presentation. It does not enable a tunnel, remote pairing, offline editing or automatic phone access to a desktop installation. A phone needs network access to a correctly configured ReviewFlow server. See the server deployment guide; desktop localhost addresses refer to the device displaying them.

## Implementation and rollback

`custom_frontend/mobile_workspace.css` contains only width-scoped styles. The existing controls retain their IDs and handlers. A two-condition guard in `workflow_controls.js` prevents empty queues from accessing a nonexistent current article. CSV screening exports append DOI, Journal, Author(s) and Imported source file(s). Multiple activated RIS filenames share one cell; unknown legacy provenance stays blank. Team exports read source filenames from the task owner’s catalog while retaining the exporting reviewer’s decisions.

There are no new database migrations. Reverting the mobile stylesheet reference restores the preceding presentation without modifying research records. Back up the complete data directory before installing any release.

## Language availability

中文 · English · Español · 日本語 · Français · Русский · Português · Deutsch · Српски · 한국어

The mobile UI reuses the product's existing translations. User-authored titles, reasons and evidence are not translated.

## Localized release summaries

### 中文

- 全页面增加移动端适配，保留电脑布局、十种语言和电脑端浅色/深色配色；语言与主题按钮保持独立。
- 优化触控尺寸、文献阅读、决策与排除理由、PDF 与编码依据布局、设置标签页、局部表格滚动和详情面板。
- 修复空队列可能导致加载停滞的前端错误；初筛和复筛 CSV 新增 DOI、Journal、Author(s) 列。
- 无新增数据库迁移，不包含远程控制或内网穿透。升级前关闭程序并备份完整数据目录。

### Español

- Interfaz móvil en todas las páginas; diseño de escritorio conservado, diez idiomas y controles separados de idioma y tema.
- Lectura, formularios, paneles y controles táctiles mejorados. Corrección de la carga de colas vacías.
- Los CSV de ambas fases de cribado añaden DOI, Journal y Author(s). Sin nueva migración ni acceso remoto automático. Haga una copia de seguridad antes de actualizar.

### 日本語

- 全ページのモバイル表示を改善。デスクトップ表示、10言語、独立した言語・テーマ操作を維持。
- 文献閲覧、フォーム、タッチ操作を改善し、空のキューでの読み込みエラーを修正。
- 両スクリーニング段階のCSVにDOI、Journal、Author(s)を追加。新しいデータ移行や自動リモートアクセスはありません。更新前にバックアップしてください。

### Français

- Présentation mobile sur toutes les pages, avec le bureau préservé, dix langues et des commandes distinctes pour la langue et le thème.
- Lecture, formulaires et commandes tactiles améliorés; correction du chargement des files vides.
- Ajout de DOI, Journal et Author(s) aux deux exports CSV de sélection. Aucune nouvelle migration ni connexion distante automatique. Sauvegardez avant la mise à jour.

### Русский

- Мобильный интерфейс для всех страниц; сохранены настольный интерфейс, десять языков и отдельные кнопки языка и темы.
- Улучшены чтение, формы и сенсорные элементы; исправлена загрузка пустой очереди.
- В CSV обоих этапов отбора добавлены DOI, Journal и Author(s). Новых миграций и автоматического удалённого доступа нет. Перед обновлением создайте резервную копию.

### Português

- Interface móvel em todas as páginas, mantendo o layout de computador, dez idiomas e controles separados de idioma e tema.
- Melhorias na leitura, nos formulários e nos controles de toque; correção do carregamento de filas vazias.
- Os CSV das duas etapas de triagem incluem DOI, Journal e Author(s). Sem nova migração ou acesso remoto automático. Faça backup antes de atualizar.

### Deutsch

- Mobile Darstellung für alle Seiten; Desktoplayout, zehn Sprachen und getrennte Sprach- und Themensteuerung bleiben erhalten.
- Verbesserte Leseflächen, Formulare und Touchbedienung; Fehler beim Laden leerer Warteschlangen behoben.
- Beide Screening-CSV-Exporte enthalten zusätzlich DOI, Journal und Author(s). Keine neue Migration und kein automatischer Fernzugriff. Vor dem Update eine Sicherung erstellen.

### Српски

- Мобилни приказ свих страница; задржани су приказ на рачунару, десет језика и одвојене контроле језика и теме.
- Побољшани су читање, обрасци и додирне контроле; исправљено је учитавање празног реда.
- CSV извози обе фазе селекције додају DOI, Journal и Author(s). Нема нове миграције ни аутоматског удаљеног приступа. Направите резервну копију пре ажурирања.

### 한국어

- 모든 페이지의 모바일 화면을 개선했습니다. 데스크톱 레이아웃, 10개 언어, 별도의 언어 및 테마 버튼을 유지합니다.
- 문헌 읽기, 입력 양식, 터치 조작을 개선하고 빈 대기열의 로딩 오류를 수정했습니다.
- 두 선별 단계의 CSV에 DOI, Journal, Author(s) 열을 추가했습니다. 새로운 데이터베이스 마이그레이션이나 자동 원격 접속은 없습니다. 업데이트 전에 전체 데이터를 백업하세요.

## Delivery checks

- Python syntax for changed backend/export files; JavaScript syntax for changed modules, inline scripts and locale packs.
- Browser layout review on 12 authenticated pages: all ten languages at 360 pixels, English at 390/430/760/1280 pixels (168 combinations). Final review found no page errors or page-level overflow.
- Desktop header/card geometry and colors matched with and without the mobile stylesheet at 1280 pixels. This does not establish every desktop viewport or OS scenario.
- Mobile menu open/close and Escape focus restoration checked; reduced motion and separate theme/language controls retained.
- Disposable-data owner and team-member CSV exports preserved their decisions and listed `01-Wiley.ris; 02-Scopus.ris` after both sources were activated.
- Real research data and E:/ReviewFlow were not modified. No full automated regression suite was run for this UI update.
