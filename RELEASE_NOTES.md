# 木笔 ReviewFlow 2.1.0

## English

- Mobile presentation now follows the approved concept: compact navigation, separate language and theme controls, animated search, sheets and disclosures. Original desktop controls, research logic, permissions and data remain shared.
- Trace search accepts a partial article title or identifier across matching history, with date/stage/action filters and reviewer isolation. Redundant headings were removed; disclosure indicators now use SVG.

## 中文

- 移动端按已确认概念重新实现：紧凑导航、独立语言与主题按钮、搜索框动画、底部面板和详情展开；复用电脑端控件、研究逻辑、权限和数据。
- 轨迹支持按文献标题片段或标识检索符合筛选条件的历史记录，保留日期、阶段、操作筛选和个人记录隔离。删除重复标题，展开图标统一使用 SVG。

## Français

- La présentation mobile suit le concept approuvé : navigation compacte, langue et thème séparés, recherche animée, panneaux et détails dépliables. Les commandes, la logique de recherche, les permissions et les données restent communes au bureau.
- La recherche dans Trace accepte une partie du titre ou de l’identifiant, avec les filtres de date, d’étape et d’action et l’isolation des évaluateurs. Les titres redondants sont supprimés et les indicateurs utilisent SVG.

## Русский

- Мобильный интерфейс следует утверждённому макету: компактная навигация, отдельные кнопки языка и темы, анимированный поиск, панели и раскрытие деталей. Элементы управления, исследовательская логика, права и данные общие с настольной версией.
- Поиск в Trace поддерживает часть названия или идентификатора статьи с фильтрами даты, этапа и действия и изоляцией записей рецензентов. Удалены повторяющиеся заголовки; индикаторы раскрытия используют SVG.

## Español

- La interfaz móvil sigue el concepto aprobado: navegación compacta, idioma y tema separados, búsqueda animada, paneles y detalles desplegables. Comparte controles, lógica de investigación, permisos y datos con el escritorio.
- Trace busca por parte del título o identificador, manteniendo filtros de fecha, etapa y acción y el aislamiento entre revisores. Se eliminaron títulos redundantes y los indicadores usan SVG.

## 日本語

- 承認済みのモバイル案に合わせ、コンパクトなナビゲーション、独立した言語・テーマ操作、検索アニメーション、パネルと詳細展開を実装。デスクトップと同じ操作、研究処理、権限、データを使用します。
- Trace は文献タイトルの一部または識別子で履歴を検索し、日付・段階・操作の条件とレビュアー間の記録分離を維持します。重複見出しを削除し、展開アイコンを SVG に統一しました。

## Português

- A interface móvel segue o conceito aprovado: navegação compacta, idioma e tema separados, busca animada, painéis e detalhes expansíveis. Compartilha controles, lógica de pesquisa, permissões e dados com o computador.
- A busca no Trace aceita parte do título ou identificador, mantendo filtros de data, etapa e ação e o isolamento entre revisores. Títulos redundantes foram removidos e indicadores usam SVG.

## Deutsch

- Die mobile Oberfläche folgt dem freigegebenen Entwurf: kompakte Navigation, getrennte Sprach- und Themensteuerung, animierte Suche, Panels und aufklappbare Details. Bedienelemente, Forschungslogik, Rechte und Daten werden mit dem Desktop geteilt.
- Die Trace-Suche unterstützt Teile des Artikeltitels oder der Kennung mit Datums-, Phasen- und Aktionsfiltern sowie getrennten Gutachterdaten. Doppelte Überschriften wurden entfernt; Aufklappindikatoren verwenden SVG.

## Српски

- Мобилни интерфејс прати одобрени концепт: компактна навигација, одвојене контроле језика и теме, анимирана претрага, панели и прошириви детаљи. Контроле, истраживачка логика, дозволе и подаци заједнички су са приказом на рачунару.
- Trace претрага прихвата део наслова или идентификатора, уз филтере датума, фазе и операције и изолацију записа рецензената. Уклоњени су поновљени наслови; индикатори користе SVG.

## 한국어

- 승인된 모바일 콘셉트에 맞춰 간결한 탐색, 별도 언어 및 테마 버튼, 검색 애니메이션, 패널과 상세 펼치기를 구현했습니다. 데스크톱과 같은 조작, 연구 로직, 권한 및 데이터를 사용합니다.
- Trace는 문헌 제목 일부나 식별자로 이력을 검색하며 날짜·단계·작업 필터와 검토자별 기록 분리를 유지합니다. 중복 제목을 제거하고 펼치기 아이콘을 SVG로 통일했습니다.

## Distribution and compatibility

Windows x64 installer, macOS DMG, Linux x86_64 AppImage and SHA256SUMS.txt are built by GitHub Actions. All ten interface languages and desktop light/dark palettes remain available. Mobile layout applies at widths up to 760 CSS pixels.

This update introduces no persistent database migration. Existing screening, coding, Undo, CSV exports and PRISMA manual/task-data modes are retained. The Trace API adds an optional title-search query; the exact subject_id filter remains compatible. No Remote Operation or tunnel is added. Server access still requires a reachable, configured server.

Back up the complete data directory and close the application before upgrading. The local installed application at E:/ReviewFlow was not replaced.

## Validation

- Syntax checks for changed Python, JavaScript, inline scripts and language packs.
- Prior isolated GUI review: 12 routes at mobile and desktop widths, separate language/theme controls, ten languages, responsive restoration, PDF controls, menus and disclosure cleanup.
- Focused disposable Trace search checks: pagination, title/identifier matching, stage/date filters, exact-ID compatibility and reviewer isolation.
- Physical iOS/Android devices and a production HTTPS proxy were not validated; no complete automated regression suite was run.

[Mobile interface documentation](https://github.com/RUCbookshelf/mubi-reviewflow/blob/v2.1.0/docs/MOBILE_INTERFACE.md)
