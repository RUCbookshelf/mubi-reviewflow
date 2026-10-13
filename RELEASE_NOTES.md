# Mubi·ReviewFlow 2.1.2

## 中文

### 新增功能

- 新增紧凑的动态圆环加载提示，显示已加载记录数量；加载中断后可重试。
- 新增批量核验：确认 DOI 一致且无身份冲突的重复候选，或批量保留已有文献信息；操作前可查看清单，启用前支持整批撤销。

### 功能改进

- 首批记录加载后即可开始核验，其余记录在后台分批加载。
- 后台加载时保留当前记录、已选选项、展开的详情和页面位置。
- 加载提示支持十种语言；页脚显示实际应用版本。
- 导入核验采用紧凑布局：批次与加载状态集中显示，来源文件按需展开，分类选项均分一行；手机端提供上一条、记录选择和下一条。
- 确认结果保存后直接更新受影响记录并显示下一项；辅助统计在后台更新，减少连续核验时的等待。

### 问题修复

- 修复大量核验记录加载期间页面长时间停留在加载提示、无法先行操作的问题。
- 修复后台旧数据覆盖已保存核验结果的问题；保存失败保留当前记录和选择，批量操作任一项失效时整批回滚。

## English

### Added

- Added a compact animated loading ring that shows the number of records loaded and provides a retry option if loading is interrupted.
- Added bulk review for duplicate candidates with matching DOI values and no identity conflict, and for retaining existing bibliographic information. Preview the affected items before confirming and undo the whole action before activation.

### Improved

- The first batch of records is available for review while the remaining records load in the background.
- Background loading preserves the current record, selected options, expanded details and page position.
- Loading messages are available in ten languages, and the footer displays the running application version.
- Made import review more compact: batch and loading status together, expandable source files, equally spaced category tabs, and previous/select/next record controls on phones.
- Saved reviews update affected records and make the next item available immediately after the save response; supporting statistics refresh in the background.

### Fixed

- Fixed the long wait before users could work with large import-review lists.
- Fixed stale background responses overwriting saved reviews. Failed saves retain the current record and selection; an invalid bulk item rolls back the entire action.

## Français

### Nouveautés

- Ajout d’un indicateur circulaire animé et compact, qui affiche le nombre d’enregistrements chargés et permet de réessayer après une interruption.
- Ajout de la vérification en lot des doublons avec DOI identique et sans conflit d’identité, et de la conservation des informations existantes en lot. Une liste précède la confirmation et le lot peut être annulé avant activation.

### Améliorations

- Le premier lot d’enregistrements peut être vérifié pendant le chargement des suivants en arrière-plan.
- Le chargement en arrière-plan conserve l’enregistrement actuel, les options sélectionnées, les détails ouverts et la position de la page.
- Les messages de chargement sont disponibles en dix langues et le pied de page affiche la version de l’application.
- Interface de vérification plus compacte : lot et chargement réunis, fichiers sources dépliables, onglets répartis sur une ligne et navigation précédent/sélection/suivant sur téléphone.
- Après la réponse de sauvegarde, les notices concernées sont mises à jour et la suivante devient disponible ; les statistiques sont actualisées en arrière-plan.

### Corrections

- Correction de l’attente prolongée avant de pouvoir traiter les longues listes de vérification d’importation.
- Correction des réponses anciennes écrasant les vérifications sauvegardées. Un échec conserve la notice et la sélection ; un élément invalide annule tout le lot.

## Русский

### Добавлено

- Добавлен компактный анимированный круговой индикатор: он показывает число загруженных записей и позволяет повторить загрузку после сбоя.
- Добавлена пакетная проверка дубликатов с одинаковым DOI без конфликта идентичности и пакетное сохранение текущих сведений. Перед подтверждением показан список; до активации можно отменить весь пакет.

### Улучшено

- Первую группу записей можно проверять, пока остальные загружаются в фоновом режиме.
- Фоновая загрузка сохраняет текущую запись, выбранные параметры, раскрытые сведения и положение страницы.
- Сообщения о загрузке доступны на десяти языках; в нижней части страницы отображается версия приложения.
- Проверка импорта стала компактнее: пакет и загрузка рядом, раскрываемые исходные файлы, равномерные вкладки и переход назад/выбор/вперёд на телефоне.
- После ответа о сохранении обновляются затронутые записи и доступна следующая; вспомогательная статистика обновляется в фоне.

### Исправлено

- Устранено длительное ожидание перед началом работы с большими списками проверки импорта.
- Старые фоновые ответы больше не перезаписывают сохранённую проверку. При сбое сохраняются запись и выбор; недопустимый элемент откатывает весь пакет.

## Español

### Añadido

- Se añadió un indicador circular animado y compacto que muestra cuántos registros se han cargado y permite reintentar si la carga se interrumpe.
- Añadida revisión en lote de duplicados con DOI coincidente y sin conflicto de identidad, y conservación de información existente en lote. Se muestra una lista antes de confirmar y se puede deshacer el lote antes de activarlo.

### Mejorado

- El primer grupo de registros queda disponible para su revisión mientras los demás se cargan en segundo plano.
- La carga en segundo plano conserva el registro actual, las opciones seleccionadas, los detalles abiertos y la posición de la página.
- Los mensajes de carga están disponibles en diez idiomas y el pie de página muestra la versión de la aplicación.
- Revisión de importaciones más compacta: lote y carga juntos, archivos fuente desplegables, pestañas repartidas en una fila y controles anterior/selección/siguiente en móviles.
- Tras la respuesta de guardado, se actualizan los registros afectados y se habilita el siguiente; las estadísticas se actualizan en segundo plano.

### Corregido

- Se corrigió la larga espera antes de poder trabajar con listas extensas de revisión de importaciones.
- Corregidas respuestas antiguas que sobrescribían revisiones guardadas. Los fallos conservan registro y selección; un elemento no válido revierte todo el lote.

## 日本語

### 追加

- 読み込み済み件数を示し、中断後に再試行できる、コンパクトなアニメーション付き円形インジケーターを追加しました。
- DOI が一致し同一性の競合がない重複候補の一括確認と、既存書誌情報の一括保持を追加。確認前に一覧を表示し、有効化前は一括操作を取り消せます。

### 改善

- 残りの記録をバックグラウンドで読み込む間に、最初の記録から確認できるようになりました。
- バックグラウンド読み込み中も、現在の記録、選択内容、展開した詳細、ページ位置を保持します。
- 読み込みメッセージが10言語に対応し、フッターにアプリのバージョンを表示します。
- 取り込み確認をコンパクト化。バッチと読み込み状況を集約し、ソースファイルを展開表示。分類タブを均等配置し、スマートフォンに前／選択／次の操作を追加。
- 保存応答後に対象レコードを更新して次の項目を操作可能にし、補助統計はバックグラウンドで更新します。

### 修正

- 大量のインポート検証記録を操作できるまで長時間待つ問題を修正しました。
- 古い読み込み応答が保存済みの確認を上書きする問題を修正。保存失敗時はレコードと選択を保持し、一括操作に無効な項目があれば全体を取り消します。

## Português

### Adicionado

- Foi adicionado um indicador circular compacto e animado, que mostra a quantidade de registros carregados e permite tentar novamente após uma interrupção.
- Adicionada revisão em lote de duplicados com DOI correspondente e sem conflito de identidade, e manutenção das informações existentes em lote. A lista aparece antes da confirmação e o lote pode ser desfeito antes da ativação.

### Melhorias

- O primeiro grupo de registros fica disponível para revisão enquanto os demais carregam em segundo plano.
- O carregamento em segundo plano preserva o registro atual, as opções selecionadas, os detalhes abertos e a posição da página.
- As mensagens de carregamento estão disponíveis em dez idiomas, e o rodapé exibe a versão do aplicativo.
- Revisão de importações mais compacta: lote e carregamento juntos, arquivos de origem expansíveis, abas distribuídas em uma linha e controles anterior/seleção/próximo no celular.
- Após a resposta de salvamento, os registros afetados são atualizados e o próximo fica disponível; estatísticas auxiliares são atualizadas em segundo plano.

### Correções

- Foi corrigida a longa espera antes de ser possível trabalhar com listas extensas de revisão de importação.
- Corrigidas respostas antigas que sobrescreviam revisões salvas. Falhas mantêm o registro e a seleção; um item inválido reverte todo o lote.

## Deutsch

### Neu

- Ein kompakter, animierter Ladekreis zeigt die Anzahl geladener Datensätze an und ermöglicht einen erneuten Versuch nach einer Unterbrechung.
- Sammelprüfung für Duplikate mit gleichem DOI ohne Identitätskonflikt und gesammeltes Beibehalten vorhandener Informationen hinzugefügt. Eine Liste erscheint vor der Bestätigung; vor Aktivierung ist die gesamte Aktion rückgängig zu machen.

### Verbesserungen

- Der erste Datensatzstapel kann geprüft werden, während weitere Datensätze im Hintergrund geladen werden.
- Beim Laden im Hintergrund bleiben der aktuelle Datensatz, ausgewählte Optionen, geöffnete Details und die Seitenposition erhalten.
- Ladehinweise sind in zehn Sprachen verfügbar; die Fußzeile zeigt die Anwendungsversion.
- Kompaktere Importprüfung: Stapel und Ladestatus zusammen, aufklappbare Quelldateien, gleichmäßig verteilte Kategorien und Zurück/Auswahl/Weiter auf Smartphones.
- Nach der Speicherantwort werden betroffene Datensätze aktualisiert und der nächste wird bedienbar; ergänzende Statistiken werden im Hintergrund aktualisiert.

### Fehlerbehebungen

- Die lange Wartezeit, bevor umfangreiche Importprüflisten bearbeitet werden konnten, wurde behoben.
- Veraltete Hintergrundantworten überschreiben keine gespeicherten Prüfungen mehr. Fehler behalten Datensatz und Auswahl; ein ungültiges Element setzt die gesamte Sammelaktion zurück.

## Српски

### Додато

- Додат је компактан анимирани кружни показатељ који приказује број учитаних записа и омогућава поновни покушај након прекида.
- Додата групна провера дупликата са истим DOI без сукоба идентитета и групно задржавање постојећих података. Листа се приказује пре потврде; цела радња може се опозвати пре активације.

### Побољшано

- Прва група записа доступна је за проверу док се остали учитавају у позадини.
- Учитавање у позадини чува тренутни запис, изабране опције, отворене детаље и положај странице.
- Поруке о учитавању доступне су на десет језика, а подножје приказује верзију апликације.
- Компактнија провера увоза: пакет и учитавање заједно, склопиве изворне датотеке, равномерне категорије и претходни/избор/следећи запис на телефону.
- По одговору о чувању ажурирају се погођени записи и следећи је доступан; помоћна статистика се ажурира у позадини.

### Исправљено

- Отклоњено је дуго чекање пре почетка рада са великим листама за проверу увоза.
- Стари позадински одговори више не преписују сачуване провере. Грешка задржава запис и избор; неважећа ставка поништава целу групну радњу.

## 한국어

### 추가

- 불러온 기록 수를 표시하고 중단 후 다시 시도할 수 있는 작고 움직이는 원형 로딩 표시를 추가했습니다.
- DOI가 같고 식별 정보 충돌이 없는 중복 후보의 일괄 확인과 기존 서지 정보 일괄 유지를 추가했습니다. 확인 전에 목록을 표시하며 활성화 전에 전체 작업을 취소할 수 있습니다.

### 개선

- 나머지 기록이 백그라운드에서 로드되는 동안 첫 번째 기록 묶음부터 검토할 수 있습니다.
- 백그라운드 로딩 중에도 현재 기록, 선택한 옵션, 펼친 상세 내용과 페이지 위치를 유지합니다.
- 로딩 메시지는 10개 언어를 지원하며, 바닥글에 앱 버전을 표시합니다.
- 가져오기 검토를 간결하게 정리했습니다. 배치와 로딩 상태 통합, 원본 파일 펼치기, 균등한 분류 탭, 모바일 이전/선택/다음 기록 탐색을 제공합니다.
- 저장 응답 후 해당 기록을 갱신하고 다음 항목을 바로 사용할 수 있게 하며 보조 통계는 백그라운드에서 갱신합니다.

### 수정

- 가져오기 검토 기록이 많을 때 작업을 시작하기까지 오래 기다려야 하는 문제를 수정했습니다.
- 오래된 로딩 응답이 저장한 검토를 덮어쓰는 문제를 수정했습니다. 저장 실패 시 기록과 선택을 유지하고 일괄 항목이 유효하지 않으면 전체 작업을 되돌립니다.
