/* ReviewFlow · AI 抽取建议（BYOK）前端 —— 建议卡 + 逐条确认流 + 设置页（任务 B7）。
 *
 * 依据：B4 设计包 v1.1（治理 P1-P10）、B6_review 六项裁决（Q4 字段映射一期方案）、
 * B2 接入点勘察（extraction_workbench.js 范本 / workflow_evidence 控件集 / CbPdf 跳页）。
 * 后端：feat/ai-extract 的 custom_backend/ai_suggest_api.py（本模块零后端改动）。
 *
 * 结构（追加式接入，与主应用互不影响）：
 *   - 十语言词条自包含注册（RFLang.register，中文为源串，zh/en/fr/ru/es/ja/pt/de/sr/ko）；
 *   - 样式随模块注入；初始化与全部回调自捕获异常（单点故障）；
 *   - 编码页：工具条「AI 建议」按钮（.wf-queue-tools 内）+ 每字段一张
 *     details.rf-ai-suggest[data-field]（已映射字段插在对应维度卡 .wf-evidence 之后，
 *     未映射字段置于 #cbForms 末尾），挂 reviewflow:coding-form-loaded 与
 *     #cbForms childList MutationObserver；canonical（共享提交证据）模式下与
 *     #rfExtractPanel 同规则隐藏；
 *   - 确认流（P5）：采纳＝把建议值填入映射维度 .cb-v 控件（单选/多选/填空三类）或
 *     复制到剪贴板，随后 POST decision；绝不直接保存，用户仍走既有「保存本篇」；
 *     未决状态不产生 dirty（dirty 由既有控件承担），不注册额外 pendingWork；
 *   - 设置页：#wf-panel-ml 追加「个人钥匙（全局）」卡；#wf-panel-coding 追加
 *     「任务 AI 设置（owner）/成员偏好/审计与报告」卡；字段映射（Q4 一期）存
 *     localStorage（per task+user，键 rfAiFieldMap.{task}.{user}）；
 *   - R1 无 Key 零影响：判定信息一次拉取（任务 settings GET；成员 403 时回落
 *     shared-key 改为 GET status（参与者可读，B7 审查修复：personal 模式成员可见）+ 个人 key GET
 *     + 本人 preferences GET），失败静默退出；
 *     任务未启用或无可用钥匙时编码页零渲染（判定后不再发起任何请求）；
 *   - P1 身份留痕：每条建议卡带 provider · model · prompt_version 角标；
 *     共享模式常驻披露行「本任务由负责人启用了 AI（provider），请求以任务名义发出」。
 *
 * 渲染红线：所有动态数据经 el()/textContent + translate:'no'，绝不 innerHTML。
 */
(function () {
  'use strict';
  try {
    if (typeof document === 'undefined' || typeof T !== 'function' ||
        typeof el !== 'function' || typeof api !== 'function' ||
        typeof RFLang === 'undefined') return;

    /* =====================================================================
     * 一、十语言词条（模块自包含；列序 zh-CN/en/fr/ru/es/ja/pt/de/sr/ko）
     * ===================================================================== */
    RFLang.register([
      ["保存", "Save", "Enregistrer", "Сохранить", "Guardar", "保存", "Salvar", "Speichern", "Сачувај", "저장"],
      ["取消", "Cancel", "Annuler", "Отмена", "Cancelar", "キャンセル", "Cancelar", "Abbrechen", "Откажи", "취소"],
      ["删除", "Delete", "Supprimer", "Удалить", "Eliminar", "削除", "Excluir", "Löschen", "Обриши", "삭제"],
      ["关闭", "Close", "Fermer", "Закрыть", "Cerrar", "閉じる", "Fechar", "Schließen", "Затвори", "닫기"],
      ["刷新", "Refresh", "Actualiser", "Обновить", "Actualizar", "更新", "Atualizar", "Aktualisieren", "Освежи", "새로 고침"],
      ["确认", "Confirm", "Confirmer", "Подтвердить", "Confirmar", "確認", "Confirmar", "Bestätigen", "Потврди", "확인"],
      ["重试", "Retry", "Réessayer", "Повторить", "Reintentar", "再試行", "Tentar novamente", "Erneut versuchen", "Покушај поново", "다시 시도"],
      ["已保存。", "Saved.", "Enregistré.", "Сохранено.", "Guardado.", "保存しました。", "Salvo.", "Gespeichert.", "Сачувано.", "저장되었습니다."],
      ["已删除。", "Deleted.", "Supprimé.", "Удалено.", "Eliminado.", "削除しました。", "Excluído.", "Gelöscht.", "Обрисано.", "삭제되었습니다."],
      ["已复制。", "Copied.", "Copié.", "Скопировано.", "Copiado.", "コピーしました。", "Copiado.", "Kopiert.", "Копирано.", "복사되었습니다."],
      ["请先选择任务。", "Select a task first.", "Veuillez d’abord sélectionner une tâche.", "Сначала выберите задачу.", "Seleccione primero una tarea.", "先にタスクを選択してください。", "Selecione uma tarefa primeiro.", "Wählen Sie zuerst eine Aufgabe aus.", "Најпре изаберите задатак.", "먼저 작업을 선택하세요."],
      ["样本量", "Sample size", "Taille de l’échantillon", "Размер выборки", "Tamaño de la muestra", "サンプルサイズ", "Tamanho da amostra", "Stichprobengröße", "Величина узорка", "표본 크기"],
      ["研究设计", "Study design", "Design de l’étude", "Дизайн исследования", "Diseño del estudio", "研究デザイン", "Desenho do estudo", "Studiendesign", "Дизајн студије", "연구 설계"],
      ["国家/场景", "Country/setting", "Pays/contexte", "Страна/условия", "País/escenario", "国・設定環境", "País/contexto", "Land/Setting", "Земља/контекст", "국가/상황"],
      ["发表年份", "Publication year", "Année de publication", "Год публикации", "Año de publicación", "出版年", "Ano de publicação", "Erscheinungsjahr", "Година објављивања", "출판 연도"],
      ["资助来源", "Funding source", "Source de financement", "Источник финансирования", "Fuente de financiación", "資金源", "Fonte de financiamento", "Finanzierungsquelle", "Извор финансирања", "자금 출처"],
      ["纳入标准要点", "Inclusion criteria summary", "Points clés des critères d’inclusion", "Ключевые положения критериев включения", "Resumen de los criterios de inclusión", "組み入れ基準の要点", "Resumo dos critérios de inclusão", "Zusammenfassung der Einschlusskriterien", "Кључне тачке критеријума укључења", "선정 기준 요점"],
      ["研究目的", "Study objective", "Objectif de l’étude", "Цель исследования", "Objetivo del estudio", "研究目的", "Objetivo do estudo", "Studienziel", "Циљ студије", "연구 목적"],
      ["AI 建议", "AI suggestions", "Suggestions IA", "ИИ-подсказки", "Sugerencias de IA", "AI提案", "Sugestões de IA", "KI-Vorschläge", "АИ предлози", "AI 제안"],
      ["AI 建议生成中…", "Generating AI suggestions…", "Génération des suggestions IA…", "Генерация ИИ-подсказок…", "Generando sugerencias de IA…", "AI提案を生成中…", "Gerando sugestões de IA…", "KI-Vorschläge werden generiert…", "Генерисање АИ предлога…", "AI 제안 생성 중…"],
      ["定位原文", "Locate in source", "Localiser dans la source", "Найти в первоисточнике", "Localizar en la fuente", "原文で確認", "Localizar na fonte", "Im Original finden", "Пронађи у извору", "원문 위치 보기"],
      ["第 {0} 页", "Page {0}", "Page {0}", "Стр. {0}", "Página {0}", "{0} ページ", "Página {0}", "S. {0}", "Страна {0}", "{0}쪽"],
      ["采纳", "Adopt", "Adopter", "Принять", "Adoptar", "採用", "Adotar", "Übernehmen", "Прихвати", "채택"],
      ["编辑后采纳", "Edit then adopt", "Modifier puis adopter", "Изменить и принять", "Editar y adoptar", "編集して採用", "Editar e adotar", "Bearbeiten und übernehmen", "Измени и прихвати", "수정 후 채택"],
      ["忽略", "Ignore", "Ignorer", "Отклонить", "Ignorar", "無視", "Ignorar", "Verwerfen", "Игнориши", "무시"],
      ["复制", "Copy", "Copier", "Копировать", "Copiar", "コピー", "Copiar", "Kopieren", "Копирај", "복사"],
      ["已采纳", "Adopted", "Adopté", "Принято", "Adoptado", "採用済み", "Adotado", "Übernommen", "Прихваћено", "채택됨"],
      ["已编辑", "Edited", "Modifié", "Изменено", "Editado", "編集済み", "Editado", "Bearbeitet", "Измењено", "수정됨"],
      ["已忽略", "Ignored", "Ignoré", "Отклонено", "Ignorado", "無視済み", "Ignorado", "Verworfen", "Игнорисано", "무시됨"],
      ["未处理", "Pending", "En attente", "Ожидает решения", "Pendiente", "未処理", "Pendente", "Ausstehend", "Нерешено", "대기 중"],
      ["建议值", "Suggested value", "Valeur suggérée", "Предложенное значение", "Valor sugerido", "提案値", "Valor sugerido", "Vorschlagswert", "Предложена вредност", "제안 값"],
      ["原文引文", "Source quote", "Citation de la source", "Цитата из первоисточника", "Cita de la fuente", "原文引用", "Citação da fonte", "Quelle", "Цитат из извора", "원문 인용"],
      ["用量：输入 {0} · 输出 {1}", "Usage: {0} input · {1} output tokens", "Usage : {0} jetons en entrée · {1} en sortie", "Использование: вход {0} · выход {1}", "Uso: {0} de entrada · {1} de salida", "使用量：入力 {0} · 出力 {1}", "Uso: {0} de entrada · {1} de saída", "Verbrauch: {0} Eingabe · {1} Ausgabe", "Потрошња: улаз {0} · излаз {1}", "사용량: 입력 {0} · 출력 {1}"],
      ["本任务由负责人启用了 AI（{0}），请求以任务名义发出。", "AI has been enabled for this task by the owner ({0}); requests are sent on behalf of the task.", "L’IA a été activée pour cette tâche par le responsable ({0}) ; les requêtes sont envoyées au nom de la tâche.", "ИИ включён для этой задачи владельцем ({0}); запросы отправляются от имени задачи.", "El responsable ha activado la IA para esta tarea ({0}); las solicitudes se envían en nombre de la tarea.", "タスク所有者が AI を有効化しています（{0}）。リクエストはタスク名義で送信されます。", "A IA foi ativada nesta tarefa pelo responsável ({0}); as requisições são enviadas em nome da tarefa.", "Die KI wurde für diese Aufgabe vom Verantwortlichen aktiviert ({0}); Anfragen werden im Namen der Aufgabe gesendet.", "АИ је за овај задатак укључио власник ({0}); захтеви се шаљу у име задатка.", "이 작업에 AI가 소유자에 의해 활성화되었습니다({0}). 요청은 작업 명의로 전송됩니다."],
      ["尚无建议。", "No suggestions yet.", "Aucune suggestion pour le moment.", "Подсказок пока нет.", "Aún no hay sugerencias.", "提案はまだありません。", "Ainda não há sugestões.", "Noch keine Vorschläge.", "Још нема предлога.", "아직 제안이 없습니다."],
      ["点击“AI 建议”为本篇生成建议。", "Click “AI suggestions” to generate suggestions for this article.", "Cliquez sur « Suggestions IA » pour générer des suggestions pour cet article.", "Нажмите «ИИ-подсказки», чтобы создать подсказки для этой статьи.", "Haz clic en «Sugerencias de IA» para generar sugerencias de este artículo.", "「AI提案」をクリックするとこの文献の提案を生成します。", "Clique em “Sugestões de IA” para gerar sugestões para este artigo.", "Klicken Sie auf „KI-Vorschläge“, um Vorschläge für diesen Artikel zu generieren.", "Кликните „АИ предлози“ да генеришете предлоге за овај чланак.", "“AI 제안”을 클릭하면 이 문헌의 제안을 생성합니다."],
      ["未配置可用的 AI 钥匙，请先在设置页配置。", "No AI key is configured. Configure one on the settings page first.", "Aucune clé IA n’est configurée. Configurez-en une dans les paramètres.", "Ключ ИИ не настроен. Сначала настройте его на странице параметров.", "No hay ninguna clave de IA configurada. Configúrela primero en los ajustes.", "AIキーが未設定です。先に設定ページで設定してください。", "Nenhuma chave de IA configurada. Configure-a primeiro na página de configurações.", "Es ist kein KI-Schlüssel konfiguriert. Richten Sie ihn zuerst auf der Einstellungsseite ein.", "Није подешен АИ кључ. Прво га подесите на страници подешавања.", "AI 키가 구성되지 않았습니다. 설정 페이지에서 먼저 구성하세요."],
      ["该文献既无全文 PDF 也无摘要，无法生成建议。", "This article has neither a full-text PDF nor an abstract, so no suggestions can be generated.", "Cet article n’a ni PDF en texte intégral ni résumé ; aucune suggestion ne peut être générée.", "У этой публикации нет ни полного текста, ни аннотации — подсказки создать нельзя.", "Este artículo no tiene ni PDF de texto completo ni resumen, por lo que no se pueden generar sugerencias.", "この文献は全文PDFも抄録もないため、提案を生成できません。", "Este artigo não tem PDF completo nem resumo, portanto não é possível gerar sugestões.", "Dieser Artikel hat weder Volltext-PDF noch Abstract; Vorschläge können nicht generiert werden.", "Овај чланак нема ни пун текст у PDF-у ни сажетак, па предлози не могу да се генеришу.", "이 문헌에는 전문 PDF와 초록이 모두 없어 제안을 생성할 수 없습니다."],
      ["任务未启用 AI 建议。", "AI suggestions are not enabled for this task.", "Les suggestions IA ne sont pas activées pour cette tâche.", "ИИ-подсказки не включены для этой задачи.", "Las sugerencias de IA no están activadas para esta tarea.", "このタスクではAI提案が有効になっていません。", "As sugestões de IA não estão ativadas nesta tarefa.", "KI-Vorschläge sind für diese Aufgabe nicht aktiviert.", "АИ предлози нису укључени за овај задатак.", "이 작업에는 AI 제안이 활성화되어 있지 않습니다."],
      ["以下字段未开放：{0}", "The following fields are not enabled: {0}", "Les champs suivants ne sont pas activés : {0}", "Следующие поля не разрешены: {0}", "Los siguientes campos no están habilitados: {0}", "次のフィールドは許可されていません：{0}", "Os seguintes campos não estão habilitados: {0}", "Die folgenden Felder sind nicht freigegeben: {0}", "Следећа поља нису отворена: {0}", "다음 필드는 활성화되어 있지 않습니다: {0}"],
      ["{0} 条建议因缺少原文引文被丢弃。", "{0} suggestions were discarded for lacking a source quote.", "{0} suggestions ont été écartées faute de citation de la source.", "{0} подсказок отброшено из-за отсутствия цитаты.", "{0} sugerencias se descartaron por no incluir cita de la fuente.", "原文引用がないため {0} 件の提案を破棄しました。", "{0} sugestões foram descartadas por falta de citação da fonte.", "{0} Vorschläge wurden wegen fehlender Quellenzitate verworfen.", "{0} предлога је одбачено због недостатка цитата.", "출처 인용이 없어 제안 {0}건이 폐기되었습니다."],
      ["原文过长，已截断 {0} 字。", "The source text was too long; {0} characters were truncated.", "Le texte source était trop long ; {0} caractères ont été tronqués.", "Исходный текст был слишком длинным; усечено {0} символов.", "El texto fuente era demasiado largo; se truncaron {0} caracteres.", "原文が長すぎるため、{0} 文字を切り詰めました。", "O texto original era longo demais; {0} caracteres foram truncados.", "Der Ausgangstext war zu lang; {0} Zeichen wurden abgeschnitten.", "Изворни текст је предугач; скраћено је {0} знакова.", "원문이 너무 길어 {0}자를 잘라냈습니다."],
      ["AI 服务调用失败：{0}", "AI provider call failed: {0}", "Échec de l’appel au fournisseur d’IA : {0}", "Не удалось вызвать ИИ-провайдера: {0}", "Error al llamar al proveedor de IA: {0}", "AIプロバイダーの呼び出しに失敗しました：{0}", "Falha na chamada do provedor de IA: {0}", "Aufruf des KI-Anbieters fehlgeschlagen: {0}", "Позив АИ провајдера није успео: {0}", "AI 공급자 호출 실패: {0}"],
      ["PDF 尚未加载，无法跳转。", "The PDF has not loaded, so the page cannot be opened.", "Le PDF n’est pas chargé ; impossible d’ouvrir la page.", "PDF не загружен, переход невозможен.", "El PDF no se ha cargado; no se puede saltar a la página.", "PDFが未読み込みのため、移動できません。", "O PDF não foi carregado; não é possível pular para a página.", "Das PDF ist nicht geladen; die Seite kann nicht geöffnet werden.", "PDF није учитан, па није могуће прећи на страну.", "PDF가 로드되지 않아 이동할 수 없습니다."],
      ["建议值与该维度的选项不匹配，请手动选择或使用“编辑后采纳”。", "The suggested value does not match this dimension’s options. Choose manually or use “Edit then adopt”.", "La valeur suggérée ne correspond pas aux options de cette dimension. Choisissez manuellement ou utilisez « Modifier puis adopter ».", "Предложенное значение не совпадает с вариантами этой размерности. Выберите вручную или используйте «Изменить и принять».", "El valor sugerido no coincide con las opciones de esta dimensión. Elija manualmente o use «Editar y adoptar».", "提案値がこの次元の選択肢と一致しません。手動で選ぶか「編集して採用」を使用してください。", "O valor sugerido não corresponde às opções desta dimensão. Escolha manualmente ou use “Editar e adotar”.", "Der Vorschlagswert passt nicht zu den Optionen dieser Dimension. Wählen Sie manuell oder nutzen Sie „Bearbeiten und übernehmen“.", "Предложена вредност не одговара опцијама ове димензије. Изаберите ручно или користите „Измени и прихвати“.", "제안 값이 이 차원의 옵션과 일치하지 않습니다. 직접 선택하거나 “수정 후 채택”을 사용하세요."],
      ["已复制，请粘贴到对应的编码字段。", "Copied. Paste it into the corresponding coding field.", "Copié. Collez-le dans le champ de codage correspondant.", "Скопировано. Вставьте в соответствующее поле кодирования.", "Copiado. Péguelo en el campo de codificación correspondiente.", "コピーしました。対応するコーディング項目に貼り付けてください。", "Copiado. Cole no campo de codificação correspondente.", "Kopiert. Fügen Sie es in das entsprechende Kodierfeld ein.", "Копирано. Налепите га у одговарајуће поље кодирања.", "복사되었습니다. 해당 코딩 필드에 붙여넣으세요."],
      ["复制失败，请手动选择建议值。", "Copy failed. Select the suggested value manually.", "Échec de la copie. Sélectionnez manuellement la valeur suggérée.", "Не удалось скопировать. Выделите значение вручную.", "Error al copiar. Seleccione manualmente el valor sugerido.", "コピーできませんでした。提案値を手動で選択してください。", "Falha ao copiar. Selecione manualmente o valor sugerido.", "Kopieren fehlgeschlagen. Wählen Sie den Wert manuell aus.", "Копирање није успело. Изаберите вредност ручно.", "복사에 실패했습니다. 제안 값을 직접 선택하세요."],
      ["该字段未映射到编码维度，采纳将复制建议值。", "This field is not mapped to a coding dimension; adopting copies the suggested value.", "Ce champ n’est pas associé à une dimension de codage ; l’adoption copie la valeur suggérée.", "Это поле не сопоставлено с размерностью кодирования; при принятии значение копируется.", "Este campo no está asociado a una dimensión de codificación; al adoptar se copia el valor sugerido.", "このフィールドはコーディング次元にマッピングされていません。採用すると提案値をコピーします。", "Este campo não está mapeado a uma dimensão de codificação; adotar copia o valor sugerido.", "Dieses Feld ist keiner Kodierungsdimension zugeordnet; beim Übernehmen wird der Vorschlagswert kopiert.", "Ово поље није мапирано на димензију кодирања; прихватањем се копира предложена вредност.", "이 필드는 코딩 차원에 매핑되지 않았습니다. 채택하면 제안 값이 복사됩니다."],
      ["建议已更新。", "Suggestion updated.", "Suggestion mise à jour.", "Подсказка обновлена.", "Sugerencia actualizada.", "提案を更新しました。", "Sugestão atualizada.", "Vorschlag aktualisiert.", "Предлог је ажуриран.", "제안이 업데이트되었습니다."],
      ["任务未开放任何建议字段，请负责人在任务 AI 设置中勾选字段白名单。", "No suggestion fields are enabled; the owner must check fields in the task AI settings whitelist.", "Aucun champ de suggestion n’est activé ; le responsable doit cocher des champs dans la liste blanche des paramètres IA.", "Не разрешено ни одно поле подсказок; владелец должен отметить поля в белом списке настроек ИИ.", "No hay campos de sugerencia habilitados; el responsable debe marcar campos en la lista blanca de los ajustes de IA.", "提案フィールドが許可されていません。所有者がタスクAI設定の許可リストでチェックしてください。", "Nenhum campo de sugestão está habilitado; o responsável deve marcar campos na lista de permitidos das configurações de IA.", "Keine Vorschlagsfelder freigegeben; der Verantwortliche muss Felder in der Whitelist der KI-Einstellungen markieren.", "Ниједно поље предлога није отворено; власник мора означити поља у белој листи АИ подешавања.", "활성화된 제안 필드가 없습니다. 소유자가 작업 AI 설정 화이트리스트에서 필드를 선택해야 합니다."],
      ["AI 抽取建议", "AI extraction suggestions", "Suggestions d’extraction IA", "ИИ-подсказки для извлечения", "Sugerencias de extracción de IA", "AI抽出提案", "Sugestões de extração por IA", "KI-Extraktionsvorschläge", "АИ предлози за извлачење", "AI 추출 제안"],
      ["个人钥匙（全局）", "Personal key (global)", "Clé personnelle (globale)", "Личный ключ (глобальный)", "Clave personal (global)", "個人キー（グローバル）", "Chave pessoal (global)", "Persönlicher Schlüssel (global)", "Лични кључ (глобално)", "개인 키(전역)"],
      ["未配置", "Not configured", "Non configurée", "Не настроен", "Sin configurar", "未設定", "Não configurada", "Nicht konfiguriert", "Није подешен", "미구성"],
      ["当前：{0} · 来源：{1}", "Current: {0} · source: {1}", "Actuelle : {0} · source : {1}", "Текущий: {0} · источник: {1}", "Actual: {0} · origen: {1}", "現在：{0} ・ 取得元：{1}", "Atual: {0} · origem: {1}", "Aktuell: {0} · Quelle: {1}", "Тренутни: {0} · извор: {1}", "현재: {0} · 출처: {1}"],
      ["由管理员通过环境变量配置，仅可在服务器端更换或移除。", "Configured by the administrator via an environment variable; it can only be changed or removed on the server.", "Configurée par l’administrateur via une variable d’environnement ; elle ne peut être modifiée ou supprimée que sur le serveur.", "Настроен администратором через переменную окружения; изменить или удалить его можно только на сервере.", "Configurada por el administrador mediante una variable de entorno; solo puede cambiarse o eliminarse en el servidor.", "管理者が環境変数で設定しています。変更・削除はサーバー側でのみ可能です。", "Configurada pelo administrador via variável de ambiente; só pode ser alterada ou removida no servidor.", "Vom Administrator über eine Umgebungsvariable konfiguriert; Ändern oder Entfernen ist nur auf dem Server möglich.", "Подешен од администратора преко променљиве окружења; може се променити или уклонити само на серверу.", "관리자가 환경 변수로 구성했습니다. 서버에서만 변경하거나 제거할 수 있습니다."],
      ["服务商", "Provider", "Fournisseur", "Провайдер", "Proveedor", "プロバイダー", "Provedor", "Anbieter", "Провајдер", "공급자"],
      ["API Key（仅保存在数据目录）", "API key (stored only in the data directory: your device on desktop, the server in server mode)", "Clé API (stockée uniquement dans le répertoire de données : appareil en version bureautique, serveur en mode serveur)", "API-ключ (хранится только в каталоге данных: устройство в настольной версии, сервер в серверном режиме)", "Clave API (guardada solo en el directorio de datos: su equipo en la versión de escritorio, el servidor en modo servidor)", "APIキー（データディレクトリにのみ保存：デスクトップ版=端末、サーバー版=サーバー）", "Chave de API (armazenada apenas no diretório de dados: dispositivo no modo desktop, servidor no modo servidor)", "API-Schlüssel (nur im Datenverzeichnis gespeichert: Gerät im Desktopmodus, Server im Servermodus)", "API кључ (чува се само у директоријуму података: уређај у радној верзији, сервер у серверском режиму)", "API 키(데이터 디렉터리에만 저장: 데스크톱=기기, 서버 모드=서버)"],
      ["接口地址 base_url（兼容 OpenAI 协议，可指向本地服务）", "Base URL (OpenAI-compatible; can point to a local service)", "URL de base (compatible OpenAI ; peut pointer vers un service local)", "Базовый URL (совместим с OpenAI; может указывать на локальный сервис)", "URL base (compatible con OpenAI; puede apuntar a un servicio local)", "ベースURL（OpenAI互換・ローカルサービスも可）", "URL base (compatível com OpenAI; pode apontar para um serviço local)", "Basis-URL (OpenAI-kompatibel; kann auf einen lokalen Dienst zeigen)", "Базни URL (компатибилан са OpenAI; може показивати на локални сервис)", "기본 URL(OpenAI 호환, 로컬 서비스 지정 가능)"],
      ["模型名（如 gpt-4o-mini）", "Model name (e.g. gpt-4o-mini)", "Nom du modèle (p. ex. gpt-4o-mini)", "Название модели (напр. gpt-4o-mini)", "Nombre del modelo (p. ej. gpt-4o-mini)", "モデル名（例：gpt-4o-mini）", "Nome do modelo (ex.: gpt-4o-mini)", "Modellname (z. B. gpt-4o-mini)", "Назив модела (нпр. gpt-4o-mini)", "모델 이름(예: gpt-4o-mini)"],
      ["保存前验证连通性", "Verify connectivity before saving", "Vérifier la connectivité avant d’enregistrer", "Проверить соединение перед сохранением", "Verificar la conectividad antes de guardar", "保存前に接続を検証", "Verificar a conectividade antes de salvar", "Verbindung vor dem Speichern prüfen", "Провери повезаност пре чувања", "저장 전 연결 확인"],
      ["保存钥匙", "Save key", "Enregistrer la clé", "Сохранить ключ", "Guardar clave", "キーを保存", "Salvar chave", "Schlüssel speichern", "Сачувај кључ", "키 저장"],
      ["测试连接", "Test connection", "Tester la connexion", "Проверить соединение", "Probar conexión", "接続テスト", "Testar conexão", "Verbindung testen", "Тестирај везу", "연결 테스트"],
      ["删除钥匙", "Delete key", "Supprimer la clé", "Удалить ключ", "Eliminar clave", "キーを削除", "Excluir chave", "Schlüssel löschen", "Обриши кључ", "키 삭제"],
      ["连接正常 · 延迟 {0} ms", "Connection OK · latency {0} ms", "Connexion réussie · latence {0} ms", "Соединение в порядке · задержка {0} мс", "Conexión correcta · latencia {0} ms", "接続正常・レイテンシ {0} ms", "Conexão OK · latência {0} ms", "Verbindung OK · Latenz {0} ms", "Веза је у реду · кашњење {0} ms", "연결 정상 · 지연 {0}ms"],
      ["钥匙仅保存在数据目录（桌面版为本机、服务器版为服务器；权限 0600 尽力而为，一期不加密），不会保存在浏览器、任务目录或导出包。", "The key is stored only in this device’s data directory (0600 permissions, best effort); it never enters the browser, the task folder or export packages.", "La clé est stockée uniquement dans le répertoire de données de l’appareil (permissions 0600, au mieux possible) ; elle n’entre jamais dans le navigateur, le dossier de tâche ni les exports.", "Ключ хранится только в каталоге данных устройства (права 0600, насколько возможно); он не попадает в браузер, папку задачи или экспортные пакеты.", "La clave se guarda solo en el directorio de datos del equipo (permisos 0600, con carácter tentativo); nunca entra en el navegador, la carpeta de la tarea ni los paquetes exportados.", "キーはこの端末のデータディレクトリにのみ保存されます（権限0600、ベストエフォート）。ブラウザー、タスクフォルダー、書き出しパッケージには入りません。", "A chave é armazenada apenas no diretório de dados do dispositivo (permissões 0600, best effort); nunca entra no navegador, na pasta da tarefa ou em pacotes de exportação.", "Der Schlüssel liegt nur im Datenverzeichnis des Geräts (Rechte 0600, nach Möglichkeit); er gelangt nie in den Browser, den Aufgabenordner oder Exportpakete.", "Кључ се чува само у директоријуму података уређаја (дозволе 0600, најбоље могуће); никад не улази у прегледач, фасциклу задатка или извозне пакете.", "키는 이 기기의 데이터 디렉터리에만 저장됩니다(권한 0600, 최대한 보장). 브라우저, 작업 폴더, 내보내기 패키지에는 절대 들어가지 않습니다."],
      ["任务 AI 设置（负责人）", "Task AI settings (owner)", "Paramètres IA de la tâche (responsable)", "Настройки ИИ задачи (владелец)", "Ajustes de IA de la tarea (responsable)", "タスクAI設定（所有者）", "Configurações de IA da tarefa (responsável)", "Aufgaben-KI-Einstellungen (Verantwortlicher)", "Подешавања АИ за задатак (власник)", "작업 AI 설정(소유자)"],
      ["启用 AI 抽取建议（默认关闭）", "Enable AI extraction suggestions (off by default)", "Activer les suggestions d’extraction IA (désactivé par défaut)", "Включить ИИ-подсказки для извлечения (по умолчанию выключено)", "Activar sugerencias de extracción de IA (desactivado por defecto)", "AI抽出提案を有効化（既定は無効）", "Ativar sugestões de extração por IA (desativado por padrão)", "KI-Extraktionsvorschläge aktivieren (standardmäßig aus)", "Укључи АИ предлоге за извлачење (подразумевано искључено)", "AI 추출 제안 활성화(기본 꺼짐)"],
      ["钥匙模式", "Key mode", "Mode de clé", "Режим ключа", "Modo de clave", "キーモード", "Modo de chave", "Schlüsselmodus", "Режим кључа", "키 모드"],
      ["个人钥匙（各成员自带）", "Personal keys (each member brings their own)", "Clés personnelles (chaque membre fournit la sienne)", "Личные ключи (каждый участник использует свой)", "Claves personales (cada miembro aporta la suya)", "個人キー（メンバー各自で用意）", "Chaves pessoais (cada membro usa a sua)", "Persönliche Schlüssel (jedes Mitglied bringt seinen mit)", "Лични кључеви (сваки члан носи свој)", "개인 키(각 구성원이 지참)"],
      ["任务共享钥匙（全员可用）", "Task shared key (available to everyone)", "Clé partagée de tâche (disponible pour tous)", "Общий ключ задачи (доступен всем)", "Clave compartida de tarea (disponible para todos)", "タスク共有キー（全員が利用可）", "Chave compartilhada da tarefa (disponível para todos)", "Gemeinsamer Aufgabenschlüssel (für alle verfügbar)", "Заједнички кључ задатка (доступан свима)", "작업 공유 키(전체 사용 가능)"],
      ["共享钥匙已启用：{0}", "Shared key enabled: {0}", "Clé partagée activée : {0}", "Общий ключ включён: {0}", "Clave compartida habilitada: {0}", "共有キーは有効：{0}", "Chave compartilhada ativada: {0}", "Gemeinsamer Schlüssel aktiv: {0}", "Заједнички кључ је укључен: {0}", "공유 키 활성화됨: {0}"],
      ["未配置共享钥匙。", "No shared key configured.", "Aucune clé partagée configurée.", "Общий ключ не настроен.", "No hay clave compartida configurada.", "共有キーは未設定です。", "Nenhuma chave compartilhada configurada.", "Kein gemeinsamer Schlüssel konfiguriert.", "Заједнички кључ није подешен.", "공유 키가 구성되지 않았습니다."],
      ["保存共享钥匙", "Save shared key", "Enregistrer la clé partagée", "Сохранить общий ключ", "Guardar clave compartida", "共有キーを保存", "Salvar chave compartilhada", "Gemeinsamen Schlüssel speichern", "Сачувај заједнички кључ", "공유 키 저장"],
      ["删除共享钥匙", "Delete shared key", "Supprimer la clé partagée", "Удалить общий ключ", "Eliminar clave compartida", "共有キーを削除", "Excluir chave compartilhada", "Gemeinsamen Schlüssel löschen", "Обриши заједнички кључ", "공유 키 삭제"],
      ["字段白名单（建议仅覆盖勾选字段）", "Field whitelist (suggestions cover checked fields only)", "Liste blanche de champs (les suggestions ne couvrent que les champs cochés)", "Белый список полей (подсказки только для отмеченных полей)", "Lista blanca de campos (las sugerencias solo cubren los campos marcados)", "フィールド許可リスト（チェックした項目のみ提案）", "Lista de campos permitidos (sugestões cobrem apenas os marcados)", "Feld-Whitelist (Vorschläge nur für markierte Felder)", "Бела листа поља (предлози само за означена поља)", "필드 화이트리스트(선택한 필드만 제안)"],
      ["综述背景提示语（选填，随建议请求发送给模型）", "Review context prompt (optional, sent to the model with each request)", "Contexte de synthèse (facultatif, envoyé au modèle avec chaque requête)", "Контекст обзора (необязательно, отправляется модели с каждым запросом)", "Contexto de revisión (opcional, se envía al modelo con cada solicitud)", "レビュー背景プロンプト（任意・リクエストごとにモデルへ送信）", "Contexto da revisão (opcional, enviado ao modelo a cada requisição)", "Review-Kontextprompt (optional, mit jeder Anfrage an das Modell gesendet)", "Контекст прегледа (опционо, шаље се моделу уз сваки захтев)", "리뷰 배경 프롬프트(선택, 요청마다 모델로 전송)"],
      ["{0} / 2000 字", "{0} / 2000 characters", "{0} / 2000 caractères", "{0} / 2000 символов", "{0} / 2000 caracteres", "{0} / 2000 文字", "{0} / 2000 caracteres", "{0} / 2000 Zeichen", "{0} / 2000 знакова", "{0} / 2000자"],
      ["字段映射（建议采纳时自动填入对应编码维度；仅保存在当前浏览器）", "Field mapping (adoption fills the mapped coding dimension; stored in this browser only)", "Association de champs (l’adoption remplit la dimension de codage associée ; stockée uniquement dans ce navigateur)", "Сопоставление полей (при принятии заполняется сопоставленная размерность; хранится только в этом браузере)", "Asignación de campos (al adoptar se rellena la dimensión asignada; se guarda solo en este navegador)", "フィールドマッピング（採用時に対応するコーディング次元へ自動入力・このブラウザーにのみ保存）", "Mapeamento de campos (adotar preenche a dimensão mapeada; salvo apenas neste navegador)", "Feldzuordnung (beim Übernehmen wird die zugeordnete Dimension gefüllt; nur in diesem Browser gespeichert)", "Мапирање поља (прихватањем се попуњава мапирана димензија; чува се само у овом прегледачу)", "필드 매핑(채택 시 매핑된 코딩 차원에 자동 입력, 이 브라우저에만 저장)"],
      ["不映射", "Not mapped", "Non associé", "Не сопоставлено", "Sin asignar", "マッピングしない", "Não mapeado", "Nicht zugeordnet", "Није мапирано", "매핑 안 함"],
      ["保存任务 AI 设置", "Save task AI settings", "Enregistrer les paramètres IA de la tâche", "Сохранить настройки ИИ задачи", "Guardar ajustes de IA de la tarea", "タスクAI設定を保存", "Salvar configurações de IA da tarefa", "Aufgaben-KI-Einstellungen speichern", "Сачувај АИ подешавања задатка", "작업 AI 설정 저장"],
      ["设置已被修改（乐观锁冲突），请刷新后重试。", "The settings were changed elsewhere (version conflict). Refresh and retry.", "Les paramètres ont été modifiés ailleurs (conflit de version). Actualisez puis réessayez.", "Настройки были изменены (конфликт версий). Обновите и повторите.", "Los ajustes fueron modificados (conflicto de versión). Actualice y reintente.", "設定が他で変更されました（バージョン競合）。更新して再試行してください。", "As configurações foram alteradas (conflito de versão). Atualize e tente novamente.", "Die Einstellungen wurden geändert (Versionskonflikt). Aktualisieren und erneut versuchen.", "Подешавања су измењена (сукоб верзија). Освежите и покушајте поново.", "설정이 다른 곳에서 수정되었습니다(버전 충돌). 새로 고침 후 다시 시도하세요."],
      ["成员偏好（本人）", "Member preferences (yours)", "Préférences du membre (les vôtres)", "Настройки участника (личные)", "Preferencias del miembro (las suyas)", "メンバー設定（自分用）", "Preferências do membro (suas)", "Mitgliedseinstellungen (Ihre)", "Подешавања члана (ваша)", "구성원 기본 설정(본인)"],
      ["我想用自己的 Key（未配置时自动使用任务共享钥匙）", "I want to use my own key (falls back to the task shared key when not configured)", "Je veux utiliser ma propre clé (sinon la clé partagée de la tâche est utilisée)", "Использовать свой ключ (при отсутствии — общий ключ задачи)", "Quiero usar mi propia clave (si no está configurada, se usa la clave compartida de la tarea)", "自分のキーを使う（未設定の場合はタスク共有キーを使用）", "Quero usar minha própria chave (sem chave configurada, usa a compartilhada da tarefa)", "Ich möchte meinen eigenen Schlüssel verwenden (ohne Konfiguration wird der gemeinsame Schlüssel genutzt)", "Желим да користим свој кључ (без подешеног користи се заједнички кључ задатка)", "내 키를 사용하고 싶습니다(미구성 시 작업 공유 키 사용)"],
      ["触发方式", "Trigger", "Déclencheur", "Способ запуска", "Modo de disparo", "トリガー方式", "Gatilho", "Auslöser", "Начин покретања", "트리거 방식"],
      ["手动（点击按钮生成）", "Manual (click the button)", "Manuel (cliquer sur le bouton)", "Вручную (по нажатию кнопки)", "Manual (hacer clic en el botón)", "手動（ボタンで生成）", "Manual (clicar no botão)", "Manuell (Schaltfläche anklicken)", "Ручно (кликом на дугме)", "수동(버튼 클릭)"],
      ["进入文献自动获取", "Fetch automatically when an article opens", "Récupérer automatiquement à l’ouverture d’un article", "Получать автоматически при открытии публикации", "Obtener automáticamente al abrir un artículo", "文献を開いたら自動取得", "Buscar automaticamente ao abrir um artigo", "Beim Öffnen eines Artikels automatisch abrufen", "Аутоматски преузми при отварању чланка", "문서를 열면 자동으로 가져오기"],
      ["建议字段（不选=任务允许的全部字段）", "Suggestion fields (none checked = all fields allowed by the task)", "Champs de suggestion (aucun coché = tous les champs autorisés par la tâche)", "Поля подсказок (ничего не отмечено = все разрешённые задачей поля)", "Campos de sugerencia (ninguno marcado = todos los permitidos por la tarea)", "提案フィールド（未選択＝タスクが許可する全フィールド）", "Campos de sugestão (nenhum marcado = todos os permitidos pela tarefa)", "Vorschlagsfelder (keins markiert = alle von der Aufgabe erlaubten)", "Поља предлога (ништа означено = сва поља која задатак дозвољава)", "제안 필드(선택 없음 = 작업이 허용한 전체 필드)"],
      ["呈现方式", "Presentation", "Affichage", "Представление", "Presentación", "表示形式", "Apresentação", "Darstellung", "Приказ", "표시 방식"],
      ["常驻卡片", "Persistent cards", "Cartes permanentes", "Постоянные карточки", "Tarjetas permanentes", "常時カード", "Cartões persistentes", "Permanente Karten", "Сталне картице", "상시 카드"],
      ["收纳为角标（仅计数，点击展开）", "Collapsed badge (count only; click to expand)", "Badge replié (compteur seul ; cliquer pour déplier)", "Свернуто в значок (только счётчик; развернуть кликом)", "Insignia plegada (solo conteo; clic para expandir)", "バッジに格納（件数のみ・クリックで展開）", "Selo recolhido (apenas contagem; clique para expandir)", "Eingeklapptes Badge (nur Zählung; zum Aufklappen klicken)", "Сакупљено у значку (само број; кликом прошири)", "배지로 접기(건수만 표시, 클릭 시 펼침)"],
      ["保存个人偏好", "Save my preferences", "Enregistrer mes préférences", "Сохранить мои настройки", "Guardar mis preferencias", "個人設定を保存", "Salvar minhas preferências", "Meine Einstellungen speichern", "Сачувај моја подешавања", "내 기본 설정 저장"],
      ["审计与报告", "Audit & reporting", "Audit et rapports", "Аудит и отчётность", "Auditoría e informes", "監査とレポート", "Auditoria e relatórios", "Prüfung & Berichte", "Ревизија и извештаји", "감사 및 보고"],
      ["导出决策记录（CSV）", "Export decision log (CSV)", "Exporter le journal des décisions (CSV)", "Экспорт журнала решений (CSV)", "Exportar registro de decisiones (CSV)", "決定記録を書き出し（CSV）", "Exportar registro de decisões (CSV)", "Entscheidungsprotokoll exportieren (CSV)", "Извези евиденцију одлука (CSV)", "결정 기록 내보내기(CSV)"],
      ["导出决策记录（JSON）", "Export decision log (JSON)", "Exporter le journal des décisions (JSON)", "Экспорт журнала решений (JSON)", "Exportar registro de decisiones (JSON)", "決定記録を書き出し（JSON）", "Exportar registro de decisões (JSON)", "Entscheidungsprotokoll exportieren (JSON)", "Извези евиденцију одлука (JSON)", "결정 기록 내보내기(JSON)"],
      ["生成 Methods 段落", "Generate Methods paragraph", "Générer le paragraphe Méthodes", "Сгенерировать фрагмент Methods", "Generar párrafo de Métodos", "Methods段落を生成", "Gerar parágrafo de Métodos", "Methods-Absatz generieren", "Генериши пасус Methods", "Methods 단락 생성"],
      ["复制中文", "Copy Chinese", "Copier le chinois", "Копировать китайский", "Copiar chino", "中国語をコピー", "Copiar chinês", "Chinesisch kopieren", "Копирај кинески", "중국어 복사"],
      ["复制英文", "Copy English", "Copier l’anglais", "Копировать английский", "Copiar inglés", "英語をコピー", "Copiar inglês", "Englisch kopieren", "Копирај енглески", "영어 복사"],
      ["中文段落", "Chinese paragraph", "Paragraphe chinois", "Китайский фрагмент", "Párrafo en chino", "中国語段落", "Parágrafo em chinês", "Chinesischer Abschnitt", "Кинески пасус", "중국어 단락"],
      ["英文段落", "English paragraph", "Paragraphe anglais", "Английский фрагмент", "Párrafo en inglés", "英語段落", "Parágrafo em inglês", "Englischer Abschnitt", "Енглески пасус", "영어 단락"],
      ["尚无建议记录。", "No suggestion records yet.", "Aucun enregistrement de suggestion pour le moment.", "Записей подсказок пока нет.", "Aún no hay registros de sugerencias.", "提案記録はまだありません。", "Ainda não há registros de sugestões.", "Noch keine Vorschlagsdatensätze.", "Још нема записа предлога.", "아직 제안 기록이 없습니다."],
      ["任务由负责人启用共享钥匙后，成员无需各自配置钥匙。", "Once the owner configures the shared key, members need no key of their own.", "Une fois la clé partagée configurée par le responsable, les membres n’ont pas besoin de clé.", "Когда владелец настроит общий ключ, участникам не нужны собственные ключи.", "Cuando el responsable configure la clave compartida, los miembros no necesitarán la suya.", "所有者が共有キーを設定すると、メンバーは各自のキーが不要になります。", "Depois que o responsável configurar a chave compartilhada, os membros não precisam de chave própria.", "Sobald der Verantwortliche den gemeinsamen Schlüssel eingerichtet hat, brauchen Mitglieder keinen eigenen.", "Када власник подеси заједнички кључ, чланови не требају сопствене кључеве.", "소유자가 공유 키를 구성하면 구성원은 각자 키가 필요 없습니다."],
      ["载入失败：{0}", "Load failed: {0}", "Échec du chargement : {0}", "Не удалось загрузить: {0}", "Error al cargar: {0}", "読み込みに失敗：{0}", "Falha ao carregar: {0}", "Laden fehlgeschlagen: {0}", "Учитавање није успело: {0}", "불러오기 실패: {0}"],
      ["服务商与 API Key 不能为空。", "Provider and API key are required.", "Le fournisseur et la clé API sont obligatoires.", "Укажите провайдера и API-ключ.", "El proveedor y la clave API son obligatorios.", "プロバイダーとAPIキーは必須です。", "O provedor e a chave de API são obrigatórios.", "Anbieter und API-Schlüssel sind erforderlich.", "Провајдер и АПИ кључ су обавезни.", "공급자와 API 키는 필수입니다."],
      ["确认删除个人 AI 钥匙？", "Delete your personal AI key?", "Supprimer votre clé IA personnelle ?", "Удалить ваш личный ИИ-ключ?", "¿Eliminar su clave de IA personal?", "個人のAIキーを削除しますか？", "Excluir sua chave de IA pessoal?", "Ihren persönlichen KI-Schlüssel löschen?", "Обрисати ваш лични АИ кључ?", "개인 AI 키를 삭제할까요?"],
      ["确认删除任务共享钥匙？", "Delete the task shared key?", "Supprimer la clé partagée de la tâche ?", "Удалить общий ключ задачи?", "¿Eliminar la clave compartida de la tarea?", "タスク共有キーを削除しますか？", "Excluir a chave compartilhada da tarefa?", "Den gemeinsamen Aufgabenschlüssel löschen?", "Обрисати заједнички кључ задатка?", "작업 공유 키를 삭제할까요?"],
      ["有建议因引文未能在原文中定位被丢弃。", "Some suggestions were discarded because their quotes could not be located in the source text.", "Des suggestions ont été écartées car leurs citations sont introuvables dans le texte source.", "Часть предложений отброшена: цитаты не найдены в исходном тексте.", "Se descartaron sugerencias porque sus citas no se localizaron en el texto original.", "原文内に引用が見つからなかったため、破棄された提案があります。", "Algumas sugestões foram descartadas porque suas citações não foram localizadas no texto original.", "Einige Vorschläge wurden verworfen, da ihre Zitate im Originaltext nicht gefunden wurden.", "Неки предлози су одбачени јер њихови цитати нису пронађени у изворном тексту.", "원문에서 인용을 찾을 수 없어 버려진 제안이 있습니다."],
      ["已复制并采纳", "Copied & adopted", "Copié et adopté", "Скопировано и принято", "Copiado y adoptado", "コピーして採用", "Copiado e adotado", "Kopiert & übernommen", "Копирано и прихваћено", "복사하고 채택"],
      ["请按 Ctrl+C 复制选中内容后再采纳。", "Press Ctrl+C to copy the selected text, then adopt.", "Appuyez sur Ctrl+C pour copier le texte sélectionné, puis adoptez.", "Нажмите Ctrl+C, чтобы скопировать выделенное, затем примите.", "Pulse Ctrl+C para copiar el texto seleccionado y luego adopte.", "Ctrl+C で選択内容をコピーしてから採用してください。", "Pressione Ctrl+C para copiar o texto selecionado e depois adote.", "Kopieren Sie mit Strg+C den markierten Text und übernehmen Sie dann.", "Притисните Ctrl+C да копирате означени текст, па прихватите.", "Ctrl+C로 선택한 내용을 복사한 뒤 채택하세요."],
      ["自动复制失败：已选中建议值，请按 Ctrl+C，再点击“已复制并采纳”。", "Auto-copy failed: the suggested value is selected — press Ctrl+C, then click \"Copied & adopted\".", "Copie automatique échouée : la valeur suggérée est sélectionnée — appuyez sur Ctrl+C, puis cliquez sur « Copié et adopté ».", "Автокопирование не удалось: предложенное значение выделено — нажмите Ctrl+C, затем нажмите «Скопировано и принято».", "La copia automática falló: el valor sugerido está seleccionado; pulse Ctrl+C y luego “Copiado y adoptado”.", "自動コピーに失敗しました。提案値を選択済みです。Ctrl+C を押してから「コピーして採用」をクリックしてください。", "A cópia automática falhou: o valor sugerido está selecionado — pressione Ctrl+C e clique em “Copiado e adotado”.", "Automatisches Kopieren fehlgeschlagen: der vorgeschlagene Wert ist markiert — Strg+C drücken, dann „Kopiert & übernommen“ klicken.", "Аутоматско копирање није успело: предложена вредност је изабрана — притисните Ctrl+C, па кликните „Копирано и прихваћено“.", "자동 복사 실패: 제안 값이 선택되었습니다. Ctrl+C를 누른 뒤 '복사하고 채택'을 클릭하세요."],
      ["此任务为升级前创建（无负责人）。该任务的历史参与者可在服务器模式下重新登记负责人以恢复管理；协作库损坏时先执行协作库修复。也可在桌面版打开同一数据目录重建任务协作。", "This task predates team features (no owner). A past participant can re-register the owner in server mode to restore management; if the collaboration store is corrupt, repair it first. You can also open the same data directory on desktop to recreate the task collaboration.", "Cette tâche est antérieure aux fonctions d’équipe (aucun responsable). Aucune procédure autonome de réclamation n’existe dans cette version : recréez la collaboration de tâche en version bureautique ou contactez l’administrateur du serveur.", "Эта задача создана до появления командных функций (без владельца). Самостоятельно заявить права в этой версии нельзя: пересоздайте совместную задачу в настольной версии или обратитесь к администратору сервера.", "Esta tarea es anterior a las funciones de equipo (sin responsable). Esta versión no ofrece una vía autónoma para reclamar la propiedad: recree la colaboración de la tarea en la versión de escritorio o contacte al administrador del servidor.", "このタスクはチーム機能より前の作成で（責任者なし）。現バージョンに自助での引継ぎ手段はありません。デスクトップ版でタスク协作を再作成するか、サーバー管理者に連絡してください。", "Esta tarefa é anterior aos recursos de equipe (sem responsável). Esta versão não oferece meio autônomo de assumir a propriedade: recrie a colaboração da tarefa na versão desktop ou contate o administrador do servidor.", "Diese Aufgabe stammt aus der Zeit vor den Teamfunktionen (kein Eigentümer). Diese Version bietet keinen Self-Service-Weg zum Übernehmen: Legen Sie die Aufgabenkollaboration in der Desktop-Version neu an oder wenden Sie sich an den Serveradministrator.", "Овај задатак је настао пре тимских функција (без власника). У овој верзији нема самосталног преузимања: поново направите сарадњу задатка у радној верзији или контактирајте администратора сервера.", "이 작업은 팀 기능 이전에 생성되어(소유자 없음) 이 버전에는 셀프 인계 기능이 없습니다. 데스크톱 버전에서 작업 협업을 다시 만들거나 서버 관리자에게 문의하세요."],
      ["任务已启用 AI 建议（使用你的个人钥匙）。", "AI suggestions are enabled for this task (using your personal key).", "Les suggestions IA sont activées pour cette tâche (avec votre clé personnelle).", "ИИ-подсказки включены для этой задачи (используется ваш личный ключ).", "Las sugerencias de IA están activadas para esta tarea (con su clave personal).", "このタスクでは AI 提案が有効です（あなたの個人キーを使用）。", "As sugestões de IA estão ativadas para esta tarefa (usando sua chave pessoal).", "KI-Vorschläge sind für diese Aufgabe aktiviert (mit Ihrem persönlichen Schlüssel).", "АИ предлози су укључени за овај задатак (користи ваш лични кључ).", "이 작업에 AI 제안이 활성화되어 있습니다(개인 키 사용)."],
      ["我已手动复制，采纳", "I copied it manually, adopt", "J’ai copié manuellement, adopter", "Я скопировал вручную — принять", "Lo copié manualmente, adoptar", "手動コピーしたので採用", "Copiei manualmente, adotar", "Habe manuell kopiert, übernehmen", "Ручно сам копирао, прихвати", "수동으로 복사했으니 채택"],
      ["部分核验", "Partially verified", "Vérification partielle", "Частичная проверка", "Verificación parcial", "部分検証", "Verificação parcial", "Teilweise verifiziert", "Делимично проверено", "부분 검증"],
      ["引文核验", "Quote verified", "Citation vérifiée", "Цитата проверена", "Cita verificada", "引用検証済み", "Citação verificada", "Zitat verifiziert", "Цитат проверен", "인용 검증됨"],
      ["来源：摘要（无页码）", "Source: abstract (no page numbers)", "Source : résumé (pas de numéros de page)", "Источник: аннотация (без номеров страниц)", "Fuente: resumen (sin números de página)", "出典：抄録（ページ番号なし）", "Fonte: resumo (sem números de página)", "Quelle: Abstract (ohne Seitenzahlen)", "Извор: сажетак (без бројева страна)", "출처: 초록(쪽수 없음)"],
      ["有建议因引文过短或按可核验前缀放行（部分核验）。", "Some suggestions were accepted with a quote that was too short or on a verifiable prefix (partially verified).", "Des suggestions ont été acceptées avec une citation trop courte ou sur un préfixe vérifiable (vérification partielle).", "Часть предложений принята со слишком короткой цитатой или по проверяемому префиксу (частичная проверка).", "Algunas sugerencias se aceptaron con una cita demasiado corta o por un prefijo verificable (verificación parcial).", "引用が短すぎる、または検証可能な前置きに基づき受理された提案があります（部分検証）。", "Algumas sugestões foram aceitas com uma citação curta demais ou com base em um prefixo verificável (verificação parcial).", "Einige Vorschläge wurden mit einem zu kurzen Zitat oder anhand eines verifizierbaren Präfixes akzeptiert (teilweise verifiziert).", "Неки предлози су прихваћени са прекратким цитатом или по проверивом префиксу (делимично проверено).", "인용이 너무 짧거나 검증 가능한 접두사 기준으로 허용된 제안이 있습니다(부분 검증)."],
      ["任务已启用 AI，你尚未配置可用钥匙（个人或共享）。", "AI is enabled for this task, but you have no usable key yet (personal or shared).", "L’IA est activée pour cette tâche, mais vous n’avez pas encore de clé utilisable (personnelle ou partagée).", "ИИ включён для этой задачи, но у вас пока нет доступного ключа (личного или общего).", "La IA está activada para esta tarea, pero aún no tiene una clave utilizable (personal o compartida).", "このタスクでは AI が有効ですが、利用可能なキー（個人または共有）がまだありません。", "A IA está ativada para esta tarefa, mas você ainda não tem uma chave utilizável (pessoal ou compartilhada).", "KI ist für diese Aufgabe aktiviert, aber Sie haben noch keinen nutzbaren Schlüssel (persönlich oder geteilt).", "АИ је укључен за овај задатак, али још немате употребљив кључ (лични или дељени).", "이 작업에 AI가 활성화되어 있지만 아직 사용 가능한 키(개인 또는 공유)가 없습니다."],
      ["统计载入中…", "Loading statistics…", "Chargement des statistiques…", "Загрузка статистики…", "Cargando estadísticas…", "統計を読み込み中…", "Carregando estatísticas…", "Statistiken werden geladen…", "Учитавање статистике…", "통계를 불러오는 중…"],
      ["本人已生成 {0} 条 · 采纳 {1} · 编辑后采纳 {2} · 忽略 {3}", "Your record: {0} generated · {1} adopted · {2} adopted after editing · {3} dismissed", "Votre relevé : {0} générées · {1} adoptées · {2} adoptées après modification · {3} ignorées", "Ваша статистика: сгенерировано {0} · принято {1} · принято с правкой {2} · отклонено {3}", "Su registro: {0} generadas · {1} adoptadas · {2} adoptadas tras editar · {3} descartadas", "本人の記録：生成 {0} · 採用 {1} · 編集後採用 {2} · 無視 {3}", "Seu registro: {0} geradas · {1} adotadas · {2} adotadas após edição · {3} descartadas", "Ihre Auswertung: {0} erzeugt · {1} übernommen · {2} nach Bearbeitung übernommen · {3} verworfen", "Ваш запис: {0} генерисано · {1} прихваћено · {2} прихваћено након измене · {3} одбачено", "본인 기록: 생성 {0} · 채택 {1} · 편집 후 채택 {2} · 무시 {3}"],
      ["建议先在 20–50 篇人工编码样本上核对建议质量，再放开批量使用（先验证后放量）。", "Validate suggestion quality on 20–50 manually coded records before scaling up (verify first, then expand).", "Validez la qualité des suggestions sur 20–50 références codées manuellement avant de généraliser (valider d’abord, étendre ensuite).", "Проверьте качество предложений на 20–50 вручную закодированных записях, прежде чем расширять использование (сначала проверка, потом масштаб).", "Valide la calidad de las sugerencias en 20–50 registros codificados manualmente antes de ampliar su uso (verificar primero, ampliar después).", "まず 20～50 件の手動コーディング資料で提案の品質を確認してから、本格利用に拡張してください（先に検証、後に拡大）。", "Valide a qualidade das sugestões em 20–50 registros codificados manualmente antes de ampliar o uso (verificar primeiro, expandir depois).", "Prüfen Sie die Qualität der Vorschläge zuerst an 20–50 manuell kodierten Einträgen, bevor Sie den Einsatz ausweiten (erst verifizieren, dann ausbauen).", "Проверите квалитет предлога на 20–50 ручно кодираних записа пре ширења употребе (прво провера, па ширење).", "확대 적용 전에 20–50건의 수동 코딩 레코드로 제안 품질을 먼저 검증하세요(선검증 후 확대)."],
    ]);

    /* =====================================================================
     * 二、样式（随模块注入）
     * ===================================================================== */
    const style = document.createElement('style');
    style.textContent = [
      '.rf-ai-tools{display:inline-flex;gap:8px;align-items:center;flex-wrap:wrap}',
      '.rf-ai-tools .rf-ai-status{font-size:.78rem;color:var(--muted)}',
      '.rf-ai-tools .rf-ai-disclosure{font-size:.75rem;color:var(--muted)}',
      '.rf-ai-suggest{border:1px dashed var(--border);border-radius:10px;margin-top:8px;background:var(--surface)}',
      '.rf-ai-suggest>summary{cursor:pointer;padding:6px 10px;font-size:.82rem;font-weight:600;display:flex;gap:8px;align-items:center;flex-wrap:wrap;list-style:none}',
      '.rf-ai-suggest>summary::-webkit-details-marker{display:none}',
      '.rf-ai-suggest>summary .rf-ai-count{font-weight:400;font-size:.72rem;color:var(--muted)}',
      '.rf-ai-body{padding:2px 10px 10px}',
      '.rf-ai-item{border:1px solid var(--border);border-radius:8px;padding:8px;margin-top:6px}',
      '.rf-ai-item .rf-ai-val{font-weight:600;word-break:break-word}',
      '.rf-ai-quote{margin:4px 0 0;font-size:.78rem;color:var(--muted);white-space:pre-wrap;word-break:break-word}',
      '.rf-ai-meta{font-size:.72rem;color:var(--muted);margin-top:4px}',
      '.rf-ai-pages{display:flex;gap:6px;align-items:center;margin-top:4px;flex-wrap:wrap;font-size:.78rem}',
      '.rf-ai-actions{display:flex;gap:6px;align-items:center;margin-top:6px;flex-wrap:wrap}',
      '.rf-ai-actions .input{max-width:280px;min-width:160px}',
      '.rf-ai-badge{font-size:.72rem;border-radius:999px;padding:2px 10px;border:1px solid var(--border);color:var(--muted)}',
      '.rf-ai-badge.adopted{color:#2F6F6A;border-color:currentColor}',
      '.rf-ai-badge.ignored{color:var(--muted)}',
      '.rf-ai-settings h3{margin:0 0 4px}',
      '.rf-ai-settings h4{margin:12px 0 6px}',
      '.rf-ai-settings .lbl{display:block;font-size:.8rem;margin-bottom:2px}',
      '.rf-ai-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:8px;margin:6px 0}',
      '.rf-ai-checks{display:flex;gap:6px 14px;flex-wrap:wrap;margin:6px 0}',
      '.rf-ai-checks label{display:inline-flex;gap:4px;align-items:center;font-size:.8rem}',
      '.rf-ai-mapping{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:6px 10px;margin:6px 0}',
      '.rf-ai-methods{white-space:pre-wrap;font-size:.78rem;max-height:220px;overflow:auto;border:1px dashed var(--border);border-radius:8px;padding:8px;margin:6px 0}',
      '.rf-ai-notice{color:var(--danger);font-size:.78rem;margin:6px 0 0}',
      '.rf-ai-hint{font-size:.75rem;color:var(--muted);margin:6px 0 0}'
    ].join('\n');
    (document.head || document.documentElement).appendChild(style);

    /* =====================================================================
     * 三、公共工具
     * ===================================================================== */
    function safe(fn) { return function () { try { return fn.apply(this, arguments); } catch (e) { /* 单点故障 */ } }; }
    const node = (tag, text, attrs) => el(tag, { ...(attrs || {}), ...(text == null ? {} : { text: T(text) }) });
    const literal = (tag, text, attrs) => el(tag, { ...(attrs || {}), text: String(text == null ? '' : text), translate: 'no' });
    const btn = (text, fn, cls) => { const b = node('button', text, { type: 'button', class: cls || 'btn b-out sm' }); b.addEventListener('click', safe(fn)); return b; };

    /* 一期字段白名单镜像（P9 常量表的前端展示副本；扩展需走方法学评审，与后端 FIELD_WHITELIST 同步） */
    const FIELD_IDS = ['sample_size', 'study_design', 'country_setting', 'publication_year',
      'funding_source', 'inclusion_criteria', 'study_objective'];
    const FIELD_LABELS = {
      sample_size: '样本量', study_design: '研究设计', country_setting: '国家/场景',
      publication_year: '发表年份', funding_source: '资助来源',
      inclusion_criteria: '纳入标准要点', study_objective: '研究目的'
    };
    const fieldLabel = fid => T(FIELD_LABELS[fid] || fid);

    const scopeKey = () => [S.task?.task_id, S.user?.user_id, S.screener].join('|');
    const currentArticle = () => (Array.isArray(S.cbQueue) ? S.cbQueue[S.cbIdx] : null) || null;
    const canonical = () => window.WorkflowEvidence?.context() === 'coding_submission_evidence';

    /* ---- 状态 ---- */
    let detection = null;            // 判定结果（R1：一次拉取，缓存于 scope）
    let detecting = null;            // 进行中的判定 flight
    let generating = false;
    const articles = new Map();      // zotero_key -> { items:[], usage, warnings:[], keySource, loaded }
    const autoDone = new Set();      // 本会话内已自动获取过的文献（R3 auto 触发一次）
    let dimsCache = null;            // 编码维度（字段映射下拉）
    let methodsPreview = null;       // Methods 段落缓存（{report_zh, report_en}）

    function articleState(key) {
      if (!articles.has(key)) articles.set(key, { items: [], usage: null, warnings: [], keySource: null, loaded: false });
      return articles.get(key);
    }
    function mergeItems(state, incoming) {
      const map = new Map();
      for (const item of state.items || []) map.set(item.id, item);
      for (const item of incoming || []) if (item && item.id != null) map.set(item.id, item);
      state.items = [...map.values()].sort((a, b) => (b.id || 0) - (a.id || 0));
    }

    /* ---- 字段映射（B6_review Q4 一期：localStorage per task+user） ---- */
    const mapStorageKey = () => 'rfAiFieldMap.' + (S.task?.task_id || '') + '.' + String(S.user?.user_id ?? '');
    function loadMapping() {
      try {
        const parsed = JSON.parse(localStorage.getItem(mapStorageKey()) || '{}');
        if (!parsed || typeof parsed !== 'object') return {};
        const out = {};
        for (const fid of FIELD_IDS) if (parsed[fid] && String(parsed[fid]).trim()) out[fid] = String(parsed[fid]).trim();
        return out;
      } catch (e) { return {}; }
    }
    function saveMapping(mapping) {
      try { localStorage.setItem(mapStorageKey(), JSON.stringify(mapping)); } catch (e) { /* 隐私模式等：仅本会话内生效 */ }
    }

    /* =====================================================================
     * 四、判定（R1：判定信息一次拉取；失败静默退出）
     *   owner：GET ai-suggest/settings（200 即 owner，enabled 决定是否启用）
     *   成员：settings 403/404 → GET ai-suggest/status（参与者可读：开关/模式/白名单/
     *         共享钥匙存在性——B7 审查修复，personal 模式下配了个人钥匙的成员可见），
     *         失败视为未启用（零渲染零请求）。
     * ===================================================================== */
    function inactiveDetection(scope, extra) {
      return Object.assign({ scope, isOwner: false, active: false, keyAvailable: false, prefs: null }, extra || {});
    }
    async function detect(force = false) {
      const scope = scopeKey();
      if (!S.task || !S.user || typeof taskApi !== 'function') { detection = null; return null; }
      if (!force && detection && detection.scope === scope) return detection;
      if (detecting && detecting.scope === scope) return detecting.promise;
      const flight = { scope, promise: null };
      flight.promise = (async () => {
        const base = taskApi();
        let taskSettings = null, sharedMeta = null, isOwner = false;
        try {
          taskSettings = await api(base + '/ai-suggest/settings');
          isOwner = true;                                   // 仅 owner 可读（B6 端点）
        } catch (e) {
          if (e && (e.status === 403 || e.status === 404)) {
            try { sharedMeta = await api(base + '/ai-suggest/status'); }
            catch (e2) { return inactiveDetection(scope); } // 失败静默：未启用
          } else {
            return inactiveDetection(scope);                // 网络等错误：静默退出
          }
        }
        /* 个人钥匙元数据（永回掩码；设置页“未配置”显示与编码页钥匙可用性判定共用） */
        let personal = null;
        try { personal = await api('/api/ai/key'); } catch (e) { personal = null; }

        const enabled = isOwner ? taskSettings.enabled === true : sharedMeta.enabled === true;
        let prefs = null;
        if (enabled) {
          try { prefs = await api(base + '/ai-suggest/preferences'); } catch (e) { prefs = null; }
        }
        const sharedAvailable = isOwner
          ? (taskSettings.key_mode === 'shared' && taskSettings.shared_key_enabled === true)
          : !!(sharedMeta && sharedMeta.key_mode === 'shared' && sharedMeta.shared_available === true);
        const personalConfigured = !!(personal && personal.configured);
        const personalEffective = !prefs || prefs.use_personal_key === undefined ? personalConfigured
          : (prefs.use_personal_key !== false && personalConfigured);
        return {
          scope, isOwner, active: enabled,
          settings: isOwner ? taskSettings : null,
          settingsVersion: isOwner ? (taskSettings.version ?? 0) : null,
          allowedFields: isOwner ? (taskSettings.allowed_field_ids || [])
                                 : ((sharedMeta && sharedMeta.allowed_field_ids) || []),
          fieldLabels: (isOwner && taskSettings.field_whitelist)
                       || (sharedMeta && sharedMeta.field_labels) || null,
          contextPrompt: isOwner ? (taskSettings.context_prompt || '') : '',
          sharedAvailable: !!sharedAvailable,
          sharedProviderName: isOwner ? (taskSettings.shared_provider_name || null)
                                       : (sharedMeta ? sharedMeta.shared_provider_name : null),
          owner_unset: !isOwner && !!(sharedMeta && sharedMeta.owner_unset),   /* B9 F13 */
          personal, prefs,
          keyAvailable: personalConfigured || !!sharedAvailable,
          sharedEffective: enabled && !personalEffective && !!sharedAvailable
        };
      })();
      detecting = flight;
      let result = null;
      try {
        result = await flight.promise;
      } catch (e) {
        result = inactiveDetection(scope);
      } finally {
        if (detecting === flight) detecting = null;
      }
      if (flight.scope === scopeKey()) detection = result;
      renderAll();
      return result;
    }
    let detectDebounce = null;
    function scheduleDetect(force = false) {
      clearTimeout(detectDebounce);
      detectDebounce = setTimeout(safe(() => detect(force)), 400);
    }
    /* 局部状态变化后重派生派生量（钥匙可用性/共享披露），避免整轮重新判定 */
    function rederive() {
      if (!detection) return;
      const personalConfigured = !!(detection.personal && detection.personal.configured);
      const usePersonal = !detection.prefs || detection.prefs.use_personal_key !== false;
      detection.sharedAvailable = detection.isOwner
        ? (detection.settings && detection.settings.key_mode === 'shared' && detection.settings.shared_key_enabled === true)
        : detection.sharedAvailable;
      detection.sharedProviderName = detection.isOwner
        ? ((detection.settings && detection.settings.shared_provider_name) || null)
        : detection.sharedProviderName;
      detection.keyAvailable = personalConfigured || !!detection.sharedAvailable;
      detection.sharedEffective = detection.active && !(usePersonal && personalConfigured) && !!detection.sharedAvailable;
    }

    /* =====================================================================
     * 五、编码页建议区
     * ===================================================================== */
    function benchToolsHost() { return document.querySelector('section[data-p="bench"] .wf-queue-tools'); }

    function renderStatusInto(span, note) {
      span.replaceChildren();
      if (note && note.parts && note.parts.filter(Boolean).length) {
        span.append(literal('span', note.parts.filter(Boolean).join('　')));
      }
      if (note && note.retry) span.append(btn('重试', () => generate(false), 'btn b-out sm'));
    }
    /* 状态行按文献暂存：重绘工具条/建议卡不丢失最近一次结果或错误 */
    function setStatus(parts, retry) {
      const cur = currentArticle();
      if (!cur) return;
      const st = articleState(cur.zotero_key);
      st.statusNote = (parts && parts.filter(Boolean).length) ? { parts, retry: !!retry } : null;
      const span = document.getElementById('rfAiStatus');
      if (span) renderStatusInto(span, st.statusNote);
    }
    function statusForArticle(st) {
      if (st.statusNote) return st.statusNote;
      const parts = [];
      if (st.usage) parts.push(usageText(st.usage));
      for (const w of st.warnings || []) parts.push(warningText(w));
      return parts.length ? { parts, retry: false } : null;
    }

    function renderBenchTools() {
      const host = benchToolsHost();
      if (!host) return;
      let wrap = document.getElementById('rfAiTools');
      const show = !!(detection && detection.active && detection.keyAvailable && S.task && !canonical());
      if (!show) { if (wrap) wrap.remove(); return; }
      if (!wrap) {
        wrap = el('span', { id: 'rfAiTools', class: 'rf-ai-tools' });
        host.append(wrap);
      }
      const parts = [];
      parts.push(generating ? node('button', 'AI 建议生成中…', { type: 'button', class: 'btn b-out sm', disabled: '' })
        : btn('AI 建议', () => generate(false)));
      const status = el('span', { id: 'rfAiStatus', class: 'rf-ai-status', role: 'status' });
      parts.push(status);
      /* P1/隐私：共享模式常驻披露行（建议区顶部） */
      const cur = currentArticle();
      const st = cur ? articleState(cur.zotero_key) : null;
      const sharedActive = (st && st.keySource === 'shared') || detection.sharedEffective;
      if (sharedActive) {
        parts.push(el('span', { class: 'rf-ai-disclosure', text: T('本任务由负责人启用了 AI（{0}），请求以任务名义发出。', detection.sharedProviderName || '—') }));
      }
      wrap.replaceChildren(...parts);
      if (st) renderStatusInto(status, statusForArticle(st) || { parts: [T('点击“AI 建议”为本篇生成建议。')], retry: false });
    }

    function usageText(usage) {
      if (!usage) return '';
      const input = usage.prompt_tokens ?? usage.input_tokens;
      const output = usage.completion_tokens ?? usage.output_tokens;
      return T('用量：输入 {0} · 输出 {1}', input == null ? '—' : input, output == null ? '—' : output);
    }
    function warningText(warning) {
      const m = /^dropped_no_quote:(\d+)$/.exec(String(warning || ''));
      if (m) return T('{0} 条建议因缺少原文引文被丢弃。', m[1]);
      if (/^unverified_quote:/.test(String(warning || '')))
        return T('有建议因引文未能在原文中定位被丢弃。');   /* B9 F1：服务端引文核验 */
      if (/^partial_quote:/.test(String(warning || '')))
        return T('有建议因引文过短或按可核验前缀放行（部分核验）。');   /* B9 R-1 / MM2-01 */
      const t = /^context_truncated:(\d+)$/.exec(String(warning || ''));
      if (t) return T('原文过长，已截断 {0} 字。', t[1]);
      if (warning === 'ai_key_missing') return T('未配置可用的 AI 钥匙，请先在设置页配置。');
      return '';
    }

    function buildItemCard(item, mapped) {
      const card = el('div', { class: 'rf-ai-item', 'data-sid': String(item.id ?? '') });
      const pending = !item.status || item.status === 'pending';
      card.append(el('div', { class: 'rf-ai-val' }, literal('b', item.decidedValue || item.final_value || item.value || '')));
      /* P4：引文展示（≤300 字） */
      const quote = String(item.quote || '');
      if (quote) {
        const shown = quote.length > 300 ? quote.slice(0, 300) + '…' : quote;
        card.append(el('p', { class: 'rf-ai-quote' }, el('span', { text: T('原文引文') + '：' }), literal('span', shown)));
      }
      /* P4：页码定位（CbPdf 跳页）；MM2-04：仅摘要来源（无页码）显式标注 */
      const pages = Array.isArray(item.pages) ? item.pages.filter(p => Number.isFinite(p)) : [];
      if (pages.length) {
        const row = el('div', { class: 'rf-ai-pages' }, el('span', { text: T('页码') + '：' }));
        for (const page of pages.slice(0, 8)) {
          row.append(el('button', { type: 'button', class: 'btn b-out sm', text: T('第 {0} 页', String(page)) , onclick: safe(() => jumpPage(page)) }));
        }
        card.append(row);
      } else if (quote) {
        /* 服务端定位命中摘要时 pages 为空（无页可跳）——不静默，标注摘要来源 */
        card.append(el('div', { class: 'rf-ai-pages', text: T('来源：摘要（无页码）') }));
      }
      /* P1：AI 身份留痕角标；B9 R-1：部分核验显式标注；MM2-01：full 也明示角标
         （'引文核验'——明示核验的是引文存在于原文，而非值本身被核验） */
      const verifiedNote = item.verified === 'partial' ? T('部分核验')
        : item.verified === 'full' ? T('引文核验') : null;
      card.append(literal('div', [item.provider, item.model, item.prompt_version,
        verifiedNote].filter(Boolean).join(' · '), { class: 'rf-ai-meta' }));
      const actions = el('div', { class: 'rf-ai-actions' });
      if (!pending) {
        const badgeText = item.status === 'adopted' ? '已采纳' : item.status === 'edited' ? '已编辑' : item.status === 'ignored' ? '已忽略' : '未处理';
        actions.append(node('span', badgeText, { class: 'rf-ai-badge ' + (item.status || '') }));
        if (item.status === 'edited' && item.final_value) {
          actions.append(literal('span', item.final_value, { class: 'rf-ai-meta' }));
        }
        card.append(actions);
        return card;
      }
      if (mapped) actions.append(btn('采纳', ev => adoptItem(item)));
      else {
        actions.append(btn('复制', () => copyItem(item)));
        actions.append(el('span', { class: 'rf-ai-hint', text: T('该字段未映射到编码维度，采纳将复制建议值。') }));
      }
      actions.append(btn('编辑后采纳', () => editItem(item, card, actions, mapped)));
      actions.append(btn('忽略', () => decideItem(item, 'ignored', null)));
      card.append(actions);
      return card;
    }

    function buildFieldDetails(fid, items, mapping) {
      const mapped = !!(mapping[fid] && document.getElementById('wf-field-' + mapping[fid]));
      const badgeMode = detection?.prefs?.presentation === 'badge';
      /* R3 呈现偏好：card=常驻（展开）；badge=收纳为角标（仅计数，点击展开） */
      const details = el('details', { class: 'rf-ai-suggest', 'data-field': fid });
      if (!badgeMode) details.open = true;
      const summary = el('summary', {},
        el('span', { text: T('AI 建议') + ' · ' + fieldLabel(fid) }),
        literal('span', '(' + items.length + ')', { class: 'rf-ai-count' }));
      details.append(summary);
      const body = el('div', { class: 'rf-ai-body' });
      for (const item of items) body.append(buildItemCard(item, mapped));
      details.append(body);
      return details;
    }

    function renderSuggestions() {
      const forms = document.getElementById('cbForms');
      if (!forms) return;
      for (const node_ of Array.from(forms.querySelectorAll('.rf-ai-suggest'))) node_.remove();
      let extra = document.getElementById('rfAiExtra');
      if (extra) extra.remove();
      if (!detection || !detection.active || !detection.keyAvailable || canonical()) return;
      const cur = currentArticle();
      const key = cur?.zotero_key;
      if (!key) return;
      const st = articleState(key);
      if (!st.items.length) return;
      const mapping = loadMapping();
      const byField = new Map();
      for (const fid of FIELD_IDS) byField.set(fid, []);
      for (const item of st.items) if (byField.has(item.field_id)) byField.get(item.field_id).push(item);
      extra = el('div', { id: 'rfAiExtra' });
      for (const [fid, items] of byField) {
        if (!items.length) continue;
        const details = buildFieldDetails(fid, items, mapping);
        const dimId = mapping[fid];
        const host = dimId ? document.getElementById('wf-field-' + dimId) : null;
        const anchor = host ? host.querySelector('.wf-evidence') : null;
        if (anchor) anchor.after(details);            // B2 §1.2 推荐位：.wf-evidence 之后
        else extra.append(details);                   // 未映射/维度已删：集中置于表单末尾
      }
      if (extra.childElementCount) forms.append(extra);
      /* 与 workflow_evidence.updateBusy 同口径：只读/保存中锁定采纳动作 */
      const locked = !!(document.getElementById('cbSave') && document.getElementById('cbSave').disabled);
      for (const control of forms.querySelectorAll('.rf-ai-suggest button,.rf-ai-suggest input')) {
        control.disabled = locked;
      }
    }

    function jumpPage(page) {
      let viewer = null;
      try { viewer = (typeof CbPdf !== 'undefined' && CbPdf && typeof CbPdf.viewer === 'function') ? CbPdf.viewer() : null; } catch (e) { viewer = null; }
      if (!viewer) { toast(T('PDF 尚未加载，无法跳转。'), 'warn'); return; }
      try { viewer.currentPageNumber = Math.max(1, page | 0); } catch (e) { toast(T('PDF 尚未加载，无法跳转。'), 'warn'); }
    }

    /* P5：采纳＝填控件（单选/多选/填空三类），绝不保存；失败不记决定 */
    function adoptIntoControl(dimId, value) {
      const host = document.getElementById('wf-field-' + dimId);
      if (!host) return false;
      const text = String(value == null ? '' : value);
      const select = host.querySelector('select.cb-v');
      if (select) {
        if (![...select.options].some(o => o.value === text)) return false;
        select.value = text;
        select.dispatchEvent(new Event('change', { bubbles: true }));
        return true;
      }
      const checks = [...host.querySelectorAll('input[type=checkbox].cb-v')];
      if (checks.length) {
        const parts = text.split(/[|,，;；、]/).map(s => s.trim()).filter(Boolean);
        if (!parts.includes(text) && text) parts.push(text);
        const values = new Set(checks.map(c => String(c.value)));
        const hits = [...new Set(parts.filter(p => values.has(p)))];
        if (!hits.length) return false;
        for (const c of checks) c.checked = hits.includes(String(c.value));
        host.dispatchEvent(new Event('change', { bubbles: true }));
        return true;
      }
      const input = host.querySelector('input.cb-v');
      if (!input) return false;
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      return true;
    }

    async function copyText(value) {
      try { await navigator.clipboard.writeText(String(value)); return true; } catch (e) { /* 降级 */ }
      try {
        const helper = el('textarea', { style: 'position:fixed;opacity:0' });
        helper.value = String(value);
        document.body.append(helper);
        helper.select();
        const ok = document.execCommand('copy');
        helper.remove();
        return ok;
      } catch (e) { return false; }
    }

    async function adoptItem(item) {
      const dimId = loadMapping()[item.field_id];
      if (!dimId) { copyItem(item); return; }
      if (!adoptIntoControl(dimId, item.value)) {
        toast(T('建议值与该维度的选项不匹配，请手动选择或使用“编辑后采纳”。'), 'warn');
        return;
      }
      await decideItem(item, 'adopted', null);
    }
    async function copyItem(item) {
      const ok = await copyText(item.value);
      if (ok) {
        toast(T('已复制，请粘贴到对应的编码字段。'), 'inc');
        await decideItem(item, 'adopted', null);
        return;
      }
      /* B9 F7：自动复制失败绝不记 adopted——给手动复制框，用户确认后才落决定。 */
      const card = document.querySelector('.rf-ai-item[data-sid="' + String(item.id ?? '') + '"]');
      const actions = card ? card.querySelector('.rf-ai-actions') : null;
      if (!actions) { toast(T('复制失败，请手动选择建议值。'), 'warn'); return; }
      actions.replaceChildren();
      const input = el('input', { class: 'input', value: String(item.value == null ? '' : item.value), translate: 'no', readonly: 'readonly', style: 'max-width:60%' });
      actions.append(input,
        btn('已复制并采纳', async () => {
          const copied = await copyText(input.value);
          if (!copied) { input.select(); toast(T('请按 Ctrl+C 复制选中内容后再采纳。'), 'warn'); return; }
          await decideItem(item, 'adopted', null);
        }, 'btn b-inc sm'),
        btn('我已手动复制，采纳', () => decideItem(item, 'adopted', null), 'btn b-out sm'),  /* B9 R-7 */
        btn('取消', () => renderSuggestions(), 'btn sm'));
      input.focus(); input.select();
      toast(T('自动复制失败：已选中建议值，请按 Ctrl+C，再点击“已复制并采纳”。'), 'warn');
    }
    function editItem(item, card, actions, mapped) {
      actions.replaceChildren();
      const input = el('input', { class: 'input', value: String(item.value == null ? '' : item.value), translate: 'no', maxlength: '4000' });
      actions.append(input, btn('确认', async () => {
        const finalValue = input.value.trim();
        if (!finalValue) return;
        const ok = await decideItem(item, 'edited', finalValue);
        if (!ok) return;
        if (mapped) {
          const dimId = loadMapping()[item.field_id];
          if (!dimId || !adoptIntoControl(dimId, finalValue)) await copyText(finalValue);
        } else {
          const copied = await copyText(finalValue);
          toast(copied ? T('已复制，请粘贴到对应的编码字段。') : T('复制失败，请手动选择建议值。'), copied ? 'inc' : 'warn');
        }
      }, 'btn b-inc sm'), btn('取消', () => renderSuggestions()));
      input.focus();
    }

    /* 决策回传（P6 留痕在后端）；409 already_decided 静默同步 */
    async function decideItem(item, status, finalValue) {
      const key = currentArticle()?.zotero_key;
      try {
        const body = { status };
        if (status === 'edited') body.final_value = finalValue;
        const result = await api(taskApi() + '/coding/ai-suggestions/' + encodeURIComponent(item.id) + '/decision',
          { method: 'POST', body });
        if (result && result.suggestion) Object.assign(item, result.suggestion);
        toast(T('建议已更新。'), 'inc');
      } catch (e) {
        if (e && e.status === 409 && e.code === 'ai_suggestion_already_decided') {
          try { await fetchHistory(key, true); } catch (e2) { /* 静默 */ }
        } else {
          toast(String((e && e.message) || e), 'warn');
          return false;
        }
      }
      renderSuggestions();
      renderBenchTools();
      return true;
    }

    async function fetchHistory(key, force = false) {
      if (!key || !S.task) return;
      const st = articleState(key);
      if (st.loaded && !force) return;
      const result = await api(taskApi() + '/coding/ai-suggestions/' + encodeURIComponent(key));
      if (currentArticle()?.zotero_key !== key) return;
      mergeItems(st, result.suggestions || []);
      st.loaded = true;
    }

    function requestFields() {
      /* B8 回归修复：字段基统一取任务白名单（owner=settings、成员=status 端点，
       * 两者同源）；原实现成员误用 FIELD_IDS（全部 7 字段），任务只勾部分白名单时
       * 成员生成会被后端 ai_field_not_allowed 整单 403（B7 审查主场景实测复现）。
       * 白名单为空时由 generate() 提示“任务未开放任何建议字段”。 */
      const subset = (detection.prefs && detection.prefs.field_ids) || [];
      const base = detection.allowedFields || [];
      const chosen = subset.length ? subset.filter(f => base.includes(f)) : base;
      return [...new Set(chosen)];
    }

    async function generate(auto = false) {
      if (generating || !S.task) return;
      const cur = currentArticle();
      const key = cur?.zotero_key;
      if (!key) return;
      if (!detection || !detection.active || !detection.keyAvailable) { renderBenchTools(); return; }
      const fields = requestFields();
      if (!fields.length) { setStatus([T('任务未开放任何建议字段，请负责人在任务 AI 设置中勾选字段白名单。')], false); return; }
      const scope = scopeKey();
      generating = true;
      renderBenchTools();
      setStatus([T('AI 建议生成中…')], false);
      try {
        const result = await api(taskApi() + '/coding/ai-suggestions', { method: 'POST', body: { zotero_key: key, field_ids: fields } });
        if (scope !== scopeKey() || currentArticle()?.zotero_key !== key) return;
        if (result && result.status === 'disabled') {
          setStatus([T('未配置可用的 AI 钥匙，请先在设置页配置。')], false);
          scheduleDetect(true);                      // 钥匙状态可能已变化（如被删除）
          return;
        }
        const st = articleState(key);
        mergeItems(st, result.suggestions || []);
        st.usage = result.usage || null;
        st.warnings = result.warnings || [];
        st.keySource = result.key_source || null;
        st.loaded = true;
        renderSuggestions();
        renderBenchTools();
        const notes = [];
        if (!st.items.length) notes.push(T('尚无建议。'));
        notes.push(usageText(st.usage));
        for (const w of st.warnings) notes.push(warningText(w));
        setStatus(notes, false);
      } catch (e) {
        if (scope !== scopeKey()) return;
        const code = e && e.code;
        const detail = (e && e.message) || '';
        if (e && e.status === 502 && code === 'ai_provider_error') {
          setStatus([T('AI 服务调用失败：{0}', detail)], true);
        } else if (e && e.status === 409 && code === 'ai_no_source_text') {
          setStatus([T('该文献既无全文 PDF 也无摘要，无法生成建议。')], false);
        } else if (e && e.status === 403 && code === 'ai_field_not_allowed') {
          const fields403 = (e && e.fields) || [];
          setStatus([T('以下字段未开放：{0}', fields403.map(fieldLabel).join('、'))], false);
        } else if (e && e.status === 403 && code === 'ai_suggest_disabled') {
          setStatus([T('任务未启用 AI 建议。')], false);
          scheduleDetect(true);
        } else if (e && e.status === 404) {
          setStatus([T('载入失败：{0}', detail)], false);
        } else {
          setStatus([T('AI 服务调用失败：{0}', detail)], true);
        }
      } finally {
        generating = false;
        renderBenchTools();
      }
    }

    /* =====================================================================
     * 六、设置页（#wf-panel-ml：个人钥匙全局卡；#wf-panel-coding：任务 AI 卡）
     * ===================================================================== */
    const mlPanel = document.getElementById('wf-panel-ml');
    const codingPanel = document.getElementById('wf-panel-coding');
    if (!mlPanel || !codingPanel) return;             // 结构不符：安静退出
    const keyCard = el('section', { class: 'card rf-ai-settings', id: 'rfAiKeyCard' });
    mlPanel.append(keyCard);
    const taskCard = el('section', { class: 'card rf-ai-settings', id: 'rfAiTaskCard' });
    codingPanel.append(taskCard);

    /* 语言切换整卡重绘前保留表单输入 */
    function snapshotInputs(host) {
      const snap = {};
      for (const input of host.querySelectorAll('input[data-rf-name],textarea[data-rf-name],select[data-rf-name]')) {
        snap[input.dataset.rfName] = input.type === 'checkbox' ? input.checked : input.value;
      }
      return snap;
    }
    function restoreInputs(host, snap) {
      if (!snap) return;
      for (const input of host.querySelectorAll('input[data-rf-name],textarea[data-rf-name],select[data-rf-name]')) {
        if (!(input.dataset.rfName in snap)) continue;
        if (input.type === 'checkbox') input.checked = !!snap[input.dataset.rfName];
        else input.value = snap[input.dataset.rfName];
      }
    }

    async function ensureDims() {
      if (Array.isArray(S.dims) && S.dims.length) { dimsCache = S.dims; return; }
      if (dimsCache) return;
      try {
        const result = await api(taskApi() + '/coding/dimensions' + (typeof scrQ === 'function' ? scrQ() : ''));
        dimsCache = (result && result.dimensions) || [];
      } catch (e) { dimsCache = []; }
    }

    /* ---- 个人钥匙卡（全局；仅本人 JWT；掩码显示，永回明文） ---- */
    function renderKeyCard(preserve) {
      const snap = preserve ? snapshotInputs(keyCard) : null;
      keyCard.replaceChildren(node('h3', '个人钥匙（全局）', { style: 'margin:0 0 6px' }));
      const meta = detection && detection.personal;
      if (!meta || !meta.configured) {
        keyCard.append(node('p', '未配置', { class: 'hint' }));
      } else {
        keyCard.append(literal('p', T('当前：{0} · 来源：{1}', meta.masked || '—', meta.source || 'file'), { class: 'hint' }));
      }
      if (meta && meta.source === 'env') {
        keyCard.append(node('p', '由管理员通过环境变量配置，仅可在服务器端更换或移除。', { class: 'rf-ai-hint' }));
        keyCard.append(node('p', '钥匙仅保存在数据目录（桌面版为本机、服务器版为服务器；权限 0600 尽力而为，一期不加密），不会保存在浏览器、任务目录或导出包。', { class: 'rf-ai-hint' }));
        return;
      }
      const provider = el('input', { class: 'input', value: (meta && meta.provider) || '', translate: 'no', maxlength: '50', 'data-rf-name': 'provider', 'aria-label': T('服务商') });
      const secret = el('input', { class: 'input', type: 'password', autocomplete: 'off', translate: 'no', maxlength: '4096', 'data-rf-name': 'key', 'aria-label': T('API Key（仅保存在数据目录）') });
      const baseUrl = el('input', { class: 'input', value: (meta && meta.base_url) || '', translate: 'no', maxlength: '500', 'data-rf-name': 'base_url', 'aria-label': T('接口地址 base_url（兼容 OpenAI 协议，可指向本地服务）') });
      const model = el('input', { class: 'input', value: (meta && meta.model) || '', translate: 'no', maxlength: '100', 'data-rf-name': 'model', 'aria-label': T('模型名（如 gpt-4o-mini）') });
      const verify = el('input', { type: 'checkbox', 'data-rf-name': 'verify' });
      const status = el('p', { class: 'rf-ai-hint', role: 'status' });
      const grid = el('div', { class: 'rf-ai-grid' });
      grid.append(
        el('div', {}, el('label', { class: 'lbl', text: T('服务商') }), provider),
        el('div', {}, el('label', { class: 'lbl', text: T('API Key（仅保存在数据目录）') }), secret),
        el('div', {}, el('label', { class: 'lbl', text: T('接口地址 base_url（兼容 OpenAI 协议，可指向本地服务）') }), baseUrl),
        el('div', {}, el('label', { class: 'lbl', text: T('模型名（如 gpt-4o-mini）') }), model));
      const save = btn('保存钥匙', async () => {
        if (!provider.value.trim() || !secret.value.trim()) { status.textContent = T('服务商与 API Key 不能为空。'); return; }
        save.disabled = true;
        try {
          const result = await api('/api/ai/key', { method: 'PUT', body: { provider: provider.value.trim(), key: secret.value, base_url: baseUrl.value.trim() || null, model: model.value.trim() || null, verify: verify.checked } });
          if (detection) { detection.personal = result; rederive(); }
          toast(T('已保存。'), 'inc');
          secret.value = '';
          renderKeyCard(false);
          renderBenchTools();
        } catch (e) {
          status.textContent = T('AI 服务调用失败：{0}', (e && e.message) || '');
        } finally { save.disabled = false; }
      }, 'btn b-inc sm');
      const test = btn('测试连接', async () => {
        test.disabled = true;
        status.textContent = T('测试连接') + '…';
        try {
          const result = await api('/api/ai/key/test', { method: 'POST' });
          status.textContent = T('连接正常 · 延迟 {0} ms', String(result.latency_ms ?? '—'));
        } catch (e) {
          status.textContent = (e && e.status === 409) ? T('未配置可用的 AI 钥匙，请先在设置页配置。') : T('AI 服务调用失败：{0}', (e && e.message) || '');
        } finally { test.disabled = false; }
      });
      const del = btn('删除钥匙', async () => {
        if (!confirm(T('确认删除个人 AI 钥匙？'))) return;
        del.disabled = true;
        try {
          await api('/api/ai/key', { method: 'DELETE' });
          if (detection) { detection.personal = null; rederive(); }
          toast(T('已删除。'), 'inc');
          renderKeyCard(false);
          scheduleDetect(true);
        } catch (e) {
          status.textContent = String((e && e.message) || e);
        } finally { del.disabled = false; }
      });
      keyCard.append(grid,
        el('label', { class: 'rf-ai-checks' }, verify, el('span', { text: T('保存前验证连通性'), style: 'font-size:.8rem' })),
        el('div', { class: 'rf-ai-actions' }, save, test, meta && meta.configured ? del : null, status),
        node('p', '钥匙仅保存在数据目录（桌面版为本机、服务器版为服务器；权限 0600 尽力而为，一期不加密），不会保存在浏览器、任务目录或导出包。', { class: 'rf-ai-hint' }));
      restoreInputs(keyCard, snap);
    }

    /* ---- 任务 AI 设置 + 成员偏好 + 审计与报告卡 ---- */
    let ownerNotice = '';          // 乐观锁冲突等持久提示（至下次成功/刷新）
    function renderTaskCard(preserve) {
      const snap = preserve ? snapshotInputs(taskCard) : null;
      taskCard.replaceChildren(node('h3', 'AI 抽取建议', { style: 'margin:0 0 6px' }));
      if (!S.task) { taskCard.append(node('p', '请先选择任务。', { class: 'hint' })); return; }
      if (!detection) return;                       // 判定进行中/失败：安静（R1 精神）
      const renderOwnerAndDims = () => {
        if (!(detection.isOwner && detection.settings)) return;
        renderOwnerBlock();
        /* B9 F17：编码维度未加载过时拉取一次并重绘映射下拉（仅在本次真正取到时重绘，防循环）。 */
        const hadDims = !!dimsCache;
        ensureDims().then(safe(() => {
          if (!hadDims && Array.isArray(dimsCache) && dimsCache.length) renderTaskCard(true);
        }));
      };
      renderOwnerAndDims();
      if (!detection.isOwner) renderMemberBlock();
      if (detection.active) renderPrefsBlock();
      renderAuditBlock();   /* B9 F4：审计与报告不依赖任务开关——有记录即可导出（P8） */
      restoreInputs(taskCard, snap);
    }

    function renderOwnerBlock() {
      const s = detection.settings;
      taskCard.append(node('h4', '任务 AI 设置（负责人）'));
      const enabled = el('input', { type: 'checkbox', 'data-rf-name': 'enabled' });
      enabled.checked = s.enabled === true;
      taskCard.append(el('label', { class: 'rf-ai-checks' }, enabled, el('span', { text: T('启用 AI 抽取建议（默认关闭）'), style: 'font-size:.8rem' })));
      const mode = el('select', { class: 'input', 'data-rf-name': 'key_mode', 'aria-label': T('钥匙模式') });
      mode.append(node('option', '个人钥匙（各成员自带）', { value: 'personal' }), node('option', '任务共享钥匙（全员可用）', { value: 'shared' }));
      mode.value = s.key_mode === 'shared' ? 'shared' : 'personal';
      const modeRow = el('div', { class: 'rf-ai-grid' }, el('div', {}, el('label', { class: 'lbl', text: T('钥匙模式') }), mode));
      taskCard.append(modeRow);
      /* 共享钥匙配置（owner 写；掩码确认/删除） */
      const sharedBox = el('div', { class: 'rf-ai-shared' });
      const sharedStatus = detection.sharedAvailable
        ? literal('p', T('共享钥匙已启用：{0}', detection.sharedProviderName || '—'), { class: 'hint' })
        : node('p', '未配置共享钥匙。', { class: 'hint' });
      sharedBox.append(sharedStatus);
      const provider = el('input', { class: 'input', translate: 'no', maxlength: '50', 'data-rf-name': 'shared_provider', 'aria-label': T('服务商') });
      const secret = el('input', { class: 'input', type: 'password', autocomplete: 'off', translate: 'no', maxlength: '4096', 'data-rf-name': 'shared_key', 'aria-label': T('API Key（仅保存在数据目录）') });
      const baseUrl = el('input', { class: 'input', translate: 'no', maxlength: '500', 'data-rf-name': 'shared_base_url', 'aria-label': T('接口地址 base_url（兼容 OpenAI 协议，可指向本地服务）') });
      const model = el('input', { class: 'input', translate: 'no', maxlength: '100', 'data-rf-name': 'shared_model', 'aria-label': T('模型名（如 gpt-4o-mini）') });
      const verify = el('input', { type: 'checkbox', 'data-rf-name': 'shared_verify' });
      const sharedStatus2 = el('p', { class: 'rf-ai-hint', role: 'status' });
      const grid = el('div', { class: 'rf-ai-grid' });
      grid.append(
        el('div', {}, el('label', { class: 'lbl', text: T('服务商') }), provider),
        el('div', {}, el('label', { class: 'lbl', text: T('API Key（仅保存在数据目录）') }), secret),
        el('div', {}, el('label', { class: 'lbl', text: T('接口地址 base_url（兼容 OpenAI 协议，可指向本地服务）') }), baseUrl),
        el('div', {}, el('label', { class: 'lbl', text: T('模型名（如 gpt-4o-mini）') }), model));
      const saveShared = btn('保存共享钥匙', async () => {
        if (!provider.value.trim() || !secret.value.trim()) { sharedStatus2.textContent = T('服务商与 API Key 不能为空。'); return; }
        saveShared.disabled = true;
        try {
          await api(taskApi() + '/ai-suggest/shared-key', { method: 'PUT', body: { provider: provider.value.trim(), key: secret.value, base_url: baseUrl.value.trim() || null, model: model.value.trim() || null, verify: verify.checked } });
          toast(T('已保存。'), 'inc');
          await detect(true);
        } catch (e) {
          sharedStatus2.textContent = (e && e.status === 502) ? T('AI 服务调用失败：{0}', e.message || '') : String((e && e.message) || e);
        } finally { saveShared.disabled = false; }
      }, 'btn b-inc sm');
      const delShared = btn('删除共享钥匙', async () => {
        if (!confirm(T('确认删除任务共享钥匙？'))) return;
        delShared.disabled = true;
        try {
          await api(taskApi() + '/ai-suggest/shared-key', { method: 'DELETE' });
          toast(T('已删除。'), 'inc');
          await detect(true);
        } catch (e) { sharedStatus2.textContent = String((e && e.message) || e); }
        finally { delShared.disabled = false; }
      });
      sharedBox.append(grid,
        el('label', { class: 'rf-ai-checks' }, verify, el('span', { text: T('保存前验证连通性'), style: 'font-size:.8rem' })),
        el('div', { class: 'rf-ai-actions' }, saveShared, detection.sharedAvailable ? delShared : null, sharedStatus2),
        node('p', '任务由负责人启用共享钥匙后，成员无需各自配置钥匙。', { class: 'rf-ai-hint' }));
      const syncSharedVisibility = safe(() => { sharedBox.hidden = mode.value !== 'shared'; });
      mode.addEventListener('change', syncSharedVisibility);
      syncSharedVisibility();
      taskCard.append(sharedBox);
      /* 字段白名单（7 项常量表） */
      taskCard.append(node('h4', '字段白名单（建议仅覆盖勾选字段）'));
      const checks = el('div', { class: 'rf-ai-checks' });
      const boxes = {};
      for (const fid of FIELD_IDS) {
        const box = el('input', { type: 'checkbox', value: fid, 'data-rf-name': 'wf_' + fid });
        box.checked = (detection.allowedFields || []).includes(fid);
        boxes[fid] = box;
        checks.append(el('label', {}, box, el('span', { text: fieldLabel(fid), style: 'font-size:.8rem' })));
      }
      taskCard.append(checks);
      /* 综述背景提示语（≤2000 字计数） */
      taskCard.append(node('h4', '综述背景提示语（选填，随建议请求发送给模型）'));
      const prompt = el('textarea', { class: 'input', rows: '3', maxlength: '2000', translate: 'no', 'data-rf-name': 'context_prompt', 'aria-label': T('综述背景提示语（选填，随建议请求发送给模型）') });
      prompt.value = detection.contextPrompt || '';
      const counter = literal('span', T('{0} / 2000 字', String(prompt.value.length)), { class: 'rf-ai-hint' });
      prompt.addEventListener('input', safe(() => { counter.textContent = T('{0} / 2000 字', String(prompt.value.length)); }));
      taskCard.append(prompt, counter);
      /* 字段映射（Q4 一期：7 个下拉，选项=任务编码维度，默认不映射；localStorage per task+user） */
      taskCard.append(node('h4', '字段映射（建议采纳时自动填入对应编码维度；仅保存在当前浏览器）'));
      const mapping = loadMapping();
      const mapGrid = el('div', { class: 'rf-ai-mapping' });
      for (const fid of FIELD_IDS) {
        const select = el('select', { class: 'input', 'data-rf-name': 'map_' + fid, 'aria-label': fieldLabel(fid) });
        select.append(node('option', '不映射', { value: '' }));
        for (const dim of (Array.isArray(S.dims) && S.dims.length ? S.dims : (dimsCache || []))) {
          if (!dim || dim.id == null) continue;
          select.append(literal('option', dim.name || String(dim.id), { value: String(dim.id) }));
        }
        select.value = mapping[fid] && [...select.options].some(o => o.value === mapping[fid]) ? mapping[fid] : '';
        select.addEventListener('change', safe(() => {
          const next = loadMapping();
          if (select.value) next[fid] = select.value; else delete next[fid];
          saveMapping(next);
        }));
        mapGrid.append(el('div', {}, el('label', { class: 'lbl', text: fieldLabel(fid) }), select));
      }
      taskCard.append(mapGrid);
      /* 保存（乐观锁） */
      const notice = node('p', ownerNotice || null, { class: 'rf-ai-notice', role: 'status' });
      const save = btn('保存任务 AI 设置', async () => {
        save.disabled = true;
        try {
          const allowed = FIELD_IDS.filter(fid => boxes[fid].checked);
          const result = await api(taskApi() + '/ai-suggest/settings', {
            method: 'PUT',
            body: {
              expected_version: detection.settingsVersion ?? 0,
              enabled: enabled.checked,
              key_mode: mode.value === 'shared' ? 'shared' : 'personal',
              allowed_field_ids: allowed,
              context_prompt: prompt.value.slice(0, 2000)
            }
          });
          ownerNotice = '';
          /* PUT 响应不含共享钥匙元数据（GET 才回）——沿用旧值再重派生 */
          const prevSharedEnabled = detection.settings ? detection.settings.shared_key_enabled : undefined;
          const prevSharedProvider = detection.settings ? detection.settings.shared_provider_name : undefined;
          if (result) {
            if (result.shared_key_enabled === undefined) result.shared_key_enabled = prevSharedEnabled;
            if (result.shared_provider_name === undefined) result.shared_provider_name = prevSharedProvider;
          }
          detection.settings = result;
          detection.settingsVersion = result.version;
          detection.allowedFields = result.allowed_field_ids || [];
          detection.fieldLabels = result.field_whitelist || detection.fieldLabels;
          detection.contextPrompt = result.context_prompt || '';
          detection.active = result.enabled === true;
          rederive();
          toast(T('已保存。'), 'inc');
          renderTaskCard(false);
          renderBenchTools();
          renderSuggestions();
        } catch (e) {
          if (e && e.status === 409 && e.code === 'ai_settings_version_conflict') {
            ownerNotice = T('设置已被修改（乐观锁冲突），请刷新后重试。');
          } else {
            ownerNotice = String((e && e.message) || e);
          }
          notice.textContent = ownerNotice;
        } finally { save.disabled = false; }
      }, 'btn b-inc sm');
      const refresh = btn('刷新', async () => { ownerNotice = ''; await detect(true); });
      taskCard.append(el('div', { class: 'rf-ai-actions' }, save, refresh, notice));
    }

    function renderMemberBlock() {
      /* B9 F13/R-5 / R6-C2(#31)：升级前任务如实说明。r8 起协作属主重新登记端点已
         落地（POST /api/tasks/{id}/collaboration/rebind），文案必须指向该真实入口——
         修复前的文案与后端 409 指引自相矛盾（宣称无自助入口）。 */
      if (detection.owner_unset) {
        taskCard.append(node('p', '此任务为升级前创建（无负责人）。该任务的历史参与者可在服务器模式下重新登记负责人以恢复管理；协作库损坏时先执行协作库修复。也可在桌面版打开同一数据目录重建任务协作。', { class: 'hint' }));
        return;
      }
      if (detection.sharedAvailable) {
        taskCard.append(literal('p', T('共享钥匙已启用：{0}', detection.sharedProviderName || '—'), { class: 'hint' }));
        taskCard.append(el('span', { class: 'rf-ai-disclosure', text: T('本任务由负责人启用了 AI（{0}），请求以任务名义发出。', detection.sharedProviderName || '—'), style: 'display:block;font-size:.75rem;color:var(--muted);margin:4px 0' }));
      } else if (detection.active && detection.keyAvailable) {
        taskCard.append(node('p', '任务已启用 AI 建议（使用你的个人钥匙）。', { class: 'hint' }));  /* B9 R-4 */
      } else if (detection.active) {
        taskCard.append(node('p', '任务已启用 AI，你尚未配置可用钥匙（个人或共享）。', { class: 'hint' }));  /* B9 F20 */
      } else {
        taskCard.append(node('p', '任务未启用 AI 建议。', { class: 'hint' }));
      }
    }

    function renderPrefsBlock() {
      taskCard.append(node('h4', '成员偏好（本人）'));
      const prefs = detection.prefs || {};
      const usePersonal = el('input', { type: 'checkbox', 'data-rf-name': 'use_personal_key' });
      usePersonal.checked = prefs.use_personal_key !== false;
      taskCard.append(el('label', { class: 'rf-ai-checks' }, usePersonal, el('span', { text: T('我想用自己的 Key（未配置时自动使用任务共享钥匙）'), style: 'font-size:.8rem' })));
      const grid = el('div', { class: 'rf-ai-grid' });
      const trigger = el('select', { class: 'input', 'data-rf-name': 'trigger', 'aria-label': T('触发方式') });
      trigger.append(node('option', '手动（点击按钮生成）', { value: 'manual' }), node('option', '进入文献自动获取', { value: 'auto' }));
      trigger.value = prefs.trigger === 'auto' ? 'auto' : 'manual';
      const presentation = el('select', { class: 'input', 'data-rf-name': 'presentation', 'aria-label': T('呈现方式') });
      presentation.append(node('option', '常驻卡片', { value: 'card' }), node('option', '收纳为角标（仅计数，点击展开）', { value: 'badge' }));
      presentation.value = prefs.presentation === 'badge' ? 'badge' : 'card';
      grid.append(
        el('div', {}, el('label', { class: 'lbl', text: T('触发方式') }), trigger),
        el('div', {}, el('label', { class: 'lbl', text: T('呈现方式') }), presentation));
      taskCard.append(grid);
      taskCard.append(node('p', '建议字段（不选=任务允许的全部字段）', { class: 'lbl', style: 'margin-top:8px' }));
      const checks = el('div', { class: 'rf-ai-checks' });
      const boxes = {};
      for (const fid of FIELD_IDS) {
        const box = el('input', { type: 'checkbox', value: fid, 'data-rf-name': 'pf_' + fid });
        box.checked = (prefs.field_ids || []).includes(fid);
        boxes[fid] = box;
        checks.append(el('label', {}, box, el('span', { text: fieldLabel(fid), style: 'font-size:.8rem' })));
      }
      taskCard.append(checks);
      const status = el('p', { class: 'rf-ai-hint', role: 'status' });
      const save = btn('保存个人偏好', async () => {
        save.disabled = true;
        try {
          const body = {
            use_personal_key: usePersonal.checked,
            trigger: trigger.value === 'auto' ? 'auto' : 'manual',
            field_ids: FIELD_IDS.filter(fid => boxes[fid].checked),
            presentation: presentation.value === 'badge' ? 'badge' : 'card'
          };
          const result = await api(taskApi() + '/ai-suggest/preferences', { method: 'PUT', body });
          detection.prefs = result;
          toast(T('已保存。'), 'inc');
          renderBenchTools();
        } catch (e) { status.textContent = String((e && e.message) || e); }
        finally { save.disabled = false; }
      }, 'btn b-inc sm');
      taskCard.append(el('div', { class: 'rf-ai-actions' }, save, status));
    }

    /* B9 R-3：计数懒加载——仅任务启用时请求一次并按 scope 缓存；未启用零请求（R1 口径）。
     * B8 v2 修复：首屏连续重绘（boot 判定+workspace-changed 各渲染一次）时，计数请求
     * 在缓存落定前会重复发出——补在途（flight）判定，同一 scope 至多一次在途请求。 */
    let auditCountsCache = { scope: null, text: '' };
    let auditCountsFlight = '';
    function renderAuditBlock() {
      taskCard.append(node('h4', '审计与报告'));
      /* B9 F6：P10 先验证后放量——展示本人使用计数与小样评估引导（审计区不依赖任务开关，F4）。 */
      const countsLine = el('p', { class: 'rf-ai-hint', role: 'status' });
      const scope = scopeKey();
      if (auditCountsCache.scope === scope) {
        countsLine.textContent = auditCountsCache.text;
      } else if (detection.active && auditCountsFlight !== scope) {
        auditCountsFlight = scope;
        api(taskApi() + '/ai-suggestions/methods-report').then(safe(result => {
          const c = (result && result.counts) || null;
          const text = c
            ? T('本人已生成 {0} 条 · 采纳 {1} · 编辑后采纳 {2} · 忽略 {3}',
                String(c.total ?? 0), String(c.adopted ?? 0), String(c.edited ?? 0), String(c.ignored ?? 0))
            : T('尚无建议记录。');
          auditCountsCache = { scope, text };
          auditCountsFlight = '';
          countsLine.textContent = text;
        })).catch(safe(() => { auditCountsCache = { scope, text: '' }; auditCountsFlight = ''; }));
      }
      const box = el('div', { class: 'rf-ai-methods', hidden: '' });
      const csv = btn('导出决策记录（CSV）', () => exportDecisions('csv'));
      const json = btn('导出决策记录（JSON）', () => exportDecisions('json'));
      const methods = btn('生成 Methods 段落', async () => {
        methods.disabled = true;
        try {
          const result = await api(taskApi() + '/ai-suggestions/methods-report');
          methodsPreview = result || null;
          box.replaceChildren(
            node('b', '中文段落'),
            literal('div', (result && result.report_zh) || T('尚无建议记录。')),
            node('b', '英文段落'),
            literal('div', (result && result.report_en) || ''));
          box.hidden = false;
        } catch (e) { toast(String((e && e.message) || e), 'warn'); }
        finally { methods.disabled = false; }
      });
      const copyZh = btn('复制中文', async () => { if (await copyText((methodsPreview && methodsPreview.report_zh) || '')) toast(T('已复制。'), 'inc'); });
      const copyEn = btn('复制英文', async () => { if (await copyText((methodsPreview && methodsPreview.report_en) || '')) toast(T('已复制。'), 'inc'); });
      taskCard.append(el('div', { class: 'rf-ai-actions' }, csv, json, methods, copyZh, copyEn), box,
        countsLine,
        node('p', '建议先在 20–50 篇人工编码样本上核对建议质量，再放开批量使用（先验证后放量）。', { class: 'rf-ai-hint' }));
    }

    async function exportDecisions(format) {
      try {
        const blob = await api(taskApi() + '/ai-suggestions/export?format=' + encodeURIComponent(format), { blob: true });
        const url = URL.createObjectURL(blob);
        const link = el('a', { href: url, download: 'ai-suggestions.' + format });
        document.body.append(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 10000);
      } catch (e) { toast(String((e && e.message) || e), 'warn'); }
    }

    /* =====================================================================
     * 七、事件接入与初始化
     * ===================================================================== */
    function renderAll() {
      renderBenchTools();
      renderSuggestions();
      renderKeyCard(false);
      renderTaskCard(false);
    }

    function resetForScopeChange() {
      detection = null;
      articles.clear();
      autoDone.clear();
      dimsCache = null;
      methodsPreview = null;
      ownerNotice = '';
      generating = false;
      const wrap = document.getElementById('rfAiTools');
      if (wrap) wrap.remove();
      const extra = document.getElementById('rfAiExtra');
      if (extra) extra.remove();
    }

    function onFormLoaded() {
      detect().then(safe(() => {
        renderBenchTools();
        renderSuggestions();
        const key = currentArticle()?.zotero_key;
        if (!key || !detection || !detection.active || !detection.keyAvailable || canonical()) return;
        const st = articleState(key);
        const afterHistory = safe(() => {
          renderSuggestions();
          /* R3 auto 触发：进入文献自动获取一次 */
          if (detection.prefs?.trigger === 'auto' && !autoDone.has(key) && !generating &&
              !(document.getElementById('cbSave') && document.getElementById('cbSave').disabled)) {
            autoDone.add(key);
            generate(true);
          }
        });
        if (!st.loaded) fetchHistory(key).then(afterHistory).catch(() => {});
        else afterHistory();
      })).catch(() => { /* 静默 */ });
    }

    const formsHost = document.getElementById('cbForms');
    if (typeof MutationObserver !== 'undefined' && formsHost) {
      new MutationObserver(safe(() => {
        /* workflow_evidence 每次重绘会清空 #cbForms：编码表单仍在而建议卡丢失时补挂 */
        const key = currentArticle()?.zotero_key;
        if (!key || !detection || !detection.active || !detection.keyAvailable || canonical()) return;
        const st = articleState(key);
        if (st.items.length && formsHost.querySelector('.wf-coding-unit') && !formsHost.querySelector('.rf-ai-suggest')) {
          renderSuggestions();
        }
      })).observe(formsHost, { childList: true });
    }

    document.addEventListener('reviewflow:coding-form-loaded', safe(onFormLoaded));
    document.addEventListener('reviewflow:task-context-changed', safe(() => { resetForScopeChange(); renderAll(); detect(); }));
    document.addEventListener('reviewflow:workspace-changed', safe(event => {
      const page = event && event.detail;
      if (page === 'bench') { detect().then(safe(() => { renderBenchTools(); renderSuggestions(); })).catch(() => {}); }
      else if (page === 'home') { detect().then(safe(renderAll)).catch(() => {}); }
      /* 其余页面不动设置卡（避免清掉用户正在填写的表单） */
    }));
    document.addEventListener('reviewflow:workflow-changed', safe(() => scheduleDetect(true)));   // B4 §5：开关联动
    document.addEventListener('reviewflow:language-changed', safe(() => {
      renderKeyCard(true);
      renderTaskCard(true);
      renderBenchTools();
      renderSuggestions();
    }));

    /* 首次加载已停在编码/首页（hash 直达）：立即判定 */
    if (typeof S !== 'undefined' && S.task && S.user) detect().then(safe(renderAll)).catch(() => {});
  } catch (e) { /* 单点故障：模块初始化失败不影响主应用 */ }
})();
