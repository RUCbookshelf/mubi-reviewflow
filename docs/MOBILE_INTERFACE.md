# Mobile interface — 2.1.0

ReviewFlow uses the same application, account, permissions and database on small screens. The mobile presentation is enabled at viewport widths of 760 CSS pixels or less. Wider desktop layouts remain unchanged.

## Layout

The approved concept is implemented using the existing application controls. APIs, authentication, authorization, screening/ranking/coding algorithms and research data are shared. Trace title search uses the same server query from both presentations; identifiers can still be filtered exactly by existing API clients.

- Separate language and light/dark controls; both use the existing ten-language interface and desktop theme tokens.
- Touch-sized navigation, settings tabs and controls. Existing keyboard and reduced-motion behavior remains available.
- Title-and-abstract screening prioritizes the article reader. Decisions share a row; exclusion reason and custom text use full-width rows.
- Full-text reading, annotations and coding evidence stack vertically. Existing PDF zoom and save/Undo actions are retained.
- Import review, team management, exports, coding dimensions, analysis, Trace, help and PRISMA use bounded panels. Wide tables and PRISMA diagrams scroll within their panels.
- Stopping guidance and Trace details appear as compact sheets. Existing permissions and confirmation handlers are reused.

## Access

This release improves small-screen presentation. It does not enable a tunnel, remote pairing, offline editing or automatic phone access to a desktop installation. A phone needs network access to a correctly configured ReviewFlow server. See the server deployment guide; desktop localhost addresses refer to the device displaying them.

## Implementation and rollback

`custom_frontend/mobile_concept.css` provides width-scoped layouts. `mobile_concept.js` moves the original controls and restores them above the mobile breakpoint; `mobile_motion.js` adds presentation animations and observes actual save results. `disclosure_controls.css` standardizes SVG disclosure indicators in both layouts. The existing controls retain their IDs and handlers. A two-condition guard in `workflow_controls.js` prevents empty queues from accessing a nonexistent current article. CSV screening exports append DOI, Journal, Author(s) and Imported source file(s). Multiple activated RIS filenames share one cell; unknown legacy provenance stays blank. Team exports read source filenames from the task owner’s catalog while retaining the exporting reviewer’s decisions.

There are no new database migrations. Reverting the mobile presentation asset references restores the preceding presentation without modifying research records. Back up the complete data directory before installing any release.

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

## 2.1.0 refinements

- Compact header, animated navigation/search, reversible inline disclosures and reduced-motion support.
- Original PDF controls, evidence forms and filters remain available; desktop layout restores when the viewport widens.
- Removed repeated disclosure headings and redundant PRISMA Details containers. Auto-fill and manual/task metric entry remain directly accessible.
- Trace search matches partial titles or identifiers before pagination, preserving stage/date/action filters and reviewer boundaries. Enter or Search submits the same query on mobile and desktop.

### Localized 2.1.0 changes

#### English

Mobile presentation now follows the approved concept: compact navigation, separate language and theme controls, animated search, sheets and disclosures. Original desktop controls, research logic, permissions and data remain shared. Trace search accepts a partial article title or identifier across matching history, with date/stage/action filters and reviewer isolation. Redundant headings were removed; disclosure indicators now use SVG.

#### 中文

移动端按已确认概念重新实现：紧凑导航、独立语言与主题按钮、搜索框动画、底部面板和详情展开；复用电脑端控件、研究逻辑、权限和数据。 轨迹支持按文献标题片段或标识检索符合筛选条件的历史记录，保留日期、阶段、操作筛选和个人记录隔离。删除重复标题，展开图标统一使用 SVG。

#### Français

La présentation mobile suit le concept approuvé : navigation compacte, langue et thème séparés, recherche animée, panneaux et détails dépliables. Les commandes, la logique de recherche, les permissions et les données restent communes au bureau. La recherche dans Trace accepte une partie du titre ou de l’identifiant, avec les filtres de date, d’étape et d’action et l’isolation des évaluateurs. Les titres redondants sont supprimés et les indicateurs utilisent SVG.

#### Русский

Мобильный интерфейс следует утверждённому макету: компактная навигация, отдельные кнопки языка и темы, анимированный поиск, панели и раскрытие деталей. Элементы управления, исследовательская логика, права и данные общие с настольной версией. Поиск в Trace поддерживает часть названия или идентификатора статьи с фильтрами даты, этапа и действия и изоляцией записей рецензентов. Удалены повторяющиеся заголовки; индикаторы раскрытия используют SVG.

#### Español

La interfaz móvil sigue el concepto aprobado: navegación compacta, idioma y tema separados, búsqueda animada, paneles y detalles desplegables. Comparte controles, lógica de investigación, permisos y datos con el escritorio. Trace busca por parte del título o identificador, manteniendo filtros de fecha, etapa y acción y el aislamiento entre revisores. Se eliminaron títulos redundantes y los indicadores usan SVG.

#### 日本語

承認済みのモバイル案に合わせ、コンパクトなナビゲーション、独立した言語・テーマ操作、検索アニメーション、パネルと詳細展開を実装。デスクトップと同じ操作、研究処理、権限、データを使用します。 Trace は文献タイトルの一部または識別子で履歴を検索し、日付・段階・操作の条件とレビュアー間の記録分離を維持します。重複見出しを削除し、展開アイコンを SVG に統一しました。

#### Português

A interface móvel segue o conceito aprovado: navegação compacta, idioma e tema separados, busca animada, painéis e detalhes expansíveis. Compartilha controles, lógica de pesquisa, permissões e dados com o computador. A busca no Trace aceita parte do título ou identificador, mantendo filtros de data, etapa e ação e o isolamento entre revisores. Títulos redundantes foram removidos e indicadores usam SVG.

#### Deutsch

Die mobile Oberfläche folgt dem freigegebenen Entwurf: kompakte Navigation, getrennte Sprach- und Themensteuerung, animierte Suche, Panels und aufklappbare Details. Bedienelemente, Forschungslogik, Rechte und Daten werden mit dem Desktop geteilt. Die Trace-Suche unterstützt Teile des Artikeltitels oder der Kennung mit Datums-, Phasen- und Aktionsfiltern sowie getrennten Gutachterdaten. Doppelte Überschriften wurden entfernt; Aufklappindikatoren verwenden SVG.

#### Српски

Мобилни интерфејс прати одобрени концепт: компактна навигација, одвојене контроле језика и теме, анимирана претрага, панели и прошириви детаљи. Контроле, истраживачка логика, дозволе и подаци заједнички су са приказом на рачунару. Trace претрага прихвата део наслова или идентификатора, уз филтере датума, фазе и операције и изолацију записа рецензената. Уклоњени су поновљени наслови; индикатори користе SVG.

#### 한국어

승인된 모바일 콘셉트에 맞춰 간결한 탐색, 별도 언어 및 테마 버튼, 검색 애니메이션, 패널과 상세 펼치기를 구현했습니다. 데스크톱과 같은 조작, 연구 로직, 권한 및 데이터를 사용합니다. Trace는 문헌 제목 일부나 식별자로 이력을 검색하며 날짜·단계·작업 필터와 검토자별 기록 분리를 유지합니다. 중복 제목을 제거하고 펼치기 아이콘을 SVG로 통일했습니다.
