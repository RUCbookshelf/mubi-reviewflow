/* Shared interface vocabulary. Display strings only: never API enum values or user content. */
(function(){ 'use strict';
 const rows = [
["导出内容过多，无法生成预览。请缩小导出范围或减少内容后重试。", "The export is too large to preview. Reduce the selected range or content and try again.", "L’export est trop volumineux pour être prévisualisé. Réduisez la sélection ou le contenu, puis réessayez.", "Экспорт слишком велик для предварительного просмотра. Сократите диапазон или объём данных и повторите попытку.", "La exportación es demasiado grande para obtener una vista previa. Reduzca el rango o el contenido y vuelva a intentarlo.", "エクスポート内容が多すぎてプレビューできません。範囲または内容を減らして、もう一度お試しください。", "A exportação é grande demais para pré-visualizar. Reduza o intervalo ou o conteúdo e tente novamente.", "Der Export ist zu umfangreich für eine Vorschau. Verringern Sie den Bereich oder den Inhalt und versuchen Sie es erneut.", "Извоз је превелик за преглед. Смањите опсег или количину података и покушајте поново.", "내보내기 내용이 너무 많아 미리 볼 수 없습니다. 범위나 내용을 줄인 뒤 다시 시도하세요."],
["导出预览完整性检查未通过，请重新生成预览后重试。", "The export preview could not be verified. Generate a new preview and try again.", "L’intégrité de l’aperçu de l’export n’a pas pu être vérifiée. Générez un nouvel aperçu et réessayez.", "Не удалось проверить целостность предварительного просмотра экспорта. Создайте новый предварительный просмотр и повторите попытку.", "No se pudo comprobar la integridad de la vista previa de exportación. Genere una nueva y vuelva a intentarlo.", "エクスポートのプレビューを確認できませんでした。プレビューを作り直して、もう一度お試しください。", "Não foi possível verificar a integridade da pré-visualização da exportação. Gere outra e tente novamente.", "Die Integrität der Exportvorschau konnte nicht geprüft werden. Erstellen Sie eine neue Vorschau und versuchen Sie es erneut.", "Није било могуће проверити целовитост прегледа извоза. Направите нови преглед и покушајте поново.", "내보내기 미리보기의 무결성을 확인하지 못했습니다. 미리보기를 새로 만든 뒤 다시 시도하세요."],
["当前任务无法导入个人轨迹。", "Personal trace records cannot be imported into this task right now.", "Les traces personnelles ne peuvent pas être importées dans cette tâche pour le moment.", "Сейчас личные записи истории нельзя импортировать в эту задачу.", "Ahora no se pueden importar registros de actividad personales en esta tarea.", "現在、このタスクには個人の記録をインポートできません。", "Não é possível importar registros pessoais de atividade para esta tarefa no momento.", "Persönliche Verlaufsdaten können derzeit nicht in diese Aufgabe importiert werden.", "Тренутно није могуће увести личне записе активности у овај задатак.", "현재 이 작업으로 개인 기록을 가져올 수 없습니다."],
["当前无法核对来源文献是否属于本任务；可将其保留为未关联来源后继续。", "We cannot confirm that the source reference belongs to this task. You can keep it unlinked and continue.", "Impossible de confirmer que la référence source appartient à cette tâche. Vous pouvez la conserver sans lien et continuer.", "Не удалось подтвердить, что исходная публикация относится к этой задаче. Можно сохранить её без привязки и продолжить.", "No se pudo confirmar que la referencia de origen pertenezca a esta tarea. Puede conservarla sin vincular y continuar.", "この出典文献が現在のタスクに属するか確認できません。未関連のまま保持して続行できます。", "Não foi possível confirmar se a referência de origem pertence a esta tarefa. Você pode mantê-la sem vínculo e continuar.", "Die Quellenreferenz konnte dieser Aufgabe nicht zugeordnet werden. Sie können sie unverknüpft beibehalten und fortfahren.", "Није могуће потврдити да изворна референца припада овом задатку. Можете је оставити неповезану и наставити.", "출처 문헌이 현재 작업에 속하는지 확인할 수 없습니다. 연결하지 않은 상태로 두고 계속할 수 있습니다."],
["服务暂时无法完成请求，请稍后重试。", "The service could not complete the request. Please try again later.", "Le service n’a pas pu traiter la demande. Veuillez réessayer plus tard.", "Служба не смогла выполнить запрос. Повторите попытку позже.", "El servicio no pudo completar la solicitud. Inténtelo de nuevo más tarde.", "サービスがリクエストを完了できませんでした。後でもう一度お試しください。", "O serviço não conseguiu concluir a solicitação. Tente novamente mais tarde.", "Der Dienst konnte die Anfrage nicht abschließen. Versuchen Sie es später erneut.", "Услуга није могла да доврши захтев. Покушајте поново касније.", "서비스가 요청을 완료하지 못했습니다. 나중에 다시 시도하세요."],
  [
    "当前位置",
    "Current location"
  ],
  [
    "切换工作区",
    "Switch workspace"
  ],
  [
    "工作区",
    "Workspaces"
  ],
  [
    "当前任务中的工作区",
    "Workspaces in this task"
  ],
  [
    "未选择任务",
    "No task selected"
  ],
  [
    "关闭导航",
    "Close navigation"
  ],
  [
    "前往{0}",
    "Go to {0}"
  ],
  [
    "切换工作区；当前任务：{0}",
    "Switch workspace; current task: {0}"
  ],
  [
    "首页 & 设置",
    "Home & settings"
  ],
  [
    "转换方式",
    "Conversion"
  ],
  [
    "选择转换方式",
    "Choose Conversion"
  ],
  [
    "两组近似风险比",
    "Two-arm approximate hazard ratio"
  ],
  [
    "单组近似事件发生率",
    "Single-arm approximate event rate"
  ],
  [
    "模型假设",
    "Model assumption"
  ],
  [
    "选择模型假设",
    "Choose Model assumption"
  ],
  [
    "我接受生存时间服从指数分布（即瞬时风险率恒定）及不确定性近似估计的假设",
    "I accept a constant hazard (exponential survival) model and approximate uncertainty"
  ],
  [
    "中位数定义",
    "Median definition"
  ],
  [
    "选择中位数定义",
    "Choose Median definition"
  ],
  [
    "已达到所定义事件的 Kaplan–Meier 中位时间",
    "Reached Kaplan–Meier median time to the defined event"
  ],
  [
    "时间单位（两组一致）",
    "Time unit (same for both arms)"
  ],
  [
    "估计量",
    "Quantity"
  ],
  [
    "选择估计量",
    "Choose quantity"
  ],
  [
    "区间水平（%）",
    "Interval level (%)"
  ],
  [
    "事件数",
    "Events"
  ],
  [
    "人时单位",
    "Person-time unit"
  ],
  [
    "对照组事件数",
    "Control events"
  ],
  [
    "研究来源、事件定义及表格或页码",
    "Study source, event definition and table/page"
  ],
  [
    "单组中位数",
    "single median"
  ],
  [
    "选择研究特征",
    "Choose study characteristic"
  ],
  [
    "选择评价框架",
    "Choose framework"
  ],
  [
    "选择总体判断",
    "Choose overall judgment"
  ],
  [
    "保存评价",
    "Save assessment"
  ],
  [
    "固定效应",
    "Fixed effect"
  ],
  [
    "正态区间",
    "Normal interval"
  ],
  [
    "修正 HKSJ",
    "Modified HKSJ"
  ],
  [
    "保存四格表",
    "Save 2×2 table"
  ],
  [
    "模型",
    "Model"
  ],
  [
    "选择模型",
    "Choose model"
  ],
  [
    "零单元格处理规则",
    "Zero-cell policy"
  ],
  [
    "导出 HSROC JSON",
    "Export HSROC JSON"
  ],
  [
    "选择可信度",
    "Choose credibility"
  ],
  [
    "明确可信",
    "unequivocal"
  ],
  [
    "可信",
    "credible"
  ],
  [
    "缺乏支持",
    "unsupported"
  ],
  [
    "保存研究发现",
    "Save finding"
  ],
  [
    "归类研究发现",
    "Group findings"
  ],
  [
    "综合类别",
    "Synthesize categories"
  ],
  [
    "单组事件数", "One-group events", "Événements dans un seul groupe", "События в одной группе", "Eventos en un solo grupo", "単群イベント数", "Eventos em um único grupo", "Ereignisse in einer Gruppe", "Догађаји у једној групи", "단일군 사건 수"
  ],
  [
    "单组总人数",
    "One-group total"
  ],
  [
    "人时", "Person-time", "Personne-temps", "Человеко-время", "Persona-tiempo", "人時間", "Pessoa-tempo", "Personenzeit", "Особа-време", "인시"
  ],
  [
    "例如：95",
    "e.g. 95"
  ],
  [
    "例如：人年",
    "e.g. person-years"
  ],
  [
    "作者、年份；表格或页码", "Author, year; table or page", "Auteur, année ; tableau ou page", "Автор, год; таблица или страница", "Autor, año; tabla o página", "著者、年；表またはページ", "Autor, ano; tabela ou página", "Autor, Jahr; Tabelle oder Seite", "Аутор, година; табела или страница", "저자, 연도; 표 또는 페이지"
  ],
  [
    "参考治疗方案", "Reference treatment", "Traitement de référence", "Референтное лечение", "Tratamiento de referencia", "参照治療", "Tratamento de referência", "Referenzbehandlung", "Референтна терапија", "기준 치료"
  ],
  [
    "偏倚风险评价的比较组", "Comparison for risk of bias", "Groupe de comparaison pour l’évaluation du risque de biais", "Сравнение при оценке риска систематической ошибки", "Comparación para la evaluación del riesgo de sesgo", "バイアスリスク評価の比較群", "Comparação para a avaliação do risco de viés", "Vergleichsgruppe für die Bewertung des Verzerrungsrisikos", "Група за поређење у процени ризика од пристрасности", "비뚤림 위험 평가의 비교군"
  ],
  [
    "评价框架", "Framework", "Cadre d’évaluation", "Система оценки", "Marco de evaluación", "評価枠組み", "Estrutura de avaliação", "Bewertungsrahmen", "Оквир процене", "평가 체계"
  ],
  [
    "来源报告",
    "Source report"
  ],
  [
    "支持该判断的页码、表格或附录", "Page, table or appendix supporting this judgement", "Page, tableau ou annexe étayant ce jugement", "Страница, таблица или приложение, подтверждающие эту оценку", "Página, tabla o anexo que respalda este juicio", "判定を裏付けるページ、表、または付録", "Página, tabela ou apêndice que fundamenta este julgamento", "Seite, Tabelle oder Anhang als Beleg für diese Beurteilung", "Страница, табела или додатак који поткрепљује ову процену", "판정을 뒷받침하는 페이지, 표 또는 부록"
  ],
  [
    "偏倚风险判断的来源页码、表格或附录", "Risk-of-bias source page, table or appendix", "Page, tableau ou annexe source du jugement de risque de biais", "Страница, таблица или приложение — источник оценки риска систематической ошибки", "Página, tabla o anexo fuente del juicio sobre el riesgo de sesgo", "バイアスリスク判定の根拠となるページ、表、または付録", "Página, tabela ou apêndice de origem do julgamento do risco de viés", "Seite, Tabelle oder Anhang als Quelle der Risikobeurteilung", "Страница, табела или додатак са извором процене ризика од пристрасности", "비뚤림 위험 판정의 근거가 되는 페이지, 표 또는 부록"
  ],
  [
    "总体判断", "Overall judgement", "Jugement global", "Общая оценка", "Valoración global", "全体判定", "Julgamento geral", "Gesamtbewertung", "Укупна процена", "전반적 판정"
  ],
  [
    "总体判断依据", "Overall rationale", "Justification du jugement global", "Обоснование общей оценки", "Justificación de la valoración global", "全体判定の根拠", "Justificativa do julgamento geral", "Begründung der Gesamtbewertung", "Образложење укупне процене", "전반적 판정 근거"
  ],
  [
    "偏倚风险敏感性分析的评价框架", "Risk-of-bias sensitivity framework", "Cadre d’analyse de sensibilité au risque de biais", "Система анализа чувствительности к риску систематической ошибки", "Marco de sensibilidad al riesgo de sesgo", "バイアスリスク感度分析の評価枠組み", "Estrutura da análise de sensibilidade ao risco de viés", "Rahmen für die Sensitivitätsanalyse zum Verzerrungsrisiko", "Оквир анализе осетљивости на ризик од пристрасности", "비뚤림 위험 민감도 분석 평가 체계"
  ],
  [
    "偏倚风险敏感性分析的合并模型", "Risk-of-bias sensitivity synthesis model", "Modèle de synthèse pour l’analyse de sensibilité au risque de biais", "Модель синтеза для анализа чувствительности к риску систематической ошибки", "Modelo de combinación para el análisis de sensibilidad al riesgo de sesgo", "バイアスリスク感度分析の統合モデル", "Modelo de síntese para a análise de sensibilidade ao risco de viés", "Synthesemodell für die Sensitivitätsanalyse zum Verzerrungsrisiko", "Модел синтезе за анализу осетљивости на ризик од пристрасности", "비뚤림 위험 민감도 분석 통합 모형"
  ],
  [
    "偏倚风险敏感性分析的区间方法", "Risk-of-bias sensitivity interval method", "Méthode d’intervalle pour l’analyse de sensibilité au risque de biais", "Метод интервала для анализа чувствительности к риску систематической ошибки", "Método de intervalo para el análisis de sensibilidad al riesgo de sesgo", "バイアスリスク感度分析の区間法", "Método de intervalo para a análise de sensibilidade ao risco de viés", "Intervallmethode für die Sensitivitätsanalyse zum Verzerrungsrisiko", "Метода интервала за анализу осетљивости на ризик од пристрасности", "비뚤림 위험 민감도 분석 구간 방법"
  ],
  [
    "诊断研究", "Diagnostic study", "Étude diagnostique", "Диагностическое исследование", "Estudio diagnóstico", "診断研究", "Estudo diagnóstico", "Diagnosestudie", "Дијагностичка студија", "진단 연구"
  ],
  [
    "待评价试验",
    "Index test"
  ],
  [
    "目标疾病或状况", "Target condition", "Affection cible", "Целевое заболевание или состояние", "Afección de interés", "対象とする疾患または状態", "Condição-alvo", "Zielerkrankung oder Zielzustand", "Циљна болест или стање", "대상 질환 또는 상태"
  ],
  [
    "阈值及单位", "Threshold and unit", "Seuil et unité", "Порог и единица измерения", "Umbral y unidad", "閾値と単位", "Limiar e unidade", "Schwellenwert und Einheit", "Праг и јединица мере", "임계값 및 단위"
  ],
  [
    "参考标准",
    "Reference standard"
  ],
  [
    "来源页码、表格或图号",
    "Source page, table or figure"
  ],
  [
    "诊断研究的来源页码、表格或图号", "Diagnostic source page, table or figure", "Page, tableau ou figure source de l’étude diagnostique", "Страница, таблица или номер рисунка в источнике диагностического исследования", "Página, tabla o figura fuente del estudio diagnóstico", "診断研究の出典ページ、表、または図番号", "Página, tabela ou figura de origem do estudo diagnóstico", "Quellenseite, Tabelle oder Abbildungsnummer der Diagnosestudie", "Страница, табела или број слике у извору дијагностичке студије", "진단 연구의 출처 페이지, 표 또는 그림 번호"
  ],
  [
    "真阳性", "True positives", "Vrais positifs", "Истинно положительные", "Verdaderos positivos", "真陽性", "Verdadeiros positivos", "Richtig-Positive", "Истински позитивни", "진양성"
  ],
  [
    "假阳性", "False positives", "Faux positifs", "Ложноположительные", "Falsos positivos", "偽陽性", "Falsos positivos", "Falsch-Positive", "Лажно позитивни", "위양성"
  ],
  [
    "假阴性", "False negatives", "Faux négatifs", "Ложноотрицательные", "Falsos negativos", "偽陰性", "Falsos negativos", "Falsch-Negative", "Лажно негативни", "위음성"
  ],
  [
    "真阴性", "True negatives", "Vrais négatifs", "Истинно отрицательные", "Verdaderos negativos", "真陰性", "Verdadeiros negativos", "Richtig-Negative", "Истински негативни", "진음성"
  ],
  [
    "敏感度与假阳性率", "Sensitivity versus false-positive rate", "Sensibilité et taux de faux positifs", "Чувствительность и доля ложноположительных результатов", "Sensibilidad frente a la tasa de falsos positivos", "感度と偽陽性率", "Sensibilidade versus taxa de falsos positivos", "Sensitivität und Falsch-Positiv-Rate", "Осетљивост и стопа лажно позитивних резултата", "민감도 대 위양성률"
  ],
  [
    "各研究所选数值阈值的 JSON", "Selected numeric threshold by study JSON", "JSON des seuils numériques sélectionnés par étude", "JSON с выбранными числовыми порогами для каждого исследования", "JSON de los umbrales numéricos seleccionados por estudio", "研究ごとに選択した数値閾値の JSON", "JSON dos limiares numéricos selecionados por estudo", "JSON der ausgewählten numerischen Schwellenwerte nach Studie", "JSON са изабраним нумеричким праговима за сваку студију", "연구별로 선택한 수치 임계값 JSON"
  ],
  [
    "定性研究",
    "Qualitative study"
  ],
  [
    "研究发现的可信度",
    "Finding credibility"
  ],
  [
    "研究发现",
    "Study finding"
  ],
  [
    "来源原文引述",
    "Source quotation"
  ],
  [
    "页码或段落",
    "Page or paragraph"
  ],
  [
    "类别名称",
    "Category label"
  ],
  [
    "纳入该类别的研究发现",
    "Findings for category"
  ],
  [
    "综合研究发现",
    "Synthesized finding"
  ],
  [
    "纳入综合的类别",
    "Categories for synthesis"
  ],
  [
    "随机化过程", "randomization process", "Processus de randomisation", "Процесс рандомизации", "Proceso de aleatorización", "ランダム化過程", "Processo de randomização", "Randomisierungsprozess", "Поступак рандомизације", "무작위화 과정"
  ],
  [
    "偏离预期干预", "deviations from intended interventions", "Écarts par rapport aux interventions prévues", "Отклонения от запланированных вмешательств", "Desviaciones de las intervenciones previstas", "意図した介入からの逸脱", "Desvios das intervenções pretendidas", "Abweichungen von den vorgesehenen Interventionen", "Одступања од предвиђених интервенција", "의도한 중재에서의 이탈"
  ],
  [
    "结局数据缺失", "missing outcome data", "Données de résultat manquantes", "Отсутствующие данные об исходах", "Datos de desenlace faltantes", "アウトカムデータの欠測", "Dados de desfecho ausentes", "Fehlende Ergebnisdaten", "Недостајући подаци о исходима", "결과 자료 결측"
  ],
  [
    "结局测量",
    "measurement of outcome"
  ],
  [
    "已报告结果的选择", "selection of reported result", "Sélection du résultat rapporté", "Выбор представленного результата", "Selección del resultado comunicado", "報告結果の選択", "Seleção do resultado relatado", "Auswahl des berichteten Ergebnisses", "Избор пријављеног резултата", "보고된 결과의 선택"
  ],
  [
    "混杂", "confounding", "Facteurs de confusion", "Смешение", "Factores de confusión", "交絡", "Fatores de confusão", "Confounding", "Конфундирање", "교란"
  ],
  [
    "参与者选择", "selection of participants", "Sélection des participants", "Отбор участников", "Selección de participantes", "参加者の選択", "Seleção dos participantes", "Auswahl der Teilnehmenden", "Избор учесника", "참여자 선택"
  ],
  [
    "干预分类", "classification of interventions", "Classification des interventions", "Классификация вмешательств", "Clasificación de las intervenciones", "介入の分類", "Classificação das intervenções", "Klassifikation der Interventionen", "Класификација интервенција", "중재 분류"
  ],
  [
    "数据缺失", "missing data", "Données manquantes", "Отсутствующие данные", "Datos faltantes", "欠測データ", "Dados ausentes", "Fehlende Daten", "Недостајући подаци", "결측 자료"
  ],
  [
    "结局测量",
    "measurement of outcomes"
  ],
  [
    "受试者选择", "patient selection", "Sélection des patients", "Отбор пациентов", "Selección de pacientes", "患者選択", "Seleção de pacientes", "Patientenauswahl", "Избор пацијената", "환자 선택"
  ],
  [
    "待评价试验",
    "index test"
  ],
  [
    "参考标准",
    "reference standard"
  ],
  [
    "研究流程与时序", "flow and timing", "Déroulement et calendrier", "Ход исследования и сроки", "Flujo y calendario", "研究の流れとタイミング", "Fluxo e cronologia", "Ablauf und zeitlicher Verlauf", "Ток и временски распоред", "연구 흐름 및 시점"
  ],
  [
    "适用性：受试者选择", "applicability: patient selection", "Applicabilité : sélection des patients", "Применимость: отбор пациентов", "Aplicabilidad: selección de pacientes", "適用可能性：患者選択", "Aplicabilidade: seleção de pacientes", "Anwendbarkeit: Patientenauswahl", "Применљивост: избор пацијената", "적용 가능성: 환자 선택"
  ],
  [
    "适用性：待评价试验", "applicability: index test", "Applicabilité : test index", "Применимость: индексный тест", "Aplicabilidad: prueba índice", "適用可能性：指標検査", "Aplicabilidade: teste índice", "Anwendbarkeit: Indextest", "Применљивост: индексни тест", "적용 가능성: 지표검사"
  ],
  [
    "适用性：参考标准", "applicability: reference standard", "Applicabilité : étalon de référence", "Применимость: референсный стандарт", "Aplicabilidad: estándar de referencia", "適用可能性：参照基準", "Aplicabilidade: padrão de referência", "Anwendbarkeit: Referenzstandard", "Применљивост: референтни стандард", "적용 가능성: 참조기준"
  ],
  [
    "选择判断", "Choose judgment", "Choisir un jugement", "Выберите оценку", "Elegir una valoración", "判定を選択", "Escolher um julgamento", "Beurteilung auswählen", "Изабери процену", "판정 선택"
  ],
  [
    "判断依据与证据", "Rationale / evidence", "Justification / éléments probants", "Обоснование и доказательства", "Justificación / evidencia", "判定の根拠とエビデンス", "Justificativa / evidências", "Begründung und Belege", "Образложење и докази", "판정 근거 및 증거"
  ],
  [
    "低", "low", "faible", "Низкий", "bajo", "低い", "baixo", "Niedrig", "Низак", "낮음"
  ],
  [
    "存在一定疑虑", "some concerns", "quelques préoccupations", "Некоторые опасения", "algunas preocupaciones", "懸念あり", "algumas preocupações", "Einige Bedenken", "Извесне забринутости", "일부 우려"
  ],
  [
    "高", "high", "élevé", "Высокий", "alto", "高い", "alto", "Hoch", "Висок", "높음"
  ],
  [
    "中等", "moderate", "modéré", "Умеренный", "moderado", "中等度", "moderado", "Moderat", "Умерен", "중등도"
  ],
  [
    "严重", "serious", "sérieux", "Серьёзный", "grave", "深刻", "grave", "Schwerwiegend", "Озбиљан", "심각"
  ],
  [
    "极严重", "critical", "critique", "Критический", "crítico", "重大", "crítico", "Kritisch", "Критичан", "치명적"
  ],
  [
    "无信息", "no information", "aucune information", "Нет информации", "sin información", "情報なし", "sem informação", "Keine Informationen", "Нема информација", "정보 없음"
  ],
  [
    "不明确", "unclear", "incertain", "Неясно", "incierto", "不明", "incerto", "Unklar", "Нејасно", "불명확"
  ],
  [
    "{0}：判断", "{0} judgement", "Jugement : {0}", "{0}: оценка", "Valoración: {0}", "{0}：判定", "Julgamento: {0}", "{0}: Beurteilung", "{0}: процена", "{0}: 판정"
  ],
  [
    "{0}：判断依据", "{0} rationale", "Justification : {0}", "{0}: обоснование", "Justificación: {0}", "{0}：判定の根拠", "Justificativa: {0}", "{0}: Begründung", "{0}: образложење", "{0}: 판정 근거"
  ],
  [
    "请选择研究发现的可信度。",
    "Choose finding credibility."
  ],
  [
    "请选择总体偏倚风险判断。", "Choose an overall risk-of-bias judgment.", "Sélectionnez un jugement global sur le risque de biais.", "Выберите общую оценку риска систематической ошибки.", "Seleccione un juicio global sobre el riesgo de sesgo.", "全体的なバイアスリスク判定を選択してください。", "Selecione um julgamento global do risco de viés.", "Wählen Sie eine Gesamtbewertung des Verzerrungsrisikos aus.", "Изаберите укупну процену ризика од пристрасности.", "전반적인 비뚤림 위험 판정을 선택하세요."
  ],
  [
    "四格表已按先前输入保存，请核对后再继续。", "DTA table saved from earlier inputs. Review the saved table before continuing.", "Le tableau 2×2 a été enregistré à partir des données précédentes. Vérifiez-le avant de continuer.", "Таблица сопряжённости для диагностического исследования сохранена по предыдущим введённым данным. Проверьте её перед продолжением.", "La tabla 2×2 se guardó a partir de los datos anteriores. Revísela antes de continuar.", "以前の入力から 2×2 分割表を保存しました。続行する前に保存済みの表を確認してください。", "A tabela 2×2 foi salva com base nos dados anteriores. Confira-a antes de continuar.", "Die diagnostische Vierfeldertafel wurde mit den vorherigen Eingaben gespeichert. Prüfen Sie die gespeicherte Tabelle, bevor Sie fortfahren.", "Табела четири поља за дијагностичку тачност сачувана је на основу претходних уноса. Проверите је пре наставка.", "이전에 입력한 2×2 분할표를 저장했습니다. 계속하기 전에 저장된 표를 검토하세요."
  ],
  [
    "研究资料已按先前输入保存。再次保存前，请核对研究列表。", "Saved the previous study inputs. Review the study list before saving again.", "Les données précédentes de l’étude ont été enregistrées. Vérifiez la liste des études avant un nouvel enregistrement.", "Данные исследований сохранены по предыдущим введённым данным. Перед повторным сохранением проверьте список исследований.", "Se guardaron los datos anteriores del estudio. Revise la lista de estudios antes de volver a guardar.", "以前の研究入力を保存しました。再度保存する前に研究一覧を確認してください。", "Os dados anteriores do estudo foram salvos. Confira a lista de estudos antes de salvar novamente.", "Die Studiendaten wurden mit den vorherigen Eingaben gespeichert. Prüfen Sie vor dem erneuten Speichern die Studienliste.", "Подаци о студијама сачувани су на основу претходних уноса. Проверите списак студија пре поновног чувања.", "이전 연구 입력을 저장했습니다. 다시 저장하기 전에 연구 목록을 검토하세요."
  ],
  [
    "请选择编码维度。", "Choose a coding dimension.", "Veuillez choisir une dimension de codage.", "Выберите измерение кодирования.", "Seleccione una dimensión de codificación.", "コード化軸を選択してください。", "Selecione uma dimensão de codificação.", "Wählen Sie eine Kodierungsdimension aus.", "Изаберите димензију кодирања.", "코딩 차원을 선택하세요."
  ],
  [
    "请选择第二个编码维度。", "Choose a second coding dimension.", "Sélectionnez une deuxième dimension de codage.", "Выберите второе измерение кодирования.", "Seleccione una segunda dimensión de codificación.", "2つ目のコード化軸を選択してください。", "Selecione uma segunda dimensão de codificação.", "Wählen Sie eine zweite Kodierungsdimension aus.", "Изаберите другу димензију кодирања.", "두 번째 코딩 차원을 선택하세요."
  ],
  [
    "请填写估计值与标准误。", "Estimate and SE are required", "L’estimation et l’erreur-type sont obligatoires.", "Введите оценку и стандартную ошибку.", "La estimación y el error estándar son obligatorios.", "推定値と標準誤差を入力してください。", "A estimativa e o erro padrão são obrigatórios.", "Geben Sie Schätzwert und Standardfehler ein.", "Унесите процену и стандардну грешку.", "추정치와 표준오차를 입력하세요."
  ],
  [
    "请选择效应方向与比较组的对应关系。",
    "Choose how the effect direction matches Comparison."
  ],
  [
    "请选择来源报告中的标准化均值差方法。", "Choose the SMD method shown in the source report.", "Sélectionnez la méthode de différence moyenne standardisée indiquée dans le rapport source.", "Выберите метод стандартизованной разности средних, указанный в исходном отчёте.", "Seleccione el método de diferencia de medias estandarizada que aparece en el informe fuente.", "原資料の報告に示された標準化平均差の方法を選択してください。", "Selecione o método de diferença de médias padronizada indicado no relatório de origem.", "Wählen Sie die im Quellenbericht angegebene standardisierte Mittelwertdifferenz-Methode aus.", "Изаберите методу стандардизоване разлике средина наведену у изворном извештају.", "원자료 보고서에 제시된 표준화 평균 차이 방법을 선택하세요."
  ],
  [
    "效应量已按先前输入保存。再次保存前，请核对效应量列表。", "Saved the previous effect inputs. Review the effect list before saving again.", "Les données précédentes de l’effet ont été enregistrées. Vérifiez la liste des effets avant un nouvel enregistrement.", "Данные об эффектах сохранены по предыдущему вводу. Перед повторным сохранением проверьте список эффектов.", "Se guardaron los datos anteriores del efecto. Revise la lista de efectos antes de volver a guardar.", "以前の効果量入力を保存しました。再度保存する前に効果量一覧を確認してください。", "Os dados anteriores do efeito foram salvos. Confira a lista de efeitos antes de salvar novamente.", "Die vorherigen Effektdaten wurden gespeichert. Prüfen Sie vor dem erneuten Speichern die Effektliste.", "Подаци о ефектима сачувани су на основу претходног уноса. Проверите списак ефеката пре поновног чувања.", "이전 효과 입력을 저장했습니다. 다시 저장하기 전에 효과 목록을 검토하세요."
  ],
  [
    "请选择二分类结局的合并方法。", "Choose a binary pooling method.", "Sélectionnez une méthode de synthèse pour les critères binaires.", "Выберите метод объединения бинарных исходов.", "Seleccione un método de síntesis para los desenlaces binarios.", "二値アウトカムの統合方法を選択してください。", "Selecione um método de síntese para desfechos binários.", "Wählen Sie eine Pooling-Methode für binäre Endpunkte aus.", "Изаберите методу обједињавања бинарних исхода.", "이분형 결과의 통합 방법을 선택하세요."
  ],
  [
    "Mantel–Haenszel 支持 RR、OR 或 RD；Peto 仅支持 OR。", "Mantel–Haenszel supports RR, OR or RD; Peto supports OR only.", "Mantel–Haenszel prend en charge RR, OR ou RD ; Peto prend uniquement en charge OR.", "Mantel–Haenszel поддерживает RR, OR или RD; метод Peto — только OR.", "Mantel–Haenszel admite RR, OR o RD; Peto solo admite OR.", "Mantel–Haenszel 法は RR、OR、RD に対応します。Peto 法は OR のみ対応します。", "Mantel–Haenszel aceita RR, OR ou RD; Peto aceita apenas OR.", "Mantel–Haenszel unterstützt RR, OR oder RD; Peto unterstützt nur OR.", "Mantel–Haenszel подржава RR, OR или RD; Peto подржава само OR.", "Mantel–Haenszel은 RR, OR 또는 RD를 지원하고 Peto는 OR만 지원합니다."
  ],
  [
    "已排除的研究：", "Excluded studies:", "Études exclues :", "Исключённые исследования:", "Estudios excluidos:", "除外した研究：", "Estudos excluídos:", "Ausgeschlossene Studien:", "Искључене студије:", "제외된 연구:"
  ],
  [
    "治疗组瞬时风险率／对照组瞬时风险率", "Treatment event hazard / control event hazard", "Risque instantané des événements dans le groupe traité / groupe témoin", "Интенсивность событий в группе лечения / интенсивность событий в контрольной группе", "Riesgo instantáneo de eventos en el grupo de tratamiento / grupo de control", "治療群のイベントハザード／対照群のイベントハザード", "Taxa de risco instantâneo de eventos no grupo de tratamento / grupo controle", "Hazard in der Behandlungsgruppe / Hazard in der Kontrollgruppe", "Интензитет догађаја у терапијској групи / интензитет догађаја у контролној групи", "치료군 사건 위험률 / 대조군 사건 위험률"
  ],
  [
    "事件数来源", "event-count source", "Source du nombre d’événements", "Источник числа событий", "Fuente del recuento de eventos", "イベント数の出典", "Fonte da contagem de eventos", "Quelle der Ereigniszahl", "Извор броја догађаја", "사건 수 출처"
  ],
  [
    "报告的事件数",
    "Reported event count"
  ],
  [
    "以 n/2 近似事件数（敏感性分析）", "Approximate events as n/2 (sensitivity)", "Approximer le nombre d’événements par n/2 (analyse de sensibilité)", "Приблизить число событий как n/2 (анализ чувствительности)", "Aproximar los eventos por n/2 (análisis de sensibilidad)", "イベント数を n/2 と近似（感度分析）", "Aproximar o número de eventos por n/2 (análise de sensibilidade)", "Ereigniszahl näherungsweise als n/2 ansetzen (Sensitivitätsanalyse)", "Приближно узети број догађаја као n/2 (анализа осетљивости)", "사건 수를 n/2로 근사(민감도 분석)"
  ],
  [
    "报告的事件数",
    "reported events"
  ],
  [
    "请填写所有可见字段，并明确选择假设和事件数来源。", "Complete all visible fields and explicitly choose assumptions and event sources.", "Renseignez tous les champs visibles et indiquez explicitement les hypothèses et la source des nombres d’événements.", "Заполните все видимые поля и явно укажите допущения и источники числа событий.", "Complete todos los campos visibles e indique explícitamente los supuestos y la fuente de los eventos.", "表示されているすべての項目を入力し、仮定とイベント数の出典を明示的に選択してください。", "Preencha todos os campos visíveis e indique explicitamente as suposições e a fonte dos eventos.", "Füllen Sie alle sichtbaren Felder aus und wählen Sie Annahmen und Ereignisquellen ausdrücklich aus.", "Попуните сва видљива поља и изричито изаберите претпоставке и изворе броја догађаја.", "표시된 모든 항목을 입력하고 가정과 사건 수 출처를 명시적으로 선택하세요."
  ],
  [
    "中位数须为正值，计数须为正整数。", "Medians must be positive and counts must be positive integers.", "Les médianes doivent être positives et les nombres doivent être des entiers strictement positifs.", "Медианы должны быть положительными, а числа событий — положительными целыми числами.", "Las medianas deben ser positivas y los recuentos deben ser enteros positivos.", "中央値は正の値、件数は正の整数で入力してください。", "As medianas devem ser positivas e as contagens devem ser números inteiros positivos.", "Mediane müssen positiv sein; Zählwerte müssen positive ganze Zahlen sein.", "Медијане морају бити позитивне, а бројеви догађаја позитивни цели бројеви.", "중앙값은 양수, 개수는 양의 정수여야 합니다."
  ],
  [
    "请选择风险比方向与比较组的对应关系。", "Choose how the HR direction maps to Comparison.", "Sélectionnez la correspondance entre le sens du HR et le groupe de comparaison.", "Выберите, как направление HR соотносится с группой сравнения.", "Seleccione cómo se relaciona la dirección del HR con la comparación.", "HR の方向と比較群との対応を選択してください。", "Selecione como a direção do HR corresponde ao grupo de comparação.", "Wählen Sie die Zuordnung der HR-Richtung zur Vergleichsgruppe aus.", "Изаберите однос смера HR и групе за поређење.", "HR 방향이 비교군에 어떻게 대응하는지 선택하세요."
  ],
  [
    "请在上方记录来源表格或页码及事件定义。", "Record source table/page and event definition above.", "Indiquez ci-dessus le tableau ou la page source ainsi que la définition de l’événement.", "Укажите выше таблицу или страницу источника и определение события.", "Indique arriba la tabla o página fuente y la definición del evento.", "出典の表またはページとイベントの定義を上に記録してください。", "Informe acima a tabela ou página de origem e a definição do evento.", "Geben Sie oben die Quellentabelle oder Seite sowie die Ereignisdefinition an.", "Изнад наведите табелу или страницу извора и дефиницију догађаја.", "원자료 표 또는 페이지와 사건 정의를 위에 기록하세요."
  ],
  [
    "生存数据摘要", "survival summary", "Résumé de survie", "Сводка по данным выживаемости", "Resumen de supervivencia", "生存データの要約", "Resumo de sobrevida", "Zusammenfassung der Überlebensdaten", "Сажетак података о преживљавању", "생존 자료 요약"
  ],
  [
    "先前输入",
    "previous inputs"
  ],
  [
    "已保存先前输入。再次保存前，请核对效应量列表。", "Saved the previous inputs. Review the effect list before saving again.", "Les données précédentes ont été enregistrées. Vérifiez la liste des effets avant un nouvel enregistrement.", "Предыдущие данные сохранены. Перед повторным сохранением проверьте список эффектов.", "Se guardaron los datos anteriores. Revise la lista de efectos antes de volver a guardar.", "以前の入力を保存しました。再度保存する前に効果量一覧を確認してください。", "Os dados anteriores foram salvos. Confira a lista de efeitos antes de salvar novamente.", "Der vorherige Eintrag wurde gespeichert. Prüfen Sie vor dem erneuten Speichern die Effektliste.", "Претходни унос је сачуван. Проверите списак ефеката пре поновног чувања.", "이전 입력을 저장했습니다. 다시 저장하기 전에 효과 목록을 검토하세요."
  ],
  [
    "只读", "Read-only", "Lecture seule", "Только чтение", "Solo lectura", "読み取り専用", "Somente leitura", "Schreibgeschützt", "Само за читање", "읽기 전용"
  ],
  [
    "缺少标准误或区间，不能进入合并分析。", "No SE or interval; cannot enter synthesis.", "Erreur-type ou intervalle manquant ; impossible de procéder à la synthèse.", "Без стандартной ошибки или интервала результат нельзя включить в синтез.", "Falta el error estándar o el intervalo; no se puede realizar la síntesis.", "標準誤差または区間がないため、統合解析に使用できません。", "Falta o erro padrão ou o intervalo; não é possível fazer a síntese.", "Ohne Standardfehler oder Intervall kann das Ergebnis nicht in die Synthese eingehen.", "Без стандардне грешке или интервала резултат се не може укључити у синтезу.", "표준오차나 구간이 없어 통합 분석에 사용할 수 없습니다."
  ],
  [
    "近似的对数尺度标准误：", "Approximate log-scale SE:", "Erreur-type approximative sur l’échelle logarithmique :", "Приближённый SE на логарифмической шкале:", "Error estándar aproximado en escala logarítmica:", "近似対数尺度標準誤差：", "Erro padrão aproximado na escala logarítmica:", "Näherungsweiser SE auf der Log-Skala:", "Приближни SE на лог скали:", "근사 로그 척도 표준오차:"
  ],
  [
    "Wilson 得分区间", "Wilson score", "Intervalle de score de Wilson", "Score-интервал Уилсона", "Intervalo de puntuación de Wilson", "Wilson スコア区間", "Intervalo de escore de Wilson", "Wilson-Score-Intervall", "Wilson score интервал", "Wilson 점수 구간"
  ],
  [
    "Jeffreys 等尾可信区间", "Jeffreys equal-tailed credible interval", "Intervalle de crédibilité de Jeffreys à queues égales", "Равнохвостовой апостериорный интервал Джеффриса", "Intervalo de credibilidad de Jeffreys de colas iguales", "Jeffreys 等尾ベイズ信用区間", "Intervalo de credibilidade de Jeffreys de caudas iguais", "Jeffreys-Kredibilitätsintervall mit gleichen Randwahrscheinlichkeiten", "Jeffreys-ов равнорепни интервал веродостојности", "Jeffreys 등꼬리 베이지안 신용구간"
  ],
  [
    "Garwood 精确区间（保守；事件数不超过 2 时推荐）", "Garwood exact (conservative; recommended for ≤2 events)", "Intervalle exact de Garwood (conservateur ; recommandé pour ≤2 événements)", "Точный интервал Garwood (консервативный; рекомендуется при числе событий ≤ 2)", "Intervalo exacto de Garwood (conservador; recomendado para ≤2 eventos)", "Garwood の正確区間（保守的；イベント数が2以下の場合に推奨）", "Intervalo exato de Garwood (conservador; recomendado para ≤2 eventos)", "Exaktes Garwood-Intervall (konservativ; empfohlen bei höchstens 2 Ereignissen)", "Гарвудов тачни интервал (конзервативан; препоручује се за ≤ 2 догађаја)", "Garwood 정확 구간(보수적; 사건 수가 2 이하일 때 권장)"
  ],
  [
    "Byar 近似", "Byar approximation", "Approximation de Byar", "Аппроксимация Байара", "Aproximación de Byar", "Byar 近似", "Aproximação de Byar", "Näherung nach Byar", "Byar апроксимација", "Byar 근사"
  ],
  [
    "Newcombe 混合得分区间（研究层面推荐；不校正）", "Newcombe hybrid score (recommended study-level interval; no correction)", "Intervalle hybride score de Newcombe (recommandé au niveau de l’étude ; sans correction)", "Гибридный score-интервал Newcombe (рекомендуется на уровне исследования; без поправки)", "Intervalo híbrido de puntuación de Newcombe (recomendado a nivel de estudio; sin corrección)", "Newcombe の混合スコア区間（研究レベルで推奨；補正なし）", "Intervalo híbrido baseado em escore de Newcombe (recomendado no nível do estudo; sem correção)", "Hybrides Newcombe-Score-Intervall (für die Studienebene empfohlen; ohne Korrektur)", "Newcombe хибридни score интервал (препоручује се на нивоу студије; без корекције)", "Newcombe 혼합 점수 구간(연구 수준에서 권장; 보정 없음)"
  ],
  [
    "Wald 区间（非合并方差；仅作比较）", "Wald (unpooled; comparison only)", "Wald (sans variance combinée ; comparaison uniquement)", "Интервал Wald (без объединения дисперсий; только для сравнения)", "Wald (varianzas no combinadas; solo para comparación)", "Wald 区間（分散をプールしない；比較用のみ）", "Wald (variâncias não combinadas; apenas para comparação)", "Wald-Intervall (Varianzen nicht gepoolt; nur zum Vergleich)", "Wald интервал (необједињена варијанса; само за поређење)", "Wald 구간(분산을 통합하지 않음; 비교용만)"
  ],
  [
    "条件精确区间（保守）", "Conditional exact (conservative)", "Intervalle exact conditionnel (conservateur)", "Условный точный интервал (консервативный)", "Intervalo exacto condicional (conservador)", "条件付き正確区間（保守的）", "Intervalo exato condicional (conservador)", "Bedingtes exaktes Intervall (konservativ)", "Условни тачни интервал (конзервативан)", "조건부 정확 구간(보수적)"
  ],
  [
    "条件 mid-P 区间", "Conditional mid-P", "Intervalle conditionnel mid-P", "Условный интервал mid-P", "Intervalo mid-P condicional", "条件付き mid-P 区間", "Intervalo mid-P condicional", "Bedingtes mid-P-Intervall", "Условни mid-P интервал", "조건부 mid-P 구간"
  ],
  [
    "治疗组事件数",
    "Treatment events"
  ],
  [
    "治疗组总人时", "Treatment total person-time", "Temps-personne total du groupe traité", "Общее человеко-время группы лечения", "Persona-tiempo total del grupo de tratamiento", "治療群の総人時間", "Pessoa-tempo total do grupo de tratamento", "Gesamte Personenzeit der Behandlungsgruppe", "Укупно особа-време терапијске групе", "치료군 총 인시"
  ],
  [
    "治疗组总人数",
    "Treatment total people"
  ],
  [
    "总人时",
    "Total person-time"
  ],
  [
    "总人数",
    "Total people"
  ],
  [
    "对照组总人时", "Control total person-time", "Temps-personne total du groupe témoin", "Общее человеко-время контрольной группы", "Persona-tiempo total del grupo de control", "対照群の総人時間", "Pessoa-tempo total do grupo de controlee", "Gesamte Personenzeit der Kontrollgruppe", "Укупно особа-време контролне групе", "대조군 총 인시"
  ],
  [
    "对照组总人数",
    "Control total people"
  ],
  [
    "请选择估计量和区间方法。", "Choose a quantity and an interval method.", "Choisissez une mesure et une méthode d’intervalle.", "Выберите показатель эффекта и метод построения интервала.", "Seleccione una medida y un método de intervalo.", "対象量と区間法を選択してください。", "Escolha uma medida e um método de intervalo.", "Wählen Sie ein Effektmaß und ein Intervallverfahren aus.", "Изаберите меру ефекта и метод за интервал.", "추정할 지표와 구간 방법을 선택하세요."
  ],
  [
    "请填写事件数、暴露量和区间水平。", "Enter the event count, exposure and interval level.", "Saisissez le nombre d’événements, l’exposition et le niveau de l’intervalle.", "Введите число событий, экспозицию и уровень интервала.", "Introduzca el número de eventos, la exposición y el nivel del intervalo.", "イベント数、曝露量、区間水準を入力してください。", "Informe o número de eventos, a exposição e o nível do intervalo.", "Geben Sie die Ereigniszahl, die Exposition und das Intervallniveau ein.", "Унесите број догађаја, експозицију и ниво интервала.", "사건 수, 노출량 및 구간 수준을 입력하세요."
  ],
  [
    "事件数须为非负安全整数。", "Events must be a nonnegative safe integer.", "Le nombre d’événements doit être un entier sûr non négatif.", "Число событий должно быть безопасным неотрицательным целым числом.", "El número de eventos debe ser un entero seguro no negativo.", "イベント数は 0 以上の安全な整数で入力してください。", "O número de eventos deve ser um inteiro seguro não negativo.", "Ereignisse müssen eine nichtnegative sichere Ganzzahl sein.", "Број догађаја мора бити безбедан ненегативан цео број.", "사건 수는 0 이상의 안전한 정수여야 합니다."
  ],
  [
    "暴露量须为正值；总人数须为不小于事件数的整数。", "Exposure must be positive; total people must be an integer at least as large as events.", "L’exposition doit être positive ; le nombre total de personnes doit être un entier au moins égal au nombre d’événements.", "Экспозиция должна быть положительной; общее число участников должно быть целым числом не меньше числа событий.", "La exposición debe ser positiva; el total de personas debe ser un entero mayor o igual que el número de eventos.", "曝露量は正の値にしてください。総人数はイベント数以上の整数である必要があります。", "A exposição deve ser positiva; o total de pessoas deve ser um inteiro pelo menos igual ao número de eventos.", "Die Exposition muss positiv sein; die Gesamtzahl der Personen muss eine ganze Zahl und mindestens so groß wie die Ereigniszahl sein.", "Експозиција мора бити позитивна; укупан број особа мора бити цео број и не мањи од броја догађаја.", "노출량은 양수여야 합니다. 총인원은 사건 수 이상의 정수여야 합니다."
  ],
  [
    "区间水平须大于 0 且小于 100。", "Interval level must be between 0 and 100, excluding the endpoints.", "Le niveau de l’intervalle doit être supérieur à 0 et inférieur à 100 ; les bornes sont exclues.", "Уровень интервала должен быть больше 0 и меньше 100.", "El nivel del intervalo debe ser mayor que 0 y menor que 100; se excluyen los extremos.", "区間水準は 0 より大きく 100 未満である必要があります。", "O nível do intervalo deve ser maior que 0 e menor que 100; os extremos não são aceitos.", "Das Intervallniveau muss größer als 0 und kleiner als 100 sein.", "Ниво интервала мора бити већи од 0 и мањи од 100.", "구간 수준은 0보다 크고 100보다 작아야 합니다."
  ],
  [
    "请填写人时单位。", "Enter the person-time unit.", "Saisissez l’unité de temps-personne.", "Введите единицу человеко-времени.", "Introduzca la unidad de persona-tiempo.", "人時間の単位を入力してください。", "Informe a unidade de pessoa-tempo.", "Geben Sie die Personenzeiteinheit ein.", "Унесите јединицу особа-времена.", "인시 단위를 입력하세요."
  ],
  [
    "对照组事件数须为非负整数，暴露量须为正值，总人数须为不小于事件数的整数。", "Control events must be a nonnegative integer. Exposure must be positive; total people must be an integer at least as large as events.", "Le nombre d’événements du groupe témoin doit être un entier non négatif. L’exposition doit être positive ; le nombre total de personnes doit être un entier au moins égal au nombre d’événements.", "Число событий в контрольной группе должно быть неотрицательным целым числом. Экспозиция должна быть положительной, а общее число участников — целым числом не меньше числа событий.", "Los eventos del grupo de control deben ser un entero no negativo. La exposición debe ser positiva; el total de personas debe ser un entero mayor o igual que los eventos.", "対照群のイベント数は0以上の整数で入力してください。曝露量は正の値、総人数はイベント数以上の整数である必要があります。", "Os eventos do grupo controle devem ser um inteiro não negativo. A exposição deve ser positiva; o total de pessoas deve ser um inteiro pelo menos igual ao número de eventos.", "Ereignisse in der Kontrollgruppe müssen eine nichtnegative ganze Zahl sein. Die Exposition muss positiv und die Gesamtzahl der Personen eine ganze Zahl sein, die mindestens der Ereigniszahl entspricht.", "Број догађаја у контролној групи мора бити ненегативан цео број. Експозиција мора бити позитивна, а укупан број особа цео број који није мањи од броја догађаја.", "대조군 사건 수는 0 이상의 정수여야 합니다. 노출량은 양수이고 총인원은 사건 수 이상의 정수여야 합니다."
  ],
  [
    "可信区间", "credible interval", "Intervalle de crédibilité", "Апостериорный интервал", "Intervalo de credibilidad", "信用区間", "Intervalo de credibilidade", "Kredibilitätsintervall", "Интервал веродостојности", "신용구간"
  ],
  [
    "置信区间",
    "confidence interval"
  ],
  [
    "（发生率比，治疗组／对照组）", "(rate ratio, treatment / control)", "(rapport de taux, groupe traité / groupe témoin)", "(отношение частот событий, лечение / контроль)", "(razón de tasas, tratamiento / control)", "（発生率比、治療群／対照群）", "(razão de taxas, tratamento / controle)", "(Inzidenzratenverhältnis, Behandlung / Kontrolle)", "(однос стопа догађаја, терапијска / контролна група)", "(발생률비, 치료군 / 대조군)"
  ],
  [
    "（风险差，治疗组减对照组）", "(risk difference, treatment − control)", "(différence de risques, groupe traité − groupe témoin)", "(разность рисков, лечение минус контроль)", "(diferencia de riesgos, tratamiento − control)", "（リスク差、治療群 − 対照群）", "(diferença de riscos, tratamento − controle)", "(Risikodifferenz, Behandlung minus Kontrolle)", "(разлика ризика, терапијска минус контролна група)", "(위험차, 치료군 − 대조군)"
  ],
  [
    "尚未记录，请在发表前补充研究报告。", "Not recorded; add the study report before publishing.", "Non renseigné ; ajoutez le rapport d’étude avant publication.", "Не указано; перед публикацией добавьте отчёт об исследовании.", "No se ha registrado; añada el informe del estudio antes de publicar.", "未記録です。公開前に研究報告を追加してください。", "Não informado; inclua o relatório do estudo antes de publicar.", "Nicht erfasst; ergänzen Sie den Studienbericht vor der Veröffentlichung.", "Није забележено; додајте извештај студије пре објављивања.", "기록되지 않았습니다. 공개하기 전에 연구 보고서를 추가하세요."
  ],
  [
    "请核对方法原文，并同时引用方法与研究报告。", "Check the original method source and cite it together with the study report.", "Vérifiez la méthode originale et citez à la fois la méthode et le rapport d’étude.", "Сверьтесь с первоисточником метода и укажите в ссылках и метод, и отчёт об исследовании.", "Verifique el método original y cite tanto el método como el informe del estudio.", "方法の原典を確認し、方法と研究報告の両方を引用してください。", "Confira o método original e cite tanto o método quanto o relatório do estudo.", "Prüfen Sie die ursprüngliche Methodenquelle und zitieren Sie sowohl die Methode als auch den Studienbericht.", "Проверите изворни опис методе и цитирајте и методу и извештај студије.", "방법 원문을 확인하고 방법과 연구 보고서를 모두 인용하세요."
  ],
  [
    "请选择单组计数类型。",
    "Choose a one-group count type."
  ],
  [
    "请填写事件数与暴露量。", "Events and exposure are required", "Le nombre d’événements et l’exposition sont obligatoires.", "Введите число событий и экспозицию.", "Se requieren el número de eventos y la exposición.", "イベント数と曝露量を入力してください。", "O número de eventos e a exposição são obrigatórios.", "Geben Sie Ereigniszahl und Exposition ein.", "Унесите број догађаја и експозицију.", "사건 수와 노출량을 입력하세요."
  ],
  [
    "该响应未提供似然比。", "Likelihood ratios unavailable for this response.", "Aucun rapport de vraisemblance n’est fourni pour cette réponse.", "Для этого ответа отношения правдоподобия недоступны.", "Esta respuesta no incluye razones de verosimilitud.", "この応答では尤度比を利用できません。", "Esta resposta não fornece razões de verossimilhança.", "Für diese Antwort sind keine Likelihood Ratios verfügbar.", "За овај одговор односи веродостојности нису доступни.", "이 응답에서는 우도비를 사용할 수 없습니다."
  ],
  [
    "汇总点的比值", "Summary-point ratio", "Rapport des estimations ponctuelles récapitulatives", "Отношение итоговых точечных оценок", "Razón entre las estimaciones puntuales resumidas", "要約点の比", "Razão entre as estimativas pontuais resumidas", "Verhältnis der zusammenfassenden Punktschätzungen", "Однос збирних тачкастих процена", "요약점 비율"
  ],
  [
    "不可用，请查看说明", "Unavailable — see explanation", "Indisponible — voir l’explication", "Недоступно; см. пояснение", "No disponible; consulte la explicación", "利用できません。説明を確認してください", "Indisponível — consulte a explicação", "Nicht verfügbar; Erläuterung anzeigen", "Није доступно; погледај објашњење", "사용할 수 없습니다 — 설명을 확인하세요"
  ],
  [
    "请在发表前核对方法原文，并引用方法与研究来源。", "Verify the original text and cite the method and study sources when publishing.", "Avant publication, vérifiez la méthode originale et citez la méthode ainsi que les sources des études.", "Перед публикацией сверьтесь с оригинальным текстом и укажите в ссылках метод и источники исследований.", "Antes de publicar, verifique el texto original y cite el método y las fuentes de los estudios.", "公開時には方法の原文を確認し、方法と研究資料を引用してください。", "Antes de publicar, confira o texto original e cite o método e as fontes dos estudos.", "Prüfen Sie vor der Veröffentlichung den Originaltext und zitieren Sie Methode und Studienquellen.", "Пре објављивања проверите изворни текст и цитирајте методу и изворе студија.", "공개할 때 방법 원문을 확인하고 방법과 연구 자료를 인용하세요."
  ],
  [
    "请核对并引用方法原文与来源报告。", "Verify and cite the original method and source report.", "Vérifiez et citez la méthode originale et le rapport source.", "Сверьтесь с исходным описанием метода и укажите ссылку на него и на исходный отчёт.", "Verifique y cite el método original y el informe fuente.", "方法の原典と出典報告を確認し、引用してください。", "Confira e cite o método original e o relatório de origem.", "Prüfen Sie die ursprüngliche Methode und zitieren Sie sie sowie den Quellenbericht.", "Проверите изворну методу и цитирајте је заједно са изворним извештајем.", "방법 원문과 출처 보고서를 확인하고 인용하세요."
  ],
  [
    "来源：",
    "Sources:"
  ],
  [
    "研究来源",
    "Study sources"
  ],
  [
    "请选择研究和来源报告。", "Choose a study and source report.", "Sélectionnez une étude et son rapport source.", "Выберите исследование и исходный отчёт.", "Seleccione un estudio y su informe fuente.", "研究と出典報告を選択してください。", "Selecione um estudo e seu relatório de origem.", "Wählen Sie eine Studie und einen Quellenbericht aus.", "Изаберите студију и изворни извештај.", "연구와 출처 보고서를 선택하세요."
  ],
  [
    "请填写研究发现、原文引述和位置。", "Enter a finding, its source quotation and its locator.", "Saisissez un résultat, sa citation source et son emplacement.", "Введите результат исследования, цитату из источника и место в источнике.", "Introduzca un hallazgo, la cita de la fuente y su localización.", "研究結果、出典からの引用、該当箇所を入力してください。", "Informe um achado, sua citação da fonte e sua localização.", "Geben Sie den Befund, ein wörtliches Zitat aus der Quelle und die Fundstelle ein.", "Унесите налаз, дослован цитат из извора и његову локацију.", "연구 결과, 출처 인용문 및 해당 위치를 입력하세요."
  ],
  [
    "请填写类别名称，并选择至少一项研究发现。", "Enter a category label and select at least one finding.", "Saisissez un libellé de catégorie et sélectionnez au moins un résultat.", "Введите название категории и выберите как минимум один результат исследования.", "Introduzca el nombre de una categoría y seleccione al menos un hallazgo.", "カテゴリー名を入力し、少なくとも1つの研究結果を選択してください。", "Informe o nome da categoria e selecione pelo menos um achado.", "Geben Sie eine Kategorienbezeichnung ein und wählen Sie mindestens einen Befund aus.", "Унесите назив категорије и изаберите бар један налаз.", "범주 이름을 입력하고 연구 결과를 하나 이상 선택하세요."
  ],
  [
    "请填写综合发现，并选择至少一个类别。", "Enter a synthesized finding and select at least one category.", "Saisissez un résultat synthétisé et sélectionnez au moins une catégorie.", "Введите обобщённый результат и выберите хотя бы одну категорию.", "Introduzca un hallazgo sintetizado y seleccione al menos una categoría.", "統合した所見を入力し、少なくとも1つのカテゴリーを選択してください。", "Informe um achado sintetizado e selecione pelo menos uma categoria.", "Geben Sie einen synthetisierten Befund ein und wählen Sie mindestens eine Kategorie aus.", "Унесите синтетизовани налаз и изаберите бар једну категорију.", "종합 소견을 입력하고 범주를 하나 이상 선택하세요."
  ],
  [
    "已保存研究发现。",
    "Finding saved."
  ],
  [
    "已保存类别。",
    "Category saved."
  ],
  [
    "已保存综合发现。",
    "Synthesis saved."
  ],
  [
    "记录研究发现",
    "Record findings"
  ],
  [
    "归类支持性发现",
    "Group supported findings"
  ],
  [
    "形成综合发现",
    "Synthesize findings"
  ],
  [
    "记录",
    "Records"
  ],
  [
    "尚无记录。",
    "No records yet."
  ],
  [
    "原始数据（保留字段名）",
    "Raw data (original field names)"
  ],
  [
    "研究发现、来源引述及位置", "Findings, source quotations and locators", "Résultats, citations sources et localisations", "Результаты, цитаты из источников и места в источниках", "Hallazgos, citas de las fuentes y localizadores", "所見、出典からの引用、該当箇所", "Achados, citações das fontes e localizadores", "Befunde, Quellenzitate und Fundstellen", "Налази, цитати из извора и њихове локације", "소견, 출처 인용문 및 해당 위치"
  ],
  [
    "选择多项时可使用 Ctrl 或 Command 键。",
    "Use Ctrl or Command to select multiple items."
  ],
  [
    "输入发生变化；已保存的是提交时的内容。", "Inputs changed; the saved record uses the submitted values.", "Les données ont changé ; l’enregistrement sauvegardé correspond aux valeurs soumises.", "Данные изменились; сохранённая запись содержит значения, отправленные при сохранении.", "Los datos cambiaron; el registro guardado contiene los valores enviados.", "入力内容が変更されました。保存済みレコードには送信時の値が使われています。", "Os dados mudaram; o registro salvo contém os valores enviados.", "Die Eingaben wurden geändert; der gespeicherte Datensatz enthält die übermittelten Werte.", "Уноси су измењени; сачувани запис садржи вредности послате приликом чувања.", "입력값이 변경되었습니다. 저장된 레코드에는 제출 당시의 값이 사용됩니다."
  ],
  [
    "正在保存…",
    "Saving…"
  ],
  ["保存失败，请检查输入或展开错误详情。", "Save failed. Check your inputs or expand the error details.", "Échec de l’enregistrement. Vérifiez les champs ou développez les détails de l’erreur.", "Не удалось сохранить. Проверьте введённые данные или откройте подробности ошибки.", "No se pudo guardar. Revise los datos o despliegue los detalles del error.", "保存できませんでした。入力内容を確認するか、エラーの詳細を開いてください。", "Falha ao salvar. Confira os dados ou expanda os detalhes do erro.", "Speichern fehlgeschlagen. Prüfen Sie Ihre Eingaben oder öffnen Sie die Fehlerdetails.", "Чување није успело. Проверите унос или отворите детаље грешке.", "저장하지 못했습니다. 입력을 확인하거나 오류 세부 정보를 펼치세요."],
  ["操作未完成，请检查输入或稍后重试。", "The operation could not be completed. Check your inputs or try again later.", "L’opération n’a pas pu aboutir. Vérifiez les données saisies ou réessayez plus tard.", "Не удалось выполнить действие. Проверьте введённые данные или повторите попытку позже.", "No se pudo completar la operación. Revise los datos o inténtelo de nuevo más tarde.", "操作を完了できませんでした。入力内容を確認するか、後でもう一度お試しください。", "Não foi possível concluir a operação. Confira os dados ou tente novamente mais tarde.", "Der Vorgang konnte nicht abgeschlossen werden. Prüfen Sie Ihre Eingaben oder versuchen Sie es später erneut.", "Радња није довршена. Проверите унос или покушајте поново касније.", "작업을 완료하지 못했습니다. 입력을 확인하거나 나중에 다시 시도하세요."],
  [
    "技术详情（原文）", "Technical details (original)", "Détails techniques (originaux)", "Технические подробности (оригинал)", "Detalles técnicos (originales)", "技術的な詳細（原文）", "Detalhes técnicos (originais)", "Technische Details (Originaltext)", "Технички детаљи (изворни текст)", "기술 세부정보(원문)"
  ],
  [
    "原始错误信息未自动翻译，以避免丢失技术含义。", "The original error is preserved to avoid losing technical meaning.", "Le message d’erreur original est conservé pour éviter toute perte de sens technique.", "Исходное сообщение об ошибке сохранено без изменений, чтобы не потерять технический смысл.", "El error original se conserva para evitar la pérdida de significado técnico.", "技術的な意味が失われないよう、元のエラーメッセージを自動翻訳せずに保持しています。", "A mensagem de erro original é mantida para evitar perda de significado técnico.", "Die ursprüngliche Fehlermeldung bleibt unverändert, damit ihre technische Bedeutung erhalten bleibt.", "Изворна порука о грешци је сачувана без измена како би се очувало њено техничко значење.", "기술적 의미가 손실되지 않도록 원래 오류 메시지를 자동 번역하지 않고 보존했습니다."
  ],
  [
    "表格可横向滚动",
    "Scrollable table"
  ],
  [
    "JSON 格式不正确，请检查引号、逗号和括号。", "Invalid JSON. Check quotation marks, commas and brackets.", "JSON invalide. Vérifiez les guillemets, les virgules et les crochets.", "Некорректный JSON. Проверьте кавычки, запятые и скобки.", "JSON no válido. Revise las comillas, las comas y los corchetes.", "JSON の形式が正しくありません。引用符、カンマ、括弧を確認してください。", "JSON inválido. Confira as aspas, as vírgulas e os colchetes.", "Ungültiges JSON. Prüfen Sie Anführungszeichen, Kommas und Klammern.", "Неисправан JSON. Проверите наводнике, зарезе и заграде.", "JSON 형식이 올바르지 않습니다. 따옴표, 쉼표 및 괄호를 확인하세요."
  ],
  [
    "请求失败：{0}", "Request failed: {0}", "Échec de la requête : {0}", "Запрос не выполнен: {0}", "Error en la solicitud: {0}", "リクエストに失敗しました：{0}", "Falha na solicitação: {0}", "Anfrage fehlgeschlagen: {0}", "Захтев није успео: {0}", "요청 실패: {0}"
  ],
  [
    "缺少必填字段：{0}",
    "Required field missing: {0}"
  ],
  [
    "来自先前任务的响应已忽略。", "Response from a previous task was ignored.", "La réponse provenant d’une tâche précédente a été ignorée.", "Ответ от предыдущей задачи проигнорирован.", "Se ignoró la respuesta de una tarea anterior.", "前のタスクからの応答は無視されました。", "A resposta de uma tarefa anterior foi ignorada.", "Eine Antwort aus einer früheren Aufgabe wurde ignoriert.", "Одговор из претходног задатка је занемарен.", "이전 작업에서 온 응답은 무시되었습니다."
  ],
  [
    "选择参考方法", "Choose reference method", "Choisir la méthode de référence", "Выберите референсный метод", "Elegir el método de referencia", "参照方法を選択", "Escolher o método de referência", "Referenzmethode auswählen", "Изаберите референтну методу", "기준 방법 선택"
  ],
  [
    "参考方法",
    "Reference method"
  ],
  [
    "查看全部原始计算字段", "View all original calculation fields", "Afficher tous les champs de calcul d’origine", "Показать все исходные поля расчёта", "Ver todos los campos de cálculo originales", "元の計算フィールドをすべて表示", "Ver todos os campos de cálculo originais", "Alle ursprünglichen Berechnungsfelder anzeigen", "Прикажи сва изворна поља за израчунавање", "원래 계산 필드 모두 보기"
  ],
  [
    "无匹配记录", "No matching records", "Aucun enregistrement correspondant", "Совпадений не найдено", "No hay registros coincidentes", "一致するレコードがありません", "Nenhum registro correspondente", "Keine passenden Datensätze", "Нема одговарајућих записа", "일치하는 레코드가 없습니다"
  ],
  [
    "与",
    "and"
  ],
  [
    "，第 4 章；Borenstein 等（2021），",
    ", ch. 4; Borenstein et al. (2021),"
  ],
  [
    "，第 3–5 章；Borenstein 等（2021），",
    ", ch. 3–5; Borenstein et al. (2021),"
  ],
  [
    "，第 5 章，第 121–133 页；",
    ", ch. 5, pp. 121–133；"
  ],
  [
    "，第 6 章，第 183–184 页，公式 6.1–6.7。",
    ", ch. 6, pp. 183–184, Eqs. 6.1–6.7."
  ],
  [
    "，第 8 章，第 199–215 页；Borenstein 等（2021），",
    ", ch. 8, pp. 199–215; Borenstein et al. (2021),"
  ],
  [
    "，第 11 章，第 271–275 页；Harrer 等（2022），",
    ", ch. 11, pp. 271–275; Harrer et al. (2022),"
  ],
  [
    "，第 12 章，第 12.2.2.2 节，第 340–342 页。",
    ", Chapter 12, §12.2.2.2, pp. 340–342."
  ],
  [
    "，第 2 版，第 39 章，第 351、354–355 页；",
    ", 2nd ed., ch. 39, pp. 351, 354–355;"
  ],
  [
    "独立组中，正关联表示治疗组事件比例更高。观测到的二分类关联可选择 PHI。潜在关联的元分析可使用 Bonett–Price 近似；该方法假设潜在连续变量服从二元正态分布，并按定义给每个单元格加 0.5。两种方法均使用自然尺度标准误且受边际分布影响，须彼此分开，并与连续结局的 Fisher z 分开分析。正态区间可能超出相关系数的取值范围。", "Independent groups: positive association means a higher event proportion in group t. Choose PHI for observed binary association. For a latent association meta-analysis, Bonett–Price is the recommended approximation; it assumes underlying bivariate-normal continuous variables and adds 0.5 to every cell by definition. Both use natural-scale SE, depend on marginal splits, and must be analyzed separately from each other and continuous-outcome Fisher z. Normal intervals may exceed correlation bounds.", "Dans des groupes indépendants, une association positive indique une proportion d’événements plus élevée dans le groupe traité. Choisissez le coefficient φ pour une association binaire observée. Pour une méta-analyse d’associations latentes, l’approximation de Bonett–Price peut être utilisée ; elle suppose des variables continues sous-jacentes suivant une loi normale bivariée et ajoute par définition 0,5 à chaque cellule. Les deux méthodes utilisent une erreur-type sur l’échelle naturelle, dépendent de la répartition marginale et doivent être analysées séparément l’une de l’autre et du z de Fisher pour les critères continus. Les intervalles normaux peuvent dépasser les limites possibles d’une corrélation.", "Независимые группы: положительная связь означает более высокую долю событий в группе лечения. Для наблюдаемой бинарной связи выберите PHI. Для метаанализа латентной связи можно использовать приближение Bonett–Price; оно предполагает, что лежащие в основе непрерывные переменные имеют совместное нормальное распределение, и по определению добавляет 0,5 к каждой ячейке. Оба метода используют SE на исходной шкале и зависят от маргинальных распределений; их следует анализировать отдельно друг от друга и отдельно от Fisher z для непрерывных исходов. Нормальные интервалы могут выходить за допустимые границы корреляции.", "En grupos independientes, una asociación positiva indica una proporción de eventos más alta en el grupo de tratamiento. Elija el coeficiente phi (φ) para una asociación binaria observada. Para un metanálisis de asociaciones latentes, puede usarse la aproximación de Bonett–Price, que supone variables continuas subyacentes con distribución normal bivariante y añade 0,5 a cada celda por definición. Ambos métodos usan un error estándar en la escala natural, dependen de la distribución marginal y deben analizarse por separado entre sí y del z de Fisher para desenlaces continuos. Los intervalos normales pueden exceder los límites posibles de una correlación.", "独立群では、正の関連は治療群のイベント割合が高いことを意味します。観測された二値の関連には PHI を選択できます。潜在的な関連のメタ解析には Bonett–Price の近似を使用できます。この方法は潜在連続変数が二変量正規分布に従うと仮定し、定義により各セルに 0.5 を加えます。いずれも自然尺度の標準誤差を用い、周辺割合に依存するため、相互に、また連続アウトカムの Fisher z とは分けて解析してください。正規区間は相関係数の範囲を超えることがあります。", "Em grupos independentes, uma associação positiva indica uma proporção de eventos maior no grupo de tratamento. Escolha o coeficiente phi (φ) para uma associação binária observada. Para uma metanálise de associações latentes, pode-se usar a aproximação de Bonett–Price, que pressupõe variáveis contínuas subjacentes com distribuição normal bivariada e acrescenta 0,5 a cada célula por definição. Ambos os métodos usam erro padrão na escala natural, dependem da distribuição marginal e devem ser analisados separadamente entre si e do z de Fisher para desfechos contínuos. Intervalos normais podem ultrapassar os limites possíveis de uma correlação.", "Unabhängige Gruppen: Eine positive Assoziation bedeutet einen höheren Ereignisanteil in der Behandlungsgruppe. Für eine beobachtete binäre Assoziation wählen Sie PHI. Für eine Metaanalyse latenter Assoziationen kann die Bonett–Price-Näherung verwendet werden; sie setzt zugrunde liegende bivariate normalverteilte kontinuierliche Variablen voraus und addiert definitionsgemäß 0,5 zu jeder Zelle. Beide Verfahren verwenden den SE auf der natürlichen Skala und hängen von den Randverteilungen ab. Analysieren Sie sie getrennt voneinander und getrennt von Fisher-z für kontinuierliche Endpunkte. Normale Intervalle können außerhalb des zulässigen Korrelationsbereichs liegen.", "Независне групе: позитивна повезаност значи већи удео догађаја у терапијској групи. За посматрану бинарну повезаност изаберите PHI. За метаанализу латентне повезаности може се користити Bonett–Price апроксимација; она претпоставља да основне континуалне варијабле имају биваријантну нормалну расподелу и по дефиницији додаје 0,5 свакој ћелији. Обе методе користе SE на природној скали и зависе од маргиналних расподела; анализирајте их одвојено једну од друге и од Fisher z за континуалне исходе. Нормални интервали могу прећи границе дозвољених вредности корелације.", "독립군에서 양의 연관은 치료군의 사건 비율이 더 높다는 뜻입니다. 관측된 이분형 연관에는 PHI를 선택할 수 있습니다. 잠재 연관의 메타분석에는 Bonett–Price 근사를 사용할 수 있습니다. 이 방법은 잠재 연속 변수가 이변량 정규분포를 따른다고 가정하며 정의상 모든 셀에 0.5를 더합니다. 두 방법 모두 원척도 표준오차를 사용하고 주변 비율에 영향을 받으므로 서로 간에, 그리고 연속 결과의 Fisher z와 별도로 분석해야 합니다. 정규 구간은 상관계수 범위를 벗어날 수 있습니다."
  ],
  [
    "Glass delta 使用对照组标准差，不作小样本校正。应与 Hedges g、使用其他或未知标准化方式的标准化均值差分层分析。请选择关联报告并记录表格或页码。", "Glass delta uses the control-arm SD, with no small-sample correction. Keep it in a separate analysis stratum from Hedges g or SMDs with other or unknown standardizers. Select the linked report and record its table/page locator.", "Le delta de Glass utilise l’écart-type du groupe témoin, sans correction pour petit échantillon. Analysez-le dans une strate distincte de Hedges g ou des différences moyennes standardisées calculées avec d’autres standardisateurs ou un standardisateur inconnu. Sélectionnez le rapport associé et indiquez le tableau ou la page.", "Glass Δ использует SD контрольной группы без поправки на малую выборку. Анализируйте Glass Δ отдельно от Hedges g и от SMD с другими или неизвестными стандартизаторами. Выберите связанный отчёт и укажите таблицу или страницу источника.", "Glass delta usa la desviación estándar del grupo de control y no aplica una corrección para muestras pequeñas. Manténgalo en un estrato de análisis distinto del de Hedges g y del de las diferencias de medias estandarizadas calculadas con otros métodos de estandarización o con uno desconocido. Seleccione el informe asociado e indique la tabla o la página.", "Glass Δ は対照群の標準偏差を用い、小標本補正は行いません。Hedges g や、標準化方法が異なる、または不明な SMD とは別の解析層にしてください。リンクされた報告を選択し、表またはページの位置を記録してください。", "Glass delta usa o desvio padrão do grupo de controle, sem correção para amostras pequenas. Mantenha-o em um estrato de análise separado de Hedges g e das diferenças de médias padronizadas calculadas com outros métodos de padronização ou com método desconhecido. Selecione o relatório vinculado e indique a tabela ou a página.", "Glass Δ verwendet die SD der Kontrollgruppe ohne Korrektur für kleine Stichproben. Analysieren Sie Glass Δ getrennt von Hedges g sowie von SMDs mit anderen oder unbekannten Standardisierungen. Wählen Sie den verknüpften Bericht aus und geben Sie Tabelle oder Seitenfundstelle an.", "Glass Δ користи SD контролне групе, без корекције за мале узорке. Анализирајте га одвојено од Hedges g и SMD са другачијом или непознатом стандардизацијом. Изаберите повезани извештај и наведите табелу или страницу извора.", "Glass Δ는 대조군 표준편차를 사용하며 소표본 보정은 하지 않습니다. Hedges g 및 표준화 방법이 다르거나 불명확한 SMD와 별도의 분석 층으로 유지하세요. 연결된 보고서를 선택하고 표 또는 페이지 위치를 기록하세요."
  ],
  [
    "独立组均值比要求结局有实质意义的零点，且两组均值非零、同号。它不是二分类风险比。保存对数均值比和对数尺度标准误，通过指数变换显示均值比。均值为负时须谨慎解释；不作小样本偏倚校正。", "Ratio of independent-arm means: use an outcome with a meaningful zero and nonzero same-sign means. This is not a binary risk ratio. Store log ROM and log-scale SE; display ROM by exponentiation. Negative means require particular care interpreting the ratio. No small-sample bias correction is applied.", "Le rapport des moyennes de groupes indépendants exige une variable de résultat ayant un zéro pertinent, avec des moyennes non nulles et de même signe dans les deux groupes. Il ne s’agit pas d’un risque relatif binaire. Enregistrez le log du rapport des moyennes (log ROM) et son erreur-type sur l’échelle logarithmique ; affichez le ROM après exponentiation. Interprétez avec prudence les rapports lorsque les moyennes sont négatives. Aucune correction du biais pour petit échantillon n’est appliquée.", "Отношение средних независимых групп требует содержательно значимого нуля и ненулевых средних одного знака. Это не бинарное отношение рисков. Сохраните логарифм отношения средних и SE на логарифмической шкале; отношение средних отображается после экспоненцирования. При отрицательных средних интерпретацию следует оценивать особенно тщательно. Поправка на смещение для малых выборок не применяется.", "La razón de medias de grupos independientes requiere un desenlace con un cero significativo y medias distintas de cero y del mismo signo en ambos grupos. No es un riesgo relativo binario. Guarde el logaritmo de la razón de medias (log ROM) y su error estándar en escala logarítmica; muestre el ROM mediante exponenciación. Interprete con especial cautela la razón cuando las medias sean negativas. No se aplica corrección del sesgo para muestras pequeñas.", "独立群の平均値比を使用するには、意味のあるゼロ点を持つアウトカムで、両群の平均値がゼロでなく同符号である必要があります。これは二値アウトカムのリスク比ではありません。対数平均値比と対数尺度の標準誤差を保存し、指数変換して平均値比を表示します。平均値が負の場合は解釈に特に注意してください。小標本バイアス補正は行いません。", "A razão de médias de grupos independentes requer um desfecho com zero significativo e médias diferentes de zero e com o mesmo sinal nos dois grupos. Não é um risco relativo binário. Armazene o logaritmo da razão de médias (log ROM) e seu erro padrão na escala logarítmica; apresente o ROM por exponenciação. Interprete com especial cautela a razão quando as médias forem negativas. Não se aplica correção de viés para amostras pequenas.", "Das Mittelwertverhältnis unabhängiger Gruppen setzt einen inhaltlich sinnvollen Nullpunkt sowie von null verschiedene Mittelwerte gleichen Vorzeichens voraus. Es ist kein binäres Risikoverhältnis. Speichern Sie das logarithmische Mittelwertverhältnis und den SE auf der Log-Skala; das Mittelwertverhältnis wird durch Exponentiation angezeigt. Bei negativen Mittelwerten ist die Interpretation besonders sorgfältig zu prüfen. Es wird keine Korrektur für Klein-Stichproben-Verzerrung vorgenommen.", "Однос средина независних група захтева смислену нулту тачку исхода и средине различите од нуле и истог знака. То није бинарни однос ризика. Сачувајте логаритам односа средина и SE на лог скали; однос средина се приказује експоненцирањем. Када су средине негативне, тумачење захтева посебан опрез. Не примењује се корекција пристрасности малих узорака.", "독립군 평균비를 사용하려면 의미 있는 영점이 있는 결과여야 하고 두 군의 평균은 0이 아니며 부호가 같아야 합니다. 이는 이분형 결과의 위험비가 아닙니다. 로그 평균비와 로그 척도 표준오차를 저장하고 지수 변환하여 평균비를 표시합니다. 평균이 음수인 경우 비율을 특히 주의해서 해석하세요. 소표본 편향 보정은 적용하지 않습니다."
  ],
  [
    "输入治疗组观察事件数减期望事件数，以及对应的 log-rank 方差。O−E 为正时，治疗组相对对照组的 HR 大于 1；保存前须核对来源中的符号。", "Enter treatment-arm observed minus expected events and its corresponding log-rank variance. Positive O−E gives treatment/control HR above 1; check the source sign before saving.", "Saisissez le nombre d’événements observés moins attendus dans le groupe traité, ainsi que la variance de log-rank correspondante. Une valeur positive de O−E donne un HR traitement/témoin supérieur à 1 ; vérifiez le signe dans la source avant l’enregistrement.", "Введите для группы лечения число наблюдаемых минус ожидаемых событий и соответствующую дисперсию log-rank. При положительном O−E отношение HR (лечение/контроль) больше 1; перед сохранением проверьте знак в источнике.", "Introduzca los eventos observados menos los esperados en el grupo de tratamiento y la varianza de log-rank correspondiente. Un valor positivo de O−E da un HR de tratamiento/control superior a 1; compruebe el signo en la fuente antes de guardar.", "治療群の観測イベント数から期待イベント数を引いた値と、対応するログランク分散を入力してください。O−E が正なら治療群／対照群の HR は 1 を上回ります。保存前に原資料の符号を確認してください。", "Informe o número de eventos observados menos esperados no grupo de tratamento e a variância de log-rank correspondente. Um O−E positivo resulta em HR tratamento/controle acima de 1; confira o sinal na fonte antes de salvar.", "Geben Sie für die Behandlungsgruppe die beobachtete minus erwartete Ereigniszahl sowie die zugehörige Log-Rank-Varianz ein. Ein positives O−E bedeutet, dass die HR Behandlung/Kontrolle größer als 1 ist; prüfen Sie vor dem Speichern das Vorzeichen in der Quelle.", "Унесите број посматраних минус очекиваних догађаја у терапијској групи и одговарајућу log-rank варијансу. Позитиван O−E значи да је HR терапијска/контролна група већи од 1; проверите знак у извору пре чувања.", "치료군의 관측 사건 수에서 기대 사건 수를 뺀 값과 해당 로그순위 분산을 입력하세요. O−E가 양수이면 치료군/대조군 HR은 1보다 큽니다. 저장하기 전에 원자료의 부호를 확인하세요."
  ],
  [
    "仅适用于对同一效应相对零值（比值效应相对 1）的正态 Wald 检验所报告的精确双侧 p 值。标准误在分析尺度上反推；阈值形式的 p 值以及 t 检验、得分检验和似然比检验不适用。", "Use only an exact two-sided p-value from a normal Wald test of this same effect against zero (or ratio 1). The SE is recovered on the analysis scale; threshold p-values, t, score and likelihood-ratio tests are unsuitable.", "À utiliser uniquement pour une valeur p bilatérale exacte issue d’un test de Wald normal du même effet par rapport à zéro (ou du rapport par rapport à 1). L’erreur-type est reconstituée sur l’échelle d’analyse ; les valeurs p rapportées sous forme de seuil, ainsi que celles de tests t, de score ou du rapport de vraisemblance, ne conviennent pas.", "Используйте только точное двустороннее p-значение нормального Wald-теста этого же эффекта относительно нуля (для отношений — относительно 1). SE восстанавливается на аналитической шкале. p-значения, заданные порогом, а также t-тесты, score-тесты и тесты отношения правдоподобия не подходят.", "Úselo solo para un valor p bilateral exacto de una prueba de Wald normal del mismo efecto frente a cero (o de la razón frente a 1). El error estándar se recupera en la escala de análisis; no son adecuados los valores p comunicados solo como umbral ni los de pruebas t, de puntuación o de razón de verosimilitud.", "この方法は、同じ効果をゼロ（比の効果では1）と比較する正規 Wald 検定から得た正確な両側 p 値に限り使用できます。標準誤差は解析尺度上で逆算します。閾値形式の p 値、t 検定、スコア検定、尤度比検定には適用できません。", "Use somente um valor de p bilateral exato de um teste de Wald normal do mesmo efeito em relação a zero (ou da razão em relação a 1). O erro padrão é recuperado na escala de análise; valores de p informados apenas como limite, bem como os de testes t, de escore ou de razão de verossimilhança, não são adequados.", "Nur für einen exakten zweiseitigen p-Wert aus einem normalen Wald-Test desselben Effekts gegen null (bei Verhältnismaßen gegen 1). Der SE wird auf der Analyseskala zurückgerechnet. p-Werte in Schwellenform sowie t-, Score- und Likelihood-Ratio-Tests sind nicht geeignet.", "Само за тачну двострану p-вредност нормалног Wald теста истог ефекта у односу на нулу (за мере односа у односу на 1). SE се изводи на аналитичкој скали; p-вредности наведене само као праг, t-тестови, score тестови и тестови количника веродостојности нису погодни.", "이 방법은 동일한 효과를 0(비율 효과는 1)과 비교하는 정규 Wald 검정에서 얻은 정확한 양측 p값에만 적용됩니다. 표준오차는 분석 척도에서 역산합니다. 임계값 형태의 p값, t 검정, 점수 검정 및 우도비 검정에는 적용할 수 없습니다."
  ],
  [
    "输入独立组等方差 t 检验中带符号的 t 值，正值表示治疗组高于对照组。本转换得到 Hedges g 及 Borenstein/LS2 方差；Welch 检验、配对检验和调整后检验需要其他输入。", "Enter a signed t statistic from an independent-groups pooled-variance test. Positive means treatment > control. This conversion yields Hedges g with the Borenstein/LS2 variance; Welch, paired and adjusted tests require different inputs.", "Saisissez une statistique t signée issue d’un test t à variances égales pour groupes indépendants. Une valeur positive signifie traitement > témoin. Cette conversion produit le g de Hedges avec la variance de Borenstein/LS2 ; les tests de Welch, appariés et ajustés exigent d’autres données.", "Введите знаковое значение t из t-теста независимых групп с объединённой дисперсией. Положительное значение означает лечение > контроль. Это преобразование даёт Hedges g с дисперсией Borenstein/LS2; для тестов Welch, парных и скорректированных тестов нужны другие входные данные.", "Introduzca un estadístico t con signo de una prueba t con varianzas iguales para grupos independientes. Un valor positivo significa tratamiento > control. Esta conversión produce la g de Hedges con la varianza de Borenstein/LS2; las pruebas de Welch, pareadas y ajustadas requieren otros datos.", "独立群の等分散検定から得た符号付き t 統計量を入力してください。正の値は治療群が対照群を上回ることを示します。この変換は Hedges g と Borenstein/LS2 分散を求めます。Welch 検定、対応のある検定、調整済み検定には別の入力が必要です。", "Informe uma estatística t com sinal de um teste t com variâncias iguais para grupos independentes. Um valor positivo significa tratamento > controle. Esta conversão produz o g de Hedges com a variância de Borenstein/LS2; testes de Welch, pareados e ajustados exigem outros dados.", "Geben Sie den vorzeichenbehafteten t-Wert aus einem t-Test unabhängiger Gruppen mit gepoolter Varianz ein. Ein positiver Wert bedeutet Behandlung > Kontrolle. Diese Umrechnung ergibt Hedges g mit der Borenstein/LS2-Varianz; für Welch-, gepaarte und adjustierte Tests werden andere Eingaben benötigt.", "Унесите t статистику са знаком из t-теста независних група са обједињеном варијансом. Позитивна вредност значи терапијска > контролна група. Ово претварање даје Hedges g са Borenstein/LS2 варијансом; за Welch, упарене и кориговане тестове потребни су други уноси.", "독립군 등분산 검정의 부호가 있는 t 통계량을 입력하세요. 양수는 치료군이 대조군보다 높음을 뜻합니다. 이 변환은 Hedges g와 Borenstein/LS2 분산을 산출합니다. Welch 검정, 대응 검정 및 보정 검정에는 다른 입력이 필요합니다."
  ],
  [
    "输入独立组等方差 t 检验的精确双侧 p 值及方向。仅报告为 p 小于某个阈值时，无法确定效应量；Welch 检验、配对检验和调整后检验需要其他输入。", "Enter the exact two-sided p-value and direction from a pooled-variance independent-groups t-test. Values reported only as p < threshold cannot determine an effect size. Welch, paired and adjusted tests require different inputs.", "Saisissez la valeur p bilatérale exacte et le sens de l’effet issus d’un test t à variances égales pour groupes indépendants. Une valeur rapportée uniquement sous la forme p < seuil ne permet pas de déterminer une taille d’effet. Les tests de Welch, appariés et ajustés exigent d’autres données.", "Введите точное двустороннее p-значение и направление эффекта из t-теста независимых групп с объединённой дисперсией. Если указано только p < порогового значения, величину эффекта определить нельзя. Для тестов Welch, парных и скорректированных тестов нужны другие входные данные.", "Introduzca el valor p bilateral exacto y la dirección del efecto de una prueba t con varianzas iguales para grupos independientes. Si solo se informa como p < un umbral, no se puede determinar el tamaño del efecto. Las pruebas de Welch, pareadas y ajustadas requieren otros datos.", "等分散の独立群 t 検定から得た正確な両側 p 値と方向を入力してください。p < 閾値とのみ報告されている場合、効果量は特定できません。Welch 検定、対応のある検定、調整済み検定には別の入力が必要です。", "Informe o valor de p bilateral exato e a direção do efeito de um teste t com variâncias iguais para grupos independentes. Se o resultado for informado apenas como p < um limite, não será possível determinar o tamanho do efeito. Testes de Welch, pareados e ajustados exigem outros dados.", "Geben Sie den exakten zweiseitigen p-Wert und die Richtung aus einem t-Test unabhängiger Gruppen mit gepoolter Varianz ein. Aus einem nur als p < Schwellenwert angegebenen Wert lässt sich keine Effektgröße bestimmen. Für Welch-, gepaarte und adjustierte Tests werden andere Eingaben benötigt.", "Унесите тачну двострану p-вредност и смер из t-теста независних група са обједињеном варијансом. Ако је пријављено само p < праг, величина ефекта се не може одредити. За Welch, упарене и кориговане тестове потребни су други уноси.", "등분산 독립군 t 검정에서 얻은 정확한 양측 p값과 방향을 입력하세요. p < 임계값으로만 보고된 경우 효과크기를 정할 수 없습니다. Welch 검정, 대응 검정 및 보정 검정에는 다른 입력이 필요합니다."
  ],
  [
    "仅适用于单因素、两个独立组的等方差 F 检验：分子自由度为 1，残差自由度为 n_t+n_c−2。F 值不带符号，须单独指定治疗组减对照组的方向；调整后检验、Welch 检验及重复测量 F 检验需要其他方法。", "Only a one-factor, two-independent-group, equal-variance F test is eligible: numerator df=1 and residual df=n_t+n_c−2. Enter the treatment−control direction separately because F has no sign. Adjusted, Welch and repeated-measures F tests need another method.", "Seul un test F à un facteur, deux groupes indépendants et variances égales convient : ddl du numérateur = 1 et ddl résiduels = n_t+n_c−2. Saisissez séparément le sens traitement−témoin, car F n’a pas de signe. Les tests ajustés, de Welch et F à mesures répétées nécessitent une autre méthode.", "Подходит только однофакторный F-тест для двух независимых групп с равными дисперсиями: df числителя=1, остаточный df=n_t+n_c−2. Укажите отдельно направление «лечение минус контроль», поскольку у F нет знака. Для скорректированных тестов, теста Welch и F-тестов повторных измерений нужен другой метод.", "Solo es admisible una prueba F de un factor, dos grupos independientes y varianzas iguales: gl del numerador = 1 y gl residuales = n_t+n_c−2. Introduzca por separado la dirección tratamiento−control, ya que F no tiene signo. Las pruebas ajustadas, de Welch y F de medidas repetidas requieren otro método.", "1要因・独立2群・等分散の F 検定のみ使用できます。分子自由度は1、残差自由度は n_t+n_c−2 である必要があります。F 値には符号がないため、治療群−対照群の方向を別途入力してください。調整済み、Welch、反復測定の F 検定には別の方法が必要です。", "Somente é válido um teste F de um fator, dois grupos independentes e variâncias iguais: gl do numerador = 1 e gl residuais = n_t+n_c−2. Informe separadamente a direção tratamento−controle, pois F não tem sinal. Testes ajustados, de Welch e F de medidas repetidas exigem outro método.", "Nur ein einfaktorieller F-Test für zwei unabhängige Gruppen mit gleicher Varianz ist geeignet: Zähler-df=1 und Residual-df=n_t+n_c−2. Geben Sie die Richtung Behandlung minus Kontrolle separat an, da F kein Vorzeichen hat. Adjustierte, Welch- und Messwiederholungs-F-Tests erfordern eine andere Methode.", "Погодан је само једнофакторски F тест за две независне групе са једнаким варијансама: df бројиоца=1, резидуални df=n_t+n_c−2. Посебно наведите смер терапијска минус контролна група, јер F нема знак. За кориговане, Welch и F тестове поновљених мерења потребна је друга метода.", "단일 요인, 두 독립군, 등분산 F 검정에만 적용됩니다. 분자 자유도는 1, 잔차 자유도는 n_t+n_c−2여야 합니다. F값에는 부호가 없으므로 치료군−대조군 방향을 별도로 입력하세요. 보정 F 검정, Welch 검정 및 반복측정 F 검정에는 다른 방법이 필요합니다."
  ],
  [
    "输入基于两个独立组组内合并标准差计算的带符号 Cohen d，方向为治疗组减对照组。本方法应用精确 Hedges 校正和 Borenstein/LS2 方差；其他标准化方式需要不同方法。", "Enter a signed Cohen d based on the pooled within-group SD of two independent groups (treatment − control). This applies the exact Hedges correction and Borenstein/LS2 variance; other standardizers need a different method.", "Saisissez un d de Cohen signé, calculé avec l’écart-type intra-groupes combiné de deux groupes indépendants (traitement − témoin). La conversion applique la correction exacte de Hedges et la variance de Borenstein/LS2 ; les autres standardisateurs exigent une autre méthode.", "Введите знаковое значение Cohen d, рассчитанное с объединённым внутригрупповым SD для двух независимых групп (лечение минус контроль). Метод применяет точную поправку Hedges и дисперсию Borenstein/LS2; для других способов стандартизации нужен другой метод.", "Introduzca una d de Cohen con signo, calculada con la desviación estándar intragrupo combinada de dos grupos independientes (tratamiento − control). La conversión aplica la corrección exacta de Hedges y la varianza de Borenstein/LS2; otros métodos de estandarización requieren otro método.", "2つの独立群の群内プール標準偏差に基づく、符号付き Cohen d を入力してください（治療群−対照群）。正確な Hedges 補正と Borenstein/LS2 分散を適用します。標準化方法が異なる場合は別の方法が必要です。", "Informe um d de Cohen com sinal, calculado com o desvio padrão intragrupo combinado de dois grupos independentes (tratamento − controle). A conversão aplica a correção exata de Hedges e a variância de Borenstein/LS2; outros métodos de padronização exigem outro método.", "Geben Sie ein vorzeichenbehaftetes Cohen d ein, berechnet mit der gepoolten Innerhalb-Gruppen-SD von zwei unabhängigen Gruppen (Behandlung minus Kontrolle). Die Methode wendet die exakte Hedges-Korrektur und die Borenstein/LS2-Varianz an; für andere Standardisierungen ist eine andere Methode erforderlich.", "Унесите Cohen d са знаком, израчунат на основу обједињеног SD унутар две независне групе (терапијска минус контролна). Метода примењује тачну Hedges корекцију и Borenstein/LS2 варијансу; за друге методе стандардизације потребна је друга метода.", "두 독립군의 군내 통합 표준편차를 기준으로 한 부호 있는 Cohen d를 입력하세요(치료군−대조군). 정확한 Hedges 보정과 Borenstein/LS2 분산을 적용합니다. 다른 표준화 방법에는 별도의 방법이 필요합니다."
  ],
  [
    "完整配对的效应为条件 A 减条件 B。单组前后变化不是有对照的治疗效应；简单交叉试验摘要未调整时期效应或残留效应。", "Effect = condition A − B for complete pairs. A single-group pre/post change is not a controlled treatment effect; simple crossover summaries do not adjust period or carryover effects.", "Pour les paires complètes, l’effet est la condition A moins la condition B. Une variation avant/après dans un seul groupe n’est pas un effet thérapeutique contrôlé ; les résumés simples d’essais croisés ne tiennent compte ni de l’effet de période ni de l’effet rémanent.", "Для полных пар эффект равен условие A минус условие B. Изменение до/после в одной группе не является контролируемым эффектом лечения; простые сводки перекрёстного исследования не корректируют периодный эффект или эффект переноса.", "En los pares completos, el efecto es la condición A menos la condición B. Un cambio antes-después en un solo grupo no es un efecto del tratamiento controlado; los resúmenes simples de ensayos cruzados no ajustan por el efecto del período ni por el efecto residual.", "完全な対応ペアにおける効果は条件 A−B です。単群の前後変化は対照群のある治療効果ではありません。単純なクロスオーバー試験の要約値は時期効果や持ち越し効果を調整しません。", "Para os pares completos, o efeito é a condição A menos a condição B. Uma mudança antes/depois em um único grupo não é um efeito de tratamento controlado; resumos simples de ensaios cruzados não ajustam o efeito de período nem o efeito residual.", "Bei vollständigen Paaren ist der Effekt Bedingung A minus Bedingung B. Eine Vorher-Nachher-Veränderung in einer Gruppe ist kein kontrollierter Behandlungseffekt; einfache Cross-over-Zusammenfassungen berücksichtigen weder Perioden- noch Carry-over-Effekte.", "За потпуне парове ефекат је услов А минус услов Б. Промена пре/после у једној групи није контролисани ефекат лечења; једноставни сажети подаци из crossover студије не прилагођавају ефекат периода нити carry-over ефекат.", "완전한 대응 쌍의 효과는 조건 A−B입니다. 단일군 전후 변화는 대조군이 있는 치료 효과가 아닙니다. 단순 교차시험 요약값은 기간 효과나 잔류 효과를 보정하지 않습니다."
  ],
  [
    "效应为（治疗组终点减基线）减（对照组终点减基线）。每组需要实际分析的配对人数，以及变化值标准差或基线与终点的相关系数。请说明假定相关系数的来源，并检验合理范围。", "Effect = (treatment final − baseline) − (control final − baseline). Each arm needs its own analyzed paired n and change SD or baseline–final correlation. State the source of any assumed correlation and check a plausible range.", "L’effet est (valeur finale − valeur initiale dans le groupe traité) − (valeur finale − valeur initiale dans le groupe témoin). Pour chaque groupe, il faut fournir le nombre de participants appariés analysés et l’écart-type des changements ou la corrélation initiale-finale. Indiquez la source de toute corrélation supposée et examinez une plage plausible.", "Эффект равен (конечное значение лечения − исходное значение лечения) − (конечное значение контроля − исходное значение контроля). Для каждой группы нужны фактическое число участников с парными измерениями и SD изменений либо корреляция исходного и конечного значения. Укажите источник предполагаемой корреляции и проверьте правдоподобный диапазон.", "El efecto es (valor final − valor inicial en el grupo de tratamiento) − (valor final − valor inicial en el grupo de control). Para cada grupo, indique el número analizado de participantes con datos pareados y la desviación estándar del cambio o la correlación entre el valor inicial y el final. Indique la fuente de toda correlación supuesta y compruebe un rango plausible.", "効果は（治療群の最終値−ベースライン）−（対照群の最終値−ベースライン）です。各群について、解析対象となった対応ペア数と、変化量の標準偏差またはベースラインと最終値の相関係数が必要です。仮定した相関係数の出典を示し、妥当な範囲で感度を確認してください。", "O efeito é (valor final − inicial no grupo de tratamento) − (valor final − inicial no grupo de controle). Para cada grupo, informe o número de participantes pareados analisados e o desvio padrão da mudança ou a correlação entre os valores inicial e final. Indique a fonte de qualquer correlação presumida e avalie uma faixa plausível.", "Der Effekt ist (Endpunkt Behandlung − Ausgangswert Behandlung) − (Endpunkt Kontrolle − Ausgangswert Kontrolle). Für jede Gruppe werden die analysierte Zahl gepaarter Personen sowie die SD der Veränderung oder die Korrelation zwischen Ausgangs- und Endwert benötigt. Geben Sie die Quelle einer angenommenen Korrelation an und prüfen Sie einen plausiblen Wertebereich.", "Ефекат је (коначно мерење терапијске групе − почетно мерење терапијске групе) − (коначно мерење контролне групе − почетно мерење контролне групе). За сваку групу потребни су стварни број учесника са упареним мерењима и SD промене или корелација почетног и коначног мерења. Наведите извор претпостављене корелације и проверите веродостојан опсег.", "효과는 (치료군 최종값−기준선)−(대조군 최종값−기준선)입니다. 각 군에 분석된 대응 표본 수와 변화량 표준편차 또는 기준선과 최종값의 상관계수가 필요합니다. 가정한 상관계수의 출처를 밝히고 타당한 범위에서 민감도를 확인하세요."
  ],
  [
    "两个顺序组的差值都须为同一参与者的 A−B。即使顺序组样本量不等，对 AB 与 BA 取平均也可抵消共同的加性时期效应；但差异性残留效应、顺序特有时期效应和配对缺失偏倚仍可能存在。有已发表的调整后估计时应优先考虑。", "Both sequence differences must be A − B within the same participant. Averaging AB and BA cancels a common additive period effect, even with unequal sequence sizes. Differential carryover, sequence-specific period effects and missing-pair bias remain possible; prefer a published adjusted estimate when available.", "Dans les deux séquences, la différence doit être A−B pour une même personne. La moyenne des séquences AB et BA annule un effet de période additif commun, même si les effectifs des séquences diffèrent. Des effets rémanents différentiels, des effets de période propres à chaque séquence et le biais dû aux paires manquantes restent possibles ; privilégiez si possible une estimation ajustée publiée.", "Разности в обеих последовательностях должны быть A−B у одних и тех же участников. Усреднение AB и BA устраняет общий аддитивный эффект периода даже при разном размере последовательностей. Возможны дифференциальные эффекты переноса, периодные эффекты, специфичные для последовательности, и смещение из-за отсутствующих пар; если доступна опубликованная скорректированная оценка, отдайте ей предпочтение.", "En ambas secuencias, la diferencia debe ser A−B dentro de la misma persona. Promediar AB y BA cancela un efecto aditivo común del período, incluso si los tamaños de las secuencias son distintos. Aún pueden existir efectos residuales diferenciales, efectos del período específicos de la secuencia y sesgo por pares faltantes; si está disponible, prefiera una estimación ajustada publicada.", "2つの順序群の差はいずれも、同一参加者内の A−B である必要があります。順序群の標本数が異なっていても、AB と BA の平均を取ることで共通の加法的な時期効果を相殺できます。ただし、差異のある持ち越し効果、順序特有の時期効果、ペア欠損によるバイアスは残る可能性があります。公表済みの調整後推定値がある場合は、そちらを優先してください。", "Nas duas sequências, a diferença deve ser A−B para a mesma pessoa. A média de AB e BA cancela um efeito aditivo comum de período, mesmo quando os tamanhos das sequências diferem. Ainda podem existir efeitos residuais diferenciais, efeitos de período específicos da sequência e viés por pares ausentes; quando disponível, prefira uma estimativa ajustada publicada.", "Beide Differenzen der Sequenzgruppen müssen innerhalb derselben Person A−B sein. Der Mittelwert aus AB und BA hebt einen gemeinsamen additiven Periodeneffekt auch bei ungleichen Gruppengrößen auf. Unterschiedliche Carry-over-Effekte, sequenzspezifische Periodeneffekte und Verzerrungen durch fehlende Paare bleiben möglich; bevorzugen Sie eine publizierte adjustierte Schätzung, falls verfügbar.", "Разлике у обе секвенце морају бити A−B код истог учесника. Усредњавање AB и BA поништава заједнички адитивни ефекат периода чак и када су величине секвенци неједнаке. Могући су различити carry-over ефекти, ефекти периода специфични за секвенцу и пристрасност због недостајућих парова; ако постоји објављена коригована процена, дајте јој предност.", "두 순서군의 차이는 모두 동일한 참여자 내 A−B여야 합니다. 순서군별 표본 수가 달라도 AB와 BA를 평균하면 공통 가산 기간 효과를 상쇄할 수 있습니다. 그러나 차등 잔류 효과, 순서별 기간 효과 및 대응 자료 누락 편향은 여전히 가능성이 있습니다. 발표된 보정 추정치가 있으면 이를 우선 사용하세요."
  ],
  [
    "单组均值不是均值差。仅合并结局量表及单位一致的结果。", "A one-group mean is not a mean difference. Pool only the same outcome scale and unit.", "Une moyenne dans un seul groupe n’est pas une différence moyenne. Ne regroupez que des résultats portant sur la même échelle de mesure et la même unité.", "Среднее для одной группы не является разностью средних. Объединяйте только результаты с одинаковыми шкалой и единицей измерения.", "La media de un solo grupo no es una diferencia de medias. Combine únicamente resultados expresados en la misma escala de desenlace y con la misma unidad.", "単群平均は平均差ではありません。同じアウトカム尺度と単位の結果のみを統合してください。", "A média de um único grupo não é uma diferença de médias. Combine somente resultados na mesma escala de desfecho e unidade.", "Ein Mittelwert einer Gruppe ist keine Mittelwertdifferenz. Fassen Sie nur Ergebnisse derselben Skala und Einheit zusammen.", "Средња вредност једне групе није разлика средина. Обједињујте само резултате са истом скалом исхода и јединицом мере.", "단일군 평균은 평균차가 아닙니다. 동일한 결과 척도와 단위의 결과만 통합하세요."
  ],
  [
    "在 logit 尺度合并，并通过逆 logit 变换报告比例。零事件或全事件研究需要二项模型，本逆方差格式不接受这些研究。", "Pool on the logit scale and report the inverse-logit proportion. Zero or all-event studies need a binomial model and are not accepted by this inverse-variance format.", "Effectuez la méta-analyse sur l’échelle logit et rapportez la proportion après transformation logit inverse. Les études avec zéro événement ou tous les événements nécessitent un modèle binomial et ne sont pas acceptées dans ce format à variance inverse.", "Объединяйте результаты на шкале logit и представляйте долю после обратного преобразования функцией inverse logit. Для исследований без событий или только с событиями нужна биномиальная модель; этот формат обратной дисперсии их не принимает.", "Combine en la escala logit y comunique la proporción tras aplicar la transformación logit inversa. Los estudios con cero eventos o con todos los eventos requieren un modelo binomial y no se admiten en este formato de varianza inversa.", "ロジット尺度で統合し、逆ロジット変換して割合を報告します。ゼロイベントまたは全例イベントの研究には二項モデルが必要で、この逆分散形式では受け付けません。", "Combine na escala logit e apresente a proporção após a transformação logit inversa. Estudos com zero eventos ou com todos os eventos exigem um modelo binomial e não são aceitos neste formato de variância inversa.", "Fassen Sie auf der Logit-Skala zusammen und geben Sie den Anteil nach Rücktransformation mit der inversen Logit-Funktion an. Studien mit null oder ausschließlich Ereignissen erfordern ein Binomialmodell und werden in diesem Invers-Varianz-Format nicht akzeptiert.", "Обједињујте на logit скали и прикажите удео након обрнуте logit трансформације. Студије са нула или свим догађајима захтевају биномни модел и овај формат обрнуте варијансе их не прихвата.", "로짓 척도에서 통합하고 역로짓 변환으로 비율을 보고합니다. 사건이 0건이거나 전원이 사건인 연구에는 이항 모형이 필요하며 이 역분산 형식에서는 처리하지 않습니다."
  ],
  [
    "合并事件发生率的对数，再按明确的人时单位报告发生率。零事件研究需要 Poisson 模型，本逆方差格式不接受这些研究。", "Pool log event rates and report rates per stated person-time unit. Zero-event studies need a Poisson model and are not accepted by this inverse-variance format.", "Combinez les logarithmes des taux d’événements, puis rapportez les taux par unité de temps-personne indiquée. Les études sans événement nécessitent un modèle de Poisson et ne sont pas acceptées dans ce format à variance inverse.", "Объединяйте логарифмы частот событий и представляйте частоты на явно указанную единицу человеко-времени. Для исследований без событий нужна модель Пуассона; этот формат обратной дисперсии их не принимает.", "Combine los logaritmos de las tasas de eventos y comunique después las tasas por la unidad de persona-tiempo indicada. Los estudios sin eventos requieren un modelo de Poisson y no se admiten en este formato de varianza inversa.", "イベント発生率の対数を統合し、明示した人時間単位あたりの発生率を報告します。イベントゼロの研究にはポアソンモデルが必要で、この逆分散形式では受け付けません。", "Combine os logaritmos das taxas de eventos e apresente as taxas por unidade de pessoa-tempo informada. Estudos sem eventos exigem um modelo de Poisson e não são aceitos neste formato de variância inversa.", "Fassen Sie die logarithmierten Ereignisraten zusammen und geben Sie die Raten je ausdrücklich genannter Personenzeiteinheit an. Studien ohne Ereignisse erfordern ein Poisson-Modell und werden in diesem Invers-Varianz-Format nicht akzeptiert.", "Обједињујте логаритме стопа догађаја и прикажите стопе на јасно наведеној јединици особа-време. Студије без догађаја захтевају Poisson модел и овај формат обрнуте варијансе их не прихвата.", "사건 발생률의 로그를 통합하고 명시한 인시 단위당 발생률을 보고합니다. 사건이 0건인 연구에는 포아송 모형이 필요하며 이 역분산 형식에서는 처리하지 않습니다."
  ],
  [
    "输入单组原始估计，注明报告的标准误、方差或区间是在原始尺度还是变换后的分析尺度。正态/Wald 区间换算及原始尺度 delta 换算均为近似；须核对原始方法和单位。", "Enter a raw one-group estimate, then identify whether the reported SE, variance or interval is on the raw or transformed analysis scale. Normal/Wald CI conversion and raw-scale delta conversion are approximations; verify the original method and unit.", "Saisissez une estimation brute pour un seul groupe, puis précisez si l’erreur-type, la variance ou l’intervalle rapporté est sur l’échelle brute ou sur l’échelle d’analyse transformée. Les conversions d’IC normal/Wald et par la méthode delta sur l’échelle brute sont des approximations ; vérifiez la méthode originale et l’unité.", "Введите исходную оценку для одной группы и укажите, приведены ли SE, дисперсия или интервал на исходной или преобразованной аналитической шкале. Пересчёт нормального/Wald-доверительного интервала и дельта-пересчёт на исходной шкале являются приближениями; проверьте исходный метод и единицу измерения.", "Introduzca una estimación bruta de un solo grupo e indique si el error estándar, la varianza o el intervalo comunicado están en la escala bruta o en la escala de análisis transformada. La conversión de IC normal/Wald y la conversión mediante el método delta en escala bruta son aproximaciones; compruebe el método original y la unidad.", "単群の生推定値を入力し、報告された標準誤差、分散、区間が生尺度と変換後の解析尺度のどちらに基づくかを指定してください。正規/Wald 区間からの換算と生尺度の delta 換算はいずれも近似です。原法と単位を確認してください。", "Informe uma estimativa bruta de um único grupo e indique se o erro padrão, a variância ou o intervalo informado está na escala bruta ou na escala de análise transformada. A conversão de IC normal/Wald e a conversão pelo método delta na escala bruta são aproximações; confira o método original e a unidade.", "Geben Sie eine Rohschätzung für eine Gruppe ein und kennzeichnen Sie, ob der berichtete SE, die Varianz oder das Intervall auf der Rohskala oder der transformierten Analyseskala liegt. Die Umrechnung eines Normal-/Wald-Konfidenzintervalls und die Delta-Umrechnung auf der Rohskala sind Näherungen; prüfen Sie Originalmethode und Einheit.", "Унесите изворну процену за једну групу и наведите да ли су пријављени SE, варијанса или интервал на изворној или трансформисаној аналитичкој скали. Конверзија нормалног/Wald интервала поверења и делта конверзија на изворној скали су апроксимације; проверите изворну методу и јединицу.", "단일군 원척도 추정값을 입력하고 보고된 표준오차, 분산 또는 구간이 원척도인지 변환된 분석 척도인지 지정하세요. 정규/Wald 구간 환산과 원척도 델타 환산은 모두 근사입니다. 원래 방법과 단위를 확인하세요."
  ],
  [
    "风险比方向", "HR direction", "Sens du HR", "Направление HR", "Dirección del HR", "HR の方向", "Direção do HR", "Richtung der HR", "Смер HR", "HR 방향"
  ],
  [
    "选择风险比方向", "Choose HR direction", "Choisir le sens du HR", "Выберите направление HR.", "Elegir la dirección del HR", "HR の方向を選択", "Escolher a direção do HR", "Wählen Sie die Richtung der HR aus.", "Изаберите смер HR.", "HR 방향 선택"
  ],
  [
    "治疗组中位数", "treatment median", "Médiane du groupe traité", "Медиана в группе лечения", "Mediana del grupo de tratamiento", "治療群の中央値", "Mediana do grupo de tratamento", "Median der Behandlungsgruppe", "Медијана терапијске групе", "치료군 중앙값"
  ],
  [
    "治疗组风险集人数", "treatment number at risk", "Effectif à risque du groupe traité", "Число участников под риском в группе лечения", "Número en riesgo del grupo de tratamiento", "治療群のリスク集合人数", "Número em risco no grupo de tratamento", "Anzahl der Personen unter Risiko in der Behandlungsgruppe", "Број особа под ризиком у терапијској групи", "치료군 위험집합 인원"
  ],
  [
    "治疗组事件数来源", "treatment event-count source", "Source du nombre d’événements du groupe traité", "Источник числа событий в группе лечения", "Fuente del recuento de eventos del grupo de tratamiento", "治療群イベント数の出典", "Fonte da contagem de eventos do grupo de tratamento", "Quelle der Ereigniszahl in der Behandlungsgruppe", "Извор броја догађаја у терапијској групи", "치료군 사건 수 출처"
  ],
  [
    "选择治疗组事件数来源", "Choose treatment event-count source", "Choisir la source du nombre d’événements du groupe traité", "Выберите источник числа событий в группе лечения.", "Elegir la fuente del recuento de eventos del grupo de tratamiento", "治療群イベント数の出典を選択", "Escolher a fonte da contagem de eventos do grupo de tratamento", "Wählen Sie die Quelle der Ereigniszahl in der Behandlungsgruppe aus.", "Изаберите извор броја догађаја у терапијској групи.", "치료군 사건 수 출처 선택"
  ],
  [
    "对照组中位数", "control median", "Médiane du groupe témoin", "Медиана в контрольной группе", "Mediana del grupo de control", "対照群の中央値", "Mediana do grupo de controlee", "Median der Kontrollgruppe", "Медијана контролне групе", "대조군 중앙값"
  ],
  [
    "对照组风险集人数", "control number at risk", "Effectif à risque du groupe témoin", "Число участников под риском в контрольной группе", "Número en riesgo del grupo de control", "対照群のリスク集合人数", "Número em risco no grupo de controlee", "Anzahl der Personen unter Risiko in der Kontrollgruppe", "Број особа под ризиком у контролној групи", "대조군 위험집합 인원"
  ],
  [
    "对照组事件数来源", "control event-count source", "Source du nombre d’événements du groupe témoin", "Источник числа событий в контрольной группе", "Fuente del recuento de eventos del grupo de control", "対照群イベント数の出典", "Fonte da contagem de eventos do grupo de controlee", "Quelle der Ereigniszahl in der Kontrollgruppe", "Извор броја догађаја у контролној групи", "대조군 사건 수 출처"
  ],
  [
    "选择对照组事件数来源", "Choose control event-count source", "Choisir la source du nombre d’événements du groupe témoin", "Выберите источник числа событий в контрольной группе.", "Elegir la fuente del recuento de eventos del grupo de control", "対照群イベント数の出典を選択", "Escolher a fonte da contagem de eventos do grupo de controlee", "Wählen Sie die Quelle der Ereigniszahl in der Kontrollgruppe aus.", "Изаберите извор броја догађаја у контролној групи.", "대조군 사건 수 출처 선택"
  ],
  [
    "单组事件数来源", "single event-count source", "Source du nombre d’événements pour un seul groupe", "Источник числа событий для одной группы", "Fuente del recuento de eventos de un solo grupo", "単群イベント数の出典", "Fonte da contagem de eventos de um único grupo", "Quelle der Ereigniszahl für eine Gruppe", "Извор броја догађаја за једну групу", "단일군 사건 수 출처"
  ],
  [
    "选择单组事件数来源", "Choose single event-count source", "Choisir la source du nombre d’événements pour un seul groupe", "Выберите источник числа событий для одной группы.", "Elegir la fuente del recuento de eventos de un solo grupo", "単群イベント数の出典を選択", "Escolher a fonte da contagem de eventos de um único grupo", "Wählen Sie die Quelle der Ereigniszahl für eine Gruppe aus.", "Изаберите извор броја догађаја за једну групу.", "단일군 사건 수 출처 선택"
  ],
  [
    "单组中位数",
    "single median"
  ],
  [
    "单组风险集人数", "single number at risk", "Effectif à risque d’un seul groupe", "Число участников под риском в одной группе", "Número de pacientes en riesgo de un solo grupo", "単群のリスク集合人数", "Número em risco em um único grupo", "Anzahl der Personen unter Risiko in einer Gruppe", "Број особа под ризиком у једној групи", "단일군 위험집합 인원"
  ],
  [
    "治疗组报告的事件数", "treatment reported events", "Événements rapportés dans le groupe traité", "Сообщённые события в группе лечения", "Eventos comunicados en el grupo de tratamiento", "治療群で報告されたイベント数", "Eventos relatados no grupo de tratamento", "Gemeldete Ereignisse in der Behandlungsgruppe", "Пријављени догађаји у терапијској групи", "치료군에서 보고된 사건 수"
  ],
  [
    "对照组报告的事件数", "control reported events", "Événements rapportés dans le groupe témoin", "Сообщённые события в контрольной группе", "Eventos comunicados en el grupo de control", "対照群で報告されたイベント数", "Eventos relatados no grupo de controle", "Gemeldete Ereignisse in der Kontrollgruppe", "Пријављени догађаји у контролној групи", "대조군에서 보고된 사건 수"
  ],
  [
    "单组报告的事件数", "single reported events", "Événements rapportés dans un seul groupe", "Сообщённые события в одной группе", "Eventos comunicados en un solo grupo", "単群で報告されたイベント数", "Eventos relatados em um único grupo", "Gemeldete Ereignisse in einer Gruppe", "Пријављени догађаји у једној групи", "단일군에서 보고된 사건 수"
  ],
  [
    "Clopper–Pearson 区间（保守）", "Clopper–Pearson (conservative)", "Intervalle de Clopper–Pearson (conservateur)", "Интервал Клоппера–Пирсона (консервативный)", "Intervalo de Clopper–Pearson (conservador)", "Clopper–Pearson 区間（保守的）", "Intervalo de Clopper–Pearson (conservador)", "Clopper–Pearson-Intervall (konservativ)", "Clopper–Pearson интервал (конзервативан)", "Clopper–Pearson 구간(보수적)"
  ],
  [
    "排除低风险", "Exclude low", "Exclure les études à faible risque", "Исключить низкий риск", "Excluir los estudios de bajo riesgo", "低リスクを除外", "Excluir estudos de baixo risco", "Niedriges Risiko ausschließen", "Искључи низак ризик", "낮은 위험 제외"
  ],
  [
    "排除存在一定疑虑", "Exclude some concerns", "Exclure les études avec quelques préoccupations", "Исключить случаи с некоторыми опасениями", "Excluir los estudios con algunas preocupaciones", "懸念ありを除外", "Excluir estudos com algumas preocupações", "Bewertungen mit einigen Bedenken ausschließen", "Искључи случајеве са извесним забринутостима", "일부 우려 제외"
  ],
  [
    "排除高风险", "Exclude high", "Exclure les études à risque élevé", "Исключить высокий риск", "Excluir los estudios de alto riesgo", "高リスクを除外", "Excluir estudos de alto risco", "Hohes Risiko ausschließen", "Искључи висок ризик", "높은 위험 제외"
  ],
  [
    "排除中等风险", "Exclude moderate", "Exclure les études à risque modéré", "Исключить умеренный риск", "Excluir los estudios de riesgo moderado", "中等度リスクを除外", "Excluir estudos de risco moderado", "Moderates Risiko ausschließen", "Искључи умерен ризик", "중등도 위험 제외"
  ],
  [
    "排除严重风险", "Exclude serious", "Exclure les études à risque sérieux", "Исключить серьёзный риск", "Excluir los estudios de riesgo grave", "深刻なリスクを除外", "Excluir estudos de risco grave", "Schwerwiegendes Risiko ausschließen", "Искључи озбиљан ризик", "심각한 위험 제외"
  ],
  [
    "排除极严重风险", "Exclude critical", "Exclure les études à risque critique", "Исключить критический риск", "Excluir los estudios de riesgo crítico", "重大なリスクを除外", "Excluir estudos de risco crítico", "Kritisches Risiko ausschließen", "Искључи критичан ризик", "치명적 위험 제외"
  ],
  [
    "排除无信息", "Exclude no information", "Exclure les études sans information", "Исключить оценки «нет информации»", "Excluir los estudios sin información", "情報なしを除外", "Excluir estudos sem informação", "Bewertungen ohne Informationen ausschließen", "Искључи оцене „нема информација“", "정보 없음 제외"
  ],
  [
    "排除：{0}", "Exclude {0}", "Exclure : {0}", "Исключить: {0}", "Excluir: {0}", "除外：{0}", "Excluir: {0}", "{0} ausschließen", "Искључи: {0}", "제외: {0}"
  ],
  [
    "固定效应逆方差模型",
    "fixed-effect inverse variance"
  ],
  [
    "DerSimonian–Laird 随机效应",
    "DerSimonian-Laird random effects"
  ],
  [
    "REML 随机效应",
    "restricted maximum likelihood random effects"
  ],
  [
    "Wald 正态区间",
    "Wald normal interval"
  ],
  [
    "修正 Hartung–Knapp–Sidik–Jonkman t 区间",
    "modified Hartung-Knapp-Sidik-Jonkman t interval"
  ],
  [
    "Higgins 型预测区间",
    "Higgins-type prediction interval"
  ],
  [
    "最大似然随机效应",
    "Maximum-likelihood random-effects model"
  ],
  [
    "合并效应 ML 轮廓似然区间",
    "pooled-effect ML profile likelihood interval"
  ],
  [
    "研究数较少或 τ² 处于边界时，渐近似然比界限可能不准确。", "Asymptotic likelihood-ratio limits can be inaccurate with few studies or a boundary tau-squared estimate.", "Les limites du rapport de vraisemblance asymptotique peuvent être imprécises lorsque le nombre d’études est faible ou que l’estimation de τ² est à la frontière.", "Асимптотические границы отношения правдоподобия могут быть неточными при малом числе исследований или оценке τ² на границе области параметров.", "Los límites asintóticos de la razón de verosimilitud pueden ser inexactos cuando hay pocos estudios o la estimación de τ² está en el límite.", "研究数が少ない場合や τ² 推定値が境界にある場合、漸近尤度比限界の精度が低いことがあります。", "Os limites assintóticos da razão de verossimilhança podem ser imprecisos quando há poucos estudos ou a estimativa de τ² está no limite.", "Asymptotische Likelihood-Ratio-Grenzen können bei wenigen Studien oder einer τ²-Schätzung am Rand des Parameterraums ungenau sein.", "Асимптотске границе количника веродостојности могу бити непрецизне када има мало студија или је процена τ² на граници простора параметара.", "연구 수가 적거나 τ² 추정치가 경계값에 있을 때 점근적 우도비 한계가 부정확할 수 있습니다."
  ],
  [
    "修正 HKSJ 区间用于随机效应模型；主合并分析不接受固定效应 + HKSJ。此处仅供并列敏感性比较。",
    "The modified HKSJ interval is designed for random-effects models; primary synthesis does not accept a fixed-effect model paired with HKSJ. Shown here only for side-by-side sensitivity comparison."
  ],
  [
    "固定效应模型将 τ² 设为 0，因此该预测区间只反映同质性假设下新研究的抽样误差；报告时应使用随机效应预测区间。",
    "The fixed-effect model sets tau-squared to 0, so this prediction interval covers only the sampling variation of a new study under homogeneity; random-effect prediction intervals are the reportable ones."
  ],
  [
    "预测区间至少需要三项研究（t 分位数自由度为 k−2）。",
    "prediction intervals need at least three studies (t quantile with k - 2 df)"
  ],
  [
    "至少需要三项研究。",
    "needs at least three studies"
  ],
  [
    "REML τ² 轮廓区间目前仅支持 95% 水平。",
    "profile REML tau-squared interval is implemented at level 0.95 only"
  ],
  [
    "上限不可用；该区间不是有限区间。", "Upper bound unavailable; not a finite interval.", "Borne supérieure indisponible ; l’intervalle n’est pas fini.", "Верхняя граница недоступна; интервал не является конечным.", "Límite superior no disponible; el intervalo no es finito.", "上限を取得できません。この区間は有限区間ではありません。", "Limite superior indisponível; o intervalo não é finito.", "Obergrenze nicht verfügbar; das Intervall ist nicht endlich.", "Горња граница није доступна; интервал није коначан.", "상한을 구할 수 없습니다. 이 구간은 유한 구간이 아닙니다."
  ],
  [
    "未收敛。", "Did not converge.", "N’a pas convergé.", "Сходимость не достигнута.", "No convergió.", "収束しませんでした。", "Não convergiu.", "Nicht konvergiert.", "Није конвергирало.", "수렴하지 않았습니다."
  ],
  [
    "可用",
    "Available"
  ],
  [
    "H 与 I² 转换区间至少需要三项研究。",
    "H and I-squared transformed intervals need at least three studies"
  ],
  [
    "不同区间对是否跨越无效值的结论不一致。",
    "Intervals disagree on whether they cross the null. "
  ],
  [
    "第一层参数自助法百分位区间", "first-level parametric bootstrap percentile interval", "Intervalle percentile du bootstrap paramétrique de premier niveau", "Процентильный интервал параметрического бутстрепа первого уровня", "Intervalo percentil del bootstrap paramétrico de primer nivel", "第1段階のパラメトリック・ブートストラップ百分位区間", "Intervalo percentil do bootstrap paramétrico de primeiro nível", "Perzentilintervall des parametrischen Bootstrapverfahrens erster Stufe", "Перцентилни интервал параметарског bootstrap-а првог нивоа", "1단계 모수적 부트스트랩 백분위수 구간"
  ],
  [
    "第一层参数自助法基本区间", "first-level parametric bootstrap basic interval", "Intervalle basic du bootstrap paramétrique de premier niveau", "Базовый интервал параметрического бутстрепа первого уровня", "Intervalo básico del bootstrap paramétrico de primer nivel", "第1段階のパラメトリック・ブートストラップ基本区間", "Intervalo básico do bootstrap paramétrico de primeiro nível", "Basisintervall des parametrischen Bootstrapverfahrens erster Stufe", "Основни интервал параметарског bootstrap-а првог нивоа", "1단계 모수적 부트스트랩 기본 구간"
  ],
  [
    "REML τ² 轮廓似然区间", "REML profile likelihood interval for tau-squared", "Intervalle de vraisemblance profilée de τ² selon REML", "Профильный интервал правдоподобия REML для τ²", "Intervalo de verosimilitud perfilada de τ² según REML", "REML τ² のプロファイル尤度区間", "Intervalo de verossimilhança perfilada de τ² segundo REML", "REML-Profil-Likelihood-Intervall für τ²", "Профилни интервал веродостојности REML за τ²", "REML τ² 프로파일 우도 구간"
  ],
  [
    "固定逆方差 Q 的非中心卡方反演（近似；DL 点估计）",
    "Noncentral chi-square inversion of fixed-effect Q using inverse sampling-variance weights (approximate; DL point estimate)"
  ],
  [
    "Viechtbauer（2007）τ² Q-profile 区间",
    "Viechtbauer (2007) Q-profile interval for tau-squared"
  ],
  [
    "Higgins 与 Thompson（2002）τ² 转换区间（H 界限经典型方差 (k−1)/C 映射）",
    "Higgins & Thompson (2002) transformed interval for tau-squared (H limits mapped through the typical variance (k-1)/C)"
  ],
  [
    "Higgins 与 Thompson（2002）τ² 转换区间",
    "Higgins & Thompson (2002) transformed interval for tau-squared"
  ],
  [
    "Higgins 与 Thompson（2002）转换区间",
    "Higgins & Thompson (2002) transformed interval"
  ],
  [
    "第一层参数自助法是条件性敏感性分析，不会重新生成研究间异质性。basic 区间请核对 Davison 与 Hinkley（1997）《Bootstrap Methods and their Application》第 5.6 式；报告时引用方法，并报告重复次数和随机种子。", "First-level parametric bootstrap is a conditional sensitivity analysis; it does not regenerate between-study heterogeneity. Check Davison & Hinkley (1997), Bootstrap Methods and their Application, eq. 5.6 for basic intervals; cite the method and report replicate count and seed.", "Le bootstrap paramétrique de premier niveau est une analyse de sensibilité conditionnelle ; il ne régénère pas l’hétérogénéité interétudes. Pour les intervalles basic, consultez l’équation 5.6 de Davison et Hinkley (1997), Bootstrap Methods and their Application. Dans le rapport, citez la méthode et indiquez le nombre de réplications et la graine aléatoire.", "Параметрический бутстреп первого уровня — условный анализ чувствительности; межисследовательская гетерогенность при этом не генерируется заново. Для базовых интервалов сверьтесь с уравнением 5.6 в книге Davison & Hinkley (1997), Bootstrap Methods and their Application. Укажите метод, число повторений и начальное значение генератора случайных чисел.", "El bootstrap paramétrico de primer nivel es un análisis de sensibilidad condicional; no vuelve a generar la heterogeneidad entre estudios. Para los intervalos básicos, consulte la ecuación 5.6 de Davison y Hinkley (1997), Bootstrap Methods and their Application. En el informe, cite el método e indique el número de réplicas y la semilla aleatoria.", "第1段階のパラメトリック・ブートストラップは条件付きの感度分析であり、研究間異質性を再生成しません。基本区間については Davison と Hinkley（1997）『Bootstrap Methods and their Application』の式 5.6 を確認してください。報告時には方法を引用し、反復回数と乱数シードを記載してください。", "O bootstrap paramétrico de primeiro nível é uma análise de sensibilidade condicional; não gera novamente a heterogeneidade entre estudos. Para intervalos básicos, consulte a equação 5.6 de Davison e Hinkley (1997), Bootstrap Methods and their Application. No relatório, cite o método e informe o número de réplicas e a semente aleatória.", "Der parametrische Bootstrap erster Stufe ist eine bedingte Sensitivitätsanalyse; die Zwischenstudienheterogenität wird nicht neu erzeugt. Prüfen Sie Davison & Hinkley (1997), Bootstrap Methods and their Application, Gl. 5.6 zu Basisintervallen. Zitieren Sie die Methode und geben Sie die Zahl der Wiederholungen und den Seed an.", "Параметарски bootstrap првог нивоа је условна анализа осетљивости; њиме се не генерише поново хетерогеност између студија. За основне интервале проверите једначину 5.6 у књизи Davison & Hinkley (1997), Bootstrap Methods and their Application. Наведите методу, број понављања и почетно семе.", "1단계 모수적 부트스트랩은 조건부 민감도 분석이며 연구 간 이질성을 다시 생성하지 않습니다. 기본 구간은 Davison과 Hinkley(1997), 『Bootstrap Methods and their Application』의 식 5.6을 확인하세요. 보고할 때 방법을 인용하고 반복 횟수와 난수 시드를 기재하세요."
  ],
  [
    "SUCRA 和 P(rank 1) 是相对的模型排名摘要；排名最高的治疗方案不一定是最佳选择，排名也不能证明临床优越性或重要性。",
    "SUCRA and p-best are relative model-based ranking summaries; the top-ranked treatment is not necessarily the best choice and the ranking does not establish clinical superiority or importance."
  ],
  [
    "netmeta::rankogram / netrank(method=\"SUCRA\") 从拟合协方差矩阵的对角线抽取独立正态效应，并使用 netmeta 自身的 τ² 口径；本模块从 network_random 完整的参考组编码协方差中抽样。只有拟合相关性可忽略时，两者才会在蒙特卡洛误差范围内一致。",
    "netmeta::rankogram / netrank(method=\"SUCRA\") resample independent normal effects from the diagonal of the fitted covariance and pair them with netmeta's own tau-squared convention; this module resamples the full reference-coded covariance of network_random, so the two implementations coincide only up to Monte Carlo error when the fitted correlations are negligible."
  ],
  [
    "排名以拟合得到的共同 τ² 和正态近似为条件，不传播异质性估计的不确定性，并沿用一致性模型的传递性假设。",
    "Ranking conditions on the fitted common tau-squared and the normal approximation, does not propagate heterogeneity estimation uncertainty, and inherits the consistency model's transitivity assumptions."
  ],
  [
    "双侧渐近 Kendall τ-b 检验，已调整并列秩。",
    "two-sided asymptotic Kendall tau-b with tie adjustment"
  ],
  [
    "所有效应值对应同一比较、结局、时间点和指标；每行是一项独立研究。",
    "Effects share one comparison, outcome, time point, and measure; each row is an independent study."
  ],
  [
    "标准误与所分析的效应值使用同一尺度，并用于计算抽样方差。",
    "Standard errors are on the analyzed effect scale and give the sampling variances."
  ],
  [
    "效应值以逆方差加权均值为中心，按校正后的方差标准化；检验使用渐近零假设近似。",
    "Effects are standardized around their inverse-variance weighted mean using adjusted variances; the test uses an asymptotic null approximation."
  ],
  [
    "在研究相互独立的假设下，按 Stouffer 非加权方法合并已观察研究的 Z 值。",
    "The observed study Z scores are combined with Stouffer's unweighted sum under independence."
  ],
  [
    "假定每项缺失研究的 Z 值恰为 0；显著性水平为双侧 α=0.05。",
    "Each missing study is assumed to have exactly Z=0; significance uses a two-sided alpha of 0.05."
  ],
  [
    "零效应值与标准误均在分析尺度上：比值类为对数尺度，相关系数为 Fisher z，其余为自然尺度。",
    "null_effect is on the analyzed scale (log for ratios, Fisher z for FISHER_Z, natural otherwise); standard errors are on that same scale."
  ],
  [
    "研究数少于 10：检验效能和校准可能不足，请谨慎解读；10 只是经验提醒，不是有效性门槛。",
    "Fewer than 10 studies: interpret cautiously because power and calibration may be limited; 10 is a rule-of-thumb warning, not a validity cutoff."
  ],
  [
    "Begg 检验的常规渐近 p 值可能校准不佳。漏斗图不对称或失效安全数不能证明发表偏倚，也不能解释研究缺失原因。",
    "Begg's conventional asymptotic p-value can be miscalibrated; asymmetry or a fail-safe N does not prove publication bias or identify why studies are missing."
  ],
  [
    "每行是一项独立研究，计算已观察效应值的算术平均值时各研究权重相同。",
    "Each row is one independent study and contributes equally to the arithmetic observed mean."
  ],
  [
    "假定每项缺失研究的效应值都等于所填的缺失研究平均效应值；目标值和假定值都使用分析尺度。",
    "Every missing study has the explicitly supplied missing_effect; target_effect and missing_effect are on the analyzed scale."
  ],
  [
    "原始比值效应在计算前取对数；FISHER_Z 输入已是 Fisher z；其他效应保持自然尺度。",
    "Ratio estimates are log transformed, FISHER_Z estimates are already Fisher z, and other measures stay on their natural scale."
  ],
  [
    "标准误会检查为有限正数，但不参与 Orwin 公式计算。",
    "Standard errors are checked for finite positive values but are not used in this formula."
  ],
  [
    "此敏感性指标不能估计实际未发表研究数，也不能证明发表偏倚。",
    "This sensitivity count does not estimate how many unpublished studies exist and does not prove publication bias."
  ],
  [
    "算术平均值未考虑研究精度、异质性或研究间依赖；每项独立研究只取一个效应值。",
    "The arithmetic-mean calculation does not model study precision, heterogeneity, or dependencies beyond one selected effect per independent study."
  ],
  [
    "Card 书中算例使用原始 r；FISHER_Z 分析的目标值和假定缺失值必须用 Fisher z，不能直接套用书中的 r 阈值。",
    "Card's worked example uses raw r; FISHER_Z analyses must use Fisher z targets and missing-effect values, so its raw-r thresholds do not transfer directly."
  ],
  [
    "已知抽样方差的 WLS 正态 95% Wald 区间。",
    "normal 95% Wald; known-sampling-variance WLS"
  ],
  [
    "研究少于 10 项时请谨慎解读：回归效能和校准可能有限。10 项只是经验参考，不是有效性门槛。",
    "Fewer than 10 studies: interpret cautiously because regression power and calibration may be limited; 10 is a rule of thumb, not a validity cutoff."
  ],
  [
    "每行代表同一比较、结局、时间点和效应指标中的一项独立研究效应。",
    "Each row is one independent study effect for the same comparison, outcome, time point, and measure."
  ],
  [
    "PET 将效应对 SE 回归；PEESE 将效应对 SE² 回归；两者均使用 1/SE² 权重。",
    "PET regresses effect on SE; PEESE regresses effect on SE squared; both use weights 1/SE squared."
  ],
  [
    "截距是外推至 SE=0 时的拟合效应；区间使用已知抽样方差计算。",
    "The intercept is the fitted effect extrapolated to SE=0, and intervals use the known sampling variances."
  ],
  [
    "比值估计在对数尺度分析，标准误也须已在该尺度；FISHER_Z 保持 Fisher z 尺度。",
    "Ratio estimates are analyzed on the log scale, with standard errors already on that scale; FISHER_Z remains on the Fisher z scale."
  ],
  [
    "PET 和 PEESE 分别报告，不按条件规则自动择取估计值。",
    "PET and PEESE are reported separately; no conditional estimate-selection rule is applied."
  ],
  [
    "小样本研究效应模式也可能来自异质性、偶然因素或研究设计差异。本拟合不能证明发表偏倚或结局报告偏倚，也不能给出因果解释。",
    "Small-study patterns can reflect heterogeneity, chance, or study-design differences; these fits do not establish publication or outcome-reporting bias and do not provide a causal explanation."
  ],
  [
    "此区间仅描述单项研究的配对表；不提供标准误，也不能从区间端点反推标准误用于合并。Meta 分析仍应使用已有的 RD 与标准误。",
    "This is a research-level presentation interval for one paired table: the module exports no standard error and none may be back-derived from the interval limits for pooling. Meta-analytic combination must keep using the existing RD+SE pipeline."
  ],
  [
    "所选区间是渐近近似，实际覆盖率可能偏离标称水平，区间通常也不以原始观察值为中心。",
    "The selected interval is an asymptotic approximation, not an exact-coverage method: its realized coverage can depart from the nominal level, and the limits are generally not centred on the raw point estimate."
  ],
  [
    "置信区间端点超出风险差的可取范围 [−1, 1]，已截断到边界。",
    "A confidence limit left the achievable risk-difference range [-1, 1] and was truncated to the boundary."
  ],
  [
    "至少一个边际计数为零；按 Newcombe 方法本身的定义，相关系数 ψ 设为 0。",
    "At least one marginal count is zero, so the Newcombe correlation coefficient estimate psi is set to 0 by the method's own definition."
  ],
  [
    "至少一个边际比例为 0/n 或 n/n，相应 Wilson 区间退化，故该方法得出零宽区间。",
    "The interval is zero-width at the point estimate because at least one marginal count is 0/n or n/n and the corresponding Wilson score interval collapses; this is the method's own behaviour at fully determined margins."
  ],
  [
    "一个不一致配对格为零，Wald 方差依据的信息很少；Bonett–Price 和 Newcombe 区间更适合此类表格。",
    "One discordant count is zero, so the paired Wald variance rests on very little discordant information; the recommended Bonett-Price and Newcombe intervals handle such tables more reliably."
  ],
  [
    "风险差区间跨越零，NNT 区间因此无界，包含获益和伤害两侧；按 Altman（1998）的双分支形式报告。",
    "The risk-difference interval spans zero, so the NNT interval is unbounded and contains both infinitely large NNTB and NNTH values; it is reported in the Altman (1998) form 'NNTH a to infinity to NNTB b'."
  ],
  [
    "风险差区间的一端恰为零，相应 NNT 边界为无穷大，按单侧无界区间报告。",
    "A risk-difference confidence limit is exactly zero; the corresponding NNT bound is infinite and is reported as an unbounded one-sided interval."
  ],
  [
    "点估计值不在输入的置信区间内，请核对原始数据。",
    "The point estimate lies outside the supplied confidence interval; check the inputs."
  ],
  [
    "NNT 取决于输入的对照组事件风险（CER）；使用前请按目标人群实际 CER 重新计算。",
    "The NNT depends on the supplied control event rate (CER); recompute it with the actual control event rate of the target population before use."
  ],
  [
    "结局为二分类，且两组采用相同的事件定义。",
    "The outcome is binary and the same event definition applies to both arms."
  ],
  [
    "RD 为治疗组风险减对照组风险；正值表示事件风险增加（伤害，NNTH），负值表示风险降低（获益，NNTB）。",
    "RD is the risk in the treatment arm minus the risk in the control arm, so a positive RD means the treatment increases the event probability (harm, NNTH) and a negative RD means it lowers it (benefit, NNTB)."
  ],
  [
    "区间两端分别按同一公式换算（Daly 代入法）；输入的置信水平只作记录，不重新推算；所得 NNT 区间未计入假定 CER 的不确定性。",
    "Confidence limits are transformed by applying the same formula to each limit (Daly substitution); the level of the supplied interval is taken as given and is not re-derived, and the resulting NNT interval does not reflect uncertainty in the assumed control event rate."
  ],
  [
    "CER 是从输入效应值以外取得的假设；更换 CER 会改变换算后的 NNT。",
    "The control event rate (CER) is an assumption imported from outside the data supplied here; the converted NNT changes whenever a different CER is used."
  ],
  [
    "优势比不可折叠；即使 OR 不变，NNT 仍随 CER 改变。只应对目标人群适用的 CER 报告 NNT。",
    "The odds ratio is not collapsible: with the same OR the converted NNT still depends on the chosen CER, so report the NNT only for a CER that is relevant to the population of interest."
  ],
  [
    "所有效应值和标准误须使用同一分析尺度（比值取对数，相关系数用 Fisher z）；上游零单元格或连续性校正也会改变失效安全数。",
    "The count depends on all effects and standard errors sharing one consistent analysis scale (log for ratios, Fisher z for correlations) and on the zero-cell or continuity corrections applied upstream; changing the scale or corrections changes the fail-safe number."
  ],
  [
    "失效安全数不能证明发表偏倚，也不能估计实际缺失研究数或解释缺失原因。",
    "A fail-safe number is a sensitivity indicator: it does not prove publication bias, does not estimate how many studies are actually unpublished, and does not explain why studies are missing (Rosenberg 2005, p. 467)."
  ],
  [
    "每行是一项独立研究，按抽样方差的倒数加权：w=1/SE²（Rosenberg 2005，式 3–4）。",
    "Each row is one independent study weighted by the inverse of its sampling variance, w = 1/se^2 (Rosenberg 2005, equations 3-4)."
  ],
  [
    "假定缺失研究的平均效应恰为零、平均权重为 Σw/k；该数值以双侧 α 为目标（Rosenberg 2005，式 10 后）。",
    "Missing studies are assumed to have mean effect exactly zero and the mean observed weight sum(w)/k (Rosenberg 2005, after equation 10); the count targets a two-sided alpha."
  ],
  [
    "使用固定效应临界值：z 选项采用双侧标准正态分位数（metafor 惯例）；t 选项采用 Rosenberg 的 Student t 分位数，并迭代求解自由度 k+N−1。",
    "The fixed-effects critical value is used: test='z' takes the two-sided standard normal quantile (metafor convention; identical to this package's Rosenthal fail-safe N), test='t' takes Rosenberg's Student-t quantile with df = k + N - 1 solved iteratively."
  ],
  [
    "此敏感性指标不能证明发表偏倚、估计实际未发表研究数，也不能校正已存在的发表偏倚（Rosenberg 2005，第 467 页）。",
    "This sensitivity count does not prove publication bias, does not estimate how many unpublished studies exist, and is not a method for accounting for publication bias (Rosenberg 2005, p. 467)."
  ],
  [
    "此处仅实现固定效应版本；原文随机效应版本需迭代按 1/(SE²+合并 τ²) 重新加权，且经常退化为固定效应结果（式 11–13）。",
    "Only the fixed-effects version is implemented; the paper's random-effects variant (equations 11-13) re-weights by 1/(se^2 + tau^2_pooled) iteratively and often collapses to the fixed-effects count."
  ],
  [
    "N>5k+10 是任意的经验阈值，不是有效性标准（Rosenthal 1991；见 Rosenberg 2005，第 466 页）。",
    "The classic robustness threshold N > 5k + 10 (Rosenthal 1991, cited in Rosenberg 2005, p. 466) is an arbitrary rule of thumb, not a validity criterion."
  ],
  [
    "亚组差异检验在各亚组内共用一个 DL τ²。亚组应在综述方案中预先定义，且每项研究只能归入一个亚组（Cochrane Handbook §10.11）。",
    "The between-subgroups test uses one DerSimonian-Laird tau-squared shared by all subgroups; subgroups should be prespecified in the review protocol (Cochrane Handbook section 10.11) and each study must contribute to one subgroup only."
  ],
  [
    "每个亚组分别估计 DL τ²；每组研究较少时估计不精确。每组少于约 10–20 项研究时通常宜共用 τ²（Borenstein 2021，第 21 章）。",
    "Each subgroup uses its own DerSimonian-Laird tau-squared; with few studies per subgroup these estimates are imprecise and a pooled tau-squared is usually preferable below roughly 10-20 studies per subgroup (Borenstein 2021, chapter 21)."
  ],
  [
    "共用 τ² 截断为零；此时共用 τ² 的随机效应结果与固定效应结果一致。",
    "Pooled tau-squared was truncated at zero; the shared-tau-squared random-effects analysis coincides with the fixed-effect analysis."
  ],
  [
    "研究数少于 10：亚组分析效能低，τ² 估计不精确（Cochrane Handbook §10.11.5.1）。",
    "Fewer than ten studies: subgroup analyses have low power and tau-squared estimates are imprecise (Cochrane Handbook section 10.11.5.1)."
  ],
  [
    "P-score 是基于正态模型的两两比较确定性平均值，不是成为最佳的概率；它不能验证传递性，也不能证明临床优越性或临床重要性。",
    "P-scores summarize pairwise normal-model certainty; they are not probabilities of being best and do not establish transitivity, clinical superiority, or clinical importance."
  ],
  [
    "原始表中有零格，已对四格均加 0.5；请与不校正结果比较。",
    "The selected 0.5 correction was added to all four cells because the raw table contains a zero; compare this sensitivity with the uncorrected result."
  ],
  [
    "原始表中有零格，未应用连续性校正。",
    "The raw table contains zero cells; no continuity correction was applied."
  ],
  [
    "两组均无事件或均为全事件；根据 Cochrane 方法，这类表格不能为相对效应提供信息。任何校正后数值仅供敏感性分析。",
    "Both arms have the same outcome status (no events or all events); Cochrane treats this table as uninformative for a relative effect. Any corrected value is a sensitivity calculation."
  ],
  [
    "当前计算无法得到有限的对数比值；估计值和标准误不可用。",
    "The selected calculation cannot produce a finite log ratio; estimate and SE are unavailable."
  ],
  [
    "估计值有限，但抽样方差为零，无法用于逆方差加权。",
    "The estimate is finite but its sampling variance is zero; inverse-variance weighting is unavailable."
  ],
  [
    "每行是同一结局和比较中的一项独立研究估计。",
    "Each row is one independent study estimate for a common outcome and comparison."
  ],
  [
    "固定效应逆方差模型假定所有研究具有共同真实效应；SE 使用分析尺度。",
    "A fixed-effect inverse-variance model assumes all studies share one true effect; SEs are on the analysis scale."
  ],
  [
    "假定研究仅在所选漏斗图一侧缺失；L0 根据中心化效应的带符号秩估计缺失数。",
    "Missingness is one-sided on the requested funnel-plot side; L0 estimates the number from signed ranks of centered effects."
  ],
  [
    "填补效应以最终修剪后的合并值为中心，镜像另一侧最极端的研究，并沿用其 SE。",
    "Imputed effects mirror the most extreme opposite-side studies around the final trimmed pool and reuse their SEs."
  ],
  [
    "漏斗图缺失侧由用户预先选择，不从数据中推断。",
    "The requested funnel-plot side is selected in advance; it is not inferred from the data."
  ],
  [
    "R0、Q0 和 L0 均为带符号秩估计量；中心化效应绝对值相同时，按效应排序后的输入顺序定秩。",
    "R0, Q0, and L0 are signed-rank estimators; ties in absolute centered effects are ranked in input order after sorting effects."
  ],
  [
    "FE 使用抽样方差倒数加权；DL 和 REML 会在每次修剪拟合及填补后的拟合中重新估计研究间方差。",
    "FE uses inverse sampling-variance weights; DL and REML re-estimate between-study variance in each trimmed fit and in the augmented fit."
  ],
  [
    "填补效应以最终修剪后的合并值为中心，镜像最极端的被修剪研究，并沿用其标准误。",
    "Imputed effects mirror the most extreme trimmed studies around the final trimmed pooled estimate and reuse their standard errors."
  ],
  [
    "漏斗图不对称不能证明发表偏倚；异质性、偶然因素和其他机制也会造成不对称。",
    "Funnel asymmetry does not prove publication bias; heterogeneity, chance, and other mechanisms can also create asymmetry."
  ],
  [
    "填补后的估计值属于敏感性分析，不能保证无偏。",
    "The adjusted estimate is a sensitivity analysis and is not guaranteed to be unbiased."
  ],
  [
    "同一结局与时间点下，每项独立研究只取一个效应值；模型未处理研究内协方差。",
    "One effect per independent study for one outcome and time point; within-study covariance is not modeled."
  ],
  [
    "研究层面的关联属于生态关联，不能证明参与者层面的效应修饰或因果关系。",
    "Study-level associations are ecological and do not establish participant-level effect modification or causality."
  ],
  [
    "至少需要 10 项研究、满秩设计矩阵和正的残差自由度。",
    "Requires at least 10 studies, a full-rank design, and positive residual degrees of freedom."
  ],
  [
    "协变量类别样本稀少或变量接近共线时，系数估计可能不稳定。",
    "Coefficient estimates can be unstable with sparse covariate levels or near-collinearity."
  ],
  [
    "空模型 τ² 大于零时，解释的异质性 R²*=max(0, 1−τ²模型/τ²空模型)；空模型 τ² 为零时该指标不可计算。",
    "Explained heterogeneity R²* = max(0, 1 - tau2_model/tau2_null) when tau2_null > 0; it is None when tau2_null is zero."
  ],
  [
    "比值类效应的标准误必须在对数比值尺度输入；程序不会从原始比值尺度自动换算。",
    "For ratio measures, se must be the standard error on the log-ratio scale; it is not transformed from the raw ratio scale."
  ],
  [
    "修正 Knapp–Hartung 使用 max(1，残差 Q/自由度) 调整尺度，并采用 t 分布。",
    "Modified KNHA (ad hoc) uses max(1, residual Q/df) and a t reference distribution."
  ],
  [
    "研究数较少时，正态 Wald 区间可能低估不确定性。",
    "Normal Wald intervals may understate uncertainty in small meta-regressions."
  ],
  [
    "DL τ² 截断为零；稳健区间可应对方差设定错误，但不能消除真实的研究间异质性。",
    "DL tau2 was floored at zero; robust intervals then protect against variance misspecification but not against real between-study heterogeneity."
  ],
  [
    "Profile REML τ² 截断为零；稳健区间可应对方差设定错误，但不能消除真实的研究间异质性。",
    "Profile REML tau2 was floored at zero; robust intervals then protect against variance misspecification but not against real between-study heterogeneity."
  ],
  [
    "至少一项研究的杠杆值与 1 相差不超过 1e−12；其 CR2 调整按 clubSandwich 约定设为零。",
    "At least one study had leverage within 1e-12 of one; its CR2 adjustment was set to zero, matching the clubSandwich convention."
  ],
  [
    "稳健推断只改变标准误、检验和区间；点估计值与 τ² 与模型拟合结果相同。",
    "Robust inference changes standard errors, tests, and intervals only; the point estimates and tau2 are identical to the model-based fit."
  ],
  [
    "这里的稳健推断不同于 Knapp–Hartung：KH 按残差异质性调整模型协方差；本工具的三明治估计将每项研究作为独立簇，在方差设定错误时仍可保持一致性。簇稳健文献更推荐小样本使用 CR2 与 Satterthwaite 自由度（Pustejovsky 与 Tipton 2018；Imbens 与 Kolesar 2016）。",
    "This robust approach is not Knapp-Hartung: KH (and its modified variant in coscreen.meta_regression_model) rescales the model-based covariance by a residual heterogeneity factor, while the sandwich estimators here are consistent under variance misspecification with each study as its own cluster. CR2 with Satterthwaite df, rather than modified KH, is the recommended small-sample default in the cluster-robust literature (Pustejovsky & Tipton 2018; Imbens & Kolesar 2016)."
  ],
  [
    "按公式估算的均值和 SD 不是观测数据；普通效应量标准误未计入重建不确定性。",
    "Formula-estimated means and SDs are not observed data; reconstruction uncertainty is omitted from ordinary effect SEs."
  ],
  [
    "请明确选择 Hozo、Wan 或 Luo；Luo 均值搭配 Wan SD。请比较纳入与排除估算研究的结果，并引用所用方法。",
    "Choose Hozo, Wan or Luo explicitly; Luo mean uses Wan SD. Compare analyses with and without estimated studies and cite each method used."
  ],
  [
    "一个不一致配对格为零，已对两个不一致格各加 0.5。校正结果仅供敏感性分析，不应作为主要结果。",
    "The selected add-half correction was applied to both discordant counts because one of them is zero; the corrected result is a sensitivity analysis and not a main result."
  ],
  [
    "一个不一致配对格为零，配对优势比可用的信息很少；该区间可能不可靠。合并时优先考虑精确条件区间。",
    "One discordant count is zero, so the matched-pair odds ratio rests on very few informative pairs; treat the interval as unreliable and prefer an exact conditional interval when pooling."
  ],
  [
    "正在准备报告…已等待 {0} 秒。", "Preparing report… {0} s elapsed.", "Préparation du rapport… {0} s écoulées.", "Подготовка отчёта… прошло {0} с.", "Preparando el informe… {0} s transcurridos.", "レポートを準備中… {0} 秒経過。", "Preparando o relatório… {0} s decorridos.", "Bericht wird vorbereitet… {0} s vergangen.", "Припрема извештаја… прошло је {0} с.", "보고서 준비 중… {0}초 경과."
  ],
  [
    "正在合并二分类结局…已等待 {0} 秒。", "Calculating binary pooling… {0} s elapsed.", "Calcul de la méta-analyse des critères binaires… {0} s écoulées.", "Calculating binary pooling… {0} s elapsed.", "Calculando la combinación de resultados binarios… {0} s transcurridos.", "二値アウトカムを統合中… {0} 秒経過。", "Calculando a metanálise de desfechos binários… {0} s decorridos.", "Calculating binary pooling… {0} s elapsed.", "Calculating binary pooling… {0} s elapsed.", "이분형 결과 통합 중… {0}초 경과."
  ],
  [
    "正在计算 Peters 回归…已等待 {0} 秒。", "Calculating Peters regression… {0} s elapsed.", "Calcul de la régression de Peters… {0} s écoulées.", "Расчёт регрессии Петерса… прошло {0} с.", "Calculando la regresión de Peters… {0} s transcurridos.", "Peters 回帰を計算中… {0} 秒経過。", "Calculando a regressão de Peters… {0} s decorridos.", "Regression nach Peters wird berechnet… {0} s vergangen.", "Израчунавање Петерс регресије… прошло је {0} с.", "Peters 회귀 계산 중… {0}초 경과."
  ],
  [
    "正在拟合双变量模型…已等待 {0} 秒。", "Fitting bivariate model… {0} s elapsed.", "Ajustement du modèle bivarié… {0} s écoulées.", "Подгонка двумерной модели… прошло {0} с.", "Ajustando el modelo bivariado… {0} s transcurridos.", "二変量モデルを適合中… {0} 秒経過。", "Ajustando o modelo bivariado… {0} s decorridos.", "Bivariates Modell wird angepasst… {0} s vergangen.", "Прилагођавање биваријантног модела… прошло је {0} с.", "이변량 모형 적합 중… {0}초 경과."
  ],
  [
    "正在拟合阈值模型…已等待 {0} 秒。", "Fitting threshold model… {0} s elapsed.", "Ajustement du modèle à seuil… {0} s écoulées.", "Подгонка пороговой модели… прошло {0} с.", "Ajustando el modelo de umbral… {0} s transcurridos.", "閾値モデルを適合中… {0} 秒経過。", "Ajustando o modelo de limiar… {0} s decorridos.", "Schwellenwertmodell wird angepasst… {0} s vergangen.", "Прилагођавање модела прага… прошло је {0} с.", "임계값 모형 적합 중… {0}초 경과."
  ],
  [
    "正在计算探索性似然比…已等待 {0} 秒。", "Calculating exploratory likelihood ratios… {0} s elapsed.", "Calcul des rapports de vraisemblance exploratoires… {0} s écoulées.", "Расчёт поисковых отношений правдоподобия… прошло {0} с.", "Calculando las razones de verosimilitud exploratorias… {0} s transcurridos.", "探索的尤度比を計算中… {0} 秒経過。", "Calculando as razões de verossimilhança exploratórias… {0} s decorridos.", "Explorative Likelihood Ratios werden berechnet… {0} s vergangen.", "Израчунавање истраживачких односа веродостојности… прошло је {0} с.", "탐색적 우도비 계산 중… {0}초 경과."
  ],
  [
    "正在计算单项研究区间…已等待 {0} 秒。", "Calculating study interval… {0} s elapsed.", "Calcul de l’intervalle de l’étude… {0} s écoulées.", "Расчёт интервала для отдельного исследования… прошло {0} с.", "Calculando el intervalo del estudio… {0} s transcurridos.", "研究単位の区間を計算中… {0} 秒経過。", "Calculando o intervalo do estudo… {0} s decorridos.", "Intervall für eine einzelne Studie wird berechnet… {0} s vergangen.", "Израчунавање интервала за појединачну студију… прошло је {0} с.", "연구별 구간 계산 중… {0}초 경과."
  ],
  [
    "正在计算偏倚风险敏感性分析…已等待 {0} 秒。暂无中间进度。", "Calculating risk-of-bias sensitivity… {0} s elapsed. No intermediate progress is available.", "Calcul de l’analyse de sensibilité au risque de biais… {0} s écoulées. Aucune progression intermédiaire n’est disponible.", "Расчёт анализа чувствительности к риску систематической ошибки… прошло {0} с. Промежуточный прогресс недоступен.", "Calculando el análisis de sensibilidad al riesgo de sesgo… {0} s transcurridos. No hay información de progreso intermedio.", "バイアスリスク感度分析を計算中… {0} 秒経過。途中経過は表示されません。", "Calculando a análise de sensibilidade ao risco de viés… {0} s decorridos. Não há informação de progresso intermediário.", "Sensitivitätsanalyse zum Verzerrungsrisiko wird berechnet… {0} s vergangen. Ein Zwischenstand ist nicht verfügbar.", "Израчунавање анализе осетљивости на ризик од пристрасности… прошло је {0} с. Међурезултати нису доступни.", "비뚤림 위험 민감도 분석 계산 중… {0}초 경과. 중간 진행률은 표시되지 않습니다."
  ],
  [
    "正在拟合单组计数模型…已等待 {0} 秒。随机效应模型使用数值积分，暂无中间进度。", "Fitting single-group count models… {0} s elapsed. The random model uses numerical integration; no intermediate progress is available.", "Ajustement des modèles de comptage à un seul groupe… {0} s écoulées. Le modèle à effets aléatoires utilise une intégration numérique ; aucune progression intermédiaire n’est disponible.", "Подгонка моделей счётных данных для одной группы… прошло {0} с. В модели случайных эффектов используется численное интегрирование; промежуточный прогресс недоступен.", "Ajustando los modelos de recuento de un solo grupo… {0} s transcurridos. El modelo de efectos aleatorios usa integración numérica; no hay información de progreso intermedio.", "単群カウントモデルを適合中… {0} 秒経過。ランダム効果モデルでは数値積分を使用するため、途中経過は表示されません。", "Ajustando os modelos de contagem de um único grupo… {0} s decorridos. O modelo de efeitos aleatórios usa integração numérica; não há informação de progresso intermediário.", "Ein-Gruppen-Zählmodelle werden angepasst… {0} s vergangen. Das Random-Effects-Modell verwendet numerische Integration; ein Zwischenstand ist nicht verfügbar.", "Прилагођавање модела броја догађаја за једну групу… прошло је {0} с. Модел случајних ефеката користи нумеричку интеграцију; међурезултати нису доступни.", "단일군 계수 모형 적합 중… {0}초 경과. 랜덤효과 모형은 수치적분을 사용하므로 중간 진행률은 표시되지 않습니다."
  ],
  [
    "正在计算{0}…已等待 {1} 秒。", "Calculating {0}… {1} s elapsed.", "Calcul de {0}… {1} s écoulées.", "Расчёт: {0}… прошло {1} с.", "Calculando {0}… {1} s transcurridos.", "{0} を計算中… {1} 秒経過。", "Calculando {0}… {1} s decorridos.", "{0} wird berechnet… {1} s vergangen.", "Израчунавање {0}… прошло је {1} с.", "{0} 계산 중… {1}초 경과."
  ],
  [
    "缺少必填字段：{0}", "Required field missing: {0}", "Champ obligatoire manquant : {0}", "Не заполнено обязательное поле: {0}", "Falta un campo obligatorio: {0}", "必須フィールドがありません：{0}", "Campo obrigatório ausente: {0}", "Pflichtfeld fehlt: {0}", "Недостаје обавезно поље: {0}", "필수 필드 누락: {0}"
  ],
  [
    "已保存研究 {0} 的 {1} 条原始组别记录。", "Saved {0}; {1} raw arm record(s).", "{1} enregistrement(s) brut(s) de bras sauvegardé(s) pour l’étude {0}.", "Для исследования {0} сохранено записей исходных данных по группам: {1}.", "Se guardaron {1} registros de brazo originales del estudio {0}.", "研究 {0} を保存しました。生の群データを {1} 件記録しました。", "Foram salvos {1} registros brutos de braço do estudo {0}.", "{1} Rohdaten zu Studienarmen für Studie {0} gespeichert.", "Сачувано је {1} изворних записа о групама за студију {0}.", "연구 {0} 저장 완료. 원시 군 자료 {1}건을 기록했습니다."
  ],
  [
    "{0} · {1}：{2}（95% 置信区间 {3}–{4}）；{5} 项研究", "{0} · {1}: {2} (95% CI {3}–{4}); {5} studies", "{0} · {1} : {2} (IC à 95 % {3}–{4}) ; {5} études", "{0} · {1}: {2} (95%-й доверительный интервал {3}–{4}); исследований: {5}", "{0} · {1}: {2} (IC del 95 % {3}–{4}); {5} estudios", "{0} · {1}：{2}（95%信頼区間 {3}–{4}）；研究 {5} 件", "{0} · {1}: {2} (IC 95% {3}–{4}); {5} estudos", "{0} · {1}: {2} (95%-Konfidenzintervall {3}–{4}); {5} Studien", "{0} · {1}: {2} (95% интервал поверења {3}–{4}); студија: {5}", "{0} · {1}: {2}(95% 신뢰구간 {3}–{4}); 연구 {5}건"
  ],
  [
    "已纳入：", "Included:", "Inclus :", "Включено:", "Incluidos:", "採用：", "Incluídos:", "Eingeschlossen:", "Укључено:", "포함:"
  ],
  [
    "纳入 {1} 项研究中的 {0} 项 · t={2} · 自由度={3} · p={4}", "{0} of {1} studies included · t={2} · df={3} · p={4}", "{0} études sur {1} incluses · t={2} · ddl={3} · p={4}", "Включено исследований: {0} из {1} · t={2} · df={3} · p={4}", "{0} de {1} estudios incluidos · t={2} · gl={3} · p={4}", "{1} 件中 {0} 件の研究を採用 · t={2} · 自由度={3} · p={4}", "{0} de {1} estudos incluídos · t={2} · gl={3} · p={4}", "{0} von {1} Studien eingeschlossen · t={2} · df={3} · p={4}", "Укључено студија: {0} од {1} · t={2} · df={3} · p={4}", "연구 {1}건 중 {0}건 포함 · t={2} · 자유도={3} · p={4}"
  ],
  [
    "比例的 logit 与总人数倒数的关系：斜率 {0}（标准误 {1}），95% 置信区间 [{2}, {3}]。", "Logit proportion versus 1/total: slope {0} (SE {1}), 95% CI [{2}, {3}].", "Logit de la proportion en fonction de 1/total : pente {0} (erreur-type {1}), IC à 95 % [{2}, {3}].", "Доля на шкале logit в зависимости от 1/общего числа: наклон {0} (SE {1}), 95%-й доверительный интервал [{2}, {3}].", "Logit de la proporción frente a 1/total: pendiente {0} (error estándar {1}), IC del 95 % [{2}, {3}].", "ロジット割合と総数の逆数の関係：傾き {0}（標準誤差 {1}）、95%信頼区間 [{2}, {3}]。", "Logit da proporção em função de 1/total: inclinação {0} (erro padrão {1}), IC de 95% [{2}, {3}].", "Logit-Anteil gegen 1/Gesamtzahl: Steigung {0} (SE {1}), 95%-Konfidenzintervall [{2}, {3}].", "Логит удео у зависности од 1/укупног броја: нагиб {0} (SE {1}), 95% интервал поверења [{2}, {3}].", "로짓 비율과 총수 역수의 관계: 기울기 {0}(표준오차 {1}), 95% 신뢰구간 [{2}, {3}]."
  ],
  [
    "{0} 项研究 · 敏感度 {1} · 特异度 {2}", "{0} studies · sensitivity {1} · specificity {2}", "{0} études · sensibilité {1} · spécificité {2}", "{0} исследований · чувствительность {1} · специфичность {2}", "{0} estudios · sensibilidad {1} · especificidad {2}", "研究 {0} 件 · 感度 {1} · 特異度 {2}", "{0} estudos · sensibilidade {1} · especificidade {2}", "{0} Studien · Sensitivität {1} · Spezifität {2}", "{0} студија · осетљивост {1} · специфичност {2}", "연구 {0}건 · 민감도 {1} · 특이도 {2}"
  ],
  [
    "logit 敏感度与 logit 特异度尺度上的研究间标准差分别为 {0} 和 {1}。", "Between-study SD on logit sensitivity and logit specificity scales: {0} and {1}.", "Les écarts-types inter-études sur les échelles logit de la sensibilité et de la spécificité sont respectivement {0} et {1}.", "Стандартные отклонения между исследованиями на шкалах logit чувствительности и logit специфичности: {0} и {1}.", "Las desviaciones estándar entre estudios en las escalas logit de sensibilidad y especificidad son {0} y {1}, respectivamente.", "ロジット感度尺度とロジット特異度尺度における研究間標準偏差：{0}、{1}。", "Os desvios padrão entre estudos nas escalas logit de sensibilidade e especificidade são {0} e {1}, respectivamente.", "Zwischenstudien-SD auf der Skala der Logit-Sensitivität und der Logit-Spezifität: {0} und {1}.", "SD између студија на logit скали осетљивости и logit скали специфичности: {0} и {1}.", "로짓 민감도 및 로짓 특이도 척도에서의 연구 간 표준편차: {0}, {1}."
  ],
  [
    "{0} 项研究 · {1} 个数值阈值 · 平均阈值 {2} · {3}。", "{0} studies · {1} numeric thresholds · mean threshold {2} · {3}.", "{0} études · {1} seuils numériques · seuil moyen {2} · {3}.", "{0} исследований · числовых порогов: {1} · средний порог {2} · {3}.", "{0} estudios · {1} umbrales numéricos · umbral medio {2} · {3}.", "研究 {0} 件 · 数値閾値 {1} 個 · 平均閾値 {2} · {3}。", "{0} estudos · {1} limiares numéricos · limiar médio {2} · {3}.", "{0} Studien · {1} numerische Schwellenwerte · mittlerer Schwellenwert {2} · {3}.", "{0} студија · {1} нумеричких прагова · просечни праг {2} · {3}.", "연구 {0}건 · 수치 임계값 {1}개 · 평균 임계값 {2} · {3}."
  ],
  [
    "研究平均阈值处的敏感度为 {0}，特异度为 {1}。", "Sensitivity {0}; specificity {1} at the mean study threshold.", "Sensibilité {0} ; spécificité {1} au seuil moyen des études.", "Чувствительность {0}; специфичность {1} при среднем пороге исследований.", "Sensibilidad {0}; especificidad {1} en el umbral medio de los estudios.", "研究の平均閾値における感度 {0}、特異度 {1}。", "Sensibilidade {0}; especificidade {1} no limiar médio dos estudos.", "Sensitivität {0}; Spezifität {1} am mittleren Schwellenwert der Studien.", "Осетљивост {0}; специфичност {1} на просечном прагу студија.", "연구 평균 임계값에서 민감도 {0}, 특이도 {1}."
  ],
  [
    "研究来源：{0}",
    "Study sources: {0}"
  ],
  [
    "{0}（{1}；阈值 {2}）", "{0} ({1}; threshold {2})", "{0} ({1} ; seuil {2})", "{0} ({1}; порог {2})", "{0} ({1}; umbral {2})", "{0}（{1}；閾値 {2}）", "{0} ({1}; limiar {2})", "{0} ({1}; Schwellenwert {2})", "{0} ({1}; праг {2})", "{0}({1}; 임계값 {2})"
  ],
  [
    "评价框架：{0}；已排除的总体判断：{1}。来源：{2}。排除后的变化不能证明存在偏倚；请引用评价与合并分析方法。", "Framework {0}; excluded overall labels: {1}. Sources: {2}. Do not interpret exclusion as proof of bias; cite the assessment and synthesis methods.", "Cadre d’évaluation : {0} ; catégories globales exclues : {1}. Sources : {2}. N’interprétez pas les changements après exclusion comme une preuve de biais ; citez les méthodes d’évaluation et de synthèse.", "Система оценки: {0}; исключённые общие оценки: {1}. Источники: {2}. Изменение после исключения не доказывает наличие смещения; укажите в ссылках методы оценки и синтеза.", "Marco de evaluación: {0}; categorías generales excluidas: {1}. Fuentes: {2}. No interprete los cambios tras la exclusión como prueba de sesgo; cite los métodos de evaluación y síntesis.", "評価枠組み：{0}；除外された全体判定：{1}。出典：{2}。除外後に変化しても、バイアスの存在を証明するものではありません。評価と統合解析の方法を引用してください。", "Estrutura de avaliação: {0}; categorias de julgamento geral excluídas: {1}. Fontes: {2}. Não interprete as mudanças após a exclusão como prova de viés; cite os métodos de avaliação e síntese.", "Bewertungsrahmen: {0}; ausgeschlossene Gesamtbewertungen: {1}. Quellen: {2}. Eine Veränderung nach dem Ausschluss beweist keine Verzerrung; zitieren Sie die Bewertungs- und Synthesemethoden.", "Оквир процене: {0}; искључене укупне оцене: {1}. Извори: {2}. Промена након искључења не доказује пристрасност; цитирајте методе процене и синтезе.", "평가 체계: {0}; 제외된 전반적 판정: {1}. 출처: {2}. 제외 후 변화가 있다고 해서 편향이 입증되는 것은 아닙니다. 평가 및 통합 분석 방법을 인용하세요."
  ],
  [
    "分析",
    "Analysis"
  ],
  [
    "研究数",
    "Studies"
  ],
  [
    "合并估计", "Pooled", "Estimation combinée", "Объединённая оценка", "Estimación combinada", "統合推定値", "Estimativa combinada", "Gepoolt", "Обједињено", "통합 추정치"
  ],
  [
    "排除的研究编号", "Excluded IDs", "Identifiants des études exclues", "Идентификаторы исключённых исследований", "Identificadores de los estudios excluidos", "除外した研究 ID", "Identificadores dos estudos excluídos", "IDs der ausgeschlossenen Studien", "ID-јеви искључених студија", "제외된 연구 ID"
  ],
  [
    "所有已评价研究", "All assessed studies", "Toutes les études évaluées", "Все оценённые исследования", "Todos los estudios evaluados", "評価したすべての研究", "Todos os estudos avaliados", "Alle bewerteten Studien", "Све процењене студије", "평가한 모든 연구"
  ],
  [
    "排除后", "After exclusion", "Après exclusion", "После исключения", "Tras la exclusión", "除外後", "Após a exclusão", "Nach dem Ausschluss", "Након искључења", "제외 후"
  ],
  [
    "探索性分别合并 — {0}% 区间", "Exploratory separate pooling — {0}% intervals", "Combinaison exploratoire séparée — intervalles à {0} %", "Раздельное объединение в разведочном анализе — интервалы {0}%", "Combinación exploratoria por separado — intervalos del {0} %", "探索的な個別統合 — {0}% 区間", "Combinação exploratória separada — intervalos de {0}%", "Exploratives getrenntes Pooling — {0}%-Intervalle", "Истраживачко одвојено обједињавање — интервали од {0}%", "탐색적 개별 통합 — {0}% 구간"
  ],
  [
    "{0}；纳入 {2} 项研究中的 {1} 项。{3}。即使选择固定权重，DL τ² 也仅为诊断性估计。", "{0}; {1}/{2} studies used. {3}. DL τ² is a diagnostic estimate even when fixed weights are selected.", "{0} ; {1} études sur {2} utilisées. {3}. Même avec des poids fixes, τ² de DL reste une estimation diagnostique.", "{0}; использовано исследований: {1} из {2}. {3}. Оценка DL τ² остаётся диагностической даже при выборе фиксированных весов.", "{0}; se utilizaron {1} de {2} estudios. {3}. Incluso si se eligen pesos fijos, τ² de DL sigue siendo una estimación diagnóstica.", "{0}；{2} 件中 {1} 件の研究を使用。{3}。固定重みを選択した場合も、DL τ² は診断目的の推定値にすぎません。", "{0}; {1} de {2} estudos utilizados. {3}. Mesmo com pesos fixos selecionados, τ² de DL continua sendo uma estimativa diagnóstica.", "{0}; {1} von {2} Studien verwendet. {3}. DL-τ² ist auch bei ausgewählten festen Gewichten nur eine diagnostische Schätzung.", "{0}; коришћено студија: {1} од {2}. {3}. DL τ² је само дијагностичка процена чак и када су изабране фиксне тежине.", "{0}; 연구 {2}건 중 {1}건 사용. {3}. 고정 가중치를 선택해도 DL τ²는 진단용 추정치입니다."
  ],
  [
    "已排除 {0}：{1}", "Excluded {0}: {1}", "{0} exclu(s) : {1}", "Исключено {0}: {1}", "{0} excluidos: {1}", "{0} を除外：{1}", "{0} excluídos: {1}", "Ausgeschlossen {0}: {1}", "Искључено {0}: {1}", "{0} 제외: {1}"
  ],
  [
    "{0} · {1}% {2}",
    "{0} · {1}% {2}"
  ],
  [
    "估计值 {0}；区间 [{1}]{2}。", "Estimate {0}; interval [{1}]{2}.", "Estimation {0} ; intervalle [{1}]{2}.", "Оценка {0}; интервал [{1}]{2}.", "Estimación {0}; intervalo [{1}]{2}.", "推定値 {0}；区間 [{1}]{2}。", "Estimativa {0}; intervalo [{1}]{2}.", "Schätzwert {0}; Intervall [{1}]{2}.", "Процена {0}; интервал [{1}]{2}.", "추정치 {0}; 구간 [{1}]{2}."
  ],
  [
    "每 {0} 的事件数", " events per {0}", " événements par {0}", " событий на {0}", " eventos por {0}", "{0} あたりのイベント数", " eventos por {0}", " Ereignisse je {0}", " догађаја на {0}", "{0}당 사건 수"
  ],
  [
    "（比例）", "(proportion)", "(proportion)", "(доля)", "(proporción)", "（割合）", "(proporção)", "(Anteil)", "(удео)", "(비율)"
  ],
  [
    "按每 {0} 计算的发生率：治疗组 {1}，对照组 {2}。无穷界限显示为 ∞，并在 JSON 记录中保留为明确字符串。", "Rates per {0}: treatment {1}, control {2}. Infinite bounds are shown as ∞ and preserved as explicit strings in the JSON record.", "Taux pour chaque {0} : groupe traité {1}, groupe témoin {2}. Les bornes infinies sont affichées comme ∞ et conservées sous forme de chaînes explicites dans l’enregistrement JSON.", "Частоты на {0}: лечение {1}, контроль {2}. Бесконечные границы отображаются как ∞ и сохраняются в JSON как явные строки.", "Tasas por {0}: tratamiento {1}, control {2}. Los límites infinitos se muestran como ∞ y se conservan como cadenas explícitas en el registro JSON.", "{0} あたりの発生率：治療群 {1}、対照群 {2}。無限の限界は ∞ と表示し、JSON レコードには明示的な文字列として保持します。", "Taxas por {0}: tratamento {1}, controle {2}. Limites infinitos são exibidos como ∞ e mantidos como cadeias explícitas no registro JSON.", "Raten je {0}: Behandlung {1}, Kontrolle {2}. Unendliche Grenzen werden als ∞ angezeigt und im JSON-Datensatz als explizite Zeichenfolgen gespeichert.", "Стопе на {0}: терапијска група {1}, контролна група {2}. Бесконачне границе приказују се као ∞ и чувају као изричите текстуалне вредности у JSON запису.", "{0}당 발생률: 치료군 {1}, 대조군 {2}. 무한 한계는 ∞로 표시하고 JSON 레코드에는 명시적인 문자열로 보존합니다."
  ],
  [
    "研究来源：{0}",
    "Study source: {0}"
  ],
  [
    "已在先前任务 {0} 中保存生存效应。", "Saved a survival effect in the earlier task {0}.", "Un effet de survie a été enregistré dans la tâche précédente {0}.", "В предыдущей задаче {0} сохранён эффект выживаемости.", "Se guardó un efecto de supervivencia en la tarea anterior {0}.", "前のタスク {0} で生存効果を保存しました。", "Um efeito de sobrevida foi salvo na tarefa anterior {0}.", "In der vorherigen Aufgabe {0} wurde ein Überlebenseffekt gespeichert.", "Ефекат преживљавања је сачуван у претходном задатку {0}.", "이전 작업 {0}에서 생존 효과를 저장했습니다."
  ],
  [
    "{0} {1}：{2}。{3}。",
    "{0} {1}: {2}. {3}."
  ],
  [
    "已保存",
    "Saved"
  ],
  [
    "重构的 O−E={0}，V={1}；这些并非原文报告的 log-rank 统计量。", "Reconstructed O−E={0}, V={1}; these are not reported log-rank statistics.", "O−E={0} et V={1} reconstitués ; ces statistiques ne sont pas rapportées dans la source.", "Реконструированные O−E={0}, V={1}; эти значения не являются исходно опубликованными статистиками log-rank.", "O−E={0} y V={1} reconstruidos; no son estadísticas de log-rank comunicadas en la fuente.", "再構成した O−E={0}、V={1}；これらは原文で報告されたログランク統計量ではありません。", "O−E={0}, V={1} reconstruídos; não são estatísticas de log-rank informadas na fonte.", "Rekonstruiertes O−E={0}, V={1}; dies sind keine im Original berichteten Log-Rank-Statistiken.", "Реконструисани O−E={0}, V={1}; то нису изворно пријављене log-rank статистике.", "재구성한 O−E={0}, V={1}; 원문에 보고된 로그순위 통계량이 아닙니다."
  ],
  [
    "研究间方差 τ²={0}（{1}；{2}方差尺度）；{3}。", "Between-study variance τ²={0} ({1}; {2} variance scale); {3}.", "Variance inter-études τ²={0} ({1} ; échelle de variance {2}) ; {3}.", "Межисследовательская дисперсия τ²={0} ({1}; шкала дисперсии {2}); {3}.", "Varianza entre estudios τ²={0} ({1}; escala de varianza {2}); {3}.", "研究間分散 τ²={0}（{1}；{2} 分散尺度）；{3}。", "Variância entre estudos τ²={0} ({1}; escala de variância {2}); {3}.", "Zwischenstudienvarianz τ²={0} ({1}; Varianzskala {2}); {3}.", "Варијанса између студија τ²={0} ({1}; скала варијансе {2}); {3}.", "연구 간 분산 τ²={0}({1}; {2} 분산 척도); {3}."
  ],
  [
    "{0}（{1}；{2}方差尺度）", "{0} ({1}; {2} variance scale)", "{0} ({1} ; échelle de variance {2})", "{0} ({1}; шкала дисперсии {2})", "{0} ({1}; escala de varianza {2})", "{0}（{1}；{2} 分散尺度）", "{0} ({1}; escala de variância {2})", "{0} ({1}; Varianzskala {2})", "{0} ({1}; скала варијансе {2})", "{0}({1}; {2} 분산 척도)"
  ],
  [
    "{0}／{1} 的协方差", "{0} / {1} covariance", "Covariance de {0} / {1}", "Ковариация {0}/{1}", "Covarianza de {0} / {1}", "{0}／{1} の共分散", "Covariância de {0} / {1}", "Kovarianz {0}/{1}", "Коваријанса {0}/{1}", "{0}/{1} 공분산"
  ],
  [
    "{0} 的协方差来源", "{0} covariance source", "Source de covariance de {0}", "Источник ковариации {0}", "Fuente de covarianza de {0}", "{0} の共分散の出典", "Fonte da covariância de {0}", "Quelle der Kovarianz von {0}", "Извор коваријансе за {0}", "{0} 공분산 출처"
  ],
  [
    "纳入结果 {0}", "Include result {0}", "Inclure le résultat {0}", "Включить результат {0}", "Incluir el resultado {0}", "結果 {0} を含める", "Incluir o resultado {0}", "Ergebnis {0} einbeziehen", "Укључи резултат {0}", "결과 {0} 포함"
  ],
  [
    "选择计数结果 {0}", "Select count result {0}", "Sélectionner le résultat du décompte {0}", "Выбрать результат счёта {0}", "Seleccionar el resultado del recuento {0}", "計数アウトカム {0} を選択", "Selecionar o resultado da contagem {0}", "Zählergebnis {0} auswählen", "Изабери резултат бројања {0}", "계수 결과 {0} 선택"
  ],
  [
    "{0} 项独立研究 · {1}{2}", "{0} independent studies · {1}{2}", "{0} études indépendantes · {1}{2}", "{0} независимых исследований · {1}{2}", "{0} estudios independientes · {1}{2}", "独立した研究 {0} 件 · {1}{2}", "{0} estudos independentes · {1}{2}", "{0} unabhängige Studien · {1}{2}", "{0} независних студија · {1}{2}", "독립 연구 {0}건 · {1}{2}"
  ],
  [
    "／{0}",
    " per {0}"
  ],
  [
    "{0}：{1}（95% 置信区间 {2}）。", "{0}: {1} (95% CI {2}).", "{0} : {1} (IC à 95 % {2}).", "{0}: {1} (95%-й доверительный интервал {2}).", "{0}: {1} (IC del 95 % {2}).", "{0}：{1}（95%信頼区間 {2}）。", "{0}: {1} (IC de 95% {2}).", "{0}: {1} (95%-Konfidenzintervall {2}).", "{0}: {1} (95% интервал поверења {2}).", "{0}: {1}(95% 신뢰구간 {2})."
  ],
  [
    "{0}：{1}；条件估计 {2}（95% 置信区间 {3}）；边际均值 {4}；τ² {5}（ML；{6}方差尺度）。", "{0}: {1}; conditional estimate {2} (95% CI {3}); marginal mean {4}; τ² {5} (ML; {6} variance scale).", "{0} : {1} ; estimation conditionnelle {2} (IC à 95 % {3}) ; moyenne marginale {4} ; τ² {5} (ML ; échelle de variance {6}).", "{0}: {1}; условная оценка {2} (95%-й доверительный интервал {3}); маргинальное среднее {4}; τ² {5} (ML; шкала дисперсии {6}).", "{0}: {1}; estimación condicional {2} (IC del 95 % {3}); media marginal {4}; τ² {5} (ML; escala de varianza {6}).", "{0}：{1}；条件付き推定値 {2}（95%信頼区間 {3}）；周辺平均 {4}；τ² {5}（ML；{6} 分散尺度）。", "{0}: {1}; estimativa condicional {2} (IC de 95% {3}); média marginal {4}; τ² {5} (ML; escala de variância {6}).", "{0}: {1}; bedingte Schätzung {2} (95%-Konfidenzintervall {3}); marginaler Mittelwert {4}; τ² {5} (ML; Varianzskala {6}).", "{0}: {1}; условна процена {2} (95% интервал поверења {3}); маргинална средња вредност {4}; τ² {5} (ML; скала варијансе {6}).", "{0}: {1}; 조건부 추정치 {2}(95% 신뢰구간 {3}); 주변 평균 {4}; τ² {5}(ML; {6} 분산 척도)."
  ],
  [
    "研究间方差：{0}。研究间相关：{1}。{2}", "Between-study variances: {0}. Between-study correlations: {1}. {2}", "Variances inter-études : {0}. Corrélations inter-études : {1}. {2}", "Межисследовательские дисперсии: {0}. Межисследовательские корреляции: {1}. {2}", "Varianzas entre estudios: {0}. Correlaciones entre estudios: {1}. {2}", "研究間分散：{0}。研究間相関：{1}。{2}", "Variâncias entre estudos: {0}. Correlações entre estudos: {1}. {2}", "Zwischenstudienvarianzen: {0}. Zwischenstudienkorrelationen: {1}. {2}", "Варијансе између студија: {0}. Корелације између студија: {1}. {2}", "연구 간 분산: {0}. 연구 간 상관: {1}. {2}"
  ],
  [
    "边界解，谨慎解读相关系数。", "Boundary solution; interpret correlations cautiously.", "Solution à la limite ; interprétez les coefficients de corrélation avec prudence.", "Решение на границе; интерпретируйте корреляции осторожно.", "Solución en el límite; interprete las correlaciones con cautela.", "境界解です。相関係数は慎重に解釈してください。", "Solução no limite; interprete as correlações com cautela.", "Randlösung; Korrelationen mit Vorsicht interpretieren.", "Решење на граници; корелације тумачите опрезно.", "경계해입니다. 상관계수는 주의해서 해석하세요."
  ],
  [
    "不可识别", "Not identifiable", "Non identifiable", "Не идентифицируется", "No identificable", "識別不能", "Não identificável", "Nicht identifizierbar", "Не може се идентификовати", "식별 불가"
  ],
  [
    "{0} · {1} 尺度", "{0} · {1} scale", "Échelle {0} · {1}", "{0} · шкала {1}", "Escala {0} · {1}", "{0} · {1} 尺度", "Escala {0} · {1}", "{0} · Skala {1}", "{0} · скала {1}", "{0} · {1} 척도"
  ],
  [
    "异质性 Q（抽样协方差）", "Heterogeneity Q (sampling covariance)", "Q d’hétérogénéité (covariance d’échantillonnage)", "Q гетерогенности (выборочная ковариация)", "Q de heterogeneidad (covarianza de muestreo)", "異質性 Q（標本共分散）", "Q de heterogeneidade (covariância amostral)", "Heterogenitäts-Q (Stichprobenkovarianz)", "Q хетерогености (узорачка коваријанса)", "이질성 Q(표본 공분산)"
  ],
  [
    "等效应残差 Q", "Equal-effects residual Q", "Q résiduel à effets égaux", "Остаточное Q при равных эффектах", "Q residual de efectos iguales", "等効果残差 Q", "Q residual de efectos iguais", "Residuales Q bei gleichen Effekten", "Резидуални Q за једнаке ефекте", "동일 효과 잔차 Q"
  ],
  [
    "请填写 {0} 的结局间协方差", "Enter between-outcome covariance for {0}", "Saisissez la covariance entre les critères pour {0}.", "Введите ковариацию между исходами для {0}.", "Introduzca la covarianza entre desenlaces para {0}.", "{0} のアウトカム間共分散を入力してください。", "Informe a covariância entre desfechos para {0}.", "Geben Sie die Kovarianz zwischen den Endpunkten für {0} ein.", "Унесите коваријансу између исхода за {0}.", "{0}의 결과 간 공분산을 입력하세요."
  ],
  [
    "请填写 {0} 的协方差来源或假设", "Enter a covariance source or assumption for {0}", "Saisissez la source ou l’hypothèse de covariance pour {0}.", "Укажите источник ковариации для {0} или допущение о ней.", "Introduzca la fuente o el supuesto de covarianza para {0}.", "{0} の共分散の出典または仮定を入力してください。", "Informe a fonte ou a hipótese de covariância para {0}.", "Geben Sie eine Quelle oder Annahme für die Kovarianz von {0} ein.", "Унесите извор коваријансе за {0} или претпоставку о њој.", "{0}의 공분산 출처 또는 가정을 입력하세요."
  ],
  ["正在计算 {0}：已等待 {1} 秒。此分析运行期间不显示中途进度。{2}", "Calculating {0}: {1} seconds elapsed. Progress is not shown while this analysis runs. {2}", "Calcul de {0} : {1} secondes écoulées. La progression n’est pas affichée pendant cette analyse. {2}", "Выполняется расчёт {0}: прошло {1} с. Во время анализа промежуточный прогресс не отображается. {2}", "Calculando {0}: han transcurrido {1} s. No se muestra el progreso durante este análisis. {2}", "{0} を計算中：{1} 秒経過。分析の実行中は途中経過を表示しません。{2}", "Calculando {0}: {1} s decorridos. O progresso não é exibido durante esta análise. {2}", "{0} wird berechnet: {1} Sekunden vergangen. Während dieser Analyse wird kein Zwischenstand angezeigt. {2}", "Рачунање {0}: прошло је {1} с. Током ове анализе не приказује се међупрогрес. {2}", "{0} 계산 중: {1}초 경과. 분석이 실행되는 동안 진행 상황은 표시되지 않습니다. {2}"],
  ["运行时间随数据量和系统负载变化。", "Runtime varies with data size and system load.", "La durée varie selon le volume de données et la charge du système.", "Время выполнения зависит от объёма данных и нагрузки на систему.", "El tiempo de ejecución varía según el volumen de datos y la carga del sistema.", "実行時間はデータ量とシステム負荷によって異なります。", "O tempo de execução varia conforme o volume de dados e a carga do sistema.", "Die Laufzeit hängt vom Datenumfang und der Systemauslastung ab.", "Време извршавања зависи од количине података и оптерећења система.", "실행 시간은 데이터 양과 시스템 부하에 따라 달라집니다."],
  ["此信息暂未提供当前语言译文；展开可查看原文。", "This message is not translated into the current language. Expand it to view the original.", "Ce message n’est pas traduit dans la langue actuelle. Développez-le pour voir le texte original.", "Это сообщение пока не переведено на выбранный язык. Разверните его, чтобы увидеть оригинал.", "Este mensaje aún no está traducido al idioma actual. Despliéguelo para ver el original.", "このメッセージは現在の言語に翻訳されていません。展開すると原文を確認できます。", "Esta mensagem ainda não foi traduzida para o idioma atual. Expanda-a para ver o original.", "Diese Nachricht liegt noch nicht in der aktuellen Sprache vor. Öffnen Sie sie, um den Originaltext zu sehen.", "Ова порука још није преведена на изабрани језик. Отворите је да бисте видели оригинал.", "이 메시지는 현재 언어로 아직 번역되지 않았습니다. 펼쳐서 원문을 확인하세요."],
  [
    "当前位置",
    "Current location",
    "Emplacement actuel",
    "Текущее расположение",
    "Ubicación actual",
    "現在の位置",
    "Localização atual",
    "Aktueller Bereich",
    "Тренутна локација",
    "현재 위치"
  ],
  [
    "切换工作区",
    "Switch workspace",
    "Changer d’espace de travail",
    "Переключить рабочую область",
    "Cambiar de espacio de trabajo",
    "ワークスペースを切り替え",
    "Mudar de área de trabalho",
    "Arbeitsbereich wechseln",
    "Промени радни простор",
    "작업 공간 전환"
  ],
  [
    "工作区",
    "Workspaces",
    "Espaces de travail",
    "Рабочие области",
    "Espacios de trabajo",
    "ワークスペース",
    "Áreas de trabalho",
    "Arbeitsbereiche",
    "Радни простори",
    "작업 공간"
  ],
  [
    "当前任务中的工作区",
    "Workspaces in this task",
    "Espaces de travail de cette tâche",
    "Рабочие области этой задачи",
    "Espacios de trabajo de esta tarea",
    "このタスクのワークスペース",
    "Áreas de trabalho desta tarefa",
    "Arbeitsbereiche dieser Aufgabe",
    "Радни простори овог задатка",
    "이 작업의 작업 공간"
  ],
  [
    "未选择任务",
    "No task selected",
    "Aucune tâche sélectionnée",
    "Задача не выбрана",
    "Ninguna tarea seleccionada",
    "タスク未選択",
    "Nenhuma tarefa selecionada",
    "Keine Aufgabe ausgewählt",
    "Ниједан задатак није изабран",
    "선택한 작업 없음"
  ],
  [
    "关闭导航",
    "Close navigation",
    "Fermer la navigation",
    "Закрыть навигацию",
    "Cerrar navegación",
    "ナビゲーションを閉じる",
    "Fechar navegação",
    "Navigation schließen",
    "Затвори навигацију",
    "탐색 닫기"
  ],
  [
    "前往{0}",
    "Go to {0}",
    "Aller à {0}",
    "Перейти к {0}",
    "Ir a {0}",
    "{0}へ移動",
    "Ir para {0}",
    "Zu {0} wechseln",
    "Иди на {0}",
    "{0}(으)로 이동"
  ],
  [
    "切换工作区；当前任务：{0}",
    "Switch workspace; current task: {0}",
    "Changer d’espace de travail ; tâche actuelle : {0}",
    "Переключить рабочую область; текущая задача: {0}",
    "Cambiar de espacio de trabajo; tarea actual: {0}",
    "ワークスペースを切り替え。現在のタスク：{0}",
    "Mudar de área de trabalho; tarefa atual: {0}",
    "Arbeitsbereich wechseln; aktuelle Aufgabe: {0}",
    "Промени радни простор; тренутни задатак: {0}",
    "작업 공간 전환. 현재 작업: {0}"
  ],
  [
    "首页 & 设置",
    "Home & settings",
    "Accueil et paramètres",
    "Главная и настройки",
    "Inicio y configuración",
    "ホームと設定",
    "Início e configurações",
    "Start und Einstellungen",
    "Почетна и подешавања",
    "홈 및 설정"
  ],
  [
    "分析工作区",
    "Analysis workspace",
    "Espace d’analyse",
    "Рабочая область анализа",
    "Espacio de análisis",
    "分析ワークスペース",
    "Área de análise",
    "Analysebereich",
    "Радни простор за анализу",
    "분석 작업 공간"
  ],
  [
    "打开分析目录",
    "Open analysis directory",
    "Ouvrir le répertoire d’analyse",
    "Открыть каталог анализов",
    "Abrir el directorio de análisis",
    "分析一覧を開く",
    "Abrir o diretório de análises",
    "Analyseverzeichnis öffnen",
    "Отвори каталог анализа",
    "분석 목록 열기"
  ],
  [
    "分析目录",
    "Analysis directory",
    "Répertoire d’analyse",
    "Каталог анализов",
    "Directorio de análisis",
    "分析一覧",
    "Diretório de análises",
    "Analyseverzeichnis",
    "Каталог анализа",
    "분석 목록"
  ],
  [
    "贝叶斯先验敏感性",
    "Bayesian prior sensitivity",
    "Sensibilité aux a priori bayésiens",
    "Чувствительность к априорным распределениям",
    "Sensibilidad a las distribuciones a priori",
    "ベイズ事前分布の感度分析",
    "Sensibilidade às distribuições a priori",
    "Sensitivität gegenüber bayesschen Prior-Verteilungen",
    "Осетљивост на Бајесове априорне расподеле",
    "베이지안 사전분포 민감도"
  ],
  [
    "未找到匹配的方法",
    "No matching methods",
    "Aucune méthode correspondante",
    "Подходящих методов нет",
    "No hay métodos coincidentes",
    "該当する手法なし",
    "Nenhum método correspondente",
    "Keine passenden Methoden",
    "Нема одговарајућих метода",
    "일치하는 방법 없음"
  ],
  [
    "研究（仅用于录入）",
    "Study (data entry only)",
    "Étude (saisie uniquement)",
    "Исследование (только ввод данных)",
    "Estudio (solo entrada de datos)",
    "研究（データ入力専用）",
    "Estudo (apenas entrada de dados)",
    "Studie (nur Dateneingabe)",
    "Студија (само унос података)",
    "연구(데이터 입력용)"
  ],
  [
    "未运行",
    "Not run",
    "Non exécuté",
    "Не выполнено",
    "Sin ejecutar",
    "未実行",
    "Não executado",
    "Nicht ausgeführt",
    "Није покренуто",
    "실행 전"
  ],
  [
    "图表与报告",
    "Figure & reports",
    "Figure et rapports",
    "График и отчёты",
    "Gráfico e informes",
    "図とレポート",
    "Gráfico e relatórios",
    "Abbildung und Berichte",
    "Графикон и извештаји",
    "그림 및 보고서"
  ],
  [
    "导出当前森林图",
    "Export current forest plot",
    "Exporter le graphique en forêt actuel",
    "Экспортировать текущий лесовидный график",
    "Exportar el gráfico de bosque actual",
    "現在のフォレストプロットを出力",
    "Exportar o gráfico de floresta atual",
    "Aktuellen Forest-Plot exportieren",
    "Извези тренутни шумски дијаграм",
    "현재 포리스트 플롯 내보내기"
  ],
  [
    "重新计算并导出 Word",
    "Recompute & export Word",
    "Recalculer et exporter vers Word",
    "Пересчитать и экспортировать в Word",
    "Recalcular y exportar a Word",
    "再計算してWordへ出力",
    "Recalcular e exportar para Word",
    "Neu berechnen und als Word exportieren",
    "Поново израчунај и извези у Word",
    "다시 계산하고 Word로 내보내기"
  ],
  [
    "重新计算并导出 PowerPoint",
    "Recompute & export PowerPoint",
    "Recalculer et exporter vers PowerPoint",
    "Пересчитать и экспортировать в PowerPoint",
    "Recalcular y exportar a PowerPoint",
    "再計算してPowerPointへ出力",
    "Recalcular e exportar para PowerPoint",
    "Neu berechnen und als PowerPoint exportieren",
    "Поново израчунај и извези у PowerPoint",
    "다시 계산하고 PowerPoint로 내보내기"
  ],
  [
    "重新计算并导出 LaTeX",
    "Recompute & export LaTeX",
    "Recalculer et exporter vers LaTeX",
    "Пересчитать и экспортировать в LaTeX",
    "Recalcular y exportar a LaTeX",
    "再計算してLaTeXへ出力",
    "Recalcular e exportar para LaTeX",
    "Neu berechnen und als LaTeX exportieren",
    "Поново израчунај и извези у LaTeX",
    "다시 계산하고 LaTeX으로 내보내기"
  ],
  [
    "数据与审计记录",
    "Data & audit records",
    "Données et traces d’audit",
    "Данные и записи аудита",
    "Datos y registros de auditoría",
    "データと監査記録",
    "Dados e registros de auditoria",
    "Daten und Prüfprotokolle",
    "Подаци и записи ревизије",
    "데이터 및 감사 기록"
  ],
  [
    "导出合并审计 CSV",
    "Export synthesis audit CSV",
    "Exporter l’audit de synthèse en CSV",
    "Экспортировать аудит синтеза в CSV",
    "Exportar auditoría de síntesis en CSV",
    "統合分析の監査記録をCSV出力",
    "Exportar auditoria da síntese em CSV",
    "Synthese-Prüfprotokoll als CSV exportieren",
    "Извези ревизију синтезе у CSV",
    "통합 분석 감사 기록 CSV 내보내기"
  ],
  [
    "报告会按已应用的设置重新计算。",
    "Reports are recomputed using the applied settings.",
    "Les rapports sont recalculés avec les paramètres appliqués.",
    "Отчёты пересчитываются с применёнными настройками.",
    "Los informes se recalculan con la configuración aplicada.",
    "レポートは適用済み設定で再計算されます。",
    "Os relatórios são recalculados com as configurações aplicadas.",
    "Berichte werden mit den übernommenen Einstellungen neu berechnet.",
    "Извештаји се поново израчунавају са примењеним подешавањима.",
    "보고서는 적용된 설정으로 다시 계산됩니다."
  ],
  [
    "结果",
    "Results",
    "Résultats",
    "Результаты",
    "Resultados",
    "結果",
    "Resultados",
    "Ergebnisse",
    "Резултати",
    "결과"
  ],
  [
    "模型与区间比较",
    "Model & interval comparison",
    "Comparaison des modèles et des intervalles",
    "Сравнение моделей и интервалов",
    "Comparación de modelos e intervalos",
    "モデルと区間の比較",
    "Comparação de modelos e intervalos",
    "Modell- und Intervallvergleich",
    "Поређење модела и интервала",
    "모형 및 구간 비교"
  ],
  [
    "研究诊断",
    "Study diagnostics",
    "Diagnostics des études",
    "Диагностика исследований",
    "Diagnóstico de estudios",
    "研究の診断",
    "Diagnóstico dos estudos",
    "Studiendiagnostik",
    "Дијагностика студија",
    "연구 진단"
  ],
  [
    "方法与引用",
    "Methods & citations",
    "Méthodes et références",
    "Методы и ссылки",
    "Métodos y referencias",
    "手法と引用",
    "Métodos e referências",
    "Methoden und Quellen",
    "Методе и извори",
    "방법 및 인용"
  ],
  [
    "设置已更新，以下仍为上次运行结果。",
    "Settings have changed. The result below is from the previous run.",
    "Les paramètres ont changé. Le résultat ci-dessous provient de l’exécution précédente.",
    "Настройки изменены. Ниже показан результат предыдущего запуска.",
    "La configuración ha cambiado. El resultado inferior corresponde a la ejecución anterior.",
    "設定が変更されました。以下は前回の実行結果です。",
    "As configurações mudaram. O resultado abaixo é da execução anterior.",
    "Die Einstellungen wurden geändert. Unten steht das Ergebnis des vorherigen Laufs.",
    "Подешавања су промењена. Испод је резултат претходног покретања.",
    "설정이 변경되었습니다. 아래는 이전 실행 결과입니다."
  ],
  [
    "等待运行",
    "Ready to analyse",
    "Prêt pour l’analyse",
    "Готово к анализу",
    "Listo para analizar",
    "分析の準備完了",
    "Pronto para analisar",
    "Bereit zur Analyse",
    "Спремно за анализу",
    "분석 준비 완료"
  ],
  [
    "先确认分析范围和模型，再运行合并分析。",
    "Confirm the analysis scope and model, then run the synthesis.",
    "Vérifiez le périmètre et le modèle, puis lancez la synthèse.",
    "Проверьте область анализа и модель, затем запустите синтез.",
    "Confirme el alcance y el modelo y ejecute la síntesis.",
    "分析範囲とモデルを確認してから統合分析を実行してください。",
    "Confirme o escopo e o modelo e execute a síntese.",
    "Prüfen Sie Analyseumfang und Modell und starten Sie die Synthese.",
    "Потврдите обухват и модел, па покрените синтезу.",
    "분석 범위와 모형을 확인한 후 통합 분석을 실행하세요."
  ],
  [
    "结果将显示合并效应、区间和森林图。此处不会生成示例数据。",
    "The result will show the pooled effect, interval and forest plot. No sample data are generated here.",
    "Le résultat affichera l’effet combiné, l’intervalle et le graphique en forêt. Aucune donnée d’exemple n’est générée ici.",
    "Результат включает объединённый эффект, интервал и лесовидный график. Примерные данные здесь не создаются.",
    "El resultado mostrará el efecto combinado, el intervalo y el gráfico de bosque. Aquí no se generan datos de ejemplo.",
    "結果には統合効果量、区間、フォレストプロットが表示されます。サンプルデータは生成されません。",
    "O resultado mostrará o efeito combinado, o intervalo e o gráfico de floresta. Não são gerados dados de exemplo.",
    "Das Ergebnis zeigt den gepoolten Effekt, das Intervall und den Forest-Plot. Es werden keine Beispieldaten erzeugt.",
    "Резултат приказује обједињени ефекат, интервал и шумски дијаграм. Примерни подаци се не генеришу.",
    "통합 효과, 구간 및 포리스트 플롯이 표시됩니다. 예제 데이터는 생성하지 않습니다."
  ],
  [
    "显示设置",
    "Display settings",
    "Paramètres d’affichage",
    "Настройки отображения",
    "Configuración de visualización",
    "表示設定",
    "Configurações de exibição",
    "Darstellungseinstellungen",
    "Подешавања приказа",
    "표시 설정"
  ],
  [
    "异质性说明",
    "Heterogeneity details",
    "Détails de l’hétérogénéité",
    "Подробности неоднородности",
    "Detalles de heterogeneidad",
    "異質性の詳細",
    "Detalhes da heterogeneidade",
    "Details zur Heterogenität",
    "Детаљи хетерогености",
    "이질성 세부 정보"
  ],
  [
    "效应与区间方法",
    "Effect & interval methods",
    "Méthodes d’effet et d’intervalle",
    "Методы оценки эффекта и интервала",
    "Métodos del efecto y del intervalo",
    "効果量と区間の手法",
    "Métodos do efeito e do intervalo",
    "Effekt- und Intervallmethoden",
    "Методе ефекта и интервала",
    "효과 및 구간 방법"
  ],
  [
    "基本法（反向百分位）",
    "Basic (reverse percentile)",
    "Méthode de base (percentiles inversés)",
    "Базовый метод (обратные процентили)",
    "Método básico (percentiles inversos)",
    "基本法（逆パーセンタイル）",
    "Método básico (percentis inversos)",
    "Basismethode (umgekehrte Perzentile)",
    "Основна метода (обрнути перцентили)",
    "기본법(역백분위)"
  ],
  [
    "研究间方差估计比较",
    "Compare between-study variance estimators",
    "Comparer les estimateurs d’hétérogénéité",
    "Сравнить методы оценки межисследовательской дисперсии",
    "Comparar estimadores de heterogeneidad",
    "研究間分散の推定量を比較",
    "Comparar estimadores de heterogeneidade",
    "Schätzer der Zwischenstudienvarianz vergleichen",
    "Упореди методе за процену варијансе између студија",
    "연구 간 분산 추정량 비교"
  ],
  [
    "τ² 区间",
    "τ² intervals",
    "Intervalles de τ²",
    "Интервалы τ²",
    "Intervalos de τ²",
    "τ²の区間",
    "Intervalos de τ²",
    "τ²-Intervalle",
    "Интервали τ²",
    "τ² 구간"
  ],
  [
    "本次实际采用的方法",
    "Methods used in this run",
    "Méthodes de cette exécution",
    "Методы текущего запуска",
    "Métodos de esta ejecución",
    "今回使用した手法",
    "Métodos desta execução",
    "Methoden dieses Laufs",
    "Методе овог покретања",
    "이번 실행에 사용된 방법"
  ],
  [
    "复制方法说明",
    "Copy method notes",
    "Copier les notes méthodologiques",
    "Скопировать описание методов",
    "Copiar notas metodológicas",
    "手法の説明をコピー",
    "Copiar notas metodológicas",
    "Methodenhinweise kopieren",
    "Копирај методолошке напомене",
    "방법 설명 복사"
  ],
  [
    "尚未运行；以下为候选方法说明。",
    "Not run yet; the following describes the available methods.",
    "Non exécuté ; les méthodes disponibles sont décrites ci-dessous.",
    "Ещё не выполнено; ниже описаны доступные методы.",
    "Aún no ejecutado; se describen los métodos disponibles.",
    "未実行です。以下は利用可能な手法の説明です。",
    "Ainda não executado; abaixo estão os métodos disponíveis.",
    "Noch nicht ausgeführt; unten werden verfügbare Methoden beschrieben.",
    "Још није покренуто; испод су описане доступне методе.",
    "아직 실행하지 않았습니다. 아래는 사용 가능한 방법 설명입니다."
  ],
  [
    "所选研究与来源",
    "Selected studies & sources",
    "Études et sources sélectionnées",
    "Выбранные исследования и источники",
    "Estudios y fuentes seleccionados",
    "選択した研究と出典",
    "Estudos e fontes selecionados",
    "Ausgewählte Studien und Quellen",
    "Изабране студије и извори",
    "선택한 연구 및 출처"
  ],
  [
    "查看原始计算记录",
    "View original computation record",
    "Voir le calcul d’origine",
    "Просмотреть исходную запись расчёта",
    "Ver registro de cálculo original",
    "元の計算記録を表示",
    "Ver registro de cálculo original",
    "Originales Berechnungsprotokoll anzeigen",
    "Прикажи изворни запис прорачуна",
    "원본 계산 기록 보기"
  ],
  [
    "原始字段保留用于核查，不作为界面文案翻译。",
    "Original fields are preserved for auditing, not translated as interface text.",
    "Les champs d’origine sont conservés pour l’audit et ne sont pas traduits.",
    "Исходные поля сохранены для аудита и не переводятся как текст интерфейса.",
    "Los campos originales se conservan para auditoría y no se traducen.",
    "監査のため元のフィールドを保持し、画面文言として翻訳しません。",
    "Os campos originais são preservados para auditoria, sem tradução.",
    "Originalfelder bleiben für Prüfungen erhalten und werden nicht als Oberflächentext übersetzt.",
    "Изворна поља се чувају за ревизију и не преводе као текст интерфејса.",
    "원본 필드는 감사를 위해 보존하며 화면 문구로 번역하지 않습니다."
  ],
  [
    "参考方法说明",
    "Reference methods",
    "Méthodes de référence",
    "Справочные методы",
    "Métodos de referencia",
    "参照手法",
    "Métodos de referência",
    "Referenzmethoden",
    "Референтне методе",
    "참고 방법"
  ],
  [
    "模型配置",
    "Model configuration",
    "Configuration du modèle",
    "Настройка модели",
    "Configuración del modelo",
    "モデル設定",
    "Configuração do modelo",
    "Modellkonfiguration",
    "Подешавање модела",
    "모형 설정"
  ],
  [
    "取消",
    "Cancel",
    "Annuler",
    "Отмена",
    "Cancelar",
    "キャンセル",
    "Cancelar",
    "Abbrechen",
    "Откажи",
    "취소"
  ],
  [
    "修改仅在点击“应用设置”后生效。",
    "Changes take effect only after you choose Apply settings.",
    "Les modifications prennent effet après « Appliquer les paramètres ».",
    "Изменения вступают в силу после нажатия «Применить настройки».",
    "Los cambios se aplican al elegir «Aplicar configuración».",
    "「設定を適用」を選択してから変更が有効になります。",
    "As alterações só entram em vigor ao aplicar as configurações.",
    "Änderungen gelten erst nach „Einstellungen übernehmen“.",
    "Измене ступају на снагу тек након примене подешавања.",
    "「설정 적용」을 선택해야 변경 사항이 적용됩니다."
  ],
  [
    "固定效应 · 共同效应",
    "Fixed effect · common effect",
    "Effet fixe · effet commun",
    "Фиксированный эффект · общий эффект",
    "Efecto fijo · efecto común",
    "固定効果・共通効果",
    "Efeito fixo · efeito comum",
    "Fester Effekt · gemeinsamer Effekt",
    "Фиксни ефекат · заједнички ефекат",
    "고정 효과 · 공통 효과"
  ],
  [
    "随机效应 · DL",
    "Random effects · DL",
    "Effets aléatoires · DL",
    "Случайные эффекты · DL",
    "Efectos aleatorios · DL",
    "変量効果・DL",
    "Efeitos aleatórios · DL",
    "Zufällige Effekte · DL",
    "Случајни ефекти · DL",
    "무작위 효과 · DL"
  ],
  [
    "随机效应 · PM",
    "Random effects · PM",
    "Effets aléatoires · PM",
    "Случайные эффекты · PM",
    "Efectos aleatorios · PM",
    "変量効果・PM",
    "Efeitos aleatórios · PM",
    "Zufällige Effekte · PM",
    "Случајни ефекти · PM",
    "무작위 효과 · PM"
  ],
  [
    "随机效应 · REML",
    "Random effects · REML",
    "Effets aléatoires · REML",
    "Случайные эффекты · REML",
    "Efectos aleatorios · REML",
    "変量効果・REML",
    "Efeitos aleatórios · REML",
    "Zufällige Effekte · REML",
    "Случајни ефекти · REML",
    "무작위 효과 · REML"
  ],
  [
    "正态 / Wald · 95%",
    "Normal / Wald · 95%",
    "Normale / Wald · 95 %",
    "Нормальный / Вальд · 95%",
    "Normal / Wald · 95 %",
    "正規／Wald・95%",
    "Normal / Wald · 95%",
    "Normal / Wald · 95 %",
    "Нормални / Валдов · 95%",
    "정규 / Wald · 95%"
  ],
  [
    "修正 HKSJ · 95%（随机效应）",
    "Modified HKSJ · 95% (random effects)",
    "HKSJ modifié · 95 % (effets aléatoires)",
    "Модифицированный HKSJ · 95% (случайные эффекты)",
    "HKSJ modificado · 95 % (efectos aleatorios)",
    "修正HKSJ・95%（変量効果）",
    "HKSJ modificado · 95% (efeitos aleatórios)",
    "Modifiziertes HKSJ · 95 % (zufällige Effekte)",
    "Измењени HKSJ · 95% (случајни ефекти)",
    "수정 HKSJ · 95%(무작위 효과)"
  ],
  [
    "取消修改",
    "Discard changes",
    "Abandonner les modifications",
    "Отменить изменения",
    "Descartar cambios",
    "変更を破棄",
    "Descartar alterações",
    "Änderungen verwerfen",
    "Одбаци измене",
    "변경 사항 취소"
  ],
  [
    "应用设置",
    "Apply settings",
    "Appliquer les paramètres",
    "Применить настройки",
    "Aplicar configuración",
    "設定を適用",
    "Aplicar configurações",
    "Einstellungen übernehmen",
    "Примени подешавања",
    "설정 적용"
  ],
  [
    "尚有未应用的模型修改",
    "Unapplied model changes",
    "Modifications du modèle non appliquées",
    "Неприменённые изменения модели",
    "Cambios del modelo sin aplicar",
    "モデルの未適用の変更",
    "Alterações do modelo não aplicadas",
    "Nicht übernommene Modelländerungen",
    "Непримењене измене модела",
    "적용되지 않은 모형 변경"
  ],
  [
    "切换任务将放弃这些修改。已经完成的运行不会因此被重算。",
    "Switching tasks discards these changes. Completed runs will not be recomputed.",
    "Changer de tâche abandonne ces modifications, sans recalculer les analyses terminées.",
    "При смене задачи изменения будут потеряны. Завершённые расчёты не повторяются.",
    "Cambiar de tarea descarta estos cambios. Los análisis completados no se recalculan.",
    "タスクを切り替えると変更が破棄されます。完了済みの分析は再計算されません。",
    "Mudar de tarefa descarta estas alterações. As análises concluídas não serão recalculadas.",
    "Beim Aufgabenwechsel werden diese Änderungen verworfen. Abgeschlossene Läufe werden nicht neu berechnet.",
    "Промена задатка одбацује ове измене. Завршене анализе се не израчунавају поново.",
    "작업을 바꾸면 변경 사항이 취소됩니다. 완료된 분석은 다시 계산하지 않습니다."
  ],
  [
    "保留修改并继续编辑",
    "Keep changes and continue editing",
    "Conserver les modifications et continuer",
    "Сохранить изменения и продолжить редактирование",
    "Conservar cambios y seguir editando",
    "変更を保持して編集を続行",
    "Manter alterações e continuar editando",
    "Änderungen behalten und weiter bearbeiten",
    "Задржи измене и настави уређивање",
    "변경 사항 유지하고 편집 계속"
  ],
  [
    "放弃修改并切换",
    "Discard changes and switch",
    "Abandonner les modifications et changer",
    "Отменить изменения и переключиться",
    "Descartar cambios y cambiar",
    "変更を破棄して切り替え",
    "Descartar alterações e mudar",
    "Änderungen verwerfen und wechseln",
    "Одбаци измене и промени задатак",
    "변경 사항 취소하고 전환"
  ],
  [
    "记录研究发现",
    "Record findings",
    "Enregistrer les constats",
    "Записать результаты",
    "Registrar hallazgos",
    "知見を記録",
    "Registrar achados",
    "Befunde erfassen",
    "Забележи налазе",
    "발견 내용 기록"
  ],
  [
    "定性研究",
    "Qualitative study",
    "Étude qualitative",
    "Качественное исследование",
    "Estudio cualitativo",
    "質的研究",
    "Estudo qualitativo",
    "Qualitative Studie",
    "Квалитативна студија",
    "질적 연구"
  ],
  [
    "研究发现的可信度",
    "Finding credibility",
    "Crédibilité du constat",
    "Достоверность результата",
    "Credibilidad del hallazgo",
    "知見の信頼性",
    "Credibilidade do achado",
    "Glaubwürdigkeit des Befunds",
    "Веродостојност налаза",
    "발견 내용의 신뢰성"
  ],
  [
    "选择可信度",
    "Choose credibility",
    "Choisir la crédibilité",
    "Выберите достоверность",
    "Seleccionar credibilidad",
    "信頼性を選択",
    "Selecionar credibilidade",
    "Glaubwürdigkeit auswählen",
    "Изабери веродостојност",
    "신뢰성 선택"
  ],
  [
    "明确可信",
    "unequivocal",
    "sans équivoque",
    "однозначный",
    "inequívoco",
    "明確な裏付けあり",
    "inequívoco",
    "eindeutig",
    "недвосмислен",
    "명확한 근거 있음"
  ],
  [
    "可信",
    "credible",
    "crédible",
    "достоверный",
    "creíble",
    "信頼できる",
    "credível",
    "glaubwürdig",
    "веродостојан",
    "신뢰할 수 있음"
  ],
  [
    "缺乏支持",
    "unsupported",
    "non étayé",
    "неподтверждённый",
    "sin respaldo",
    "裏付けなし",
    "sem suporte",
    "nicht belegt",
    "непоткрепљен",
    "근거 없음"
  ],
  [
    "研究发现",
    "Study finding",
    "Constat de l’étude",
    "Результат исследования",
    "Hallazgo del estudio",
    "研究の知見",
    "Achado do estudo",
    "Studienbefund",
    "Налаз студије",
    "연구의 발견 내용"
  ],
  [
    "来源原文引述",
    "Source quotation",
    "Citation extraite de la source",
    "Цитата из источника",
    "Cita de la fuente",
    "出典からの引用",
    "Citação da fonte",
    "Quellenzitat",
    "Цитат из извора",
    "출처 인용문"
  ],
  [
    "页码或段落",
    "Page or paragraph",
    "Page ou paragraphe",
    "Страница или абзац",
    "Página o párrafo",
    "ページまたは段落",
    "Página ou parágrafo",
    "Seite oder Absatz",
    "Страница или пасус",
    "페이지 또는 문단"
  ],
  [
    "保存研究发现",
    "Save finding",
    "Enregistrer le constat",
    "Сохранить результат",
    "Guardar hallazgo",
    "知見を保存",
    "Salvar achado",
    "Befund speichern",
    "Сачувај налаз",
    "발견 내용 저장"
  ],
  [
    "归类支持性发现",
    "Group supported findings",
    "Regrouper les constats étayés",
    "Сгруппировать подтверждённые результаты",
    "Agrupar hallazgos respaldados",
    "裏付けのある知見を分類",
    "Agrupar achados sustentados",
    "Belegte Befunde gruppieren",
    "Групиши поткрепљене налазе",
    "근거 있는 발견 내용 분류"
  ],
  [
    "类别名称",
    "Category label",
    "Libellé de catégorie",
    "Название категории",
    "Nombre de categoría",
    "カテゴリ名",
    "Nome da categoria",
    "Kategoriename",
    "Назив категорије",
    "범주 이름"
  ],
  [
    "纳入该类别的研究发现",
    "Findings for category",
    "Constats de la catégorie",
    "Результаты для категории",
    "Hallazgos de la categoría",
    "カテゴリに含める知見",
    "Achados da categoria",
    "Befunde für die Kategorie",
    "Налази за категорију",
    "범주에 포함할 발견 내용"
  ],
  [
    "归类研究发现",
    "Group findings",
    "Regrouper les constats",
    "Сгруппировать результаты",
    "Agrupar hallazgos",
    "知見を分類",
    "Agrupar achados",
    "Befunde gruppieren",
    "Групиши налазе",
    "발견 내용 분류"
  ],
  [
    "形成综合发现",
    "Synthesize findings",
    "Synthétiser les constats",
    "Синтезировать результаты",
    "Sintetizar hallazgos",
    "知見を統合",
    "Sintetizar achados",
    "Befunde synthetisieren",
    "Синтетиши налазе",
    "발견 내용 통합"
  ],
  [
    "综合研究发现",
    "Synthesized finding",
    "Constat synthétisé",
    "Синтезированный результат",
    "Hallazgo sintetizado",
    "統合された知見",
    "Achado sintetizado",
    "Synthetisierter Befund",
    "Синтетисани налаз",
    "통합된 발견 내용"
  ],
  [
    "纳入综合的类别",
    "Categories for synthesis",
    "Catégories à synthétiser",
    "Категории для синтеза",
    "Categorías para síntesis",
    "統合するカテゴリ",
    "Categorias para síntese",
    "Kategorien für die Synthese",
    "Категорије за синтезу",
    "통합할 범주"
  ],
  [
    "综合类别",
    "Synthesize categories",
    "Synthétiser les catégories",
    "Синтезировать категории",
    "Sintetizar categorías",
    "カテゴリを統合",
    "Sintetizar categorias",
    "Kategorien synthetisieren",
    "Синтетиши категорије",
    "범주 통합"
  ],
  [
    "选择多项时可使用 Ctrl 或 Command 键。",
    "Use Ctrl or Command to select multiple items.",
    "Utilisez Ctrl ou Commande pour sélectionner plusieurs éléments.",
    "Для выбора нескольких элементов используйте Ctrl или Command.",
    "Use Ctrl o Comando para seleccionar varios elementos.",
    "複数選択にはCtrlまたはCommandキーを使います。",
    "Use Ctrl ou Command para selecionar vários itens.",
    "Mehrere Einträge mit Strg oder Befehl auswählen.",
    "Користите Ctrl или Command за избор више ставки.",
    "여러 항목을 선택하려면 Ctrl 또는 Command를 사용하세요."
  ],
  [
    "记录",
    "Records",
    "Enregistrements",
    "Записи",
    "Registros",
    "記録",
    "Registros",
    "Datensätze",
    "Записи",
    "기록"
  ],
  [
    "尚无记录。",
    "No records yet.",
    "Aucun enregistrement.",
    "Записей пока нет.",
    "Aún no hay registros.",
    "記録はまだありません。",
    "Ainda não há registros.",
    "Noch keine Datensätze.",
    "Још нема записа.",
    "아직 기록이 없습니다."
  ],
  [
    "原始数据（保留字段名）",
    "Raw data (original field names)",
    "Données brutes (noms de champs d’origine)",
    "Исходные данные (оригинальные имена полей)",
    "Datos originales (nombres de campos originales)",
    "生データ（元のフィールド名）",
    "Dados brutos (nomes de campos originais)",
    "Rohdaten (originale Feldnamen)",
    "Сирови подаци (изворни називи поља)",
    "원시 데이터(원래 필드 이름)"
  ],
  [
    "保存评价",
    "Save assessment",
    "Enregistrer l’évaluation",
    "Сохранить оценку",
    "Guardar evaluación",
    "評価を保存",
    "Salvar avaliação",
    "Bewertung speichern",
    "Сачувај процену",
    "평가 저장"
  ],
  [
    "选择评价框架",
    "Choose framework",
    "Choisir le cadre",
    "Выберите систему оценки",
    "Seleccionar marco",
    "評価枠組みを選択",
    "Selecionar estrutura",
    "Bewertungsrahmen auswählen",
    "Изабери оквир",
    "평가 체계 선택"
  ],
  [
    "选择总体判断",
    "Choose overall judgment",
    "Choisir l’appréciation globale",
    "Выберите общую оценку",
    "Seleccionar valoración global",
    "総合判定を選択",
    "Selecionar avaliação global",
    "Gesamturteil auswählen",
    "Изабери укупну процену",
    "전체 판단 선택"
  ],
  [
    "保存四格表",
    "Save 2×2 table",
    "Enregistrer le tableau 2×2",
    "Сохранить таблицу 2×2",
    "Guardar tabla 2×2",
    "2×2表を保存",
    "Salvar tabela 2×2",
    "2×2-Tabelle speichern",
    "Сачувај табелу 2×2",
    "2×2 표 저장"
  ],
  [
    "双变量合并",
    "Bivariate synthesis",
    "Synthèse bivariée",
    "Двумерный синтез",
    "Síntesis bivariada",
    "二変量統合分析",
    "Síntese bivariada",
    "Bivariate Synthese",
    "Биваријантна синтеза",
    "이변량 통합 분석"
  ],
  [
    "导出诊断图 SVG",
    "Export diagnostic plot SVG",
    "Exporter le graphique diagnostique en SVG",
    "Экспортировать диагностический график SVG",
    "Exportar gráfico diagnóstico en SVG",
    "診断プロットをSVG出力",
    "Exportar gráfico diagnóstico em SVG",
    "Diagnostikdiagramm als SVG exportieren",
    "Извези дијагностички графикон у SVG",
    "진단 그림 SVG 내보내기"
  ],
  [
    "选择模型",
    "Choose model",
    "Choisir le modèle",
    "Выберите модель",
    "Seleccionar modelo",
    "モデルを選択",
    "Selecionar modelo",
    "Modell auswählen",
    "Изабери модел",
    "모형 선택"
  ],
  [
    "固定效应逆方差",
    "Fixed-effect inverse variance",
    "Effet fixe avec pondération par l’inverse de la variance",
    "Модель с фиксированным эффектом и обратными дисперсионными весами",
    "Efecto fijo con ponderación por varianza inversa",
    "固定効果の逆分散法",
    "Efeito fixo com ponderação pelo inverso da variância",
    "Inverse-Varianz-Modell mit festem Effekt",
    "Модел фиксног ефекта са пондерима обрнуте варијансе",
    "고정 효과 역분산법"
  ],
  [
    "随机效应（DerSimonian–Laird）",
    "Random effects (DerSimonian–Laird)",
    "Effets aléatoires (DerSimonian–Laird)",
    "Случайные эффекты (DerSimonian–Laird)",
    "Efectos aleatorios (DerSimonian–Laird)",
    "変量効果（DerSimonian–Laird）",
    "Efeitos aleatórios (DerSimonian–Laird)",
    "Zufällige Effekte (DerSimonian–Laird)",
    "Случајни ефекти (DerSimonian–Laird)",
    "무작위 효과(DerSimonian–Laird)"
  ],
  [
    "零单元格处理规则",
    "Zero-cell policy",
    "Règle pour les cellules nulles",
    "Правило для нулевых ячеек",
    "Regla para celdas nulas",
    "ゼロセルの処理",
    "Regra para células nulas",
    "Regel für Nullzellen",
    "Правило за нулте ћелије",
    "0 셀 처리 규칙"
  ],
  [
    "选择零单元格处理",
    "Choose zero-cell policy",
    "Choisir la règle pour les cellules nulles",
    "Выберите правило для нулевых ячеек",
    "Seleccionar regla para celdas nulas",
    "ゼロセルの処理を選択",
    "Selecionar regra para células nulas",
    "Nullzellenregel auswählen",
    "Изабери правило за нулте ћелије",
    "0 셀 처리 규칙 선택"
  ],
  [
    "排除含零单元格的表",
    "Exclude tables containing a zero",
    "Exclure les tableaux contenant un zéro",
    "Исключить таблицы с нулём",
    "Excluir tablas con algún cero",
    "ゼロを含む表を除外",
    "Excluir tabelas com zero",
    "Tabellen mit einer Null ausschließen",
    "Искључи табеле које садрже нулу",
    "0이 포함된 표 제외"
  ],
  [
    "仅对含零单元格的表，四格均加 0.5",
    "Add 0.5 to all four cells of zero-containing tables only",
    "Ajouter 0,5 aux quatre cellules uniquement des tableaux contenant un zéro",
    "Добавить 0,5 ко всем четырём ячейкам только таблиц с нулём",
    "Añadir 0,5 a las cuatro celdas solo en tablas con algún cero",
    "ゼロを含む表のみ、全4セルに0.5を加える",
    "Somar 0,5 às quatro células apenas das tabelas com zero",
    "Nur bei Tabellen mit Nullzellen 0,5 zu allen vier Zellen addieren",
    "Додај 0,5 у све четири ћелије само табела са нулом",
    "0이 있는 표에서만 네 셀 모두에 0.5 추가"
  ],
  [
    "比较研究似然比",
    "Compare study likelihood ratios",
    "Comparer les rapports de vraisemblance des études",
    "Сравнить отношения правдоподобия исследований",
    "Comparar razones de verosimilitud de los estudios",
    "研究の尤度比を比較",
    "Comparar razões de verossimilhança dos estudos",
    "Likelihood-Quotienten der Studien vergleichen",
    "Упореди количнике веродостојности студија",
    "연구별 우도비 비교"
  ],
  [
    "导出探索性似然比审计（JSON）",
    "Export exploratory LR audit (JSON)",
    "Exporter l’audit exploratoire des rapports de vraisemblance (JSON)",
    "Экспортировать аудит разведочного анализа LR (JSON)",
    "Exportar auditoría exploratoria de RV (JSON)",
    "探索的尤度比分析の監査記録をJSON出力",
    "Exportar auditoria exploratória das razões de verossimilhança (JSON)",
    "Exploratives LR-Prüfprotokoll exportieren (JSON)",
    "Извези ревизију експлораторне анализе LR (JSON)",
    "탐색적 우도비 감사 기록 내보내기(JSON)"
  ],
  [
    "导出似然比 JSON",
    "Export likelihood-ratio JSON",
    "Exporter les rapports de vraisemblance en JSON",
    "Экспортировать отношения правдоподобия в JSON",
    "Exportar razones de verosimilitud en JSON",
    "尤度比をJSON出力",
    "Exportar razões de verossimilhança em JSON",
    "Likelihood-Quotienten als JSON exportieren",
    "Извези количнике веродостојности у JSON",
    "우도비 JSON 내보내기"
  ],
  [
    "导出 HSROC JSON",
    "Export HSROC JSON",
    "Exporter HSROC en JSON",
    "Экспортировать HSROC в JSON",
    "Exportar HSROC en JSON",
    "HSROCをJSON出力",
    "Exportar HSROC em JSON",
    "HSROC als JSON exportieren",
    "Извези HSROC у JSON",
    "HSROC JSON 내보내기"
  ],
  [
    "可选：以研究编号为键的阈值选择 JSON",
    "Optional threshold choice JSON keyed by study ID",
    "Choix facultatif des seuils en JSON, indexé par identifiant d’étude",
    "Необязательный выбор порогов в JSON по идентификатору исследования",
    "Selección opcional de umbrales en JSON por ID de estudio",
    "研究IDをキーとする任意の閾値選択JSON",
    "Escolha opcional de limiares em JSON por ID do estudo",
    "Optionale Schwellenwertauswahl als JSON nach Studien-ID",
    "Опциони избор прагова у JSON по ID-у студије",
    "연구 ID별 선택적 역치 JSON"
  ],
  [
    "运行探索性阈值元回归",
    "Run exploratory threshold meta-regression",
    "Lancer la méta-régression exploratoire des seuils",
    "Запустить разведочную метарегрессию порогов",
    "Ejecutar metarregresión exploratoria de umbrales",
    "探索的閾値メタ回帰を実行",
    "Executar metarregressão exploratória de limiares",
    "Explorative Schwellenwert-Metaregression ausführen",
    "Покрени експлораторну метарегресију прагова",
    "탐색적 역치 메타회귀 실행"
  ],
  [
    "正在保存…",
    "Saving…",
    "Enregistrement…",
    "Сохранение…",
    "Guardando…",
    "保存中…",
    "Salvando…",
    "Wird gespeichert…",
    "Чување…",
    "저장 중…"
  ],
  [
    "表格可横向滚动",
    "Scrollable table",
    "Tableau à défilement horizontal",
    "Таблица с горизонтальной прокруткой",
    "Tabla desplazable",
    "横スクロール可能な表",
    "Tabela com rolagem",
    "Horizontal scrollbare Tabelle",
    "Табела са померањем",
    "가로 스크롤 가능한 표"
  ],
  [
    "转换方式",
    "Conversion",
    "Conversion",
    "Преобразование",
    "Conversión",
    "変換",
    "Conversão",
    "Umrechnung",
    "Претварање",
    "변환"
  ],
  [
    "选择转换方式",
    "Choose Conversion",
    "Choisir une conversion",
    "Выберите преобразование",
    "Seleccionar conversión",
    "変換を選択",
    "Selecionar conversão",
    "Umrechnung auswählen",
    "Изабери претварање",
    "변환 선택"
  ],
  [
    "两组近似风险比",
    "Two-arm approximate hazard ratio",
    "Rapport des risques instantanés approximatif à deux groupes",
    "Приближённое отношение рисков для двух групп",
    "Razón de riesgos instantáneos aproximada de dos grupos",
    "2群の近似ハザード比",
    "Razão de riscos instantâneos aproximada de dois grupos",
    "Approximative Hazard Ratio zweier Gruppen",
    "Приближан однос хазарда за две групе",
    "두 집단의 근사 위험비(HR)"
  ],
  [
    "单组近似事件发生率",
    "Single-arm approximate event rate",
    "Taux d’événements approximatif à un groupe",
    "Приближённая частота событий в одной группе",
    "Tasa de eventos aproximada de un grupo",
    "単群の近似イベント発生率",
    "Taxa de eventos aproximada de um grupo",
    "Approximative Ereignisrate einer Gruppe",
    "Приближна стопа догађаја једне групе",
    "단일 집단의 근사 사건 발생률"
  ],
  [
    "模型假设",
    "Model assumption",
    "Hypothèse du modèle",
    "Предположение модели",
    "Supuesto del modelo",
    "モデルの仮定",
    "Pressuposto do modelo",
    "Modellannahme",
    "Претпоставка модела",
    "모형 가정"
  ],
  [
    "选择模型假设",
    "Choose Model assumption",
    "Choisir l’hypothèse du modèle",
    "Выберите предположение модели",
    "Seleccionar supuesto del modelo",
    "モデルの仮定を選択",
    "Selecionar pressuposto do modelo",
    "Modellannahme auswählen",
    "Изабери претпоставку модела",
    "모형 가정 선택"
  ],
  [
    "我接受生存时间服从指数分布（即瞬时风险率恒定）及不确定性近似估计的假设",
    "I accept a constant hazard (exponential survival) model and approximate uncertainty",
    "J’accepte l’hypothèse d’un risque instantané constant (modèle exponentiel) et d’une incertitude approximative.",
    "Я принимаю постоянную интенсивность риска в экспоненциальной модели выживаемости и приближённую оценку неопределённости.",
    "Acepto el supuesto de un riesgo instantáneo constante (modelo exponencial) y de incertidumbre aproximada.",
    "生存時間が指数分布に従う（瞬間ハザード率が一定）との仮定、および不確実性を近似評価することを受け入れる",
    "Aceito o pressuposto de uma taxa de risco instantâneo constante (modelo exponencial) e de incerteza aproximada.",
    "Ich akzeptiere eine konstante Hazardrate im Exponentialmodell der Überlebenszeit und eine näherungsweise Unsicherheitsberechnung.",
    "Прихватам константну стопу хазарда у експоненцијалном моделу преживљавања и приближну процену неизвесности.",
    "생존시간이 지수분포를 따른다(순간 위험률이 일정하다)는 가정과 불확실성을 근사적으로 평가하는 방식을 수용합니다."
  ],
  [
    "中位数定义",
    "Median definition",
    "Définition de la médiane",
    "Определение медианы",
    "Definición de la mediana",
    "中央値の定義",
    "Definição da mediana",
    "Mediandefinition",
    "Дефиниција медијане",
    "중앙값 정의"
  ],
  [
    "选择中位数定义",
    "Choose Median definition",
    "Choisir la définition de la médiane",
    "Выберите определение медианы",
    "Seleccionar definición de la mediana",
    "中央値の定義を選択",
    "Selecionar definição da mediana",
    "Mediandefinition auswählen",
    "Изабери дефиницију медијане",
    "중앙값 정의 선택"
  ],
  [
    "已达到所定义事件的 Kaplan–Meier 中位时间",
    "Reached Kaplan–Meier median time to the defined event",
    "Médiane Kaplan–Meier atteinte du délai jusqu’à l’événement défini",
    "Достигнутая медиана времени до заданного события по Каплану–Мейеру",
    "Mediana alcanzada de Kaplan–Meier del tiempo hasta el evento definido",
    "定義したイベントまでの到達済みKaplan–Meier中央時間",
    "Mediana de Kaplan–Meier atingida do tempo até o evento definido",
    "Erreichter Kaplan–Meier-Median bis zum definierten Ereignis",
    "Достигнута Каплан–Мајерова медијана времена до дефинисаног догађаја",
    "정의된 사건까지 도달한 Kaplan–Meier 중앙 시간"
  ],
  [
    "时间单位（两组一致）",
    "Time unit (same for both arms)",
    "Unité de temps (identique pour les deux groupes)",
    "Единица времени (одинаковая для обеих групп)",
    "Unidad temporal (igual para ambos grupos)",
    "時間単位（両群で共通）",
    "Unidade de tempo (igual nos dois grupos)",
    "Zeiteinheit (für beide Gruppen gleich)",
    "Временска јединица (иста за обе групе)",
    "시간 단위(두 집단 동일)"
  ],
  [
    "查看单研究区间：比例、发生率、风险差或发生率比",
    "View study intervals: proportion, rate, risk difference or rate ratio",
    "Voir les intervalles d’étude : proportion, taux, différence de risques ou rapport de taux",
    "Интервалы исследования: доля, частота, разность рисков или отношение частот",
    "Ver intervalos del estudio: proporción, tasa, diferencia de riesgos o razón de tasas",
    "研究の区間を表示：割合、発生率、リスク差、発生率比",
    "Ver intervalos do estudo: proporção, taxa, diferença de riscos ou razão de taxas",
    "Studienintervalle: Anteil, Rate, Risikodifferenz oder Ratenverhältnis",
    "Интервали студије: пропорција, стопа, разлика ризика или однос стопа",
    "연구 구간 보기: 비율, 발생률, 위험차 또는 발생률비"
  ],
  [
    "运行 Peters 单组比例检验",
    "Run Peters single-proportion test",
    "Lancer le test de Peters pour une proportion unique",
    "Запустить тест Петерса для одной доли",
    "Ejecutar prueba de Peters para una proporción",
    "単群割合のPeters検定を実行",
    "Executar teste de Peters para uma proporção",
    "Peters-Test für einzelne Anteile ausführen",
    "Покрени Петерсов тест за једну пропорцију",
    "단일 비율 Peters 검정 실행"
  ],
  [
    "导出 Peters JSON",
    "Export Peters JSON",
    "Exporter Peters en JSON",
    "Экспортировать тест Петерса в JSON",
    "Exportar Peters en JSON",
    "Peters検定をJSON出力",
    "Exportar Peters em JSON",
    "Peters als JSON exportieren",
    "Извези Петерсов тест у JSON",
    "Peters 검정 JSON 내보내기"
  ],
  [
    "配对观测请使用",
    "For matched observations, use the",
    "Pour les observations appariées, utiliser",
    "Для парных наблюдений используйте",
    "Para observaciones emparejadas, utilice",
    "対応のある観測には次を使用：",
    "Para observações pareadas, use",
    "Für gepaarte Beobachtungen verwenden Sie",
    "За упарена посматрања користите",
    "대응 관측치에는 다음을 사용하세요:"
  ],
  [
    "配对表区间查看器",
    "paired table interval viewer",
    "visionneuse d’intervalles pour tableaux appariés",
    "просмотр интервалов парных таблиц",
    "visor de intervalos de tablas pareadas",
    "対応表の区間ビューア",
    "visualizador de intervalos de tabelas pareadas",
    "Intervallansicht für gepaarte Tabellen",
    "преглед интервала упарених табела",
    "대응표 구간 보기"
  ],
  [
    "估计量",
    "Quantity",
    "Mesure",
    "Величина",
    "Medida",
    "推定対象",
    "Medida",
    "Größe",
    "Величина",
    "추정 대상"
  ],
  [
    "选择估计量",
    "Choose quantity",
    "Choisir une mesure",
    "Выберите величину",
    "Seleccionar la medida",
    "推定対象を選択",
    "Selecionar a medida",
    "Größe auswählen",
    "Изабери величину",
    "추정 대상 선택"
  ],
  [
    "比例（事件数／人数）",
    "Proportion (events / people)",
    "Proportion (événements / personnes)",
    "Доля (события / участники)",
    "Proporción (eventos / personas)",
    "割合（イベント数／人数）",
    "Proporção (eventos / pessoas)",
    "Anteil (Ereignisse / Personen)",
    "Пропорција (догађаји / особе)",
    "비율(사건 수 / 사람 수)"
  ],
  [
    "发生率（事件数／人时）",
    "Rate (events / person-time)",
    "Taux (événements / personnes-temps)",
    "Частота (события / человеко-время)",
    "Tasa (eventos / persona-tiempo)",
    "発生率（イベント数／人時間）",
    "Taxa (eventos / pessoa-tempo)",
    "Rate (Ereignisse / Personenzeit)",
    "Стопа (догађаји / особа-време)",
    "발생률(사건 수 / 관찰 인시)"
  ],
  [
    "风险差（独立组）",
    "Risk difference (independent groups)",
    "Différence de risques (groupes indépendants)",
    "Разность рисков (независимые группы)",
    "Diferencia de riesgos (grupos independientes)",
    "リスク差（独立2群）",
    "Diferença de riscos (grupos independentes)",
    "Risikodifferenz (unabhängige Gruppen)",
    "Разлика ризика (независне групе)",
    "위험차(독립 집단)"
  ],
  [
    "发生率比（独立泊松计数）",
    "Rate ratio (independent Poisson counts)",
    "Rapport de taux (comptages de Poisson indépendants)",
    "Отношение частот (независимые пуассоновские счётчики)",
    "Razón de tasas (recuentos de Poisson independientes)",
    "発生率比（独立Poisson計数）",
    "Razão de taxas (contagens de Poisson independentes)",
    "Ratenverhältnis (unabhängige Poisson-Zählwerte)",
    "Однос стопа (независни Поасонови бројеви)",
    "발생률비(독립 포아송 계수)"
  ],
  [
    "请先选择统计量",
    "Choose quantity first",
    "Choisissez d’abord une mesure",
    "Сначала выберите величину",
    "Seleccione primero una medida",
    "最初に推定対象を選択",
    "Selecione primeiro uma medida",
    "Zuerst Größe auswählen",
    "Прво изаберите величину",
    "먼저 추정 대상 선택"
  ],
  [
    "区间水平（%）",
    "Interval level (%)",
    "Niveau de l’intervalle (%)",
    "Уровень интервала (%)",
    "Nivel del intervalo (%)",
    "区間水準（%）",
    "Nível do intervalo (%)",
    "Intervallniveau (%)",
    "Ниво интервала (%)",
    "구간 수준(%)"
  ],
  [
    "人数或人时",
    "People or person-time",
    "Personnes ou personnes-temps",
    "Участники или человеко-время",
    "Personas o persona-tiempo",
    "人数または人時間",
    "Pessoas ou pessoa-tempo",
    "Personen oder Personenzeit",
    "Особе или особа-време",
    "사람 수 또는 관찰 인시"
  ],
  [
    "研究报告及页码／表格（写入导出记录）",
    "Study report and page / table (for the exported record)",
    "Rapport et page / tableau (pour l’export)",
    "Отчёт и страница / таблица (для экспорта)",
    "Informe y página / tabla (para la exportación)",
    "研究報告とページ／表（出力記録用）",
    "Relatório e página / tabela (para exportação)",
    "Studienbericht und Seite / Tabelle (für den Export)",
    "Извештај и страница / табела (за извоз)",
    "연구 보고서와 페이지 / 표(내보내기 기록용)"
  ],
  [
    "计算单研究区间",
    "Calculate study interval",
    "Calculer l’intervalle de l’étude",
    "Вычислить интервал исследования",
    "Calcular intervalo del estudio",
    "研究の区間を計算",
    "Calcular intervalo do estudo",
    "Studienintervall berechnen",
    "Израчунај интервал студије",
    "연구 구간 계산"
  ],
  [
    "导出区间记录（JSON）",
    "Export interval record (JSON)",
    "Exporter l’intervalle (JSON)",
    "Экспортировать запись интервала (JSON)",
    "Exportar registro del intervalo (JSON)",
    "区間記録をJSON出力",
    "Exportar registro do intervalo (JSON)",
    "Intervallprotokoll exportieren (JSON)",
    "Извези запис интервала (JSON)",
    "구간 기록 내보내기(JSON)"
  ],
  [
    "研究来源、事件定义及表格或页码",
    "Study source, event definition and table/page",
    "Source, définition de l’événement et tableau/page",
    "Источник, определение события и таблица/страница",
    "Fuente, definición del evento y tabla/página",
    "研究の出典、イベント定義、表／ページ",
    "Fonte, definição do evento e tabela/página",
    "Studienquelle, Ereignisdefinition und Tabelle/Seite",
    "Извор, дефиниција догађаја и табела/страница",
    "연구 출처, 사건 정의 및 표/페이지"
  ],
  [
    "单组中位数",
    "single median",
    "médiane unique",
    "одна медиана",
    "mediana única",
    "単一中央値",
    "mediana única",
    "einzelner Median",
    "једна медијана",
    "단일 중앙값"
  ],
  [
    "选择研究特征",
    "Choose study characteristic",
    "Choisir la caractéristique de l’étude",
    "Выберите характеристику исследования",
    "Seleccionar característica del estudio",
    "研究特性を選択",
    "Selecionar característica do estudo",
    "Studienmerkmal auswählen",
    "Изабери карактеристику студије",
    "연구 특성 선택"
  ],
  [
    "随机效应（PM）",
    "Random effects (PM)",
    "Effets aléatoires (PM)",
    "Случайные эффекты (PM)",
    "Efectos aleatorios (PM)",
    "変量効果（PM）",
    "Efeitos aleatórios (PM)",
    "Zufällige Effekte (PM)",
    "Случајни ефекти (PM)",
    "무작위 효과(PM)"
  ],
  [
    "正态区间",
    "Normal interval",
    "Intervalle normal",
    "Нормальный интервал",
    "Intervalo normal",
    "正規区間",
    "Intervalo normal",
    "Normalintervall",
    "Нормални интервал",
    "정규 구간"
  ],
  [
    "修正 HKSJ",
    "Modified HKSJ",
    "HKSJ modifié",
    "Модифицированный HKSJ",
    "HKSJ modificado",
    "修正HKSJ",
    "HKSJ modificado",
    "Modifiziertes HKSJ",
    "Измењени HKSJ",
    "수정 HKSJ"
  ],
  [
    "比较完整样本与排除后样本",
    "Compare the full and post-exclusion samples",
    "Comparer avant et après exclusion",
    "Сравнить полную выборку с выборкой после исключения",
    "Comparar antes y después de la exclusión",
    "全研究と判定に基づく除外後を比較",
    "Comparar antes e após a exclusão",
    "Vollständige Stichprobe mit der Stichprobe nach Ausschlüssen vergleichen",
    "Упоредити потпуни узорак са узорком након искључења",
    "전체 연구와 판단별 제외 후 비교"
  ],
  [
    "当前范围按比较组、结局、时间点和效应指标筛选，不按单个研究筛选。",
    "The analysis is filtered by comparison, outcome, time point and effect measure, not by the entry study selector.",
    "L’analyse est filtrée par comparaison, résultat, temps et mesure d’effet, pas par l’étude choisie pour la saisie.",
    "Анализ фильтруется по сравнению, исходу, сроку и мере эффекта, а не по исследованию для ввода данных.",
    "El análisis se filtra por comparación, resultado, momento y medida del efecto, no por el estudio de entrada de datos.",
    "分析は比較、アウトカム、時点、効果指標で絞り込みます。入力用の研究選択では絞り込みません。",
    "A análise é filtrada por comparação, desfecho, momento e medida do efeito, não pelo estudo de entrada de dados.",
    "Gefiltert wird nach Vergleich, Ergebnis, Zeitpunkt und Effektmaß, nicht nach der Studie für die Dateneingabe.",
    "Анализа се филтрира по поређењу, исходу, времену и мери ефекта, а не по студији за унос.",
    "분석은 비교, 결과, 시점 및 효과 지표로 필터링하며 입력용 연구 선택으로 필터링하지 않습니다."
  ],
  [
    "敏感性分析应依据预先规定的比较，不根据显著性挑选结果。",
    "Use prespecified sensitivity comparisons; do not select a result based on significance.",
    "Utilisez des comparaisons de sensibilité prédéfinies ; ne choisissez pas un résultat selon sa significativité.",
    "Используйте заранее заданные проверки чувствительности; не выбирайте результат по значимости.",
    "Use comparaciones de sensibilidad predefinidas; no elija resultados por su significación.",
    "事前に定めた感度比較を用い、有意性で結果を選ばないでください。",
    "Use comparações de sensibilidade predefinidas; não selecione resultados pela significância.",
    "Verwenden Sie vorab festgelegte Sensitivitätsvergleiche; wählen Sie Ergebnisse nicht nach Signifikanz.",
    "Користите унапред одређена поређења осетљивости; не бирајте резултат по значајности.",
    "사전에 정한 민감도 비교를 사용하고 유의성에 따라 결과를 선택하지 마세요."
  ],
  [
    "逐研究删除并重新拟合，检查单项研究对结果的影响。",
    "Delete one study at a time and refit to examine its influence.",
    "Retirez une étude à la fois et réestimez le modèle pour examiner son influence.",
    "Удаляйте по одному исследованию и переоценивайте модель для оценки его влияния.",
    "Retire un estudio cada vez y vuelva a ajustar para examinar su influencia.",
    "研究を1件ずつ除外して再推定し、その影響を調べます。",
    "Remova um estudo de cada vez e reajuste para examinar sua influência.",
    "Entfernen Sie jeweils eine Studie und passen Sie das Modell erneut an, um ihren Einfluss zu prüfen.",
    "Уклањајте по једну студију и поново прилагодите модел ради испитивања утицаја.",
    "연구를 하나씩 제외하여 모형을 다시 적합하고 영향을 확인합니다."
  ],
  [
    "诊断值较大不是自动排除研究的理由。",
    "A large diagnostic value is not an automatic reason to exclude a study.",
    "Une valeur diagnostique élevée ne justifie pas automatiquement l’exclusion d’une étude.",
    "Большое диагностическое значение само по себе не служит основанием для исключения исследования.",
    "Un valor diagnóstico alto no justifica automáticamente excluir un estudio.",
    "診断値が大きいことだけでは研究を自動的に除外できません。",
    "Um valor diagnóstico alto não justifica automaticamente excluir um estudo.",
    "Ein hoher Diagnosewert ist kein automatischer Grund zum Ausschluss einer Studie.",
    "Висока дијагностичка вредност није аутоматски разлог за искључење студије.",
    "진단값이 크다는 이유만으로 연구를 자동 제외하지 않습니다."
  ],
  [
    "MD 使用各组自身的方差。将已报告区间换算为标准误，假定其在分析尺度上为对称正态区间。请核对参考书和适用条件；引用来源不代表数值与其他软件逐项一致。",
    "MD uses arm-specific variances. Converting a reported CI to SE assumes a symmetric normal interval on the analysis scale. Check the cited books and applicability; a citation does not establish numerical parity with other software.",
    "La différence de moyennes utilise les variances propres à chaque groupe. Convertir un intervalle publié en erreur-type suppose un intervalle normal symétrique sur l’échelle d’analyse. Vérifiez les ouvrages et l’applicabilité ; une référence ne garantit pas l’identité numérique avec un autre logiciel.",
    "Разность средних использует дисперсии отдельных групп. Преобразование указанного доверительного интервала в стандартную ошибку предполагает симметричный нормальный интервал в шкале анализа. Проверьте источники и применимость: ссылка не гарантирует численного совпадения с другим ПО.",
    "La diferencia de medias usa varianzas específicas de cada grupo. Convertir un intervalo publicado a error estándar supone un intervalo normal simétrico en la escala de análisis. Revise las fuentes y la aplicabilidad; una cita no garantiza equivalencia numérica con otro programa.",
    "平均差には各群の分散を用います。報告区間から標準誤差への変換は分析尺度上で対称な正規区間を仮定します。引用書と適用条件を確認してください。引用があっても他のソフトとの数値的一致は保証されません。",
    "A diferença de médias usa variâncias específicas dos grupos. Converter um intervalo publicado em erro padrão pressupõe um intervalo normal simétrico na escala de análise. Verifique as fontes e a aplicabilidade; uma citação não garante equivalência numérica com outro programa.",
    "Die Mittelwertdifferenz nutzt gruppenspezifische Varianzen. Die Umrechnung eines berichteten Konfidenzintervalls in einen Standardfehler setzt ein symmetrisches Normalintervall auf der Analyseskala voraus. Prüfen Sie Quellen und Anwendbarkeit; ein Zitat garantiert keine numerische Übereinstimmung mit anderer Software.",
    "Разлика средина користи варијансе по групама. Претварање пријављеног интервала поверења у стандардну грешку претпоставља симетрични нормални интервал на скали анализе. Проверите изворе и применљивост; цитат не гарантује бројчано поклапање са другим софтвером.",
    "평균차는 각 집단의 분산을 사용합니다. 보고된 구간을 표준오차로 변환하려면 분석 척도에서 대칭인 정규 구간을 가정합니다. 인용 자료와 적용 가능성을 확인하세요. 인용만으로 다른 소프트웨어와의 수치 일치가 보장되지는 않습니다."
  ],
  [
    "此操作同时运行两个模型。共同概率或发生率模型假定各研究共享同一参数，并使用精确区间；随机截距模型允许 logit 或对数尺度上的研究间差异，使用近似 Wald 区间。运行前请确定报告哪个模型。",
    "This runs both models. The common probability or rate model assumes one value across studies and uses an exact interval. The random-intercept model allows between-study variation on the logit or log scale and uses an approximate Wald interval. Decide which result to report before running.",
    "Les deux modèles sont exécutés. Le modèle à probabilité ou taux commun suppose une valeur identique entre études et utilise un intervalle exact. Le modèle à intercept aléatoire autorise une variation interétudes sur l’échelle logit ou logarithmique et utilise un intervalle de Wald approximatif. Décidez du résultat à rapporter avant l’exécution.",
    "Выполняются обе модели. Модель общей вероятности или частоты предполагает одно значение для всех исследований и использует точный интервал. Модель со случайным интерсептом допускает межисследовательские различия в логит- или лог-шкале и использует приближённый интервал Вальда. Заранее выберите результат для отчёта.",
    "Se ejecutan ambos modelos. El de probabilidad o tasa común supone un valor compartido por los estudios y usa un intervalo exacto. El de intercepto aleatorio admite variación entre estudios en escala logit o logarítmica y usa un intervalo de Wald aproximado. Decida antes cuál informará.",
    "両モデルを実行します。共通確率・共通発生率モデルは全研究で同じ値を仮定し、正確区間を用います。ランダム切片モデルはlogitまたは対数尺度上の研究間変動を許容し、近似Wald区間を用います。報告する結果は実行前に決めてください。",
    "Os dois modelos são executados. O modelo de probabilidade ou taxa comum pressupõe um valor entre estudos e usa intervalo exato. O de intercepto aleatório permite variação entre estudos na escala logit ou log e usa intervalo de Wald aproximado. Decida antes qual resultado relatar.",
    "Beide Modelle werden ausgeführt. Das Modell mit gemeinsamer Wahrscheinlichkeit oder Rate nimmt einen Wert für alle Studien an und nutzt ein exaktes Intervall. Das Modell mit zufälligem Interzept erlaubt Unterschiede auf Logit- oder Log-Skala und nutzt ein approximatives Wald-Intervall. Legen Sie das zu berichtende Ergebnis vorher fest.",
    "Покрећу се оба модела. Модел заједничке вероватноће или стопе претпоставља исту вредност у свим студијама и користи тачан интервал. Модел случајног одсечка дозвољава варирање између студија на логит или лог скали и користи приближан Валдов интервал. Унапред одредите који резултат пријављујете.",
    "두 모형을 모두 실행합니다. 공통 확률·발생률 모형은 연구 간 동일한 값을 가정하고 정확 구간을 사용합니다. 무작위 절편 모형은 logit 또는 로그 척도에서 연구 간 변동을 허용하고 근사 Wald 구간을 사용합니다. 보고할 결과는 실행 전에 정하세요."
  ],
  [
    "输入一项研究的计数。自然尺度区间用于描述该研究，并非合并治疗效应；不能反推标准误后用于逆方差合并。",
    "Enter counts from one study. These natural-scale intervals describe that study; they are not pooled treatment effects and must not be converted into an SE for inverse-variance pooling.",
    "Saisissez les effectifs d’une seule étude. Ces intervalles sur l’échelle naturelle décrivent cette étude, pas un effet combiné du traitement. Ne les convertissez pas en erreur-type pour une synthèse par variance inverse.",
    "Введите числа для одного исследования. Интервалы в естественной шкале описывают это исследование, а не объединённый эффект лечения. Не преобразуйте их в стандартную ошибку для объединения методом обратной дисперсии.",
    "Introduzca recuentos de un estudio. Estos intervalos en escala natural describen ese estudio, no efectos combinados del tratamiento. No los convierta en error estándar para combinar por varianza inversa.",
    "1研究の件数を入力します。元の尺度の区間はその研究を記述するもので、統合治療効果ではありません。逆分散統合の標準誤差に変換しないでください。",
    "Insira contagens de um estudo. Estes intervalos na escala natural descrevem esse estudo, não efeitos combinados do tratamento. Não os converta em erro padrão para combinação por variância inversa.",
    "Geben Sie Zählwerte einer Studie ein. Diese Intervalle auf der natürlichen Skala beschreiben die einzelne Studie, nicht einen gepoolten Behandlungseffekt. Wandeln Sie sie nicht in Standardfehler für eine inverse Varianzgewichtung um.",
    "Унесите бројеве из једне студије. Интервали на природној скали описују ту студију, а не обједињени ефекат лечења. Не претварајте их у стандардну грешку за синтезу инверзном варијансом.",
    "한 연구의 계수를 입력하세요. 원척도 구간은 해당 연구를 설명하며 통합 치료효과가 아닙니다. 역분산 통합을 위한 표준오차로 변환하지 마세요."
  ],
  [
    "方法来源随结果及导出记录提供。发表时请核对原始论文或书籍，并引用实际使用的方法和研究报告。此查看器不会向合并分析数据集保存效应量。",
    "Method sources appear with the result and export. Check the original paper or book, and cite the method actually used plus the study report when publishing. This viewer does not save an effect to the synthesis dataset.",
    "Les sources méthodologiques accompagnent le résultat et l’export. Consultez l’article ou l’ouvrage original et citez la méthode réellement utilisée ainsi que le rapport d’étude. Cette visionneuse n’ajoute pas d’effet aux données de synthèse.",
    "Методические источники показаны с результатом и экспортом. Проверьте оригинальную статью или книгу и цитируйте использованный метод и отчёт исследования. Просмотр не сохраняет эффект в набор данных синтеза.",
    "Las fuentes metodológicas aparecen en el resultado y la exportación. Revise el artículo o libro original y cite el método utilizado y el informe del estudio. Este visor no guarda un efecto en los datos de síntesis.",
    "手法の出典は結果と出力に表示されます。原著論文・書籍を確認し、実際の手法と研究報告を引用してください。このビューアは統合用データに効果量を保存しません。",
    "As fontes metodológicas acompanham o resultado e a exportação. Confira o artigo ou livro original e cite o método utilizado e o relatório do estudo. Este visualizador não salva um efeito nos dados de síntese.",
    "Methodenquellen stehen beim Ergebnis und im Export. Prüfen Sie Originalartikel oder -buch und zitieren Sie die tatsächlich verwendete Methode sowie den Studienbericht. Diese Ansicht speichert keinen Effekt im Synthesedatensatz.",
    "Методолошки извори су уз резултат и извоз. Проверите изворни рад или књигу и наведите коришћену методу и извештај студије. Овај преглед не чува ефекат у скупу података за синтезу.",
    "방법 출처는 결과와 내보내기에 표시됩니다. 원 논문 또는 책을 확인하고 실제 사용한 방법과 연구 보고서를 인용하세요. 이 보기는 통합 데이터에 효과크기를 저장하지 않습니다."
  ],
  [
    "领域及整体判断由评价者录入。本表单不执行官方信号问题算法。",
    "Domain and overall judgements are entered by the reviewer. This form does not implement the official signalling-question algorithm.",
    "L’évaluateur saisit les jugements par domaine et le jugement global. Ce formulaire n’implémente pas l’algorithme officiel des questions de signalement.",
    "Оценки по областям и общая оценка вводятся рецензентом. Форма не реализует официальный алгоритм сигнальных вопросов.",
    "El revisor introduce los juicios por dominio y globales. El formulario no implementa el algoritmo oficial de preguntas de señalización.",
    "領域別判定と総合判定はレビュー担当者が入力します。公式のシグナリング質問アルゴリズムは実装していません。",
    "O revisor insere os julgamentos por domínio e global. O formulário não implementa o algoritmo oficial de perguntas sinalizadoras.",
    "Domänen- und Gesamturteile werden von der prüfenden Person eingegeben. Das Formular implementiert nicht den offiziellen Algorithmus der Signalisierungsfragen.",
    "Процене по доменима и укупну процену уноси рецензент. Образац не примењује званични алгоритам сигналних питања.",
    "영역별 판단과 전체 판단은 검토자가 입력합니다. 이 양식은 공식 신호 질문 알고리즘을 구현하지 않습니다."
  ],
  [
    "阳性和阴性似然比由同一拟合的敏感度与特异度推导。近似 95% 区间使用拟合均值参数的协方差，包括其相关。研究间异质性不能替代估计协方差。",
    "LR+ and LR− are derived from the same fitted sensitivity and specificity. The approximate 95% intervals use the covariance of the fitted mean parameters, including their correlation. Between-study heterogeneity is not substituted for estimation covariance.",
    "LR+ et LR− sont calculés à partir de la même sensibilité et spécificité ajustées. Les intervalles approximatifs à 95 % utilisent la covariance des paramètres moyens estimés, corrélation comprise. L’hétérogénéité interétudes ne remplace pas cette covariance d’estimation.",
    "LR+ и LR− получены из одной и той же оценённой чувствительности и специфичности. Приближённые 95%-интервалы используют ковариацию средних параметров, включая корреляцию. Межисследовательская неоднородность не заменяет ковариацию оценивания.",
    "LR+ y LR− derivan de la misma sensibilidad y especificidad ajustadas. Los intervalos aproximados del 95 % usan la covarianza de los parámetros medios, incluida su correlación. La heterogeneidad entre estudios no sustituye la covarianza de estimación.",
    "LR+とLR−は同じ推定感度・特異度から算出します。近似95%区間には相関を含む平均パラメータの推定共分散を用います。研究間異質性を推定共分散の代わりには使いません。",
    "LR+ e LR− derivam da mesma sensibilidade e especificidade ajustadas. Os intervalos aproximados de 95% usam a covariância dos parâmetros médios, incluindo sua correlação. A heterogeneidade entre estudos não substitui a covariância de estimação.",
    "LR+ und LR− werden aus derselben geschätzten Sensitivität und Spezifität abgeleitet. Approximative 95%-Intervalle nutzen die Kovarianz der geschätzten Mittelwertparameter einschließlich ihrer Korrelation. Heterogenität ersetzt diese Schätzkovarianz nicht.",
    "LR+ и LR− потичу од исте оцењене осетљивости и специфичности. Приближни интервали од 95% користе коваријансу средњих параметара, укључујући корелацију. Хетерогеност између студија не замењује коваријансу оцењивања.",
    "LR+와 LR−는 동일하게 적합된 민감도와 특이도에서 도출합니다. 근사 95% 구간은 상관을 포함한 평균 모수의 추정 공분산을 사용하며 연구 간 이질성으로 대체하지 않습니다."
  ],
  [
    "仅供探索性比较：分别合并两种似然比会忽略相关，可能产生不相容的汇总。优先采用上方双变量汇总点推导的似然比。请明确指定当前检验、目标疾病、阈值及参照标准。",
    "Exploratory comparison only: separate LR pooling ignores their correlation and can produce incompatible summaries. Prefer LR derived at the bivariate summary point above. Uses the selected index test, target condition, threshold and reference standard; specify all four.",
    "Comparaison exploratoire uniquement : combiner séparément les LR ignore leur corrélation et peut produire des résumés incompatibles. Préférez les LR dérivés du point de synthèse bivarié ci-dessus. Précisez le test index, l’affection cible, le seuil et l’étalon de référence.",
    "Только разведочное сравнение: раздельное объединение LR игнорирует корреляцию и может дать несовместимые оценки. Предпочтительны LR из двумерной сводной точки выше. Укажите индексный тест, целевое состояние, порог и эталон.",
    "Solo comparación exploratoria: combinar LR por separado ignora su correlación y puede generar resúmenes incompatibles. Prefiera LR derivados del punto de síntesis bivariada anterior. Especifique prueba índice, condición objetivo, umbral y patrón de referencia.",
    "探索的比較のみです。LRの個別統合は相関を無視し、整合しない要約になる場合があります。上の二変量要約点から導くLRを優先してください。指標検査、対象疾患、閾値、参照基準をすべて指定します。",
    "Apenas comparação exploratória: combinar LR separadamente ignora a correlação e pode gerar resumos incompatíveis. Prefira LR derivados do ponto de síntese bivariada acima. Especifique teste índice, condição-alvo, limiar e padrão de referência.",
    "Nur explorativer Vergleich: Getrenntes LR-Pooling ignoriert deren Korrelation und kann unvereinbare Ergebnisse liefern. Bevorzugen Sie LR aus dem bivariaten Zusammenfassungspunkt oben. Geben Sie Indextest, Zielerkrankung, Schwelle und Referenzstandard an.",
    "Само експлораторно поређење: одвојено обједињавање LR занемарује корелацију и може дати неусклађене сажетке. Предност дајте LR из горње биваријантне тачке. Наведите индексни тест, циљно стање, праг и референтни стандард.",
    "탐색적 비교 전용입니다. LR을 따로 통합하면 상관을 무시하여 서로 양립하지 않는 요약이 나올 수 있습니다. 위 이변량 요약점에서 도출한 LR을 우선하세요. 지표 검사, 대상 질환, 역치, 참조 기준을 모두 지정하세요."
  ],
  [
    "这是拟合双变量模型的等价参数化：每项研究一个阈值，不含协变量。不重新拟合，也不额外估计不确定性。形状参数 β = 0 对应对称的 SROC。",
    "Equivalent parameterization of the fitted bivariate model: one threshold per study, without covariates. No refitting or additional uncertainty estimation. Shape β = 0 corresponds to a symmetric SROC.",
    "Paramétrisation équivalente du modèle bivarié ajusté : un seuil par étude, sans covariables. Pas de nouvel ajustement ni d’estimation supplémentaire d’incertitude. β = 0 correspond à une SROC symétrique.",
    "Эквивалентная параметризация оценённой двумерной модели: один порог на исследование, без ковариат. Без повторного оценивания и дополнительной оценки неопределённости. β = 0 соответствует симметричной SROC.",
    "Parametrización equivalente del modelo bivariado ajustado: un umbral por estudio, sin covariables. Sin reajuste ni estimación adicional de incertidumbre. β = 0 corresponde a una SROC simétrica.",
    "推定した二変量モデルの等価なパラメータ表現です。共変量なしで研究ごとに1閾値とします。再推定や追加の不確実性推定は行いません。形状β=0は対称なSROCに対応します。",
    "Parametrização equivalente do modelo bivariado ajustado: um limiar por estudo, sem covariáveis. Sem reajuste ou estimativa adicional de incerteza. β = 0 corresponde a uma SROC simétrica.",
    "Äquivalente Parametrisierung des angepassten bivariaten Modells: eine Schwelle je Studie, keine Kovariaten. Keine Neuanpassung oder zusätzliche Unsicherheitsschätzung. β = 0 entspricht einer symmetrischen SROC.",
    "Еквивалентна параметризација прилагођеног биваријантног модела: један праг по студији, без коваријата. Без поновног прилагођавања или додатне процене неизвесности. β = 0 одговара симетричној SROC.",
    "적합된 이변량 모형의 동등한 모수화입니다. 연구당 한 역치이며 공변량은 없습니다. 재적합이나 추가 불확실성 추정은 하지 않습니다. 형태 β=0은 대칭 SROC에 해당합니다."
  ],
  [
    "至少使用十项独立研究，每项一个可比数值阈值，且至少三个不同阈值。上方检验、目标疾病及参照标准定义分析范围。某研究有多个已存阈值时，可在下方映射中指定唯一阈值。阈值单位和方向须一致。",
    "Use at least ten independent studies with one comparable numeric threshold each and at least three distinct values. The index test, target condition and reference standard above define the analysis. If a study has multiple saved thresholds, choose exactly one in the optional mapping below. Threshold units and direction must match.",
    "Utilisez au moins dix études indépendantes, chacune avec un seuil numérique comparable, et au moins trois valeurs distinctes. Le test index, l’affection et l’étalon définissent l’analyse. Si plusieurs seuils sont enregistrés par étude, choisissez-en exactement un ci-dessous. Unités et direction des seuils doivent correspondre.",
    "Нужно не менее десяти независимых исследований с одним сопоставимым числовым порогом в каждом и минимум тремя различными значениями. Анализ задают тест, целевое состояние и эталон. При нескольких порогах выберите ровно один на исследование ниже. Единицы и направление должны совпадать.",
    "Use al menos diez estudios independientes, cada uno con un umbral numérico comparable, y al menos tres valores distintos. Prueba índice, condición y patrón definen el análisis. Si hay varios umbrales por estudio, elija exactamente uno debajo. Unidades y dirección deben coincidir.",
    "比較可能な数値閾値を1つずつ持つ独立研究が10件以上、異なる閾値が3種類以上必要です。指標検査、対象疾患、参照基準が分析を定義します。1研究に複数の保存閾値があれば下で必ず1つ選びます。単位と方向を一致させてください。",
    "Use pelo menos dez estudos independentes, cada um com um limiar numérico comparável, e ao menos três valores distintos. Teste índice, condição e padrão definem a análise. Se houver vários limiares por estudo, escolha exatamente um abaixo. Unidades e direção devem coincidir.",
    "Nutzen Sie mindestens zehn unabhängige Studien mit je einer vergleichbaren numerischen Schwelle und mindestens drei verschiedenen Werten. Indextest, Zielerkrankung und Referenzstandard definieren die Analyse. Bei mehreren gespeicherten Schwellen wählen Sie unten genau eine je Studie. Einheiten und Richtung müssen übereinstimmen.",
    "Користите најмање десет независних студија, по један упоредив нумерички праг и најмање три различите вредности. Тест, циљно стање и стандард дефинишу анализу. За више сачуваних прагова по студији изаберите тачно један испод. Јединице и смер морају бити усклађени.",
    "독립 연구가 최소 10개 필요하며 각 연구에 비교 가능한 수치 역치가 하나씩 있어야 하고 서로 다른 역치 값이 최소 3개 필요합니다. 검사, 대상 질환, 참조 기준이 분석을 정의합니다. 연구에 역치가 여러 개면 아래에서 정확히 하나를 선택하세요. 단위와 방향이 같아야 합니다."
  ],
  [
    "记录研究发现、原文引语及来源定位，再将有依据的发现归类并形成综合发现。",
    "Record findings with their source quotation and locator, then group supported findings into categories and synthesized findings.",
    "Enregistrez les constats, leur citation source et leur emplacement, puis regroupez les constats étayés en catégories et en constats synthétisés.",
    "Запишите результаты, цитаты из источников и указание места в источнике, затем объедините подтверждённые результаты в категории и синтезированные выводы.",
    "Registre hallazgos, citas de la fuente y localizadores; después agrupe los hallazgos respaldados en categorías y hallazgos sintetizados.",
    "知見、出典の引用、該当箇所を記録し、裏付けのある知見をカテゴリに分類して統合します。",
    "Registre achados, citações da fonte e localizadores; depois agrupe achados sustentados em categorias e achados sintetizados.",
    "Erfassen Sie Befunde mit Quellenzitat und Fundstelle und ordnen Sie belegte Befunde anschließend Kategorien und synthetisierten Befunden zu.",
    "Забележите налазе, цитате и место у извору, па групишите поткрепљене налазе у категорије и синтетисане налазе.",
    "발견 내용, 출처 인용문 및 위치를 기록한 후 근거가 있는 내용을 범주화하고 통합합니다."
  ],
  [
    "请核对 Bonett（2021）第 3 卷第 3.4 节、公式 3.11–3.12 及例 3.6/3.8；四分相关近似见 Bonett 与 Price（2005）。该近似使用校正后的优势比及依赖边际分布的指数，并非最大似然估计。请引用实际方法和研究报告；不要使用连续 Pearson 相关的标准误 1/√(n−3) 替代。",
    "Verify Bonett (2021), Volume 3, §3.4, equations 3.11–3.12 and examples 3.6/3.8; Bonett & Price (2005) for the tetrachoric approximation. The approximation uses a corrected odds ratio and a marginal-adaptive exponent, not maximum likelihood. Cite the chosen method and source report; review the original book text. Do not substitute the continuous-Pearson SE 1/√(n−3).",
    "Vérifiez Bonett (2021), vol. 3, §3.4, équations 3.11–3.12 et exemples 3.6/3.8, et Bonett & Price (2005) pour l’approximation tétrachorique. Celle-ci utilise un odds ratio corrigé et un exposant adapté aux marges, pas le maximum de vraisemblance. Citez la méthode et le rapport d’étude, après consultation des originaux. Ne remplacez pas l’erreur-type par 1/√(n−3), réservée au Pearson continu.",
    "Проверьте Bonett (2021), т. 3, §3.4, формулы 3.11–3.12, примеры 3.6/3.8, и Bonett & Price (2005) для тетрахорической аппроксимации. Используются скорректированное отношение шансов и показатель, зависящий от маргинальных распределений, а не максимальное правдоподобие. Проверьте оригиналы и цитируйте метод и отчёт. Не подставляйте стандартную ошибку 1/√(n−3) для непрерывного Pearson.",
    "Revise Bonett (2021), vol. 3, §3.4, ecuaciones 3.11–3.12 y ejemplos 3.6/3.8, y Bonett & Price (2005) para la aproximación tetracórica. Usa una odds ratio corregida y un exponente adaptado a los márgenes, no máxima verosimilitud. Consulte los originales y cite el método y el informe. No sustituya el error estándar por 1/√(n−3), propio de Pearson continuo.",
    "Bonett (2021)、第3巻§3.4、式3.11–3.12、例3.6/3.8、および四分相関近似のBonett & Price (2005)を確認してください。近似は補正オッズ比と周辺分布に適応する指数を使い、最尤法ではありません。原典を確認し、選択した手法と研究報告を引用してください。連続Pearson相関の標準誤差1/√(n−3)で代用しないでください。",
    "Confira Bonett (2021), vol. 3, §3.4, equações 3.11–3.12 e exemplos 3.6/3.8, e Bonett & Price (2005) para a aproximação tetracórica. Ela usa razão de chances corrigida e expoente adaptado às margens, não máxima verossimilhança. Consulte os originais e cite o método e o relatório. Não substitua pelo erro padrão 1/√(n−3) do Pearson contínuo.",
    "Prüfen Sie Bonett (2021), Band 3, §3.4, Gleichungen 3.11–3.12 und Beispiele 3.6/3.8 sowie Bonett & Price (2005) zur tetrachorischen Approximation. Diese nutzt ein korrigiertes Odds Ratio und einen randverteilungsabhängigen Exponenten, keine Maximum-Likelihood-Schätzung. Prüfen und zitieren Sie Originalmethode und Studienbericht. Nicht durch den Standardfehler 1/√(n−3) für kontinuierliche Pearson-Korrelation ersetzen.",
    "Проверите Bonett (2021), том 3, §3.4, једначине 3.11–3.12, примере 3.6/3.8 и Bonett & Price (2005) за тетрахоричну апроксимацију. Она користи коригован однос шанси и експонент прилагођен маргинама, а не максималну веродостојност. Проверите оригинале и наведите методу и извештај. Не замените стандардном грешком 1/√(n−3) за континуални Пирсонов коефицијент.",
    "Bonett (2021) 제3권 §3.4의 식 3.11–3.12와 예 3.6/3.8, 사분상관 근사의 Bonett & Price (2005)를 확인하세요. 이 근사는 보정 오즈비와 주변분포에 적응하는 지수를 사용하며 최대우도법이 아닙니다. 원문을 검토하고 방법과 연구 보고서를 인용하세요. 연속형 Pearson 상관의 표준오차 1/√(n−3)로 대체하지 마세요."
  ],
  [
    "请核对 Harrer 等（2022）《Doing Meta-Analysis with R》第 4.2.2 节，第 112–114 页（Glass 段落在第 113 页），以及 Hedges（1982）。计算对应 metafor 的 SMD1、correct=FALSE、vtype=LS。请核对原文，并引用标准化方式、方差方法和研究报告。",
    "Check Harrer et al. (2022), Doing Meta-Analysis with R, §4.2.2, pp. 112–114 (Glass passage p. 113), and Hedges (1982). The calculation matches metafor SMD1, correct=FALSE, vtype=LS. Verify the original methods and cite the standardizer, variance method and study reports.",
    "Consultez Harrer et al. (2022), Doing Meta-Analysis with R, §4.2.2, p. 112–114 (Glass p. 113), et Hedges (1982). Le calcul correspond à metafor SMD1, correct=FALSE, vtype=LS. Vérifiez les originaux et citez le standardiseur, la méthode de variance et les rapports d’étude.",
    "См. Harrer et al. (2022), Doing Meta-Analysis with R, §4.2.2, с. 112–114 (Glass с. 113), и Hedges (1982). Расчёт соответствует metafor SMD1, correct=FALSE, vtype=LS. Проверьте оригиналы; укажите стандартизатор, метод дисперсии и отчёты.",
    "Consulte Harrer et al. (2022), Doing Meta-Analysis with R, §4.2.2, pp. 112–114 (Glass p. 113), y Hedges (1982). El cálculo coincide con metafor SMD1, correct=FALSE, vtype=LS. Verifique y cite el estandarizador, el método de varianza y los informes.",
    "Harrer et al. (2022), Doing Meta-Analysis with R, §4.2.2、pp.112–114（Glassはp.113）とHedges (1982)を確認してください。計算はmetafor SMD1、correct=FALSE、vtype=LSに対応します。原典を確認し、標準化に用いた分母、分散の手法、研究報告を引用してください。",
    "Consulte Harrer et al. (2022), Doing Meta-Analysis with R, §4.2.2, pp. 112–114 (Glass p. 113), e Hedges (1982). O cálculo corresponde a metafor SMD1, correct=FALSE, vtype=LS. Verifique e cite o padronizador, o método da variância e os relatórios.",
    "Siehe Harrer et al. (2022), Doing Meta-Analysis with R, §4.2.2, S. 112–114 (Glass S. 113), und Hedges (1982). Die Berechnung entspricht metafor SMD1, correct=FALSE, vtype=LS. Prüfen und zitieren Sie Standardisierer, Varianzmethode und Studienberichte.",
    "Погледајте Harrer et al. (2022), Doing Meta-Analysis with R, §4.2.2, стр. 112–114 (Glass стр. 113), и Hedges (1982). Прорачун одговара metafor SMD1, correct=FALSE, vtype=LS. Проверите оригинале и наведите стандардизатор, методу варијансе и извештаје.",
    "Harrer et al. (2022), Doing Meta-Analysis with R, §4.2.2, pp.112–114(Glass 부분 p.113)와 Hedges (1982)를 확인하세요. 계산은 metafor SMD1, correct=FALSE, vtype=LS에 대응합니다. 원문을 확인하고 표준화 기준, 분산 방법 및 연구 보고서를 인용하세요."
  ],
  [
    "请核对 Borenstein 等（2021）《Introduction to Meta-Analysis》第 2 版第 4 章，第 30–31 页，以及 Hedges、Gurevitch 与 Curtis（1999）公式 1。这里使用未校正的对数均值比及分组 delta 方差（metafor ROM、correct=FALSE、vtype=LS）。请引用方法和研究报告；原书印出的标准误与所印方差的平方根不一致。",
    "Verify Borenstein et al. (2021), Introduction to Meta-Analysis, 2nd ed., chapter 4, pp. 30–31, and Hedges, Gurevitch & Curtis (1999), equation 1. This uses uncorrected log ROM with separate-arm delta variances (metafor ROM, correct=FALSE, vtype=LS). Cite the method and source reports; the book's printed SE differs from the square root of its printed variance.",
    "Vérifiez Borenstein et al. (2021), Introduction to Meta-Analysis, 2e éd., ch. 4, p. 30–31, et Hedges, Gurevitch & Curtis (1999), équation 1. Le log ROM non corrigé utilise des variances delta distinctes par groupe (metafor ROM, correct=FALSE, vtype=LS). Citez méthode et rapports ; l’erreur-type imprimée dans le livre diffère de la racine de la variance imprimée.",
    "Проверьте Borenstein et al. (2021), Introduction to Meta-Analysis, 2-е изд., гл. 4, с. 30–31, и Hedges, Gurevitch & Curtis (1999), формулу 1. Используется нескорректированный log ROM с дельта-дисперсиями отдельных групп (metafor ROM, correct=FALSE, vtype=LS). Цитируйте метод и отчёты: напечатанная стандартная ошибка в книге отличается от корня напечатанной дисперсии.",
    "Revise Borenstein et al. (2021), Introduction to Meta-Analysis, 2.ª ed., cap. 4, pp. 30–31, y Hedges, Gurevitch & Curtis (1999), ecuación 1. Se usa log ROM sin corregir con varianzas delta separadas por grupo (metafor ROM, correct=FALSE, vtype=LS). Cite método e informes; el error estándar impreso difiere de la raíz de la varianza impresa.",
    "Borenstein et al. (2021), Introduction to Meta-Analysis、第2版、第4章pp.30–31とHedges, Gurevitch & Curtis (1999)の式1を確認してください。未補正log ROMと各群のデルタ分散を用います（metafor ROM、correct=FALSE、vtype=LS）。手法と研究報告を引用してください。書籍の印刷された標準誤差は、印刷された分散の平方根と異なります。",
    "Confira Borenstein et al. (2021), Introduction to Meta-Analysis, 2ª ed., cap. 4, pp. 30–31, e Hedges, Gurevitch & Curtis (1999), equação 1. Usa-se log ROM não corrigido com variâncias delta por grupo (metafor ROM, correct=FALSE, vtype=LS). Cite método e relatórios; o erro padrão impresso difere da raiz da variância impressa.",
    "Prüfen Sie Borenstein et al. (2021), Introduction to Meta-Analysis, 2. Aufl., Kap. 4, S. 30–31, und Hedges, Gurevitch & Curtis (1999), Gleichung 1. Verwendet wird unkorrigiertes log ROM mit gruppenspezifischen Delta-Varianzen (metafor ROM, correct=FALSE, vtype=LS). Zitieren Sie Methode und Berichte; der gedruckte Standardfehler weicht von der Wurzel der gedruckten Varianz ab.",
    "Проверите Borenstein et al. (2021), Introduction to Meta-Analysis, 2. изд., погл. 4, стр. 30–31, и Hedges, Gurevitch & Curtis (1999), једначину 1. Користи се некоригован log ROM са делта варијансама по групама (metafor ROM, correct=FALSE, vtype=LS). Наведите методу и извештаје; штампана стандардна грешка разликује се од корена штампане варијансе.",
    "Borenstein et al. (2021), Introduction to Meta-Analysis 제2판 제4장 pp.30–31과 Hedges, Gurevitch & Curtis (1999) 식 1을 확인하세요. 미보정 log ROM과 집단별 델타 분산을 사용합니다(metafor ROM, correct=FALSE, vtype=LS). 방법과 보고서를 인용하세요. 책에 인쇄된 표준오차는 인쇄된 분산의 제곱근과 다릅니다."
  ],
  [
    "原文报告的效应及不确定性，请核对",
    "For reported effects and uncertainty, check the",
    "Pour les effets et incertitudes publiés, consultez",
    "Для указанных в источнике эффектов и неопределённости см.",
    "Para efectos e incertidumbre publicados, consulte",
    "報告された効果と不確実性については次を確認：",
    "Para efeitos e incerteza publicados, consulte",
    "Zu berichteten Effekten und Unsicherheit siehe",
    "За пријављене ефекте и неизвесност погледајте",
    "보고된 효과와 불확실성은 다음을 확인하세요:"
  ],
  [
    "。比值效应在对数尺度上分析。请引用实际计算方法和研究报告；发表前核对检验及区间假设。",
    ". Ratio effects are analysed on the log scale. Cite the actual calculation and study report; verify the test and interval assumptions against the original methods before publication.",
    ". Les rapports sont analysés sur l’échelle logarithmique. Citez le calcul et le rapport utilisés ; vérifiez les hypothèses du test et de l’intervalle dans les méthodes originales avant publication.",
    ". Отношения анализируются в логарифмической шкале. Цитируйте фактический расчёт и отчёт; до публикации проверьте предположения теста и интервала по оригинальным методам.",
    ". Los efectos de razón se analizan en escala logarítmica. Cite el cálculo y el informe utilizados; verifique los supuestos del contraste y del intervalo con las fuentes originales.",
    "。比の効果は対数尺度で分析します。実際の計算と研究報告を引用し、出版前に原典で検定と区間の仮定を確認してください。",
    ". Efeitos de razão são analisados na escala logarítmica. Cite o cálculo e o relatório utilizados; verifique os pressupostos do teste e do intervalo nas fontes originais.",
    ". Verhältnismaße werden logarithmisch analysiert. Zitieren Sie die tatsächliche Berechnung und den Studienbericht; prüfen Sie Test- und Intervallannahmen vor Veröffentlichung in den Originalquellen.",
    ". Мере односа анализирају се на логаритамској скали. Наведите прорачун и извештај; пре објављивања проверите претпоставке теста и интервала у оригиналним методама.",
    ". 비율 효과는 로그 척도로 분석합니다. 실제 계산법과 연구 보고서를 인용하고 출판 전에 원문에서 검정 및 구간 가정을 확인하세요."
  ],
  [
    "将 log-rank 的 O−E 与 V 换算为效应时，请核对",
    "For log-rank O−E and V conversion, check",
    "Pour la conversion log-rank de O−E et V, consultez",
    "Для преобразования O−E и V логрангового теста см.",
    "Para convertir O−E y V de log-rank, consulte",
    "log-rankのO−EとVの変換については次を確認：",
    "Para converter O−E e V do log-rank, consulte",
    "Zur Umrechnung von Log-Rank-O−E und V siehe",
    "За претварање log-rank O−E и V погледајте",
    "log-rank O−E 및 V 변환은 다음을 확인하세요:"
  ],
  [
    "与",
    "and",
    "et",
    "и",
    "y",
    "および",
    "e",
    "und",
    "и",
    "및"
  ],
  [
    "。请核对治疗组符号、事件定义和原始方法，并引用换算方法和研究报告。",
    ". Verify the treatment-arm sign, event definition and original methods before publication, and cite the conversion method and study report.",
    ". Vérifiez le signe du groupe traité, la définition de l’événement et les méthodes originales avant publication ; citez la méthode de conversion et le rapport d’étude.",
    ". До публикации проверьте знак для группы лечения, определение события и оригинальные методы; цитируйте преобразование и отчёт исследования.",
    ". Verifique el signo del grupo tratado, la definición del evento y las fuentes originales antes de publicar; cite la conversión y el informe.",
    "。出版前に治療群の符号、イベント定義、原手法を確認し、変換法と研究報告を引用してください。",
    ". Verifique o sinal do grupo tratado, a definição do evento e os métodos originais antes de publicar; cite a conversão e o relatório.",
    ". Prüfen Sie Vorzeichen der Behandlungsgruppe, Ereignisdefinition und Originalmethoden vor Veröffentlichung; zitieren Sie Umrechnungsmethode und Studienbericht.",
    ". Пре објављивања проверите знак групе лечења, дефиницију догађаја и изворне методе; наведите претварање и извештај.",
    ". 출판 전 치료군의 부호, 사건 정의 및 원 방법을 확인하고 변환법과 연구 보고서를 인용하세요."
  ],
  [
    "配对与变化值数据格式，请核对 Borenstein 等（2021）",
    "For paired and change-score formats, check Borenstein et al. (2021),",
    "Pour les formats appariés et les scores de changement, consultez Borenstein et al. (2021),",
    "Для парных данных и показателей изменения см. Borenstein et al. (2021),",
    "Para formatos pareados y puntuaciones de cambio, consulte Borenstein et al. (2021),",
    "対応データと変化得点についてはBorenstein et al. (2021)を確認：",
    "Para dados pareados e escores de mudança, consulte Borenstein et al. (2021),",
    "Zu gepaarten Daten und Veränderungswerten siehe Borenstein et al. (2021),",
    "За упарене податке и скорове промене погледајте Borenstein et al. (2021),",
    "대응 자료와 변화 점수는 Borenstein et al. (2021)을 확인하세요:"
  ],
  [
    "，第 11 章，第 211–216 页；以及 Cochrane Handbook 第 6 章和第 23 章。请引用实际使用的方法、假定相关系数的来源及研究报告；撰写前核对原书和设计假设。",
    ", ch. 11, pp. 211–216; and Cochrane Handbook ch. 6 and 23. Cite the method actually used, the source of any assumed correlation, and the study report. Verify the book text and design assumptions before writing.",
    ", ch. 11, p. 211–216 ; et Cochrane Handbook, ch. 6 et 23. Citez la méthode réellement utilisée, la source de toute corrélation supposée et le rapport d’étude. Vérifiez les textes et les hypothèses du plan avant rédaction.",
    ", гл. 11, с. 211–216; и Cochrane Handbook, гл. 6 и 23. Цитируйте фактический метод, источник предполагаемой корреляции и отчёт исследования. Перед написанием проверьте текст книги и предположения дизайна.",
    ", cap. 11, pp. 211–216; y Cochrane Handbook, caps. 6 y 23. Cite el método usado, la fuente de cualquier correlación supuesta y el informe. Verifique los textos y supuestos del diseño antes de redactar.",
    "、第11章pp.211–216、およびCochrane Handbook第6・23章。実際の手法、仮定した相関の出典、研究報告を引用し、執筆前に原典と研究デザインの仮定を確認してください。",
    ", cap. 11, pp. 211–216; e Cochrane Handbook, caps. 6 e 23. Cite o método usado, a fonte de qualquer correlação assumida e o relatório. Verifique os textos e os pressupostos do delineamento antes de escrever.",
    ", Kap. 11, S. 211–216, und Cochrane Handbook, Kap. 6 und 23. Zitieren Sie die verwendete Methode, die Quelle angenommener Korrelationen und den Studienbericht. Prüfen Sie Buchtext und Designannahmen vor dem Schreiben.",
    ", погл. 11, стр. 211–216; и Cochrane Handbook, погл. 6 и 23. Наведите коришћену методу, извор претпостављене корелације и извештај. Пре писања проверите текст и претпоставке дизајна.",
    ", 제11장 pp.211–216 및 Cochrane Handbook 제6·23장. 사용한 방법, 가정한 상관의 출처 및 연구 보고서를 인용하고 작성 전에 원문과 설계 가정을 확인하세요."
  ],
  [
    "独立两组 t、精确 p 值、两组 F 或 Cohen d 换算，请核对 Borenstein 等（2021）",
    "For independent-group t, exact p, two-group F or Cohen d conversion, check Borenstein et al. (2021),",
    "Pour convertir t à groupes indépendants, p exact, F à deux groupes ou d de Cohen, consultez Borenstein et al. (2021),",
    "Для преобразования t независимых групп, точного p, F двух групп или d Коэна см. Borenstein et al. (2021),",
    "Para convertir t independiente, p exacto, F de dos grupos o d de Cohen, consulte Borenstein et al. (2021),",
    "独立2群t、正確なp値、2群F、Cohenのdの変換はBorenstein et al. (2021)を確認：",
    "Para converter t independente, p exato, F de dois grupos ou d de Cohen, consulte Borenstein et al. (2021),",
    "Zur Umrechnung unabhängiger t-Tests, exakter p-Werte, Zweigruppen-F-Werte oder Cohens d siehe Borenstein et al. (2021),",
    "За претварање независног t, тачног p, F две групе или Коеновог d погледајте Borenstein et al. (2021),",
    "독립집단 t, 정확한 p값, 두 집단 F 또는 Cohen의 d 변환은 Borenstein et al. (2021)을 확인하세요:"
  ],
  [
    "。请引用换算方法和原始研究报告；发表前核对原书、检验设计和效应方向。",
    ". Cite the conversion method and original study report; verify the book text, test design and effect direction before publication.",
    ". Citez la conversion et le rapport original ; vérifiez le texte du livre, le plan du test et la direction de l’effet avant publication.",
    ". Цитируйте преобразование и оригинальный отчёт; перед публикацией проверьте текст книги, дизайн теста и направление эффекта.",
    ". Cite la conversión y el informe original; antes de publicar, verifique el texto, el diseño de la prueba y la dirección del efecto.",
    "。変換法と原研究報告を引用し、出版前に原典、検定デザイン、効果の方向を確認してください。",
    ". Cite a conversão e o relatório original; antes de publicar, verifique o texto, o delineamento do teste e a direção do efeito.",
    ". Zitieren Sie Umrechnung und Originalstudie; prüfen Sie Buchtext, Testdesign und Effektrichtung vor Veröffentlichung.",
    ". Наведите претварање и изворни извештај; пре објављивања проверите текст, дизајн теста и смер ефекта.",
    ". 변환법과 원 보고서를 인용하고 출판 전에 원문, 검정 설계 및 효과 방향을 확인하세요."
  ],
  [
    "单组均值与比例输入，请核对 Harrer 等（2022）",
    "For one-group mean and proportion inputs, check Harrer et al. (2022),",
    "Pour les moyennes et proportions à un groupe, consultez Harrer et al. (2022),",
    "Для средних и долей одной группы см. Harrer et al. (2022),",
    "Para medias y proporciones de un grupo, consulte Harrer et al. (2022),",
    "単群の平均・割合の入力についてはHarrer et al. (2022)を確認：",
    "Para médias e proporções de um grupo, consulte Harrer et al. (2022),",
    "Zu Mittelwerten und Anteilen einer Gruppe siehe Harrer et al. (2022),",
    "За средине и пропорције једне групе погледајте Harrer et al. (2022),",
    "단일 집단의 평균·비율 입력은 Harrer et al. (2022)을 확인하세요:"
  ],
  [
    "。单组发生率，请核对",
    ". For one-group rates, check",
    ". Pour les taux à un groupe, consultez",
    ". Для частот одной группы см.",
    ". Para tasas de un grupo, consulte",
    "。単群発生率については次を確認：",
    ". Para taxas de um grupo, consulte",
    ". Zu Raten einer Gruppe siehe",
    ". За стопе једне групе погледајте",
    ". 단일 집단 발생률은 다음을 확인하세요:"
  ],
  [
    "；计数似然模型，请核对",
    "; for count likelihood models, check",
    "; pour les modèles de vraisemblance des comptages, consultez",
    "; для моделей правдоподобия счётных данных см.",
    "; para modelos de verosimilitud de recuentos, consulte",
    "。計数尤度モデルについては次を確認：",
    "; para modelos de verossimilhança de contagens, consulte",
    "; zu Likelihood-Modellen für Zähldaten siehe",
    "; за моделе веродостојности бројева погледајте",
    "; 계수 우도 모형은 다음을 확인하세요:"
  ],
  [
    "。这些格式使用正态逆方差近似；区间及原始尺度精度换算为近似方法，变换后的比例与发生率输入不接受边界值。请引用实际计算方法、研究报告及模型，并核对原文。",
    ". These formats use normal inverse-variance approximations; direct CI and raw-scale precision conversions are approximate, and transformed proportion/rate inputs reject boundary values. Cite the actual calculation, study reports and any model used, and verify the original book or method text before publication.",
    ". Ces formats utilisent des approximations normales par variance inverse. Les conversions d’intervalle et de précision brute sont approximatives ; les entrées transformées de proportion/taux rejettent les limites. Citez le calcul, les rapports et le modèle réellement utilisés, et vérifiez les originaux avant publication.",
    ". Эти форматы используют нормальные приближения с обратной дисперсией. Преобразования интервала и точности в исходной шкале приближённые; преобразованные доли/частоты не принимают граничные значения. Цитируйте фактический расчёт, отчёты и модель, проверив оригиналы до публикации.",
    ". Estos formatos usan aproximaciones normales de varianza inversa. Convertir intervalos y precisión en escala original es aproximado; las proporciones/tasas transformadas rechazan valores límite. Cite cálculo, informes y modelo usados y verifique los originales antes de publicar.",
    "。これらは正規逆分散近似を使います。直接区間と元尺度の精度の変換は近似であり、変換後の割合・発生率の入力では境界値を受け付けません。実際の計算、研究報告、モデルを引用し、出版前に原典を確認してください。",
    ". Estes formatos usam aproximações normais de variância inversa. Converter intervalos e precisão na escala original é aproximado; proporções/taxas transformadas rejeitam valores de fronteira. Cite cálculo, relatórios e modelo utilizados e confira os originais antes de publicar.",
    ". Diese Formate verwenden Normalapproximationen mit inverser Varianz. Intervall- und Präzisionsumrechnungen aus der Rohskala sind approximativ; transformierte Anteils-/Ratenangaben lehnen Randwerte ab. Zitieren Sie tatsächliche Berechnung, Studienberichte und Modelle und prüfen Sie Originalquellen vor Veröffentlichung.",
    ". Формати користе нормалне апроксимације инверзне варијансе. Претварања интервала и прецизности на изворној скали су приближна; трансформисане пропорције/стопе одбацују граничне вредности. Наведите прорачун, извештаје и модел и проверите оригинале пре објављивања.",
    ". 이 형식은 정규 역분산 근사를 사용합니다. 직접 구간 및 원척도 정밀도 변환은 근사이며 변환된 비율·발생률 입력은 경계값을 허용하지 않습니다. 실제 계산법, 보고서 및 모형을 인용하고 출판 전에 원문을 확인하세요."
  ],
  [
    "RR、OR、HR 和发生率比要求正的估计值及对数尺度标准误。FISHER_Z 保存 Fisher z，合并后显示相关系数 r。MEAN 使用结局原始单位；LOGIT_PROP 和 LOG_RATE 保存 logit 或对数估计及标准误，再显示比例或发生率。单组分析请在比较组中明确标注，并核对单位、人群与时间点的可比性。",
    "RR/OR/HR/rate ratio require a positive estimate and log-scale SE. FISHER_Z stores Fisher's z and reports pooled correlation r. MEAN uses the outcome unit; LOGIT_PROP and LOG_RATE store logit/log estimates and their SE, then display pooled proportions/rates. Use a one-group label in Comparison and keep units, populations and time points comparable.",
    "RR/OR/HR/rapport de taux exigent une estimation positive et une erreur-type logarithmique. FISHER_Z stocke z et rapporte r combiné. MEAN garde l’unité du résultat ; LOGIT_PROP et LOG_RATE stockent les estimations logit/log et leurs erreurs-types, puis affichent proportions/taux combinés. Utilisez un libellé à un groupe dans Comparaison et des unités, populations et temps comparables.",
    "RR/OR/HR/отношение частот требуют положительной оценки и стандартной ошибки в лог-шкале. FISHER_Z хранит z и сообщает объединённое r. MEAN использует единицу исхода; LOGIT_PROP и LOG_RATE хранят оценки logit/log и их ошибки, показывая объединённые доли/частоты. Для одной группы используйте соответствующую метку сравнения; согласуйте единицы, популяции и сроки.",
    "RR/OR/HR/razón de tasas requieren estimación positiva y error estándar logarítmico. FISHER_Z guarda z y muestra r combinado. MEAN usa la unidad del resultado; LOGIT_PROP y LOG_RATE guardan estimaciones logit/log y sus errores y muestran proporciones/tasas combinadas. En Comparación use una etiqueta de un grupo y mantenga unidades, poblaciones y momentos comparables.",
    "RR・OR・HR・発生率比には正の推定値と対数尺度の標準誤差が必要です。FISHER_Zはzを保存し統合相関rを表示します。MEANはアウトカム単位を使い、LOGIT_PROP・LOG_RATEはlogit/log推定値と標準誤差を保存して割合・発生率を表示します。単群分析では比較欄に単群のラベルを用い、単位・母集団・時点を比較可能にしてください。",
    "RR/OR/HR/razão de taxas exigem estimativa positiva e erro padrão logarítmico. FISHER_Z armazena z e mostra r combinado. MEAN usa a unidade do desfecho; LOGIT_PROP e LOG_RATE armazenam estimativas logit/log e seus erros, exibindo proporções/taxas combinadas. Use um rótulo de grupo único em Comparação e mantenha unidades, populações e momentos comparáveis.",
    "RR/OR/HR/Ratenverhältnisse benötigen positive Schätzwerte und logarithmische Standardfehler. FISHER_Z speichert z und berichtet gepooltes r. MEAN nutzt die Ergebniseinheit; LOGIT_PROP und LOG_RATE speichern Logit-/Log-Werte samt Standardfehlern und zeigen gepoolte Anteile/Raten. Verwenden Sie bei einer Gruppe eine entsprechende Vergleichsbezeichnung und vergleichbare Einheiten, Populationen und Zeitpunkte.",
    "RR/OR/HR/однос стопа захтевају позитивну оцену и логаритамску стандардну грешку. FISHER_Z чува z и приказује обједињено r. MEAN користи јединицу исхода; LOGIT_PROP и LOG_RATE чувају logit/log оцене и грешке, приказујући пропорције/стопе. За једну групу користите одговарајућу ознаку поређења; ускладите јединице, популације и времена.",
    "RR/OR/HR/발생률비는 양의 추정값과 로그 척도 표준오차가 필요합니다. FISHER_Z는 z를 저장하고 통합 상관 r을 표시합니다. MEAN은 결과 단위를 사용하며 LOGIT_PROP와 LOG_RATE는 logit/log 추정값과 표준오차를 저장한 뒤 통합 비율·발생률을 표시합니다. 비교란에 단일집단 표시를 사용하고 단위, 모집단, 시점을 비교 가능하게 유지하세요."
  ],
  [
    "固定效应 GLS 不估计研究间异质性。2–8 个结局的 REML 估计研究间方差及相关；三个及以上结局要求每对结局至少有三项重叠研究，并拒绝奇异边界拟合。5–8 个结局需要更多计算及足够研究来稳定估计协方差。协方差与 SE² 必须使用相同的分析尺度。",
    "Fixed-effect GLS does not estimate between-study heterogeneity. Two- to eight-outcome REML estimates between-study variances and correlations; three or more outcomes need at least three overlapping studies per outcome pair and reject singular boundary fits. Five to eight outcomes require more computation and sufficient studies for stable covariance estimates. Covariance and SE² use the same analysis scale.",
    "GLS à effet fixe n’estime pas l’hétérogénéité interétudes. REML pour deux à huit résultats estime variances et corrélations interétudes ; à partir de trois résultats, chaque paire exige au moins trois études communes et les ajustements singuliers en frontière sont rejetés. Cinq à huit résultats demandent davantage de calcul et suffisamment d’études. Covariance et SE² utilisent la même échelle d’analyse.",
    "GLS с фиксированным эффектом не оценивает межисследовательскую неоднородность. REML для двух–восьми исходов оценивает дисперсии и корреляции; для трёх и более исходов каждой паре нужны минимум три общих исследования, сингулярные граничные решения отклоняются. Пять–восемь исходов требуют больше расчётов и достаточного числа исследований. Ковариация и SE² имеют одну шкалу анализа.",
    "GLS de efecto fijo no estima heterogeneidad entre estudios. REML para dos a ocho resultados estima varianzas y correlaciones; desde tres resultados requiere al menos tres estudios coincidentes por pareja y rechaza ajustes singulares en frontera. Cinco a ocho resultados exigen más cálculo y estudios suficientes. Covarianza y SE² usan la misma escala.",
    "固定効果GLSは研究間異質性を推定しません。2～8アウトカムのREMLは研究間分散・相関を推定します。3アウトカム以上では各組合せに共通する研究が3件以上必要で、特異な境界解は拒否します。5～8アウトカムでは計算負荷と十分な研究数に注意してください。共分散とSE²は同一分析尺度です。",
    "GLS de efeito fixo não estima heterogeneidade entre estudos. REML para dois a oito desfechos estima variâncias e correlações; com três ou mais, exige ao menos três estudos comuns por par e rejeita ajustes singulares na fronteira. Cinco a oito desfechos exigem mais cálculo e estudos suficientes. Covariância e SE² usam a mesma escala.",
    "GLS mit festem Effekt schätzt keine Heterogenität. REML für zwei bis acht Ergebnisse schätzt Varianzen und Korrelationen zwischen Studien; ab drei Ergebnissen sind mindestens drei gemeinsame Studien je Paar nötig, singuläre Randlösungen werden abgelehnt. Fünf bis acht Ergebnisse benötigen mehr Rechenaufwand und ausreichend Studien. Kovarianz und SE² liegen auf derselben Analyseskala.",
    "GLS са фиксним ефектом не оцењује хетерогеност. REML за два до осам исхода оцењује варијансе и корелације; за три или више исхода потребне су најмање три заједничке студије по пару, а сингуларна гранична решења се одбацују. Пет до осам исхода захтева више прорачуна и довољно студија. Коваријанса и SE² су на истој скали.",
    "고정 효과 GLS는 연구 간 이질성을 추정하지 않습니다. 2~8개 결과의 REML은 연구 간 분산·상관을 추정합니다. 결과가 3개 이상이면 각 쌍에 공통 연구가 최소 3개 필요하며 특이 경계 적합은 거부합니다. 5~8개 결과에는 더 많은 계산과 안정된 공분산 추정을 위한 충분한 연구가 필요합니다. 공분산과 SE²는 같은 분석 척도입니다."
  ],
  [
    "使用上方比较组、结局和时间点下已选的原始比例计数。至少需要三项非边界研究，少于十项时应谨慎。此单组比例变体不支持双臂 OR 或发生率。零事件及全事件研究的回归权重为零，并列为排除；不作连续性校正。",
    "Uses selected raw proportion counts from the comparison, outcome and time point above. At least three non-boundary studies are required; fewer than ten warrants caution. This single-proportion variant does not support two-arm OR or incidence rates. Zero-event and all-event studies have zero regression weight and are listed as excluded; no continuity correction is applied.",
    "Utilise les effectifs bruts de proportion sélectionnés pour la comparaison, le résultat et le temps ci-dessus. Au moins trois études hors frontière sont nécessaires ; moins de dix impose la prudence. Cette variante à une proportion n’accepte ni OR à deux groupes ni taux d’incidence. Les études avec zéro événement ou tous les événements ont un poids nul et sont listées comme exclues, sans correction de continuité.",
    "Используются выбранные исходные числа для долей по сравнению, исходу и сроку выше. Нужны минимум три исследования без граничных долей; при числе меньше десяти нужна осторожность. Вариант для одной доли не поддерживает двухгрупповые OR или частоты заболеваемости. Исследования с нулём событий или событиями у всех имеют нулевой вес и отмечаются исключёнными; поправки непрерывности нет.",
    "Usa los recuentos brutos seleccionados para comparación, resultado y momento. Requiere al menos tres estudios no limítrofes; con menos de diez se recomienda cautela. Esta variante de una proporción no admite OR de dos grupos ni tasas de incidencia. Estudios sin eventos o con eventos en todos tienen peso cero y figuran como excluidos, sin corrección de continuidad.",
    "上で指定した比較・アウトカム・時点の選択済み割合計数を使います。境界値でない研究が3件以上必要で、10件未満は慎重な解釈が必要です。単群割合の変法であり、2群ORや発生率には対応しません。イベント0件または全員発生の研究は重み0として除外一覧に示し、連続性補正は行いません。",
    "Usa contagens brutas selecionadas para comparação, desfecho e momento. Exige ao menos três estudos fora da fronteira; menos de dez pede cautela. Esta variante de proporção única não aceita OR de dois grupos nem taxas de incidência. Estudos sem eventos ou com eventos em todos têm peso zero e são listados como excluídos, sem correção de continuidade.",
    "Verwendet ausgewählte Anteilszählwerte für Vergleich, Ergebnis und Zeitpunkt oben. Mindestens drei Studien ohne Randanteile sind nötig; bei weniger als zehn ist Vorsicht geboten. Diese Ein-Proportions-Variante unterstützt weder Zweigruppen-OR noch Inzidenzraten. Studien mit keinem oder ausschließlich Ereignissen erhalten Gewicht null und werden ohne Kontinuitätskorrektur als ausgeschlossen aufgeführt.",
    "Користе се изабрани сирови бројеви за пропорције, поређење, исход и време. Потребне су најмање три студије без граничних вредности; мање од десет захтева опрез. Варијанта једне пропорције не подржава OR две групе или стопе инциденције. Студије без догађаја или са догађајима код свих имају нулту тежину и наведене су као искључене, без корекције континуитета.",
    "위 비교·결과·시점의 선택된 원시 비율 계수를 사용합니다. 경계값이 아닌 연구가 최소 3개 필요하고 10개 미만은 주의해야 합니다. 단일 비율 변형은 두 집단 OR이나 발생률을 지원하지 않습니다. 사건이 없거나 모두에게 발생한 연구는 가중치 0으로 제외 목록에 표시하며 연속성 보정은 하지 않습니다."
  ],
  [
    "方法来源：Peters 等（2006），JAMA 295(6):676–680，DOI 10.1001/jama.295.6.676。单组比例扩展遵循 R meta 8.5-0 对 metaprop 对象的 metabias 实现。请核对原文与软件文档，引用实际使用的方法和变体；不对称不能确证发表偏倚。",
    "Method source: Peters et al. (2006), JAMA 295(6):676–680, DOI 10.1001/jama.295.6.676. The single-proportion extension follows R meta 8.5-0 metabias for metaprop objects. Verify the original paper and software documentation, and cite the method and variant used. Asymmetry cannot establish publication bias.",
    "Source : Peters et al. (2006), JAMA 295(6):676–680, DOI 10.1001/jama.295.6.676. L’extension à une proportion suit R meta 8.5-0, metabias pour les objets metaprop. Vérifiez article et documentation, et citez la méthode et sa variante. Une asymétrie ne démontre pas un biais de publication.",
    "Источник: Peters et al. (2006), JAMA 295(6):676–680, DOI 10.1001/jama.295.6.676. Расширение для одной доли следует R meta 8.5-0, metabias для metaprop. Проверьте статью и документацию, цитируйте метод и вариант. Асимметрия не доказывает публикационное смещение.",
    "Fuente: Peters et al. (2006), JAMA 295(6):676–680, DOI 10.1001/jama.295.6.676. La extensión para una proporción sigue R meta 8.5-0, metabias para metaprop. Revise artículo y documentación y cite método y variante. La asimetría no demuestra sesgo de publicación.",
    "出典：Peters et al. (2006), JAMA 295(6):676–680, DOI 10.1001/jama.295.6.676。単群割合への拡張はR meta 8.5-0のmetaprop用metabiasに従います。原論文と文書を確認し、手法と変法を引用してください。非対称性だけで出版バイアスは立証できません。",
    "Fonte: Peters et al. (2006), JAMA 295(6):676–680, DOI 10.1001/jama.295.6.676. A extensão de proporção única segue R meta 8.5-0, metabias para metaprop. Confira artigo e documentação e cite método e variante. Assimetria não comprova viés de publicação.",
    "Quelle: Peters et al. (2006), JAMA 295(6):676–680, DOI 10.1001/jama.295.6.676. Die Ein-Proportions-Erweiterung folgt R meta 8.5-0, metabias für metaprop. Prüfen Sie Artikel und Dokumentation und zitieren Sie Methode und Variante. Asymmetrie belegt keinen Publikationsbias.",
    "Извор: Peters et al. (2006), JAMA 295(6):676–680, DOI 10.1001/jama.295.6.676. Проширење за једну пропорцију прати R meta 8.5-0, metabias за metaprop. Проверите рад и документацију и наведите методу и варијанту. Асиметрија не доказује публикациону пристрасност.",
    "출처: Peters et al. (2006), JAMA 295(6):676–680, DOI 10.1001/jama.295.6.676. 단일 비율 확장은 R meta 8.5-0의 metaprop용 metabias를 따릅니다. 원 논문과 문서를 확인하고 방법과 변형을 인용하세요. 비대칭만으로 출판 편향을 입증할 수는 없습니다."
  ],
  [
    "提供二项与泊松计数似然方法。请引用实际模型及各研究报告；核对原始方法、人群、事件定义和人时单位是否可比。",
    "for binomial and Poisson count likelihoods. Cite the actual model and every source report; check the original method text and whether study populations, event definitions, and person-time units are comparable.",
    "pour les vraisemblances binomiales et de Poisson. Citez le modèle utilisé et chaque rapport ; vérifiez le texte original et la comparabilité des populations, définitions d’événement et unités de personnes-temps.",
    "для биномиальных и пуассоновских правдоподобий. Цитируйте модель и каждый отчёт; проверьте оригинал и сопоставимость популяций, определений событий и единиц человеко-времени.",
    "para verosimilitudes binomiales y de Poisson. Cite el modelo y cada informe; verifique el original y la comparabilidad de poblaciones, definiciones de eventos y unidades de persona-tiempo.",
    "は二項・Poisson計数尤度に関する文書です。実際のモデルと全研究報告を引用し、原典と母集団、イベント定義、人時間単位の比較可能性を確認してください。",
    "para verossimilhanças binomiais e de Poisson. Cite o modelo e cada relatório; confira o original e a comparabilidade de populações, definições de eventos e unidades de pessoa-tempo.",
    "für Binomial- und Poisson-Likelihoods. Zitieren Sie Modell und alle Studienberichte; prüfen Sie Originaltext sowie Vergleichbarkeit von Populationen, Ereignisdefinitionen und Personenzeiteinheiten.",
    "за биномне и Поасонове веродостојности. Наведите модел и сваки извештај; проверите оригинал и упоредивост популација, дефиниција догађаја и јединица особа-времена.",
    "는 이항 및 포아송 계수 우도에 관한 문서입니다. 모형과 모든 연구 보고서를 인용하고 원문 및 모집단, 사건 정의, 관찰 인시 단위의 비교 가능성을 확인하세요."
  ],
  [
    "共同概率／发生率分支使用精确区间：Clopper 与 Pearson（1934），DOI 10.1093/biomet/26.4.404；Garwood（1936），DOI 10.1093/biomet/28.3-4.437。随机截距分支在可识别时报告条件性参考组估计及基于观察信息的 Wald 区间；边际均值另行标注。请结合异质性、研究数较少及边界拟合解释。",
    "The common-probability/rate branch uses exact intervals: Clopper & Pearson (1934), DOI 10.1093/biomet/26.4.404; Garwood (1936), DOI 10.1093/biomet/28.3-4.437. The random-intercept branch reports a conditional reference-group estimate and an observed-information Wald interval when identifiable; its marginal mean is separately labeled. Heterogeneity, small study counts and boundary fits require interpretation.",
    "La branche à probabilité/taux commun utilise des intervalles exacts : Clopper & Pearson (1934), DOI 10.1093/biomet/26.4.404 ; Garwood (1936), DOI 10.1093/biomet/28.3-4.437. La branche à intercept aléatoire rapporte une estimation conditionnelle du groupe de référence et, si identifiable, un intervalle de Wald fondé sur l’information observée ; sa moyenne marginale est distinctement étiquetée. Interprétez avec attention hétérogénéité, faible nombre d’études et ajustements en frontière.",
    "Общая вероятность/частота использует точные интервалы: Clopper & Pearson (1934), DOI 10.1093/biomet/26.4.404; Garwood (1936), DOI 10.1093/biomet/28.3-4.437. Случайный интерсепт даёт условную оценку референтной группы и, при идентифицируемости, интервал Вальда по наблюдаемой информации; маргинальное среднее помечено отдельно. Учитывайте неоднородность, малое число исследований и граничные решения.",
    "La rama común usa intervalos exactos: Clopper & Pearson (1934), DOI 10.1093/biomet/26.4.404; Garwood (1936), DOI 10.1093/biomet/28.3-4.437. La de intercepto aleatorio informa una estimación condicional del grupo de referencia y, si es identificable, un intervalo de Wald por información observada; la media marginal se etiqueta aparte. Interprete heterogeneidad, pocos estudios y ajustes en frontera.",
    "共通確率・発生率では正確区間を用います：Clopper & Pearson (1934), DOI 10.1093/biomet/26.4.404、Garwood (1936), DOI 10.1093/biomet/28.3-4.437。ランダム切片では参照群の条件付き推定値と、識別可能な場合に観測情報に基づくWald区間を報告し、周辺平均は別に明示します。異質性、少数研究、境界解に注意してください。",
    "A ramificação comum usa intervalos exatos: Clopper & Pearson (1934), DOI 10.1093/biomet/26.4.404; Garwood (1936), DOI 10.1093/biomet/28.3-4.437. A de intercepto aleatório relata estimativa condicional do grupo de referência e, se identificável, intervalo de Wald pela informação observada; a média marginal é identificada separadamente. Interprete heterogeneidade, poucos estudos e ajustes na fronteira.",
    "Gemeinsame Wahrscheinlichkeit/Rate nutzt exakte Intervalle: Clopper & Pearson (1934), DOI 10.1093/biomet/26.4.404; Garwood (1936), DOI 10.1093/biomet/28.3-4.437. Der zufällige Interzept liefert eine bedingte Referenzgruppenschätzung und, falls identifizierbar, ein Wald-Intervall aus beobachteter Information; das marginale Mittel ist separat gekennzeichnet. Berücksichtigen Sie Heterogenität, wenige Studien und Randlösungen.",
    "Грана заједничке вероватноће/стопе користи тачне интервале: Clopper & Pearson (1934), DOI 10.1093/biomet/26.4.404; Garwood (1936), DOI 10.1093/biomet/28.3-4.437. Случајни одсечак даје условну оцену референтне групе и, када се може идентификовати, Валдов интервал из посматране информације; маргинална средина је посебно означена. Размотрите хетерогеност, мало студија и гранична решења.",
    "공통 확률·발생률 분기는 정확 구간을 사용합니다: Clopper & Pearson (1934), DOI 10.1093/biomet/26.4.404; Garwood (1936), DOI 10.1093/biomet/28.3-4.437. 무작위 절편 분기는 참조집단의 조건부 추정값과 식별 가능한 경우 관측 정보에 근거한 Wald 구간을 보고하며 주변 평균은 따로 표시합니다. 이질성, 적은 연구 수 및 경계 적합을 고려하세요."
  ],
  [
    "仅用于独立组。方向为治疗组风险减去对照组风险；正值表示治疗组事件更多，不一定有益。Newcombe 方法 10 使用未作连续性校正的 Wilson 界限，属于近似而非精确区间。接近零事件或全事件时 Wald 可能低估不确定性；两组方差均为零时拒绝计算。",
    "Independent groups only. Direction is treatment risk minus control risk; a positive value means more events under treatment, not necessarily benefit. Newcombe method 10 uses Wilson limits without continuity correction; it is an approximation, not an exact interval. Wald can understate uncertainty near zero or all events and is rejected when both arm variances are zero.",
    "Groupes indépendants uniquement. La différence est le risque traité moins le risque témoin ; une valeur positive indique plus d’événements, pas nécessairement un bénéfice. Newcombe 10 utilise des limites de Wilson sans correction de continuité : c’est une approximation. Wald peut sous-estimer l’incertitude près des frontières et est rejeté si les deux variances sont nulles.",
    "Только независимые группы. Направление: риск лечения минус риск контроля; положительное значение означает больше событий, не обязательно пользу. Newcombe 10 использует границы Уилсона без поправки непрерывности и является приближением. Вальд может занижать неопределённость у границ и отклоняется при нулевых дисперсиях обеих групп.",
    "Solo grupos independientes. La dirección es riesgo tratado menos riesgo control; un valor positivo indica más eventos, no necesariamente beneficio. Newcombe 10 usa límites de Wilson sin corrección de continuidad y es aproximado. Wald puede subestimar incertidumbre cerca de los extremos y se rechaza si ambas varianzas son cero.",
    "独立2群専用です。治療群リスクから対照群リスクを引くため、正の値はイベント増加であり利益とは限りません。Newcombe法10は連続性補正なしのWilson限界を使う近似で、正確区間ではありません。Waldは境界付近で不確実性を過小評価し得るため、両群分散が0なら拒否します。",
    "Apenas grupos independentes. A direção é risco tratado menos risco controle; valor positivo indica mais eventos, não necessariamente benefício. Newcombe 10 usa limites de Wilson sem correção de continuidade e é aproximado. Wald pode subestimar incerteza perto das fronteiras e é rejeitado se ambas as variâncias forem zero.",
    "Nur unabhängige Gruppen. Richtung: Behandlungsrisiko minus Kontrollrisiko; positive Werte bedeuten mehr Ereignisse, nicht unbedingt Nutzen. Newcombe 10 nutzt Wilson-Grenzen ohne Kontinuitätskorrektur und ist approximativ. Wald kann Unsicherheit an den Rändern unterschätzen und wird bei zwei Nullvarianzen abgelehnt.",
    "Само независне групе. Смер је ризик лечења минус контролни ризик; позитивна вредност значи више догађаја, не нужно корист. Newcombe 10 користи Вилсонове границе без корекције континуитета и приближан је. Wald може потценити неизвесност близу граница и одбацује се ако су обе варијансе нула.",
    "독립 집단 전용입니다. 치료군 위험에서 대조군 위험을 빼므로 양수는 사건 증가이며 반드시 이득은 아닙니다. Newcombe 방법 10은 연속성 보정 없는 Wilson 한계를 사용하는 근사이지 정확 구간이 아닙니다. Wald는 경계 부근에서 불확실성을 과소평가할 수 있으며 두 집단 분산이 모두 0이면 거부합니다."
  ],
  [
    "用于独立泊松计数，两组须使用相同人时单位。发生率比 =（治疗组事件数／治疗组人时）÷（对照组事件数／对照组人时）。精确区间偏保守；中 P 区间覆盖率可能低于标称水平。单组零事件可产生 0 或 ∞；两组均零事件不提供发生率比信息，拒绝计算。JSON 中无限界限记录为 \"Infinity\" 或 \"-Infinity\"，而不是缺失值。",
    "Independent Poisson counts; use the same person-time unit for both arms. IRR = (treatment events / treatment person-time) ÷ (control events / control person-time). Exact intervals are conservative; mid-P intervals can have coverage below the nominal level. A single zero-event arm can produce 0 or ∞; two zero-event arms provide no rate-ratio information and are rejected. JSON records encode infinite bounds as \"Infinity\" or \"-Infinity\", never as missing values.",
    "Comptages de Poisson indépendants, avec la même unité de personnes-temps. IRR = taux traité / taux témoin. Les intervalles exacts sont conservateurs ; mid-P peut sous-couvrir. Un seul groupe sans événement peut produire 0 ou ∞ ; deux groupes sans événement n’informent pas le rapport et sont rejetés. Les limites infinies sont encodées en JSON par \"Infinity\" ou \"-Infinity\", jamais comme valeurs manquantes.",
    "Независимые пуассоновские числа с одинаковой единицей человеко-времени. IRR = (число событий в группе лечения / человеко-время группы лечения) ÷ (число событий в контрольной группе / человеко-время контрольной группы). Точные интервалы консервативны; mid-P может недопокрывать. Одна группа без событий может дать 0 или ∞; две такие группы неинформативны и отклоняются. Бесконечные границы в JSON — \"Infinity\" или \"-Infinity\", не пропуски.",
    "Recuentos de Poisson independientes y misma unidad de persona-tiempo. IRR = tasa tratada / tasa control. Los intervalos exactos son conservadores; mid-P puede tener cobertura inferior a la nominal. Un grupo sin eventos puede producir 0 o ∞; dos no aportan información y se rechazan. JSON codifica límites infinitos como \"Infinity\" o \"-Infinity\", no como ausentes.",
    "独立Poisson計数で両群の人時間単位を揃えます。IRR＝（治療群イベント数／人時間）÷（対照群イベント数／人時間）。正確区間は保守的で、mid-Pは被覆率が名目値を下回る場合があります。片群0件なら0または∞になり得ますが、両群0件は比の情報がなく拒否します。JSONの無限限界は欠測でなく\"Infinity\"または\"-Infinity\"です。",
    "Contagens de Poisson independentes e mesma unidade de pessoa-tempo. IRR = taxa tratada / taxa controle. Intervalos exatos são conservadores; mid-P pode ter cobertura inferior à nominal. Um grupo sem eventos pode gerar 0 ou ∞; dois não fornecem informação e são rejeitados. JSON codifica limites infinitos como \"Infinity\" ou \"-Infinity\", nunca como ausentes.",
    "Unabhängige Poisson-Zählwerte mit gleicher Personenzeiteinheit. IRR = (Ereignisse in der Behandlungsgruppe / Personenzeit der Behandlungsgruppe) ÷ (Ereignisse in der Kontrollgruppe / Personenzeit der Kontrollgruppe). Exakte Intervalle sind konservativ; mid-P kann unterdecken. Eine Gruppe ohne Ereignisse kann 0 oder ∞ liefern; zwei liefern keine Information und werden abgelehnt. JSON kodiert unendliche Grenzen als \"Infinity\" oder \"-Infinity\", nie als fehlende Werte.",
    "Независни Поасонови бројеви са истом јединицом особа-времена. IRR = (број догађаја у терапијској групи / особа-време терапијске групе) ÷ (број догађаја у контролној групи / особа-време контролне групе). Тачни интервали су конзервативни; mid-P може имати покривеност испод номиналне. Једна група без догађаја може дати 0 или ∞; две нису информативне и одбацују се. JSON бележи бесконачне границе као \"Infinity\" или \"-Infinity\", не као недостајуће вредности.",
    "독립 포아송 계수이며 두 집단의 관찰 인시 단위가 같아야 합니다. IRR=(치료군 사건/인시)÷(대조군 사건/인시)입니다. 정확 구간은 보수적이고 mid-P는 명목 수준보다 낮은 포함률을 보일 수 있습니다. 한 집단의 사건이 0이면 0 또는 ∞가 가능하지만 두 집단 모두 0이면 정보가 없어 거부합니다. JSON 무한 경계는 결측이 아닌 \"Infinity\" 또는 \"-Infinity\"로 기록합니다."
  ],
  [
    "需要保守覆盖率时，可考虑 Clopper–Pearson（比例）或 Garwood（发生率）。Wilson、中 P 和 Byar 的覆盖率可能低于标称水平。Jeffreys 报告未修正的贝叶斯等尾可信区间，其边界不必包含 0 或 1。",
    "For conservative coverage, consider Clopper–Pearson (proportions) or Garwood (rates). Wilson, mid-P and Byar can have coverage below the nominal level. Jeffreys reports an unmodified Bayesian equal-tailed credible interval; its boundary limits need not contain 0 or 1.",
    "Pour une couverture conservatrice, envisagez Clopper–Pearson (proportions) ou Garwood (taux). Wilson, mid-P et Byar peuvent sous-couvrir. Jeffreys donne un intervalle bayésien de crédibilité à queues égales non modifié ; ses limites en frontière peuvent ne pas inclure 0 ou 1.",
    "Для консервативного покрытия рассмотрите Clopper–Pearson для долей или Garwood для частот. Wilson, mid-P и Byar могут недопокрывать. Jeffreys даёт неизменённый байесовский равнохвостовой апостериорный интервал; его граничные пределы могут не включать 0 или 1.",
    "Para cobertura conservadora, considere Clopper–Pearson (proporciones) o Garwood (tasas). Wilson, mid-P y Byar pueden cubrir menos del nivel nominal. Jeffreys informa un intervalo bayesiano de colas iguales sin modificar; en los límites puede no contener 0 o 1.",
    "保守的な被覆率には割合のClopper–Pearsonまたは発生率のGarwoodを検討してください。Wilson、mid-P、Byarは名目被覆率を下回る場合があります。Jeffreysは未修正の等尾ベイズ信用区間を報告し、境界で0や1を含むとは限りません。",
    "Para cobertura conservadora, considere Clopper–Pearson (proporções) ou Garwood (taxas). Wilson, mid-P e Byar podem cobrir menos que o nível nominal. Jeffreys relata intervalo bayesiano de caudas iguais não modificado; nas fronteiras pode não conter 0 ou 1.",
    "Für konservative Abdeckung erwägen Sie Clopper–Pearson für Anteile oder Garwood für Raten. Wilson, mid-P und Byar können unterdecken. Jeffreys berichtet ein unverändertes bayessches Kredibilitätsintervall mit gleichen Randwahrscheinlichkeiten; Randgrenzen müssen 0 oder 1 nicht einschließen.",
    "За конзервативну покривеност размотрите Clopper–Pearson за пропорције или Garwood за стопе. Wilson, mid-P и Byar могу покривати испод номиналног нивоа. Jeffreys даје неизмењен Бајесов интервал веродостојности са једнаким реповима; границе не морају обухватити 0 или 1.",
    "보수적 포함률에는 비율의 Clopper–Pearson 또는 발생률의 Garwood를 고려하세요. Wilson, mid-P, Byar는 명목 수준보다 낮을 수 있습니다. Jeffreys는 수정하지 않은 등꼬리 베이지안 신용구간을 보고하며 경계에서 0 또는 1을 포함하지 않을 수 있습니다."
  ],
  [
    "，第 13 章，第 13.1.1 节，第 282–283 页（相依比较）。协方差感知路径需要提供研究内协方差。REML 假定各组偏差独立、方差相同，且共享一个 τ²。独立的设计间 Q 检验仅采用固定效应；两种分析均不检验可传递性。请核对原书、模型假设和研究报告，并引用实际方法。",
    ", ch. 13, §13.1.1, pp. 282–283 (dependent comparisons). The covariance-aware path requires supplied within-study covariance. REML assumes independent equal-variance arm deviations and one common τ². The separate between-design Q test is fixed-effect only; neither analysis checks transitivity. Verify the book text, model assumptions and source reports; cite the methods actually used.",
    ", ch. 13, §13.1.1, p. 282–283 (comparaisons dépendantes). La voie tenant compte des covariances exige la covariance intraétude fournie. REML suppose des écarts de groupes indépendants à variance égale et un τ² commun. Le test Q entre plans est à effet fixe uniquement ; aucune analyse ne vérifie la transitivité. Vérifiez et citez méthodes, hypothèses et rapports.",
    ", гл. 13, §13.1.1, с. 282–283 (зависимые сравнения). Ковариационный путь требует заданных внутри-исследовательских ковариаций. REML предполагает независимые отклонения групп с равной дисперсией и общим τ². Отдельный междизайновый тест Q — только с фиксированным эффектом; транзитивность не проверяется. Проверьте и цитируйте методы, предположения и отчёты.",
    ", cap. 13, §13.1.1, pp. 282–283 (comparaciones dependientes). La ruta con covarianza exige covarianzas intraestudio suministradas. REML supone desviaciones de grupos independientes con igual varianza y un τ² común. Q entre diseños solo es de efecto fijo; ninguna ruta comprueba transitividad. Verifique y cite métodos, supuestos e informes.",
    "、第13章§13.1.1、pp.282–283（従属比較）。共分散対応の経路には研究内共分散の入力が必要です。REMLは独立・等分散の群偏差と共通τ²を仮定します。別途のデザイン間Q検定は固定効果のみで、いずれも推移性は確認しません。原典・仮定・報告を確認し実際の手法を引用してください。",
    ", cap. 13, §13.1.1, pp. 282–283 (comparações dependentes). O caminho com covariância exige covariâncias intraestudo fornecidas. REML pressupõe desvios de grupos independentes com igual variância e τ² comum. Q entre delineamentos é apenas de efeito fixo; nenhum caminho verifica transitividade. Confira e cite métodos, pressupostos e relatórios.",
    ", Kap. 13, §13.1.1, S. 282–283 (abhängige Vergleiche). Der kovarianzberücksichtigende Pfad erfordert angegebene innerstudielle Kovarianzen. REML nimmt unabhängige Armabweichungen gleicher Varianz und ein gemeinsames τ² an. Der separate Q-Test zwischen Designs gilt nur für feste Effekte; Transitivität wird nicht geprüft. Prüfen und zitieren Sie Methoden, Annahmen und Berichte.",
    ", погл. 13, §13.1.1, стр. 282–283 (зависна поређења). Путања са коваријансом захтева унету коваријансу унутар студије. REML претпоставља независна одступања група једнаке варијансе и заједничко τ². Посебни Q између дизајна је само за фиксни ефекат; транзитивност се не проверава. Проверите и наведите методе, претпоставке и извештаје.",
    ", 제13장 §13.1.1, pp.282–283(의존 비교). 공분산 고려 경로에는 연구 내 공분산을 입력해야 합니다. REML은 독립적이고 등분산인 집단 편차와 공통 τ²를 가정합니다. 별도 설계 간 Q 검정은 고정 효과만 지원하며 어느 분석도 추이성을 검증하지 않습니다. 원문, 가정, 보고서를 확인하고 실제 방법을 인용하세요."
  ],
  [
    "直接／间接对比分解会从间接网络中排除整项直接研究的数据块。随机效应为两个部分分别估计 REML τ²，不同于 netmeta 的共同网络 τ² 约定。请核对 Harrer 等（2022）第 12 章，352–353 页，以及 Dias 等（2010），DOI 10.1002/sim.3767，并引用方法和研究报告。不一致性 p 值不能评估可传递性；请检查效应修饰变量。",
    "The direct/indirect contrast split excludes entire direct-study blocks from the indirect network. Random effects estimates separate REML τ² values for each partition, unlike netmeta's common network τ² convention. Check Harrer et al. (2022), ch. 12, pp. 352–353, and Dias et al. (2010), DOI 10.1002/sim.3767. Verify the original sources and cite the method and study reports. A disagreement p-value does not assess transitivity; check effect modifiers.",
    "La séparation directe/indirecte retire les blocs entiers des études directes du réseau indirect. Les effets aléatoires estiment un τ² REML distinct par partition, contrairement au τ² commun de netmeta. Consultez Harrer et al. (2022), ch. 12, p. 352–353, et Dias et al. (2010), DOI 10.1002/sim.3767 ; vérifiez et citez méthodes et rapports. Le p de désaccord n’évalue pas la transitivité : examinez les modificateurs d’effet.",
    "При разделении прямых/косвенных контрастов из косвенной сети удаляются целые блоки прямых исследований. Случайные эффекты оценивают отдельный REML τ² для каждой части, в отличие от общего τ² netmeta. См. Harrer et al. (2022), гл. 12, с. 352–353, и Dias et al. (2010), DOI 10.1002/sim.3767; проверьте и цитируйте методы и отчёты. p несогласия не оценивает транзитивность: проверьте модификаторы эффекта.",
    "La separación directa/indirecta excluye bloques completos de estudios directos de la red indirecta. Los efectos aleatorios estiman τ² REML separado por partición, no el τ² común de netmeta. Consulte Harrer et al. (2022), cap. 12, pp. 352–353, y Dias et al. (2010), DOI 10.1002/sim.3767; verifique y cite métodos e informes. El p de desacuerdo no evalúa transitividad; revise modificadores del efecto.",
    "直接・間接分割では、直接比較研究のブロック全体を間接ネットワークから除外します。変量効果は分割ごとに別のREML τ²を推定し、netmetaの共通τ²とは異なります。Harrer et al. (2022)第12章pp.352–353とDias et al. (2010), DOI 10.1002/sim.3767を確認・引用してください。不一致p値は推移性の評価ではなく、効果修飾因子の確認が必要です。",
    "A separação direta/indireta exclui blocos inteiros de estudos diretos da rede indireta. Efeitos aleatórios estimam τ² REML separado por partição, diferente do τ² comum do netmeta. Consulte Harrer et al. (2022), cap. 12, pp. 352–353, e Dias et al. (2010), DOI 10.1002/sim.3767; confira e cite métodos e relatórios. O p de discordância não avalia transitividade; examine modificadores de efeito.",
    "Bei der direkten/indirekten Trennung werden vollständige direkte Studienblöcke aus dem indirekten Netz entfernt. Zufällige Effekte schätzen getrennte REML-τ² je Teilnetz, anders als das gemeinsame τ² von netmeta. Siehe Harrer et al. (2022), Kap. 12, S. 352–353, und Dias et al. (2010), DOI 10.1002/sim.3767; prüfen und zitieren Sie Quellen. Ein Diskrepanz-p-Wert prüft keine Transitivität; untersuchen Sie Effektmodifikatoren.",
    "Подела на директне/индиректне контрасте уклања целе блокове директних студија из индиректне мреже. Случајни ефекти оцењују посебно REML τ² по делу, за разлику од заједничког τ² у netmeta. Видети Harrer et al. (2022), погл. 12, стр. 352–353, и Dias et al. (2010), DOI 10.1002/sim.3767; проверите и наведите изворе. p неслагања не проверава транзитивност; испитајте модификаторе ефекта.",
    "직접·간접 대비 분할은 직접 연구 블록 전체를 간접 네트워크에서 제외합니다. 무작위 효과는 분할별 REML τ²를 추정하며 netmeta의 공통 τ²와 다릅니다. Harrer et al. (2022) 제12장 pp.352–353과 Dias et al. (2010), DOI 10.1002/sim.3767을 확인하고 방법·보고서를 인용하세요. 불일치 p값은 추이성을 평가하지 않으므로 효과 수정 변수를 확인하세요."
  ],
  [
    "，第 17.1 节仅提供剂量分析背景。本模块需要正确尺度上的协方差矩阵，并拟合两阶段线性斜率；这些书籍并未校准这一确切组合。",
    ", §17.1 provides dose-analysis background only. This module requires a covariance matrix on the correct scale and fits a two-stage linear slope; the books do not calibrate this exact combination.",
    ", §17.1 ne fournit qu’un contexte sur l’analyse de dose. Ce module exige une covariance sur l’échelle correcte et ajuste une pente linéaire en deux étapes ; les ouvrages ne calibrent pas cette combinaison exacte.",
    ", §17.1 даёт лишь контекст дозового анализа. Модуль требует ковариацию в правильной шкале и оценивает двухэтапный линейный наклон; книги не калибруют эту точную комбинацию.",
    ", §17.1 solo ofrece contexto del análisis de dosis. El módulo exige covarianza en la escala correcta y ajusta una pendiente lineal en dos etapas; los libros no calibran esta combinación exacta.",
    "、§17.1は用量分析の背景説明のみです。このモジュールは正しい尺度の共分散行列を必要とし、2段階の線形傾きを推定します。書籍はこの厳密な組合せを校正したものではありません。",
    ", §17.1 oferece apenas contexto de análise de dose. O módulo exige covariância na escala correta e ajusta uma inclinação linear em duas etapas; os livros não calibram esta combinação exata.",
    ", §17.1 liefert nur Hintergrund zur Dosisanalyse. Das Modul benötigt eine Kovarianzmatrix auf der richtigen Skala und passt eine zweistufige lineare Steigung an; die Bücher kalibrieren diese konkrete Kombination nicht.",
    ", §17.1 пружа само контекст анализе дозе. Модул захтева коваријансу на исправној скали и прилагођава двостепени линеарни нагиб; књиге не калибришу ову тачну комбинацију.",
    ", §17.1은 용량 분석의 배경만 제공합니다. 이 모듈은 올바른 척도의 공분산 행렬이 필요하고 2단계 선형 기울기를 적합합니다. 책은 이 정확한 조합을 보정한 근거가 아닙니다."
  ],
  [
    "使用当前分析的比较组、结局、时间点和效应指标，以及当前评价者保存的整体判断。选择要排除的标签；完整与限制样本使用相同模型。每项研究存在多个已保存结果变体时，尚无法明确对应偏倚风险评价，故拒绝分析。",
    "Uses the current analysis comparison, outcome, time point and measure, plus this reviewer's recorded overall judgments. Choose labels to exclude; the original and restricted analyses use the same model. Results with multiple saved variants per study cannot yet be linked unambiguously to a RoB assessment and are rejected.",
    "Utilise la comparaison, le résultat, le temps et la mesure actuels, ainsi que les jugements globaux enregistrés par cet évaluateur. Choisissez les niveaux à exclure ; l’analyse complète et restreinte utilisent le même modèle. Plusieurs variantes sauvegardées par étude ne peuvent pas encore être reliées sans ambiguïté à une évaluation de biais et sont rejetées.",
    "Используются текущие сравнение, исход, срок, мера и общие оценки этого рецензента. Выберите исключаемые метки; полный и ограниченный анализы используют одну модель. Несколько сохранённых вариантов на исследование пока нельзя однозначно связать с оценкой риска смещения — они отклоняются.",
    "Usa comparación, resultado, momento, medida actuales y juicios globales de este revisor. Elija etiquetas a excluir; análisis completo y restringido usan el mismo modelo. Varias versiones por estudio no pueden vincularse inequívocamente a una evaluación de sesgo y se rechazan.",
    "現在の比較・アウトカム・時点・指標と、この担当者が保存した総合判定を使います。除外する判定を選び、全件・制限後で同じモデルを使います。1研究に複数の保存済み結果があるとバイアス評価と一意に対応できないため拒否します。",
    "Usa comparação, desfecho, momento, medida atuais e julgamentos globais deste revisor. Escolha rótulos a excluir; análises completa e restrita usam o mesmo modelo. Várias versões por estudo ainda não podem ser ligadas inequivocamente à avaliação de viés e são rejeitadas.",
    "Nutzt aktuellen Vergleich, Ergebnis, Zeitpunkt, Effektmaß und gespeicherte Gesamturteile dieser prüfenden Person. Wählen Sie auszuschließende Kategorien; vollständige und eingeschränkte Analyse nutzen dasselbe Modell. Mehrere gespeicherte Varianten je Studie lassen sich noch nicht eindeutig einer Bias-Bewertung zuordnen und werden abgelehnt.",
    "Користе се тренутно поређење, исход, време, мера и укупне процене овог рецензента. Изаберите ознаке за искључење; потпуна и ограничена анализа користе исти модел. Више сачуваних варијанти по студији још не може једнозначно да се повеже са проценом пристрасности и одбацује се.",
    "현재 비교, 결과, 시점, 지표와 해당 검토자가 저장한 전체 판단을 사용합니다. 제외할 판단을 선택하며 전체·제한 분석에 같은 모형을 사용합니다. 연구당 여러 저장 결과는 편향 평가와 명확히 연결할 수 없어 거부합니다."
  ],
  [
    "偏倚风险判断不是数值化质量权重。偏倚评价与敏感性分析请核对 Cochrane Handbook 第 8、10 章；引用评价框架、合并方法和研究报告。排除研究会改变分析样本，不能据此确证偏倚导致了结果。",
    "Risk-of-bias judgments are not numeric quality weights. See Cochrane Handbook ch. 8 and ch. 10 for risk-of-bias assessment and sensitivity analysis; verify the original guidance and cite the assessment framework, synthesis method and study reports. Exclusion changes the analysis set and cannot establish that bias caused a result.",
    "Les jugements de risque de biais ne sont pas des poids numériques de qualité. Consultez Cochrane Handbook, ch. 8 et 10 ; vérifiez et citez le cadre d’évaluation, la méthode de synthèse et les rapports. Exclure change l’ensemble analysé, sans démontrer que le biais a causé un résultat.",
    "Оценки риска смещения не являются числовыми весами качества. См. Cochrane Handbook, гл. 8 и 10; проверьте и цитируйте систему оценки, метод синтеза и отчёты. Исключение меняет набор данных, но не доказывает, что результат вызван смещением.",
    "Los juicios de sesgo no son pesos numéricos de calidad. Consulte Cochrane Handbook, caps. 8 y 10; verifique y cite marco, método e informes. Excluir cambia el conjunto analizado y no demuestra que el sesgo causó un resultado.",
    "バイアスリスクの判定は数値的品質重みではありません。Cochrane Handbook第8・10章を確認し、評価枠組み、統合手法、研究報告を引用してください。除外は分析集合を変えるだけで、バイアスが結果の原因だったことを立証しません。",
    "Julgamentos de viés não são pesos numéricos de qualidade. Consulte Cochrane Handbook, caps. 8 e 10; confira e cite estrutura, método e relatórios. Excluir muda o conjunto analisado e não prova que o viés causou um resultado.",
    "Bias-Urteile sind keine numerischen Qualitätsgewichte. Siehe Cochrane Handbook, Kap. 8 und 10; prüfen und zitieren Sie Bewertungsrahmen, Synthesemethode und Studienberichte. Ausschlüsse verändern die Analysestichprobe und belegen nicht, dass Bias ein Ergebnis verursacht hat.",
    "Процене ризика пристрасности нису бројчане тежине квалитета. Видети Cochrane Handbook, погл. 8 и 10; проверите и наведите оквир, методу и извештаје. Искључење мења скуп анализе и не доказује да је пристрасност изазвала резултат.",
    "편향 위험 판단은 수치적 품질 가중치가 아닙니다. Cochrane Handbook 제8·10장을 확인하고 평가 체계, 통합 방법 및 보고서를 인용하세요. 제외는 분석 집합을 바꾸며 편향이 결과를 일으켰다는 증거는 아닙니다."
  ],
  [
    "似然比须结合具体场景的检验前概率，才能计算检验后概率（Fagan 列线图／贝叶斯公式）。本面板仅报告比值。请核对 Cochrane DTA Handbook 1.0 版（2010）第 10 章，第 10.2.3.3、10.4.2、10.5.2 节，并在发表时引用方法。",
    "Likelihood ratios require a setting-specific pre-test probability to calculate post-test probability (Fagan nomogram / Bayes). This panel reports ratios only. Check Cochrane DTA Handbook v1.0 (2010), ch. 10, §§10.2.3.3, 10.4.2 and 10.5.2; verify the original methods and cite them when publishing.",
    "Calculer une probabilité post-test à partir des LR exige une probabilité pré-test propre au contexte (nomogramme de Fagan / Bayes). Ce panneau ne rapporte que les ratios. Consultez Cochrane DTA Handbook v1.0 (2010), ch. 10, §§10.2.3.3, 10.4.2, 10.5.2 ; vérifiez et citez les méthodes originales.",
    "Для расчёта послетестовой вероятности по LR нужна дотестовая вероятность конкретной ситуации (номограмма Фагана / Байес). Панель сообщает только отношения. См. Cochrane DTA Handbook v1.0 (2010), гл. 10, §§10.2.3.3, 10.4.2, 10.5.2; проверьте и цитируйте оригинальные методы.",
    "Calcular probabilidad postest con LR exige probabilidad pretest específica del contexto (nomograma de Fagan / Bayes). El panel informa solo razones. Consulte Cochrane DTA Handbook v1.0 (2010), cap. 10, §§10.2.3.3, 10.4.2 y 10.5.2; verifique y cite métodos originales.",
    "尤度比から検査後確率を計算するには、状況に応じた検査前確率が必要です（Faganノモグラム／Bayes）。ここでは比だけを報告します。Cochrane DTA Handbook v1.0 (2010)第10章§§10.2.3.3、10.4.2、10.5.2を確認し、原手法を引用してください。",
    "Calcular probabilidade pós-teste com LR exige probabilidade pré-teste específica do contexto (nomograma de Fagan / Bayes). O painel relata apenas razões. Consulte Cochrane DTA Handbook v1.0 (2010), cap. 10, §§10.2.3.3, 10.4.2 e 10.5.2; confira e cite métodos originais.",
    "Zur Berechnung der Nachtestwahrscheinlichkeit aus LR ist eine kontextspezifische Vortestwahrscheinlichkeit nötig (Fagan-Nomogramm / Bayes). Diese Ansicht berichtet nur Quotienten. Siehe Cochrane DTA Handbook v1.0 (2010), Kap. 10, §§10.2.3.3, 10.4.2 und 10.5.2; prüfen und zitieren Sie Originalmethoden.",
    "За израчунавање вероватноће после теста из LR потребна је контекстуална вероватноћа пре теста (Фаганов номограм / Бајес). Панел приказује само количнике. Видети Cochrane DTA Handbook v1.0 (2010), погл. 10, §§10.2.3.3, 10.4.2 и 10.5.2; проверите и наведите оригиналне методе.",
    "LR로 검사 후 확률을 계산하려면 상황별 검사 전 확률이 필요합니다(Fagan 노모그램 / Bayes). 이 패널은 비율만 보고합니다. Cochrane DTA Handbook v1.0 (2010) 제10장 §§10.2.3.3, 10.4.2, 10.5.2를 확인하고 원 방법을 인용하세요."
  ],
  [
    "，第 3.2.1–3.2.2 节（59–61 页）及第 4.2.6 节（132–134 页）。由原文区间换算标准误，请核对",
    ", §§3.2.1–3.2.2 (pp. 59–61) and §4.2.6 (pp. 132–134). For reported CI to SE conversion, check",
    ", §§3.2.1–3.2.2 (p. 59–61) et §4.2.6 (p. 132–134). Pour convertir un intervalle publié en erreur-type, consultez",
    ", §§3.2.1–3.2.2 (с. 59–61) и §4.2.6 (с. 132–134). Для преобразования указанного доверительного интервала в стандартную ошибку см.",
    ", §§3.2.1–3.2.2 (pp. 59–61) y §4.2.6 (pp. 132–134). Para convertir un intervalo publicado a error estándar, consulte",
    "、§§3.2.1–3.2.2（pp.59–61）と§4.2.6（pp.132–134）。報告区間から標準誤差への変換は次を確認：",
    ", §§3.2.1–3.2.2 (pp. 59–61) e §4.2.6 (pp. 132–134). Para converter intervalo publicado em erro padrão, consulte",
    ", §§3.2.1–3.2.2 (S. 59–61) und §4.2.6 (S. 132–134). Zur Umrechnung berichteter Konfidenzintervalle in Standardfehler siehe",
    ", §§3.2.1–3.2.2 (стр. 59–61) и §4.2.6 (стр. 132–134). За претварање пријављеног интервала поверења у стандардну грешку погледајте",
    ", §§3.2.1–3.2.2(pp.59–61) 및 §4.2.6(pp.132–134). 보고된 구간의 표준오차 변환은 다음을 확인하세요:"
  ],
  [
    "研究影响诊断逐研究删除并重新拟合所选模型。请核对 Viechtbauer 与 Cheung（2010），DOI 10.1002/jrsm.11，以及 Harrer 等（2022）",
    "Study influence uses whole-study deletion and refits the selected model. Check Viechtbauer & Cheung (2010), DOI 10.1002/jrsm.11, and Harrer et al. (2022),",
    "L’influence d’une étude est évaluée en la supprimant entièrement puis en réajustant le modèle sélectionné. Consultez Viechtbauer & Cheung (2010), DOI 10.1002/jrsm.11, et Harrer et al. (2022),",
    "Влияние исследования оценивается его полным удалением и переоценкой выбранной модели. См. Viechtbauer & Cheung (2010), DOI 10.1002/jrsm.11, и Harrer et al. (2022),",
    "La influencia se evalúa retirando el estudio completo y reajustando el modelo elegido. Consulte Viechtbauer & Cheung (2010), DOI 10.1002/jrsm.11, y Harrer et al. (2022),",
    "研究全体を除外し選択モデルを再推定して影響を調べます。Viechtbauer & Cheung (2010), DOI 10.1002/jrsm.11とHarrer et al. (2022)を参照：",
    "A influência é avaliada removendo o estudo inteiro e reajustando o modelo escolhido. Consulte Viechtbauer & Cheung (2010), DOI 10.1002/jrsm.11, e Harrer et al. (2022),",
    "Der Studieneinfluss wird durch vollständiges Entfernen einer Studie und Neuanpassung des gewählten Modells bestimmt. Siehe Viechtbauer & Cheung (2010), DOI 10.1002/jrsm.11, und Harrer et al. (2022),",
    "Утицај се процењује уклањањем целе студије и поновним прилагођавањем изабраног модела. Видети Viechtbauer & Cheung (2010), DOI 10.1002/jrsm.11, и Harrer et al. (2022),",
    "연구 전체를 제외하고 선택한 모형을 다시 적합하여 영향을 확인합니다. Viechtbauer & Cheung (2010), DOI 10.1002/jrsm.11과 Harrer et al. (2022) 참조:"
  ],
  [
    "，第 5.4.2 节，第 156–162 页。诊断值较大并不自动构成排除研究的理由；发表前请核对方法和原始报告。",
    ", §5.4.2, pp. 156–162. A large diagnostic does not automatically justify excluding a study; verify method and source reports before publication.",
    ", §5.4.2, p. 156–162. Une grande valeur ne justifie pas automatiquement l’exclusion ; vérifiez méthode et rapports avant publication.",
    ", §5.4.2, с. 156–162. Большое значение не оправдывает автоматическое исключение; проверьте метод и отчёты до публикации.",
    ", §5.4.2, pp. 156–162. Un valor alto no justifica automáticamente excluir; verifique método e informes antes de publicar.",
    "、§5.4.2、pp.156–162。大きな診断値だけで自動除外せず、出版前に手法と研究報告を確認してください。",
    ", §5.4.2, pp. 156–162. Um valor alto não justifica exclusão automática; verifique método e relatórios antes de publicar.",
    ", §5.4.2, S. 156–162. Hohe Werte rechtfertigen keinen automatischen Ausschluss; prüfen Sie Methode und Studienberichte vor Veröffentlichung.",
    ", §5.4.2, стр. 156–162. Висока вредност не оправдава аутоматско искључење; проверите методу и извештаје пре објављивања.",
    ", §5.4.2, pp.156–162. 진단값이 크다고 자동 제외하지 말고 출판 전에 방법과 보고서를 확인하세요."
  ],
  [
    "基础效应量与逆方差合并：Harrer 等（2022）",
    "Core effect sizes and inverse-variance synthesis: Harrer et al. (2022),",
    "Effets de base et synthèse par variance inverse : Harrer et al. (2022),",
    "Основные меры эффекта и синтез с обратной дисперсией: Harrer et al. (2022),",
    "Efectos básicos y síntesis de varianza inversa: Harrer et al. (2022),",
    "基本効果量と逆分散統合：Harrer et al. (2022)、",
    "Efeitos básicos e síntese por variância inversa: Harrer et al. (2022),",
    "Grundlegende Effektmaße und inverse Varianz-Synthese: Harrer et al. (2022),",
    "Основне мере ефекта и синтеза методом инверзне варијансе: Harrer et al. (2022),",
    "기본 효과크기와 역분산 통합: Harrer et al. (2022),"
  ],
  [
    "，第 2 版第 11–14 章及第 17 章。SMD 须核对已保存的标准化方式：Hedges g 使用合并标准差及小样本校正（Hedges，1981，DOI 10.3102/10769986006002107）；Glass Δ 使用对照组标准差（Harrer 等，第 4.2.2 节，112–114 页）。随机效应方差估计请引用实际采用的方法：DerSimonian 与 Laird（1986），DOI 10.1016/0197-2456(86)90046-2；Paule 与 Mandel（1982）",
    ", 2nd ed., ch. 11–14, 17. For SMD, check the saved standardizer: Hedges g uses pooled SD and a small-sample correction (Hedges 1981, DOI 10.3102/10769986006002107); Glass Δ uses the control-arm SD (Harrer et al., §4.2.2, pp. 112–114). Cite the selected random-effects estimator: DerSimonian & Laird (1986), DOI 10.1016/0197-2456(86)90046-2; Paule & Mandel (1982),",
    ", 2e éd., ch. 11–14, 17. Pour SMD, vérifiez le standardiseur enregistré : g de Hedges utilise l’écart-type combiné et une correction de petit échantillon (Hedges 1981, DOI 10.3102/10769986006002107) ; Δ de Glass utilise l’écart-type témoin (Harrer, §4.2.2, p. 112–114). Citez l’estimateur aléatoire choisi : DerSimonian & Laird (1986), DOI 10.1016/0197-2456(86)90046-2 ; Paule & Mandel (1982),",
    ", 2-е изд., гл. 11–14, 17. Для SMD проверьте стандартизатор: g Хеджеса использует объединённое SD с поправкой малого объёма (Hedges 1981, DOI 10.3102/10769986006002107), Δ Glass — SD контроля (Harrer, §4.2.2, с. 112–114). Цитируйте выбранный оцениватель: DerSimonian & Laird (1986), DOI 10.1016/0197-2456(86)90046-2; Paule & Mandel (1982),",
    ", 2.ª ed., caps. 11–14, 17. Para SMD, revise el estandarizador guardado: g de Hedges usa DE combinada con corrección de muestra pequeña (Hedges 1981, DOI 10.3102/10769986006002107); Δ de Glass usa DE del control (Harrer, §4.2.2, pp. 112–114). Cite el estimador aleatorio elegido: DerSimonian & Laird (1986), DOI 10.1016/0197-2456(86)90046-2; Paule & Mandel (1982),",
    "、第2版第11～14・17章。SMDでは保存した標準化基準を確認してください。Hedgesのgは併合SDと小標本補正（Hedges 1981, DOI 10.3102/10769986006002107）、GlassのΔは対照群SD（Harrer §4.2.2、pp.112–114）を使います。選択した変量効果推定法を引用：DerSimonian & Laird (1986), DOI 10.1016/0197-2456(86)90046-2、Paule & Mandel (1982)、",
    ", 2ª ed., caps. 11–14, 17. Para SMD, confira o padronizador: g de Hedges usa DP combinado e correção de amostra pequena (Hedges 1981, DOI 10.3102/10769986006002107); Δ de Glass usa DP do controle (Harrer, §4.2.2, pp. 112–114). Cite o estimador aleatório escolhido: DerSimonian & Laird (1986), DOI 10.1016/0197-2456(86)90046-2; Paule & Mandel (1982),",
    ", 2. Aufl., Kap. 11–14, 17. Prüfen Sie bei SMD den Standardisierer: Hedges g nutzt gepoolte SD und Kleinstichprobenkorrektur (Hedges 1981, DOI 10.3102/10769986006002107); Glass Δ die Kontrollgruppen-SD (Harrer, §4.2.2, S. 112–114). Zitieren Sie den gewählten Schätzer: DerSimonian & Laird (1986), DOI 10.1016/0197-2456(86)90046-2; Paule & Mandel (1982),",
    ", 2. изд., погл. 11–14, 17. За SMD проверите стандардизатор: Хеџесово g користи обједињено SD и корекцију малог узорка (Hedges 1981, DOI 10.3102/10769986006002107), а Glass Δ SD контроле (Harrer, §4.2.2, стр. 112–114). Наведите изабрану оцену: DerSimonian & Laird (1986), DOI 10.1016/0197-2456(86)90046-2; Paule & Mandel (1982),",
    ", 제2판 제11~14·17장. SMD의 저장된 표준화 기준을 확인하세요. Hedges g는 통합 SD와 소표본 보정(Hedges 1981, DOI 10.3102/10769986006002107), Glass Δ는 대조군 SD(Harrer §4.2.2, pp.112–114)를 사용합니다. 선택한 무작위 효과 추정법을 인용하세요: DerSimonian & Laird (1986), DOI 10.1016/0197-2456(86)90046-2; Paule & Mandel (1982),"
  ],
  [
    "87:377–385；或 REML，见 Harrer 等第 4.1.2.1 节，102–103 页、",
    "87:377–385; or REML as described by Harrer et al., §4.1.2.1, pp. 102–103,",
    "87:377–385 ; ou REML décrit dans Harrer, §4.1.2.1, p. 102–103,",
    "87:377–385; или REML по Harrer, §4.1.2.1, с. 102–103,",
    "87:377–385; o REML descrito en Harrer, §4.1.2.1, pp. 102–103,",
    "87:377–385、またはHarrer §4.1.2.1、pp.102–103のREML、",
    "87:377–385; ou REML descrito em Harrer, §4.1.2.1, pp. 102–103,",
    "87:377–385; oder REML nach Harrer, §4.1.2.1, S. 102–103,",
    "87:377–385; или REML према Harrer, §4.1.2.1, стр. 102–103,",
    "87:377–385; 또는 Harrer §4.1.2.1, pp.102–103의 REML,"
  ],
  [
    "以及 Cochrane Handbook 第 10 章。修正 HKSJ 见 Harrer 第 4 章及 Knapp 与 Hartung（2003），DOI 10.1002/sim.1482。预测区间采用 Borenstein 第 17 章的 k−2 自由度约定。请核对原文，并仅引用实际使用的方法。",
    ", and the Cochrane Handbook, ch. 10. For modified HKSJ, see Harrer ch. 4 and Knapp & Hartung (2003), DOI 10.1002/sim.1482. The prediction interval uses Borenstein ch. 17's k−2 degrees of freedom convention. Check the original texts and cite the methods actually used.",
    ", et Cochrane Handbook, ch. 10. Pour HKSJ modifié : Harrer ch. 4 et Knapp & Hartung (2003), DOI 10.1002/sim.1482. L’intervalle de prédiction suit les k−2 degrés de liberté de Borenstein ch. 17. Vérifiez les originaux et citez les méthodes utilisées.",
    ", и Cochrane Handbook, гл. 10. Модифицированный HKSJ: Harrer гл. 4 и Knapp & Hartung (2003), DOI 10.1002/sim.1482. Предиктивный интервал использует k−2 степени свободы по Borenstein гл. 17. Проверьте оригиналы и цитируйте использованные методы.",
    ", y Cochrane Handbook, cap. 10. HKSJ modificado: Harrer cap. 4 y Knapp & Hartung (2003), DOI 10.1002/sim.1482. El intervalo predictivo usa k−2 grados de libertad de Borenstein cap. 17. Verifique los originales y cite los métodos usados.",
    "、およびCochrane Handbook第10章。修正HKSJはHarrer第4章とKnapp & Hartung (2003), DOI 10.1002/sim.1482を参照します。予測区間はBorenstein第17章の自由度k−2に従います。原典を確認し実際の手法を引用してください。",
    ", e Cochrane Handbook, cap. 10. HKSJ modificado: Harrer cap. 4 e Knapp & Hartung (2003), DOI 10.1002/sim.1482. O intervalo de predição usa k−2 graus de liberdade de Borenstein cap. 17. Verifique os originais e cite os métodos utilizados.",
    ", und Cochrane Handbook, Kap. 10. Modifiziertes HKSJ: Harrer Kap. 4 und Knapp & Hartung (2003), DOI 10.1002/sim.1482. Das Vorhersageintervall folgt k−2 Freiheitsgraden nach Borenstein Kap. 17. Prüfen Sie Originale und zitieren Sie verwendete Methoden.",
    ", и Cochrane Handbook, погл. 10. Измењени HKSJ: Harrer погл. 4 и Knapp & Hartung (2003), DOI 10.1002/sim.1482. Интервал предвиђања користи k−2 степена слободе према Borenstein погл. 17. Проверите оригинале и наведите коришћене методе.",
    ", 및 Cochrane Handbook 제10장. 수정 HKSJ는 Harrer 제4장과 Knapp & Hartung (2003), DOI 10.1002/sim.1482를 참조합니다. 예측 구간은 Borenstein 제17장의 자유도 k−2를 따릅니다. 원문을 확인하고 사용한 방법을 인용하세요."
  ],
  [
    "漏斗图与 Egger 选项请参见 Harrer 等（2022）",
    "For funnel-plot and Egger options: Harrer et al. (2022),",
    "Pour les graphiques en entonnoir et les options Egger : Harrer et al. (2022),",
    "Для воронкообразных графиков и опций Эггера: Harrer et al. (2022),",
    "Para gráficos de embudo y opciones de Egger: Harrer et al. (2022),",
    "ファンネルプロットとEggerの選択肢：Harrer et al. (2022)、",
    "Para gráficos de funil e opções de Egger: Harrer et al. (2022),",
    "Zu Trichterdiagrammen und Egger-Optionen: Harrer et al. (2022),",
    "За левкасте графиконе и Егерове опције: Harrer et al. (2022),",
    "깔때기 그림 및 Egger 옵션: Harrer et al. (2022),"
  ],
  [
    "，第 9 章，第 242–244 页；Egger 等（1997），DOI 10.1136/bmj.315.7109.629。请引用实际使用的方法。",
    ", ch. 9, pp. 242–244; Egger et al. (1997), DOI 10.1136/bmj.315.7109.629. Cite the method actually used.",
    ", ch. 9, p. 242–244 ; Egger et al. (1997), DOI 10.1136/bmj.315.7109.629. Citez la méthode réellement utilisée.",
    ", гл. 9, с. 242–244; Egger et al. (1997), DOI 10.1136/bmj.315.7109.629. Цитируйте фактически использованный метод.",
    ", cap. 9, pp. 242–244; Egger et al. (1997), DOI 10.1136/bmj.315.7109.629. Cite el método usado.",
    "、第9章pp.242–244、Egger et al. (1997), DOI 10.1136/bmj.315.7109.629。実際の手法を引用してください。",
    ", cap. 9, pp. 242–244; Egger et al. (1997), DOI 10.1136/bmj.315.7109.629. Cite o método utilizado.",
    ", Kap. 9, S. 242–244; Egger et al. (1997), DOI 10.1136/bmj.315.7109.629. Zitieren Sie die verwendete Methode.",
    ", погл. 9, стр. 242–244; Egger et al. (1997), DOI 10.1136/bmj.315.7109.629. Наведите коришћену методу.",
    ", 제9장 pp.242–244; Egger et al. (1997), DOI 10.1136/bmj.315.7109.629. 실제 사용한 방법을 인용하세요."
  ],
  [
    "，第 9 章，第 242–244 页。原始方法：Begg 与 Mazumdar（1994），DOI 10.2307/2533446；Rosenthal（1979），DOI 10.1037/0033-2909.86.3.638；Orwin（1983），DOI 10.3102/10769986008002157；Duval 与 Tweedie（2000），DOI 10.1111/j.0006-341X.2000.00455.x。仅引用实际使用的方法。剪补法属于敏感性分析。",
    ", ch. 9, pp. 242–244. Original methods: Begg & Mazumdar (1994), DOI 10.2307/2533446; Rosenthal (1979), DOI 10.1037/0033-2909.86.3.638; Orwin (1983), DOI 10.3102/10769986008002157; Duval & Tweedie (2000), DOI 10.1111/j.0006-341X.2000.00455.x. Cite only methods actually used. Trim-and-fill is a sensitivity analysis.",
    ", ch. 9, p. 242–244. Méthodes originales : Begg & Mazumdar (1994), DOI 10.2307/2533446 ; Rosenthal (1979), DOI 10.1037/0033-2909.86.3.638 ; Orwin (1983), DOI 10.3102/10769986008002157 ; Duval & Tweedie (2000), DOI 10.1111/j.0006-341X.2000.00455.x. Citez seulement les méthodes utilisées. Trim-and-fill est une analyse de sensibilité.",
    ", гл. 9, с. 242–244. Оригинальные методы: Begg & Mazumdar (1994), DOI 10.2307/2533446; Rosenthal (1979), DOI 10.1037/0033-2909.86.3.638; Orwin (1983), DOI 10.3102/10769986008002157; Duval & Tweedie (2000), DOI 10.1111/j.0006-341X.2000.00455.x. Цитируйте только использованные методы. Trim-and-fill — анализ чувствительности.",
    ", cap. 9, pp. 242–244. Métodos originales: Begg & Mazumdar (1994), DOI 10.2307/2533446; Rosenthal (1979), DOI 10.1037/0033-2909.86.3.638; Orwin (1983), DOI 10.3102/10769986008002157; Duval & Tweedie (2000), DOI 10.1111/j.0006-341X.2000.00455.x. Cite solo métodos usados. Trim-and-fill es un análisis de sensibilidad.",
    "、第9章pp.242–244。原手法：Begg & Mazumdar (1994), DOI 10.2307/2533446、Rosenthal (1979), DOI 10.1037/0033-2909.86.3.638、Orwin (1983), DOI 10.3102/10769986008002157、Duval & Tweedie (2000), DOI 10.1111/j.0006-341X.2000.00455.x。使用した手法のみを引用してください。trim-and-fillは感度分析です。",
    ", cap. 9, pp. 242–244. Métodos originais: Begg & Mazumdar (1994), DOI 10.2307/2533446; Rosenthal (1979), DOI 10.1037/0033-2909.86.3.638; Orwin (1983), DOI 10.3102/10769986008002157; Duval & Tweedie (2000), DOI 10.1111/j.0006-341X.2000.00455.x. Cite apenas métodos usados. Trim-and-fill é análise de sensibilidade.",
    ", Kap. 9, S. 242–244. Originalmethoden: Begg & Mazumdar (1994), DOI 10.2307/2533446; Rosenthal (1979), DOI 10.1037/0033-2909.86.3.638; Orwin (1983), DOI 10.3102/10769986008002157; Duval & Tweedie (2000), DOI 10.1111/j.0006-341X.2000.00455.x. Zitieren Sie nur verwendete Methoden. Trim-and-fill ist eine Sensitivitätsanalyse.",
    ", погл. 9, стр. 242–244. Изворне методе: Begg & Mazumdar (1994), DOI 10.2307/2533446; Rosenthal (1979), DOI 10.1037/0033-2909.86.3.638; Orwin (1983), DOI 10.3102/10769986008002157; Duval & Tweedie (2000), DOI 10.1111/j.0006-341X.2000.00455.x. Наведите само коришћене методе. Trim-and-fill је анализа осетљивости.",
    ", 제9장 pp.242–244. 원 방법: Begg & Mazumdar (1994), DOI 10.2307/2533446; Rosenthal (1979), DOI 10.1037/0033-2909.86.3.638; Orwin (1983), DOI 10.3102/10769986008002157; Duval & Tweedie (2000), DOI 10.1111/j.0006-341X.2000.00455.x. 사용한 방법만 인용하세요. trim-and-fill은 민감도 분석입니다."
  ],
  [
    "相关线性剂量趋势的 GLS 见 Greenland 与 Longnecker（1992），DOI 10.1093/oxfordjournals.aje.a116237。Cooper 等（2019）",
    "For GLS of correlated linear dose trends, see Greenland & Longnecker (1992), DOI 10.1093/oxfordjournals.aje.a116237. Cooper et al. (2019),",
    "Pour GLS des tendances de dose linéaires corrélées, voir Greenland & Longnecker (1992), DOI 10.1093/oxfordjournals.aje.a116237. Cooper et al. (2019),",
    "Для GLS коррелированных линейных дозовых трендов см. Greenland & Longnecker (1992), DOI 10.1093/oxfordjournals.aje.a116237. Cooper et al. (2019),",
    "Para GLS de tendencias lineales de dosis correlacionadas, vea Greenland & Longnecker (1992), DOI 10.1093/oxfordjournals.aje.a116237. Cooper et al. (2019),",
    "相関のある線形用量傾向のGLSはGreenland & Longnecker (1992), DOI 10.1093/oxfordjournals.aje.a116237を参照。Cooper et al. (2019)、",
    "Para GLS de tendências lineares de dose correlacionadas, veja Greenland & Longnecker (1992), DOI 10.1093/oxfordjournals.aje.a116237. Cooper et al. (2019),",
    "Zu GLS korrelierter linearer Dosistrends siehe Greenland & Longnecker (1992), DOI 10.1093/oxfordjournals.aje.a116237. Cooper et al. (2019),",
    "За GLS корелисаних линеарних трендова дозе видети Greenland & Longnecker (1992), DOI 10.1093/oxfordjournals.aje.a116237. Cooper et al. (2019),",
    "상관된 선형 용량 추세의 GLS는 Greenland & Longnecker (1992), DOI 10.1093/oxfordjournals.aje.a116237 참조. Cooper et al. (2019),"
  ],
  [
    "，第 3 版第 13 章，第 282–284 页讨论研究内相依效应；Grant 与 Di Tanna（2025）",
    ", 3rd ed., ch. 13, pp. 282–284 discusses dependent effects within studies; Grant & Di Tanna (2025),",
    ", 3e éd., ch. 13, p. 282–284 traite des effets dépendants au sein des études ; Grant & Di Tanna (2025),",
    ", 3-е изд., гл. 13, с. 282–284 обсуждает зависимые эффекты внутри исследований; Grant & Di Tanna (2025),",
    ", 3.ª ed., cap. 13, pp. 282–284 trata efectos dependientes dentro de estudios; Grant & Di Tanna (2025),",
    "、第3版第13章pp.282–284は研究内の従属効果を扱います。Grant & Di Tanna (2025)、",
    ", 3ª ed., cap. 13, pp. 282–284 discute efeitos dependentes dentro dos estudos; Grant & Di Tanna (2025),",
    ", 3. Aufl., Kap. 13, S. 282–284 behandelt abhängige Effekte innerhalb von Studien; Grant & Di Tanna (2025),",
    ", 3. изд., погл. 13, стр. 282–284 разматра зависне ефекте у студијама; Grant & Di Tanna (2025),",
    ", 제3판 제13장 pp.282–284는 연구 내 의존 효과를 다룹니다. Grant & Di Tanna (2025),"
  ],
  [
    "Borenstein 等（2021）《Introduction to Meta-Analysis》第 2 版第 39 章，第 351–355 页提供个体参与者数据的概念指导。请核对原文，并引用实际计算方法和研究报告。",
    "Borenstein et al. (2021), Introduction to Meta-Analysis, 2nd ed., Chapter 39, pp. 351–355 provides conceptual IPD guidance. Check the original text; cite the calculation methods and study reports used.",
    "Borenstein et al. (2021), Introduction to Meta-Analysis, 2e éd., ch. 39, p. 351–355 fournit des principes conceptuels sur les données individuelles. Vérifiez l’original ; citez les méthodes de calcul et les rapports utilisés.",
    "Borenstein et al. (2021), Introduction to Meta-Analysis, 2-е изд., гл. 39, с. 351–355 даёт концептуальные указания по индивидуальным данным. Проверьте оригинал; цитируйте расчётные методы и отчёты.",
    "Borenstein et al. (2021), Introduction to Meta-Analysis, 2.ª ed., cap. 39, pp. 351–355 ofrece orientación conceptual sobre datos individuales. Revise el original y cite métodos de cálculo e informes.",
    "Borenstein et al. (2021), Introduction to Meta-Analysis、第2版第39章pp.351–355は個人データの概念的指針です。原典を確認し、実際の計算法と研究報告を引用してください。",
    "Borenstein et al. (2021), Introduction to Meta-Analysis, 2ª ed., cap. 39, pp. 351–355 oferece orientação conceitual sobre dados individuais. Confira o original e cite métodos de cálculo e relatórios.",
    "Borenstein et al. (2021), Introduction to Meta-Analysis, 2. Aufl., Kap. 39, S. 351–355 gibt konzeptionelle Hinweise zu Individualdaten. Prüfen Sie den Originaltext und zitieren Sie verwendete Berechnungsmethoden und Studienberichte.",
    "Borenstein et al. (2021), Introduction to Meta-Analysis, 2. изд., погл. 39, стр. 351–355 пружа концептуалне смернице за индивидуалне податке. Проверите оригинал и наведите методе прорачуна и извештаје.",
    "Borenstein et al. (2021), Introduction to Meta-Analysis 제2판 제39장 pp.351–355는 개별 참여자 자료의 개념적 지침입니다. 원문을 확인하고 사용한 계산법과 연구 보고서를 인용하세요."
  ],
  [
    "。该书提供概念指导，并未验证这一具体个体参与者数据模型。",
    ". The book provides conceptual guidance but does not validate this exact IPD fit.",
    ". Le livre fournit des principes conceptuels mais ne valide pas cet ajustement précis sur données individuelles.",
    ". Книга даёт концептуальные указания, но не валидирует эту конкретную модель индивидуальных данных.",
    ". El libro ofrece orientación conceptual, pero no valida este ajuste exacto de datos individuales.",
    "。書籍は概念的指針であり、この個人データモデルの厳密な適合を検証したものではありません。",
    ". O livro oferece orientação conceitual, mas não valida este ajuste específico de dados individuais.",
    ". Das Buch gibt konzeptionelle Hinweise, validiert jedoch nicht diese konkrete Individualdaten-Anpassung.",
    ". Књига даје концептуалне смернице, али не потврђује ово конкретно прилагођавање индивидуалних података.",
    ". 책은 개념적 지침을 제공하지만 이 특정 개별자료 적합을 검증하지는 않습니다."
  ],
  [
    "，第 2 版第 39 章，第 354–355 页；Hua 等（2017），DOI 10.1002/sim.7171，说明了为何须区分研究内与跨研究的治疗交互。请核对原始方法和研究报告。",
    ", 2nd ed., ch. 39, pp. 354–355; Hua et al. (2017), DOI 10.1002/sim.7171, explains why within- and across-study treatment interactions must be separated. Verify the original methods and study reports.",
    ", 2e éd., ch. 39, p. 354–355 ; Hua et al. (2017), DOI 10.1002/sim.7171 explique la séparation des interactions de traitement intra- et interétudes. Vérifiez méthodes et rapports originaux.",
    ", 2-е изд., гл. 39, с. 354–355; Hua et al. (2017), DOI 10.1002/sim.7171 объясняет разделение взаимодействий лечения внутри и между исследованиями. Проверьте оригинальные методы и отчёты.",
    ", 2.ª ed., cap. 39, pp. 354–355; Hua et al. (2017), DOI 10.1002/sim.7171 explica separar interacciones de tratamiento intra- y entre estudios. Revise métodos e informes originales.",
    "、第2版第39章pp.354–355。Hua et al. (2017), DOI 10.1002/sim.7171は研究内・研究間の治療交互作用を分離すべき理由を説明します。原手法と研究報告を確認してください。",
    ", 2ª ed., cap. 39, pp. 354–355; Hua et al. (2017), DOI 10.1002/sim.7171 explica separar interações de tratamento intra e entre estudos. Confira métodos e relatórios originais.",
    ", 2. Aufl., Kap. 39, S. 354–355; Hua et al. (2017), DOI 10.1002/sim.7171 erklärt die Trennung von Behandlungsinteraktionen innerhalb und zwischen Studien. Prüfen Sie Originalmethoden und Studienberichte.",
    ", 2. изд., погл. 39, стр. 354–355; Hua et al. (2017), DOI 10.1002/sim.7171 објашњава раздвајање интеракција лечења унутар и између студија. Проверите оригиналне методе и извештаје.",
    ", 제2판 제39장 pp.354–355. Hua et al. (2017), DOI 10.1002/sim.7171은 연구 내·연구 간 치료 상호작용을 분리해야 하는 이유를 설명합니다. 원 방법과 보고서를 확인하세요."
  ],
  [
    "请核对 Cochrane Handbook for Systematic Reviews of Diagnostic Test Accuracy 1.0 版（2010）第 10 章，第 10.5.2.3 节，26–27 页。解释前核对符号及版本；发表时引用模型、变换和研究报告。",
    "Check the original Cochrane Handbook for Systematic Reviews of Diagnostic Test Accuracy, version 1.0 (2010), ch. 10, §10.5.2.3, pp. 26–27. Verify notation and edition before interpretation; cite the model, transformation and study reports when publishing.",
    "Consultez Cochrane Handbook for Systematic Reviews of Diagnostic Test Accuracy, v1.0 (2010), ch. 10, §10.5.2.3, p. 26–27. Vérifiez notation et édition ; citez modèle, transformation et rapports d’étude.",
    "См. Cochrane Handbook for Systematic Reviews of Diagnostic Test Accuracy, v1.0 (2010), гл. 10, §10.5.2.3, с. 26–27. Проверьте обозначения и издание; цитируйте модель, преобразование и отчёты.",
    "Consulte Cochrane Handbook for Systematic Reviews of Diagnostic Test Accuracy, v1.0 (2010), cap. 10, §10.5.2.3, pp. 26–27. Verifique notación y edición; cite modelo, transformación e informes.",
    "Cochrane Handbook for Systematic Reviews of Diagnostic Test Accuracy v1.0 (2010)、第10章§10.5.2.3、pp.26–27を確認してください。解釈前に記号と版を確認し、モデル・変換・研究報告を引用してください。",
    "Consulte Cochrane Handbook for Systematic Reviews of Diagnostic Test Accuracy, v1.0 (2010), cap. 10, §10.5.2.3, pp. 26–27. Confira notação e edição; cite modelo, transformação e relatórios.",
    "Siehe Cochrane Handbook for Systematic Reviews of Diagnostic Test Accuracy, v1.0 (2010), Kap. 10, §10.5.2.3, S. 26–27. Prüfen Sie Notation und Ausgabe; zitieren Sie Modell, Transformation und Studienberichte.",
    "Видети Cochrane Handbook for Systematic Reviews of Diagnostic Test Accuracy, v1.0 (2010), погл. 10, §10.5.2.3, стр. 26–27. Проверите ознаке и издање; наведите модел, трансформацију и извештаје.",
    "Cochrane Handbook for Systematic Reviews of Diagnostic Test Accuracy v1.0 (2010), 제10장 §10.5.2.3, pp.26–27을 확인하세요. 해석 전에 표기와 판본을 확인하고 모형, 변환법 및 연구 보고서를 인용하세요."
  ],
  [
    "此研究层面的双变量模型估计 logit 敏感度和特异度尺度上的关联；不能证明仅改变阈值就造成观察到的差异。请核对 Cochrane",
    "This bivariate study-level model estimates associations on logit sensitivity and specificity scales; it does not show that changing threshold alone causes the observed difference. Check the Cochrane",
    "Ce modèle bivarié au niveau des études estime des associations sur les échelles logit de sensibilité et spécificité ; il ne démontre pas que le seul changement de seuil cause la différence observée. Consultez le Cochrane",
    "Эта двумерная модель уровня исследований оценивает связи в логит-шкалах чувствительности и специфичности; она не доказывает, что изменение только порога вызывает различие. См. Cochrane",
    "Este modelo bivariado a nivel de estudio estima asociaciones en escalas logit de sensibilidad y especificidad; no demuestra que cambiar solo el umbral cause la diferencia. Consulte Cochrane",
    "この研究レベル二変量モデルは感度・特異度のlogit尺度上の関連を推定し、閾値変更だけが差の原因であることは示しません。Cochraneの次の文献を確認：",
    "Este modelo bivariado no nível de estudo estima associações nas escalas logit de sensibilidade e especificidade; não prova que mudar apenas o limiar cause a diferença. Consulte Cochrane",
    "Dieses bivariate Modell auf Studienebene schätzt Zusammenhänge auf Logit-Skalen der Sensitivität und Spezifität. Es belegt nicht, dass allein eine Schwellenänderung den Unterschied verursacht. Siehe Cochrane",
    "Овај биваријантни модел нивоа студије оцењује повезаности на логит скалама осетљивости и специфичности; не доказује да сама промена прага изазива разлику. Видети Cochrane",
    "이 연구 수준 이변량 모형은 민감도·특이도의 logit 척도에서 연관성을 추정하며 역치 변화만이 차이를 일으킨다는 뜻은 아닙니다. Cochrane의 다음 자료를 확인하세요:"
  ],
  [
    "，第 10 章，第 10.1.4.2 与 10.5.3.1 节，并核对原始研究报告。Borenstein 等（2021）第 49 章及 Cooper 等（2019）第 21 章仅提供诊断准确性范围或阅读线索，不是本模型的校准依据。发表时引用诊断模型和数据来源。",
    ", ch. 10, §§10.1.4.2 and 10.5.3.1, and verify the original source reports. Borenstein et al. (2021), ch. 49 and Cooper et al. (2019), ch. 21 provide DTA scope or reading leads, not calibration for this model. Cite the DTA model and data sources when publishing.",
    ", ch. 10, §§10.1.4.2 et 10.5.3.1 ; vérifiez les rapports originaux. Borenstein (2021), ch. 49 et Cooper (2019), ch. 21 fournissent le contexte DTA ou des pistes, pas une calibration de ce modèle. Citez modèle et sources.",
    ", гл. 10, §§10.1.4.2 и 10.5.3.1; проверьте исходные отчёты. Borenstein (2021), гл. 49 и Cooper (2019), гл. 21 дают контекст DTA или чтение, а не калибровку модели. Цитируйте модель и источники.",
    ", cap. 10, §§10.1.4.2 y 10.5.3.1; verifique informes originales. Borenstein (2021), cap. 49 y Cooper (2019), cap. 21 aportan contexto DTA o lecturas, no calibración del modelo. Cite modelo y fuentes.",
    "、第10章§§10.1.4.2、10.5.3.1。原研究報告も確認してください。Borenstein (2021)第49章とCooper (2019)第21章はDTAの範囲や読書案内であり、このモデルの校正ではありません。モデルとデータ出典を引用してください。",
    ", cap. 10, §§10.1.4.2 e 10.5.3.1; confira relatórios originais. Borenstein (2021), cap. 49 e Cooper (2019), cap. 21 dão contexto DTA ou leituras, não calibração do modelo. Cite modelo e fontes.",
    ", Kap. 10, §§10.1.4.2 und 10.5.3.1; prüfen Sie Originalberichte. Borenstein (2021), Kap. 49 und Cooper (2019), Kap. 21 bieten DTA-Kontext oder Lesehinweise, keine Modellkalibrierung. Zitieren Sie Modell und Datenquellen.",
    ", погл. 10, §§10.1.4.2 и 10.5.3.1; проверите изворне извештаје. Borenstein (2021), погл. 49 и Cooper (2019), погл. 21 дају DTA контекст или литературу, не калибрацију модела. Наведите модел и изворе.",
    ", 제10장 §§10.1.4.2 및 10.5.3.1과 원 연구 보고서를 확인하세요. Borenstein (2021) 제49장과 Cooper (2019) 제21장은 DTA 범위·읽기 안내이며 이 모형의 보정 근거가 아닙니다. 모형과 자료 출처를 인용하세요."
  ],
  [
    "双变量二项 logit 正态随机效应模型见 Chu 与 Cole（2006），DOI 10.1016/j.jclinepi.2006.06.011，以及 Cochrane",
    "For the bivariate binomial-logit normal random-effects model, see Chu & Cole (2006), DOI 10.1016/j.jclinepi.2006.06.011, and the Cochrane",
    "Pour le modèle bivarié binomial-logit à effets aléatoires normaux, voir Chu & Cole (2006), DOI 10.1016/j.jclinepi.2006.06.011, et le Cochrane",
    "Для двумерной биномиальной logit-модели с нормальными случайными эффектами см. Chu & Cole (2006), DOI 10.1016/j.jclinepi.2006.06.011, и Cochrane",
    "Para el modelo binomial-logit bivariado con efectos aleatorios normales, vea Chu & Cole (2006), DOI 10.1016/j.jclinepi.2006.06.011, y Cochrane",
    "二変量二項logit正規変量効果モデルはChu & Cole (2006), DOI 10.1016/j.jclinepi.2006.06.011とCochraneを参照：",
    "Para o modelo binomial-logit bivariado com efeitos aleatórios normais, veja Chu & Cole (2006), DOI 10.1016/j.jclinepi.2006.06.011, e Cochrane",
    "Zum bivariaten Binomial-Logit-Modell mit normalverteilten zufälligen Effekten siehe Chu & Cole (2006), DOI 10.1016/j.jclinepi.2006.06.011, und Cochrane",
    "За биваријантни биномни logit модел са нормалним случајним ефектима видети Chu & Cole (2006), DOI 10.1016/j.jclinepi.2006.06.011, и Cochrane",
    "정규 무작위 효과를 사용하는 이변량 이항-logit 모형은 Chu & Cole (2006), DOI 10.1016/j.jclinepi.2006.06.011과 Cochrane 참조:"
  ],
  [
    "，第 10 章。Borenstein（2021）第 49 章及 Cooper 等（2019）第 21 章仅提供范围或延伸阅读，未校准本模块的 11 点求积或 Wald 区间。",
    ", ch. 10. Borenstein (2021), ch. 49 and Cooper et al. (2019), ch. 21 provide scope or further-reading leads only; they do not calibrate this module's 11-point quadrature or Wald intervals.",
    ", ch. 10. Borenstein (2021), ch. 49 et Cooper (2019), ch. 21 offrent seulement un contexte ou d’autres lectures ; ils ne calibrent ni la quadrature à 11 points ni les intervalles de Wald de ce module.",
    ", гл. 10. Borenstein (2021), гл. 49 и Cooper (2019), гл. 21 дают только контекст или чтение; они не калибруют 11-точечную квадратуру или интервалы Вальда этого модуля.",
    ", cap. 10. Borenstein (2021), cap. 49 y Cooper (2019), cap. 21 solo ofrecen contexto o lecturas; no calibran la cuadratura de 11 puntos ni los intervalos de Wald del módulo.",
    "、第10章。Borenstein (2021)第49章とCooper (2019)第21章は範囲説明・参考文献案内のみで、このモジュールの11点求積やWald区間を校正したものではありません。",
    ", cap. 10. Borenstein (2021), cap. 49 e Cooper (2019), cap. 21 apenas oferecem contexto ou leituras; não calibram a quadratura de 11 pontos nem os intervalos de Wald do módulo.",
    ", Kap. 10. Borenstein (2021), Kap. 49 und Cooper (2019), Kap. 21 geben nur Kontext oder Lesehinweise; sie kalibrieren weder die 11-Punkt-Quadratur noch die Wald-Intervalle dieses Moduls.",
    ", погл. 10. Borenstein (2021), погл. 49 и Cooper (2019), погл. 21 дају само контекст или литературу; не калибришу квадратуру са 11 тачака или Валдове интервале модула.",
    ", 제10장. Borenstein (2021) 제49장과 Cooper (2019) 제21장은 범위·추가 읽기 안내일 뿐 이 모듈의 11점 구적법이나 Wald 구간을 보정한 근거가 아닙니다."
  ],
  [
    "，第 4.2.1/4.4.5、12.2.2、3.7.6 及 4.8.5 节。尚未核实该书是否明确给出了 μ 的 Student-t 先验。",
    ", §§4.2.1/4.4.5, 12.2.2, 3.7.6 and 4.8.5. The book's explicit Student-t prior on μ has not been verified.",
    ", §§4.2.1/4.4.5, 12.2.2, 3.7.6 et 4.8.5. Un a priori Student-t explicite sur μ dans l’ouvrage n’a pas été vérifié.",
    ", §§4.2.1/4.4.5, 12.2.2, 3.7.6 и 4.8.5. Явное априорное Student-t для μ в книге не подтверждено.",
    ", §§4.2.1/4.4.5, 12.2.2, 3.7.6 y 4.8.5. No se ha verificado un prior Student-t explícito para μ en el libro.",
    "、§§4.2.1/4.4.5、12.2.2、3.7.6、4.8.5。書籍中のμに対する明示的なStudent-t事前分布は未確認です。",
    ", §§4.2.1/4.4.5, 12.2.2, 3.7.6 e 4.8.5. Não foi verificada uma distribuição a priori Student-t explícita para μ no livro.",
    ", §§4.2.1/4.4.5, 12.2.2, 3.7.6 und 4.8.5. Eine explizite Student-t-Prior-Verteilung für μ im Buch wurde nicht verifiziert.",
    ", §§4.2.1/4.4.5, 12.2.2, 3.7.6 и 4.8.5. Експлицитна Student-t априорна расподела за μ у књизи није потврђена.",
    ", §§4.2.1/4.4.5, 12.2.2, 3.7.6 및 4.8.5. 책에 μ의 명시적 Student-t 사전분포가 있는지는 확인되지 않았습니다."
  ],
  ["此信息暂未提供当前语言译文；展开可查看原文。", "This message is not translated into the current language. Expand it to view the original.", "Ce message n’est pas traduit dans la langue actuelle. Développez-le pour voir le texte original.", "Это сообщение пока не переведено на выбранный язык. Разверните его, чтобы увидеть оригинал.", "Este mensaje aún no está traducido al idioma actual. Despliéguelo para ver el original.", "このメッセージは現在の言語に翻訳されていません。展開すると原文を確認できます。", "Esta mensagem ainda não foi traduzida para o idioma atual. Expanda-a para ver o original.", "Diese Nachricht liegt noch nicht in der aktuellen Sprache vor. Öffnen Sie sie, um den Originaltext zu sehen.", "Ова порука још није преведена на изабрани језик. Отворите је да бисте видели оригинал.", "이 메시지는 현재 언어로 아직 번역되지 않았습니다. 펼쳐서 원문을 확인하세요."],
  ["查看原始信息", "View original message", "Afficher le message original", "Показать исходное сообщение", "Ver mensaje original", "元のメッセージを表示", "Ver mensagem original", "Originalnachricht anzeigen", "Прикажи изворну поруку", "원본 메시지 보기"],
  [
    "请选择研究发现的可信度。",
    "Choose finding credibility.",
    "Choisissez le niveau de crédibilité du constat.",
    "Выберите уровень достоверности результата.",
    "Seleccione la credibilidad del hallazgo.",
    "研究知見の信頼度を選択してください。",
    "Selecione a credibilidade do achado.",
    "Wählen Sie die Glaubwürdigkeit des Befunds.",
    "Изаберите веродостојност налаза.",
    "연구 결과의 신뢰도를 선택하세요."
  ],
  [
    "已保存研究发现。",
    "Finding saved.",
    "Constat enregistré.",
    "Результат сохранён.",
    "Hallazgo guardado.",
    "研究知見を保存しました。",
    "Achado salvo.",
    "Befund gespeichert.",
    "Налаз је сачуван.",
    "연구 결과를 저장했습니다."
  ],
  [
    "已保存类别。",
    "Category saved.",
    "Catégorie enregistrée.",
    "Категория сохранена.",
    "Categoría guardada.",
    "カテゴリーを保存しました。",
    "Categoria salva.",
    "Kategorie gespeichert.",
    "Категорија је сачувана.",
    "범주를 저장했습니다."
  ],
  [
    "已保存综合发现。",
    "Synthesis saved.",
    "Synthèse enregistrée.",
    "Синтез сохранён.",
    "Síntesis guardada.",
    "統合結果を保存しました。",
    "Síntese salva.",
    "Synthese gespeichert.",
    "Синтеза је сачувана.",
    "종합 결과를 저장했습니다."
  ],

  [
    "选用",
    "Use",
    "Usage",
    "Использовать",
    "Usar",
    "使用",
    "Usar",
    "Verwendung",
    "Користи",
    "사용"
  ],
  [
    "编号", "ID", "ID", "ID", "ID", "ID", "ID", "ID", "ID", "ID"
  ],
  [
    "事件数",
    "Events",
    "Événements",
    "События",
    "Eventos",
    "イベント数",
    "Eventos",
    "Ereignisse",
    "Догађаји",
    "사건 수"
  ],
  [
    "暴露量",
    "Exposure",
    "Exposition",
    "Экспозиция",
    "Exposición",
    "曝露量",
    "Exposição",
    "Exposition",
    "Експозиција",
    "노출량"
  ],
  [
    "单位",
    "Unit",
    "Unité",
    "Единица",
    "Unidad",
    "単位",
    "Unidade",
    "Einheit",
    "Јединица",
    "단위"
  ],
  [
    "报告",
    "Report",
    "Rapport",
    "Отчёт",
    "Informe",
    "報告",
    "Relatório",
    "Bericht",
    "Извештај",
    "보고서"
  ],
  [
    "位置",
    "Location",
    "Emplacement",
    "Расположение",
    "Ubicación",
    "位置",
    "Localização",
    "Fundstelle",
    "Локација",
    "위치"
  ],
  [
    "已选择",
    "Selected",
    "Sélectionné",
    "Выбрано",
    "Seleccionado",
    "選択済み",
    "Selecionado",
    "Ausgewählt",
    "Изабрано",
    "선택됨"
  ],
  [
    "选择",
    "Select",
    "Sélectionner",
    "Выбрать",
    "Seleccionar",
    "選択",
    "Selecionar",
    "Auswählen",
    "Изабери",
    "선택"
  ],
  [
    "请选择单组计数类型。",
    "Choose a one-group count type.",
    "Choisissez un type de dénombrement à un groupe.",
    "Выберите тип одногруппового подсчёта.",
    "Elija un tipo de recuento de un solo grupo.",
    "単群カウントの種類を選択してください。",
    "Escolha um tipo de contagem de grupo único.",
    "Wählen Sie eine Art der Eingruppen-Zählung.",
    "Изаберите врсту пребројавања једне групе.",
    "단군 계수 유형을 선택하세요."
  ],
  [
    "事件数与暴露量为必填项。",
    "Events and exposure are required",
    "Les événements et l'exposition sont obligatoires.",
    "Необходимы числа событий и экспозиция.",
    "Se requieren los eventos y la exposición.",
    "イベント数と曝露量は必須です。",
    "Eventos e exposição são obrigatórios.",
    "Ereignisse und Exposition sind erforderlich.",
    "Број догађаја и експозиција су обавезни.",
    "사건 수와 노출량은 필수입니다."
  ],
  [
    "请选择偏倚风险评价框架。",
    "Choose a risk-of-bias framework.",
    "Choisissez un cadre d'évaluation du risque de biais.",
    "Выберите шкалу оценки риска смещения.",
    "Elija un marco de evaluación del riesgo de sesgo.",
    "バイアスリスク評価枠組みを選択してください。",
    "Escolha uma ferramenta de risco de viés.",
    "Wählen Sie ein Bias-Risiko-Instrument.",
    "Изаберите оквир за процену ризика пристрасности.",
    "비뚤림 위험 평가 도구를 선택하세요."
  ],
  [
    "请选择合并模型。",
    "Choose a synthesis model.",
    "Choisissez un modèle de synthèse.",
    "Выберите модель объединения.",
    "Elija un modelo de síntesis.",
    "統合モデルを選択してください。",
    "Escolha um modelo de síntese.",
    "Wählen Sie ein Synthesemodell.",
    "Изаберите модел синтезе.",
    "통합 모델을 선택하세요."
  ],
  [
    "请选择置信区间方法。",
    "Choose a confidence interval method.",
    "Choisissez une méthode d'intervalle de confiance.",
    "Выберите метод доверительного интервала.",
    "Elija un método de intervalo de confianza.",
    "信頼区間の方法を選択してください。",
    "Escolha um método de intervalo de confiança.",
    "Wählen Sie eine Konfidenzintervall-Methode.",
    "Изаберите метод интервала поверења.",
    "신뢰구간 방법을 선택하세요."
  ],
  [
    "修正 HKSJ 法需要随机效应模型。",
    "Modified HKSJ requires a random-effects model.",
    "Le HKSJ modifié nécessite un modèle à effets aléatoires.",
    "Модифицированный HKSJ требует модель случайных эффектов.",
    "El HKSJ modificado requiere un modelo de efectos aleatorios.",
    "修正 HKSJ 法にはランダム効果モデルが必要です。",
    "O HKSJ modificado requer um modelo de efeitos aleatórios.",
    "Modifiziertes HKSJ erfordert ein Modell mit zufälligen Effekten.",
    "Модификовани HKSJ захтева модел случајних ефеката.",
    "수정 HKSJ 법에는 무작위 효과 모형이 필요합니다."
  ],
  [
    "请至少选择一个要排除的总体偏倚风险判断。",
    "Choose at least one overall risk-of-bias judgment to exclude.",
    "Choisissez au moins un jugement global de risque de biais à exclure.",
    "Выберите хотя бы одно общее заключение о риске смещения для исключения.",
    "Elija al menos un juicio global de riesgo de sesgo para excluir.",
    "除外する全体的なバイアスリスク判定を少なくとも1つ選択してください。",
    "Escolha pelo menos um julgamento global de risco de viés para excluir.",
    "Wählen Sie mindestens ein Gesamturteil zum Bias-Risiko zum Ausschluss.",
    "Изаберите бар једну укупну оцену ризика пристрасности за искључење.",
    "제외할 전체 비뚤림 위험 판정을 하나 이상 선택하세요."
  ],
  [
    "已排除的边界研究（回归权重为零）：",
    "Excluded boundary studies (zero regression weight):",
    "Études situées à la frontière exclues (poids de régression nul) :",
    "Исключённые граничные исследования (нулевой регрессионный вес):",
    "Estudios situados en la frontera excluidos (peso de regresión cero):",
    "除外された境界研究（回帰重みゼロ）：",
    "Estudos na fronteira excluídos (peso de regressão zero):",
    "Ausgeschlossene Grenzstudien (Regressionsgewicht null):",
    "Искључене граничне студије (нула регресиона тежина):",
    "제외된 경계 연구(회귀 가중치 0):"
  ],
  [
    "请先在上方选择比例计数；该 Peters 变体需要事件数与总人数。",
    "Choose proportion counts above; this Peters variant requires events and total participants.",
    "Choisissez ci-dessus des dénombrements de proportion ; cette variante de Peters exige les événements et l'effectif total.",
    "Выберите выше подсчёты долей; этот вариант Питерса требует числа событий и общую численность.",
    "Elija arriba recuentos de proporción; esta variante de Peters requiere eventos y total de participantes.",
    "上で割合カウントを選択してください。この Peters 変種にはイベント数と総人数が必要です。",
    "Escolha acima contagens de proporção; esta variante de Peters exige eventos e total de participantes.",
    "Wählen Sie oben Anteilswertzählungen; diese Peters-Variante erfordert Ereignisse und Teilnehmerzahl.",
    "Изаберите горе бројеве пропорције; ова Peters варијанта захтева догађаје и укупан број учесника.",
    "위에서 비율 계수를 선택하세요. 이 Peters 변형에는 사건 수와 총 참가자 수가 필요합니다."
  ],
  [
    "请核对并引用原始方法文献。输入与参考文献已保存在本 SVG 中。",
    "Check and cite the original methods. Inputs and references are saved in this SVG.",
    "Vérifiez et citez les méthodes originales. Les entrées et références sont enregistrées dans ce SVG.",
    "Проверьте и процитируйте первоисточники методов. Входные данные и ссылки сохранены в этом SVG.",
    "Verifique y cite los métodos originales. Las entradas y referencias se guardan en este SVG.",
    "元の方法文献を確認し引用してください。入力と参考文献はこのSVGに保存されています。",
    "Verifique e cite os métodos originais. As entradas e referências são guardadas neste SVG.",
    "Prüfen und zitieren Sie die Originalmethoden. Eingaben und Referenzen sind in dieser SVG gespeichert.",
    "Проверите и наведите оригиналне методе. Уноси и референце су сачуване у овом SVG-у.",
    "원본 방법 문헌을 확인하고 인용하세요. 입력과 참고문헌은 이 SVG에 저장되어 있습니다."
  ],
  [
    "无法提供",
    "unavailable",
    "non disponible",
    "недоступно",
    "no disponible",
    "利用不可",
    "indisponível",
    "nicht verfügbar",
    "недоступно",
    "제공 불가"
  ],
  [
    "导出阈值 SVG",
    "Export threshold SVG",
    "Exporter le SVG du seuil",
    "Экспортировать пороговое SVG",
    "Exportar SVG del umbral",
    "しきい値SVGを書き出す",
    "Exportar SVG do limiar",
    "Schwellenwert-SVG exportieren",
    "Извези праг SVG",
    "임계값 SVG 내보내기"
  ],
  [
    "AI 模型",
    "AI model",
    "Modèle d’IA",
    "Модель ИИ",
    "Modelo de IA",
    "AI モデル",
    "Modelo de IA",
    "KI-Modell",
    "ИА модел",
    "AI 모델"
  ],
  [
    "AI 模型已切换：{0}",
    "AI model switched: {0}",
    "Modèle d’IA changé : {0}",
    "Модель ИИ переключена: {0}",
    "Modelo de IA cambiado: {0}",
    "AI モデルを切り替えました：{0}",
    "Modelo de IA alterado: {0}",
    "KI-Modell gewechselt: {0}",
    "ИА модел је промењен: {0}",
    "AI 모델 전환됨: {0}"
  ],
  [
    "AI 模型预设：切换需确认，已有的筛选标签不受影响",
    "AI model preset: switching requires confirmation; existing screening labels are unaffected",
    "Préréglage du modèle d’IA : le changement exige une confirmation ; les étiquettes de sélection existantes ne sont pas modifiées",
    "Пресет модели ИИ: переключение требует подтверждения; существующие метки отбора не затрагиваются",
    "Preajuste del modelo de IA: el cambio requiere confirmación; las etiquetas de cribado existentes no se ven afectadas",
    "AI モデル プリセット：切り替えには確認が必要です。既存のスクリーニングラベルには影響しません",
    "Predefinição do modelo de IA: a troca exige confirmação; as etiquetas de triagem existentes não são afetadas",
    "KI-Modellvorgabe: Der Wechsel erfordert eine Bestätigung; vorhandene Screening-Markierungen bleiben unverändert",
    "Пресет ИА модела: промена захтева потврду; постојеће ознаке скрининга остају нетакнуте",
    "AI 모델 사전 설정: 전환 시 확인이 필요하며 기존 선별 라벨은 영향을 받지 않습니다"
  ],
  [
    "暂不可用",
    "Temporarily unavailable",
    "Temporairement indisponible",
    "Временно недоступно",
    "No disponible temporalmente",
    "一時的に利用できません",
    "Temporariamente indisponível",
    "Vorübergehend nicht verfügbar",
    "Привремено недоступно",
    "일시적으로 사용할 수 없음"
  ],
  [
    "切换模型将重置 AI 排序，已有的筛选标签不受影响。",
    "Switching the model resets the AI ranking; existing screening labels are unaffected.",
    "Changer de modèle réinitialise le classement par IA ; les étiquettes de sélection existantes ne sont pas modifiées.",
    "Переключение модели сбрасывает ИИ-ранжирование; существующие метки отбора не затрагиваются.",
    "Cambiar de modelo reinicia el orden por IA; las etiquetas de cribado existentes no se ven afectadas.",
    "モデルを切り替えると AI ランキングがリセットされます。既存のスクリーニングラベルには影響しません。",
    "Trocar o modelo reinicia a ordenação por IA; as etiquetas de triagem existentes não são afetadas.",
    "Der Modellwechsel setzt die KI-Sortierung zurück; vorhandene Screening-Markierungen bleiben unverändert.",
    "Промена модела ресетује ИА рангирање; постојеће ознаке скрининга остају нетакнуте.",
    "모델을 전환하면 AI 정렬이 초기화됩니다. 기존 선별 라벨은 영향을 받지 않습니다."
  ],
  [
    "经典模式",
    "Classic mode",
    "Mode classique",
    "Классический режим",
    "Modo clásico",
    "クラシックモード",
    "Modo clássico",
    "Klassischer Modus",
    "Класични режим",
    "클래식 모드"
  ],
  [
    "优化模式(推荐)",
    "Optimized mode (recommended)",
    "Mode optimisé (recommandé)",
    "Оптимизированный режим (рекомендуется)",
    "Modo optimizado (recomendado)",
    "最適化モード（推奨）",
    "Modo otimizado (recomendado)",
    "Optimierter Modus (empfohlen)",
    "Оптимизовани режим (препоручено)",
    "최적화 모드(권장)"
  ],
  [
    "SVM 模式(最优)",
    "SVM mode (best)",
    "Mode SVM (meilleur)",
    "Режим SVM (наилучший)",
    "Modo SVM (mejor)",
    "SVM モード（最良）",
    "Modo SVM (melhor)",
    "SVM-Modus (beste)",
    "SVM режим (најбољи)",
    "SVM 모드(최상)"
  ],
  [
    "中文优化模式",
    "Chinese-optimized mode",
    "Mode optimisé pour le chinois",
    "Режим, оптимизированный для китайского",
    "Modo optimizado para chino",
    "中国語最適化モード",
    "Modo otimizado para chinês",
    "Für Chinesisch optimierter Modus",
    "Режим оптимизован за кинески",
    "중국어 최적화 모드"
  ],
  [
    "与旧版本行为完全一致。注意:低患病率(<1%)场景下性能较差(WSS 可能为负)。",
    "Behaves exactly like the previous version. Note: performance is poor at low prevalence (<1%); WSS can be negative.",
    "Strictement identique à l’ancienne version. Attention : performances médiocres à faible prévalence (<1 %) ; le WSS peut être négatif.",
    "Полностью совпадает с прежней версией. Внимание: при низкой распространённости (<1%) качество низкое; WSS может быть отрицательным.",
    "Idéntico a la versión anterior. Nota: rendimiento deficiente con prevalencia baja (<1 %); el WSS puede ser negativo.",
    "旧バージョンと完全に同じ動作です。注意：有病率が低い（<1%）場合は性能が低く、WSS が負になることがあります。",
    "Idêntico à versão anterior. Atenção: desempenho ruim em baixa prevalência (<1%); o WSS pode ser negativo.",
    "Verhält sich exakt wie die frühere Version. Hinweis: Bei niedriger Prävalenz (<1 %) schwache Leistung; WSS kann negativ sein.",
    "Потпуно идентично претходној верзији. Напомена: лоша перформанса при ниској преваленцији (<1%); WSS може бити негативан.",
    "이전 버전과 완전히 동일합니다. 유병률이 낮으면(<1%) 성능이 낮고 WSS가 음수가 될 수 있습니다."
  ],
  [
    "参数在 SYNERGY 基准优化,低患病率显著改善(WSS +0.148→+0.495)。",
    "Parameters tuned on the SYNERGY benchmark; marked improvement at low prevalence (WSS +0.148→+0.495).",
    "Paramètres optimisés sur le banc d’essai SYNERGY ; nette amélioration à faible prévalence (WSS +0,148→+0,495).",
    "Параметры настроены на бенчмарке SYNERGY; заметное улучшение при низкой распространённости (WSS +0.148→+0.495).",
    "Parámetros optimizados en el banco de pruebas SYNERGY; mejora notable con prevalencia baja (WSS +0.148→+0.495).",
    "パラメータは SYNERGY ベンチマークで最適化されています。低有病率で大きく改善（WSS +0.148→+0.495）。",
    "Parâmetros otimizados no benchmark SYNERGY; melhora acentuada em baixa prevalência (WSS +0.148→+0.495).",
    "Parameter auf dem SYNERGY-Benchmark optimiert; deutliche Verbesserung bei niedriger Prävalenz (WSS +0.148→+0.495).",
    "Параметри оптимизовани на SYNERGY бенчмарку; изражито побољшање при ниској преваленцији (WSS +0.148→+0.495).",
    "SYNERGY 벤치마크에서 최적화한 매개변수입니다. 낮은 유병률에서 크게 개선됩니다(WSS +0.148→+0.495)."
  ],
  [
    "线性 SVM(ASReview v3 默认),验证批宏 WSS +0.580,6 集中 5 集最优,筛查量减少 71%。",
    "Linear SVM (ASReview v3 default): validation-batch macro WSS +0.580, best in 5 of 6 sets, 71% less screening.",
    "SVM linéaire (par défaut dans ASReview v3) : WSS macro sur le lot de validation +0,580, meilleur sur 5 des 6 jeux, 71 % de tri en moins.",
    "Линейный SVM (по умолчанию в ASReview v3): макро WSS на проверочной партии +0.580, лучший в 5 из 6 наборов, объём отбора меньше на 71%.",
    "SVM lineal (predeterminado en ASReview v3): WSS macro del lote de validación +0.580, el mejor en 5 de 6 conjuntos, 71 % menos cribado.",
    "線形 SVM（ASReview v3 の既定）：検証バッチのマクロ WSS +0.580、6 データセット中 5 で最高、スクリーニング量 71% 減。",
    "SVM linear (padrão do ASReview v3): WSS macro do lote de validação +0.580, o melhor em 5 de 6 conjuntos, 71% menos triagem.",
    "Lineare SVM (ASReview-v3-Standard): Makro-WSS des Validierungsbatches +0.580, in 5 von 6 Datensätzen am besten, 71 % weniger Screening.",
    "Линеарни SVM (подразумеван у ASReview v3): макро WSS валидационог серијала +0.580, најбољи у 5 од 6 скупова, 71% мање скрининга.",
    "선형 SVM(ASReview v3 기본값): 검증 배치 매크로 WSS +0.580, 6개 세트 중 5개에서 최고, 선별량 71% 감소."
  ],
  [
    "SVM + CJK 二元组,适合中英混合文献。",
    "SVM with CJK bigrams; suited to mixed Chinese–English literature.",
    "SVM avec bigrammes CJK ; adapté à la littérature mixte chinois–anglais.",
    "SVM с CJK-биграммами; подходит для смешанной китайско-английской литературы.",
    "SVM con bigramas CJK; indicado para literatura mixta chino–inglés.",
    "SVM ＋ CJK バイグラム。中国語・英語混在文献に適します。",
    "SVM com bigramas CJK; adequado para literatura mista chinês–inglês.",
    "SVM mit CJK-Bigrammen; geeignet für gemischte chinesisch-englische Literatur.",
    "SVM са CJK биграмима; погодно за мешану кинеско-енглеску литературу.",
    "CJK 바이그램을 결합한 SVM으로 중국어·영어 혼합 문헌에 적합합니다."
  ],
  [
    "停止建议",
    "Stopping guidance",
    "Avis d’arrêt",
    "Рекомендация об остановке",
    "Orientación de parada",
    "停止の助言",
    "Orientação de parada",
    "Hinweis zum Stoppen",
    "Савет о заустављању",
    "중단 안내"
  ],
  [
    "开始抽验",
    "Start random verification",
    "Commencer la vérification aléatoire",
    "Начать выборочную проверку",
    "Iniciar verificación aleatoria",
    "無作為検証を開始",
    "Iniciar verificação aleatória",
    "Stichprobenprüfung starten",
    "Започињ случајну проверу",
    "무작위 검증 시작"
  ],
  [
    "已筛 {0} / {1} 条（{2}%）",
    "Screened {0} / {1} ({2}%)",
    "Triées {0} / {1} ({2} %)",
    "Просмотрено {0} / {1} ({2}%)",
    "Cribadas {0} / {1} ({2} %)",
    "スクリーニング済み {0} / {1} 件（{2}%）",
    "Triadas {0} / {1} ({2}%)",
    "Gescreent {0} / {1} ({2} %)",
    "Прегледано {0} / {1} ({2}%)",
    "선별됨 {0} / {1}건({2}%)"
  ],
  [
    "已发现 {0} 篇相关文献",
    "{0} relevant references found",
    "{0} références pertinentes trouvées",
    "Найдено релевантных публикаций: {0}",
    "{0} referencias pertinentes encontradas",
    "関連する文献が {0} 件見つかりました",
    "{0} referências relevantes encontradas",
    "{0} relevante Referenzen gefunden",
    "Пронађено {0} релевантних референци",
    "관련 문헌 {0}건 발견"
  ],
  [
    "抽验进行中：已判读 {0} / {1} 条，其中新发现相关 {2} 条",
    "Random verification in progress: {0} / {1} judged, {2} newly found relevant",
    "Vérification aléatoire en cours : {0} / {1} jugées, {2} nouvelles pertinentes",
    "Идёт выборочная проверка: оценено {0} / {1}, из них новых релевантных {2}",
    "Verificación aleatoria en curso: {0} / {1} evaluados, {2} nuevas pertinentes",
    "無作為検証中：{0} / {1} 件判定済み、新たに関連 {2} 件",
    "Verificação aleatória em andamento: {0} / {1} avaliados, {2} novas relevantes",
    "Stichprobenprüfung läuft: {0} / {1} bewertet, {2} neu relevant",
    "Случајна провера у току: {0} / {1} оцењено, {2} нових релевантних",
    "무작위 검증 진행 중: {0} / {1}건 판정, 새 관련 {2}건"
  ],
  [
    "抽验条目已置于队列顶部，请按正常流程逐条判读。",
    "The verification items were moved to the top of the queue; judge them one by one as usual.",
    "Les éléments de vérification ont été placés en tête de file ; jugez-les un à un comme d’habitude.",
    "Проверочные записи перемещены в начало очереди; оценивайте их по одной в обычном порядке.",
    "Los elementos de verificación se colocaron al principio de la cola; evalúelos de uno en uno como de costumbre.",
    "検証対象をキューの先頭に移動しました。通常どおり 1 件ずつ判定してください。",
    "Os itens de verificação foram movidos para o início da fila; avalie-os um a um como de costume.",
    "Die Prüfeinträge wurden an den Anfang der Warteschlange gestellt; bewerten Sie sie wie üblich einzeln.",
    "Ставке за проверу су померене на почетак реда; оцењујте их једну по једну као и обично.",
    "검증 항목을 대기열 맨 위로 옮겼습니다. 평소처럼 하나씩 판정하세요."
  ],
  [
    "建议进行随机抽验：从剩余 {0} 条中随机抽取 {1} 条进行筛选",
    "Random verification recommended: draw {1} of the remaining {0} records at random and screen them",
    "Vérification aléatoire recommandée : tirez {1} des {0} références restantes au hasard et triez-les",
    "Рекомендуется выборочная проверка: случайно отберите {1} из оставшихся {0} записей и просмотрите их",
    "Se recomienda verificación aleatoria: extraiga {1} de las {0} referencias restantes al azar y críbelas",
    "無作為検証を推奨：残り {0} 件から無作為に {1} 件を選んでスクリーニングしてください",
    "Verificação aleatória recomendada: sorteie {1} das {0} referências restantes e faça a triagem",
    "Stichprobenprüfung empfohlen: Ziehen Sie {1} der verbleibenden {0} Referenzen zufällig und screenen Sie diese",
    "Препоручује се случајна провера: случајно изаберите {1} од преосталих {0} записа и прегледајте их",
    "무작위 검증 권장: 남은 {0}건 중 무작위로 {1}건을 뽑아 선별하세요"
  ],
  [
    "证书通过 ✅ 以 ≥95% 置信度，总召回 ≥95%（剩余相关 ≤ {0} 条）",
    "Certificate passed ✅ With ≥95% confidence, total recall ≥95% (at most {0} relevant records left)",
    "Certificat validé ✅ Avec une confiance ≥95 %, rappel total ≥95 % (au plus {0} références pertinentes restantes)",
    "Сертификат пройден ✅ С уверенностью ≥95% суммарная полнота ≥95% (релевантных осталось ≤ {0})",
    "Certificado superado ✅ Con una confianza ≥95 %, recuperación total ≥95 % (quedan ≤ {0} referencias pertinentes)",
    "証明書合格 ✅ 信頼度 ≥95% で総合リコール ≥95%（残り関連文献 ≤ {0} 件）",
    "Certificado aprovado ✅ Com confiança ≥95%, recall total ≥95% (restam ≤ {0} referências relevantes)",
    "Zertifikat bestanden ✅ Mit ≥95 % Konfidenz gilt: Gesamt-Recall ≥95 % (höchstens {0} relevante Referenzen verbleiben)",
    "Сертификат положен ✅ Са поузданошћу ≥95%, укупни одзив ≥95% (преостало релевантних ≤ {0})",
    "증명서 통과 ✅ 신뢰도 ≥95%로 총 재현율 ≥95%(남은 관련 문헌 ≤ {0}건)"
  ],
  [
    "证书未通过 ❌ 抽验中发现 {0} 条新的相关文献，请继续筛选",
    "Certificate not passed ❌ {0} new relevant references found during verification; continue screening",
    "Certificat non validé ❌ {0} nouvelles références pertinentes trouvées lors de la vérification ; poursuivez le tri",
    "Сертификат не пройден ❌ В ходе проверки найдено новых релевантных: {0}; продолжайте отбор",
    "Certificado no superado ❌ Se encontraron {0} referencias pertinentes nuevas en la verificación; continúe el cribado",
    "証明書不合格 ❌ 検証で新たに {0} 件の関連文献が見つかりました。スクリーニングを続けてください",
    "Certificado reprovado ❌ {0} novas referências relevantes encontradas na verificação; continue a triagem",
    "Zertifikat nicht bestanden ❌ Bei der Prüfung wurden {0} neue relevante Referenzen gefunden; setzen Sie das Screening fort",
    "Сертификат није положен ❌ Током провере нађено {0} нових релевантних; наставите скрининг",
    "증명서 미통과 ❌ 검증 중 새 관련 문헌 {0}건이 발견되었습니다. 선별을 계속하세요"
  ],
  [
    "已连续排除 {0} 条 · 仅进度提示",
    "{0} consecutive exclusions · progress hint only",
    "{0} exclusions consécutives · simple indicateur d’avancement",
    "{0} подряд исключений · только подсказка о прогрессе",
    "{0} exclusiones consecutivas · solo aviso de progreso",
    "連続除外 {0} 件・進捗の目安のみ",
    "{0} exclusões consecutivas · apenas dica de progresso",
    "{0} aufeinanderfolgende Ausschlüsse · nur Fortschrittshinweis",
    "{0} узастопних искључења · само подсетник о напретку",
    "연속 제외 {0}건 · 진행 참고용"
  ],
  [
    "下一检查点：已筛 {0}%",
    "Next checkpoint: {0}% screened",
    "Prochain point de contrôle : {0} % trié",
    "Следующая контрольная точка: просмотрено {0}%",
    "Siguiente punto de control: {0} % cribado",
    "次のチェックポイント：スクリーニング済み {0}%",
    "Próximo ponto de controle: {0}% triado",
    "Nächster Kontrollpunkt: {0} % gescreent",
    "Следећа контролна тачка: прегледано {0}%",
    "다음 검증 지점: {0}% 선별됨"
  ],
  [
    "已随机抽取 {0} 条置于队列顶部，请逐条判读完成抽验",
    "{0} records drawn at random and moved to the top of the queue; judge each one to finish the verification",
    "{0} références tirées au hasard et placées en tête de file ; jugez-les toutes pour terminer la vérification",
    "Случайно отобрано {0} записей и перемещено в начало очереди; оцените каждую, чтобы завершить проверку",
    "Se sortearon {0} referencias y se colocaron al principio de la cola; evalúe cada una para terminar la verificación",
    "{0} 件を無作為に抽出してキューの先頭に置きました。すべて判定して検証を完了してください",
    "{0} referências sorteadas e movidas para o início da fila; avalie cada uma para concluir a verificação",
    "{0} Referenzen wurden zufällig gezogen und an den Anfang der Warteschlange gestellt; bewerten Sie jede, um die Prüfung abzuschließen",
    "Случајно је изабрано {0} записа и померено на почетак реда; оцените сваки да завршите проверу",
    "{0}건을 무작위로 뽑아 대기열 맨 위에 두었습니다. 각각 판정해 검증을 완료하세요"
  ],
  [
    "盲筛模式",
    "Blind screening mode",
    "Mode de tri à l’aveugle",
    "Режим слепого отбора",
    "Modo de cribado ciego",
    "ブラインドスクリーニングモード",
    "Modo de triagem cega",
    "Verblindetes Screening",
    "Режим слепог скрининга",
    "눈가림 선별 모드"
  ],
  [
    "盲筛模式（默认开启）",
    "Blind screening mode (on by default)",
    "Mode de tri à l’aveugle (activé par défaut)",
    "Режим слепого отбора (включён по умолчанию)",
    "Modo de cribado ciego (activado por defecto)",
    "ブラインドスクリーニングモード（既定でオン）",
    "Modo de triagem cega (ativado por padrão)",
    "Verblindetes Screening (standardmäßig aktiv)",
    "Режим слепог скрининга (подразумевано укључен)",
    "눈가림 선별 모드(기본 켜짐)"
  ],
  [
    "开启时初筛界面不显示 AI 相关度分数与徽标（文献仍按 AI 排序呈现）；关闭时显示分数并标注仅供参考。偏好仅存本机浏览器。",
    "When on, the screening view hides AI relevance scores and badges (references are still AI-ordered); when off, scores are shown with a for-reference-only note. The preference is stored in this browser only.",
    "Activé, la vue de sélection masque les scores de pertinence IA et les badges (les références restent ordonnées par l’IA) ; désactivé, les scores s’affichent avec la mention « à titre indicatif ». La préférence n’est conservée que dans ce navigateur.",
    "Во включённом режиме экран отбора скрывает оценки релевантности ИИ и значки (порядок публикаций остаётся ИИ-ранжированным); в выключенном — оценки показываются с пометкой «только для справки». Настройка хранится только в этом браузере.",
    "Activado, la vista de cribado oculta las puntuaciones y las insignias de relevancia de la IA (las referencias siguen ordenadas por la IA); desactivado, se muestran las puntuaciones con la nota de que son solo orientativas. La preferencia se guarda solo en este navegador.",
    "オンの場合、スクリーニング画面に AI の関連度スコアとバッジを表示しません（文献は引き続き AI 順で並びます）。オフの場合はスコアを表示し、参考値である旨を注記します。設定はこのブラウザーにのみ保存されます。",
    "Ativado, a visão de triagem oculta as pontuações e os emblemas de relevância da IA (as referências continuam ordenadas pela IA); desativado, as pontuações aparecem com a nota de uso apenas orientativo. A preferência fica guardada somente neste navegador.",
    "Wenn aktiviert, verbirgt die Screening-Ansicht KI-Relevanzwerte und Badges (die Referenzen bleiben KI-sortiert); wenn deaktiviert, werden die Werte mit dem Hinweis „nur zur Orientierung“ gezeigt. Die Einstellung wird nur in diesem Browser gespeichert.",
    "Када је укључено, екран скрининга скрива ИА оцене релевантности и значке (референце остају поређане по ИА); када је искључено, оцене се приказују уз напомену да су само оријентационе. Подешавање се чува само у овом прегледачу.",
    "켜면 선별 화면에서 AI 관련도 점수와 배지를 숨깁니다(문헌 순서는 여전히 AI 정렬). 끄면 점수를 표시하고 참고용임을 밝힙니다. 설정은 이 브라우저에만 저장됩니다."
  ],
  [
    "AI 分数仅供参考，不应影响判断",
    "AI scores are for reference only and should not influence your judgement",
    "Les scores IA sont donnés à titre indicatif et ne doivent pas influencer votre jugement",
    "Оценки ИИ приведены только для справки и не должны влиять на ваше суждение",
    "Las puntuaciones de la IA son solo orientativas y no deben influir en su juicio",
    "AI スコアは参考値であり、判断に影響を与えるものではありません",
    "As pontuações da IA são apenas orientativas e não devem influenciar seu julgamento",
    "KI-Werte sind nur zur Orientierung und sollten Ihr Urteil nicht beeinflussen",
    "ИА оцене су само оријентационе и не треба да утичу на вашу оцену",
    "AI 점수는 참고용이며 판단에 영향을 주어서는 안 됩니다"
  ],
  [
    "人群（P）", "Population (P)", "Population (P)", "Популяция (P)", "Población (P)", "人群（P）", "População (P)", "Population (P)", "Популација (P)", "대상 집단(P)"
  ],
  [
    "干预（I）", "Intervention (I)", "Intervention (I)", "Вмешательство (I)", "Intervención (I)", "介入（I）", "Intervenção (I)", "Intervention (I)", "Интервенција (I)", "중재(I)"
  ],
  [
    "对照（C）",
    "Comparator (C)",
    "Comparateur (C)",
    "Контроль (C)",
    "Comparador (C)",
    "対照（C）",
    "Comparador (C)",
    "Vergleich (C)",
    "Поређење (C)",
    "비교(C)"
  ],
  [
    "结局（O）",
    "Outcome (O)",
    "Critère de jugement (O)",
    "Исход (O)",
    "Desenlace (O)",
    "アウトカム（O）",
    "Desfecho (O)",
    "Endpunkt (O)",
    "Исход (O)",
    "결과(O)"
  ],
  [
    "通用格式",
    "Generic format",
    "Format générique",
    "Универсальный формат",
    "Formato genérico",
    "汎用フォーマット",
    "Formato genérico",
    "Allgemeines Format",
    "Општи формат",
    "일반 형식"
  ],
  [
    "移除 {0}",
    "Remove {0}",
    "Retirer {0}",
    "Убрать {0}",
    "Quitar {0}",
    "{0} を削除",
    "Remover {0}",
    "{0} entfernen",
    "Уклони {0}",
    "{0} 제거"
  ],
  [
    "设计方案",
    "Design protocol",
    "Concevoir le protocole",
    "Составить протокол",
    "Diseñar protocolo",
    "プロトコルを作成",
    "Elaborar protocolo",
    "Protokoll entwerfen",
    "Осмисли протокол",
    "프로토콜 설계"
  ],
  [
    "设计方案（Protocol Designer）",
    "Protocol designer",
    "Concepteur de protocole",
    "Конструктор протокола",
    "Diseñador de protocolos",
    "プロトコル デザイナー",
    "Editor de protocolo",
    "Protokoll-Designer",
    "Дизајнер протокола",
    "프로토콜 디자이너"
  ],
  [
    "综述开始前结构化定义 PICO 要素，自动生成各数据库检索式；方案保存进任务的 task.json，可导出为 Markdown（可直接粘贴到 PROSPERO 注册表单）。全部计算在本地完成。",
    "Define the PICO elements in a structured way before the review starts and generate per-database search strategies automatically; the protocol is stored in the task’s task.json and can be exported as Markdown (ready to paste into the PROSPERO registration form). All computation runs locally.",
    "Définissez de façon structurée les éléments PICO avant de commencer la revue et générez automatiquement les stratégies de recherche par base de données ; le protocole est enregistré dans le task.json de la tâche et peut être exporté en Markdown (à coller directement dans le formulaire d’enregistrement PROSPERO). Tous les calculs sont locaux.",
    "Структурируйте элементы PICO до начала обзора и автоматически формируйте поисковые стратегии для каждой базы данных; протокол сохраняется в task.json задачи и может быть экспортирован в Markdown (можно сразу вставить в форму регистрации PROSPERO). Все вычисления выполняются локально.",
    "Defina de forma estructurada los elementos PICO antes de comenzar la revisión y genere automáticamente las estrategias de búsqueda por base de datos; el protocolo se guarda en el task.json de la tarea y puede exportarse como Markdown (listo para pegar en el formulario de registro de PROSPERO). Todo el cálculo es local.",
    "レビュー開始前に PICO 要素を構造化して定義し、各データベースの検索式を自動生成します。プロトコルはタスクの task.json に保存され、Markdown として書き出せます（PROSPERO 登録フォームにそのまま貼り付け可能）。計算はすべてローカルで行われます。",
    "Defina de forma estruturada os elementos PICO antes de iniciar a revisão e gere automaticamente as estratégias de busca por base de dados; o protocolo é salvo no task.json da tarefa e pode ser exportado como Markdown (pronto para colar no formulário de registro do PROSPERO). Todo o cálculo é local.",
    "Definieren Sie die PICO-Elemente vor Beginn des Reviews strukturiert und erzeugen Sie die Suchstrategien der Datenbanken automatisch; das Protokoll wird in der task.json der Aufgabe gespeichert und lässt sich als Markdown exportieren (direkt in das PROSPERO-Registrierungsformular einfügen). Alle Berechnungen laufen lokal.",
    "Структурно дефинишите PICO елементе пре почетка прегледа и аутоматски генеришите стратегије претраге по базама; протокол се чува у task.json задатка и може се извести као Markdown (спремно за PROSPERO образац). Сви прорачуни су локални.",
    "리뷰 시작 전 PICO 요소를 구조화해 정의하고 데이터베이스별 검색식을 자동 생성합니다. 프로토콜은 작업의 task.json에 저장되며 Markdown으로 내보낼 수 있습니다(PROSPERO 등록 양식에 바로 붙여넣기 가능). 모든 계산은 로컬에서 수행됩니다."
  ],
  [
    "目标任务",
    "Target task",
    "Tâche cible",
    "Целевая задача",
    "Tarea de destino",
    "対象タスク",
    "Tarefa de destino",
    "Zielaufgabe",
    "Циљни задатак",
    "대상 작업"
  ],
  [
    "方案标题（必填，创建新任务时即任务名）",
    "Protocol title (required; becomes the task name when creating a new task)",
    "Titre du protocole (obligatoire ; devient le nom de la tâche lors de la création)",
    "Название протокола (обязательно; при создании новой задачи становится её именем)",
    "Título del protocolo (obligatorio; será el nombre de la tarea al crearla)",
    "プロトコルタイトル（必須。新規タスク作成時はタスク名になります）",
    "Título do protocolo (obrigatório; torna-se o nome da tarefa ao criá-la)",
    "Protokolltitel (erforderlich; wird beim Anlegen der Aufgabe zum Aufgabennamen)",
    "Наслов протокола (обавезно; при прављењу новог задатка постаје његов назив)",
    "프로토콜 제목(필수, 새 작업 생성 시 작업 이름이 됨)"
  ],
  [
    "如：有氧运动对 2 型糖尿病患者血糖控制的影响",
    "e.g. Effect of aerobic exercise on glycaemic control in type 2 diabetes",
    "p. ex. Effet de l’exercice aérobie sur le contrôle glycémique dans le diabète de type 2",
    "напр. Влияние аэробных нагрузок на гликемический контроль при диабете 2 типа",
    "p. ej. Efecto del ejercicio aeróbico en el control glucémico en diabetes tipo 2",
    "例：2 型糖尿病の血糖コントロールに対する有酸素運動の影響",
    "ex. Efeito do exercício aeróbico no controle glicêmico no diabetes tipo 2",
    "z. B. Wirkung von Ausdauertraining auf die glykämische Kontrolle bei Typ-2-Diabetes",
    "нпр. Утицај аеробног вежбања на гликемичку контролу код дијабетеса типа 2",
    "예: 제2형 당뇨병 환자의 혈당 조절에 대한 유산소 운동의 효과"
  ],
  [
    "研究问题",
    "Research question",
    "Question de recherche",
    "Исследовательский вопрос",
    "Pregunta de investigación",
    "リサーチクエスチョン",
    "Pergunta de pesquisa",
    "Fragestellung",
    "Истраживачко питање",
    "연구 질문"
  ],
  [
    "结构化研究问题（自由文本）",
    "Structured research question (free text)",
    "Question de recherche structurée (texte libre)",
    "Структурированный исследовательский вопрос (свободный текст)",
    "Pregunta de investigación estructurada (texto libre)",
    "構造化されたリサーチクエスチョン（自由記述）",
    "Pergunta de pesquisa estruturada (texto livre)",
    "Strukturierte Fragestellung (Freitext)",
    "Структурисано истраживачко питање (слободан текст)",
    "구조화된 연구 질문(자유 서술)"
  ],
  [
    "自然语言描述（如 adults with type 2 diabetes）",
    "Natural-language description (e.g. adults with type 2 diabetes)",
    "Description en langage naturel (p. ex. adults with type 2 diabetes)",
    "Описание на естественном языке (напр. adults with type 2 diabetes)",
    "Descripción en lenguaje natural (p. ej. adults with type 2 diabetes)",
    "自然言語による記述（例：adults with type 2 diabetes）",
    "Descrição em linguagem natural (ex. adults with type 2 diabetes)",
    "Beschreibung in natürlicher Sprache (z. B. adults with type 2 diabetes)",
    "Опис природним језиком (нпр. adults with type 2 diabetes)",
    "자연어 설명(예: adults with type 2 diabetes)"
  ],
  [
    "输入或从建议中选择 MeSH 主题词",
    "Type or pick a MeSH term from the suggestions",
    "Saisissez ou choisissez un descripteur MeSH parmi les suggestions",
    "Введите или выберите дескриптор MeSH из подсказок",
    "Escriba o elija un descriptor MeSH de las sugerencias",
    "MeSH 用語を入力するか候補から選択してください",
    "Digite ou escolha um descritor MeSH das sugestões",
    "Geben Sie einen MeSH-Begriff ein oder wählen Sie aus den Vorschlägen",
    "Унесите или изаберите MeSH појам из предлога",
    "MeSH 용어를 입력하거나 제안에서 선택하세요"
  ],
  [
    "添加 MeSH",
    "Add MeSH term",
    "Ajouter un descripteur MeSH",
    "Добавить MeSH",
    "Añadir MeSH",
    "MeSH を追加",
    "Adicionar MeSH",
    "MeSH hinzufügen",
    "Додај MeSH",
    "MeSH 추가"
  ],
  [
    "添加同义词",
    "Add synonym",
    "Ajouter un synonyme",
    "Добавить синоним",
    "Añadir sinónimo",
    "同義語を追加",
    "Adicionar sinônimo",
    "Synonym hinzufügen",
    "Додај синоним",
    "동의어 추가"
  ],
  [
    "添加",
    "Add",
    "Ajouter",
    "Добавить",
    "Añadir",
    "追加",
    "Adicionar",
    "Hinzufügen",
    "Додај",
    "추가"
  ],
  [
    "同义词/变体（如 T2DM）",
    "Synonym/variant (e.g. T2DM)",
    "Synonyme/variante (p. ex. T2DM)",
    "Синоним/вариант (напр. T2DM)",
    "Sinónimo/variante (p. ej. T2DM)",
    "同義語・別表記（例：T2DM）",
    "Sinônimo/variante (ex. T2DM)",
    "Synonym/Variante (z. B. T2DM)",
    "Синоним/варијанта (нпр. T2DM)",
    "동의어/변형(예: T2DM)"
  ],
  [
    "删除该行",
    "Delete this row",
    "Supprimer cette ligne",
    "Удалить эту строку",
    "Eliminar esta fila",
    "この行を削除",
    "Excluir esta linha",
    "Diese Zeile löschen",
    "Обриши овај ред",
    "이 행 삭제"
  ],
  [
    "输入一条标准后按回车或点击添加",
    "Type a criterion and press Enter or click Add",
    "Saisissez un critère puis appuyez sur Entrée ou cliquez sur Ajouter",
    "Введите критерий и нажмите Enter или «Добавить»",
    "Escriba un criterio y pulse Intro o haga clic en Añadir",
    "基準を入力して Enter キーを押すか「追加」をクリックしてください",
    "Digite um critério e pressione Enter ou clique em Adicionar",
    "Geben Sie ein Kriterium ein und drücken Sie die Eingabetaste oder klicken Sie auf Hinzufügen",
    "Унесите критеријум и притисните Enter или кликните „Додај“",
    "기준을 입력한 후 Enter 키를 누르거나 추가를 클릭하세요"
  ],
  [
    "纳入标准",
    "Inclusion criteria",
    "Critères d’inclusion",
    "Критерии включения",
    "Criterios de inclusión",
    "組み入れ基準",
    "Critérios de inclusão",
    "Einschlusskriterien",
    "Критеријуми укључења",
    "선정 기준"
  ],
  [
    "排除标准",
    "Exclusion criteria",
    "Critères d’exclusion",
    "Критерии исключения",
    "Criterios de exclusión",
    "除外基準",
    "Critérios de exclusão",
    "Ausschlusskriterien",
    "Критеријуми искључења",
    "제외 기준"
  ],
  [
    "生成检索式",
    "Generate search strategies",
    "Générer les stratégies de recherche",
    "Сформировать поисковые стратегии",
    "Generar estrategias de búsqueda",
    "検索式を生成",
    "Gerar estratégias de busca",
    "Suchstrategien generieren",
    "Генериши стратегије претраге",
    "검색식 생성"
  ],
  [
    "保存并创建任务",
    "Save and create task",
    "Enregistrer et créer la tâche",
    "Сохранить и создать задачу",
    "Guardar y crear tarea",
    "保存してタスクを作成",
    "Salvar e criar tarefa",
    "Speichern und Aufgabe anlegen",
    "Сачувај и направи задатак",
    "저장하고 작업 만들기"
  ],
  [
    "导出方案",
    "Export protocol",
    "Exporter le protocole",
    "Экспортировать протокол",
    "Exportar protocolo",
    "プロトコルを書き出す",
    "Exportar protocolo",
    "Protokoll exportieren",
    "Извези протокол",
    "프로토콜 내보내기"
  ],
  [
    "方案已保存并嵌入任务。下一步导入文献，然后开始初筛。",
    "Protocol saved and embedded in the task. Next import references, then start screening.",
    "Protocole enregistré et intégré à la tâche. Importez ensuite les références, puis commencez le tri.",
    "Протокол сохранён и встроен в задачу. Далее импортируйте публикации и начните отбор.",
    "Protocolo guardado e incrustado en la tarea. Importe referencias y comience el cribado.",
    "プロトコルを保存してタスクに組み込みました。次に文献をインポートし、スクリーニングを開始してください。",
    "Protocolo salvo e incorporado à tarefa. Importe referências e inicie a triagem.",
    "Protokoll gespeichert und in die Aufgabe eingebettet. Importieren Sie als Nächstes Referenzen und beginnen Sie mit dem Screening.",
    "Протокол је сачуван и уграђен у задатак. Затим увезите референце и почните скрининг.",
    "프로토콜을 저장해 작업에 포함했습니다. 다음으로 문헌을 가져온 후 선별을 시작하세요."
  ],
  [
    "新任务（保存时创建）",
    "New task (created on save)",
    "Nouvelle tâche (créée lors de l’enregistrement)",
    "Новая задача (создаётся при сохранении)",
    "Tarea nueva (se crea al guardar)",
    "新規タスク（保存時に作成）",
    "Nova tarefa (criada ao salvar)",
    "Neue Aufgabe (beim Speichern angelegt)",
    "Нови задатак (прави се при чувању)",
    "새 작업(저장 시 생성)"
  ],
  [
    "正在读取已保存方案…",
    "Loading saved protocol…",
    "Lecture du protocole enregistré…",
    "Чтение сохранённого протокола…",
    "Leyendo el protocolo guardado…",
    "保存済みプロトコルを読み込み中…",
    "Lendo o protocolo salvo…",
    "Gespeichertes Protokoll wird gelesen…",
    "Читање сачуваног протокола…",
    "저장된 프로토콜 불러오는 중…"
  ],
  [
    "该任务尚未保存方案。",
    "No protocol saved for this task yet.",
    "Aucun protocole enregistré pour cette tâche.",
    "Для этой задачи протокол ещё не сохранён.",
    "Esta tarea aún no tiene protocolo guardado.",
    "このタスクにはプロトコルが保存されていません。",
    "Esta tarefa ainda não tem protocolo salvo.",
    "Für diese Aufgabe ist noch kein Protokoll gespeichert.",
    "За овај задатак још није сачуван протокол.",
    "이 작업에는 아직 저장된 프로토콜이 없습니다."
  ],
  [
    "已载入该任务保存的方案（{0}）",
    "Loaded the protocol saved for this task ({0})",
    "Protocole enregistré pour cette tâche chargé ({0})",
    "Загружен протокол, сохранённый для этой задачи ({0})",
    "Se cargó el protocolo guardado de esta tarea ({0})",
    "このタスクの保存済みプロトコルを読み込みました（{0}）",
    "Protocolo salvo desta tarefa carregado ({0})",
    "Gespeichertes Protokoll dieser Aufgabe geladen ({0})",
    "Учитан протокол сачуван за овај задатак ({0})",
    "이 작업에 저장된 프로토콜을 불러왔습니다({0})"
  ],
  [
    "请先填写方案标题。",
    "Enter the protocol title first.",
    "Indiquez d’abord le titre du protocole.",
    "Сначала укажите название протокола.",
    "Indique primero el título del protocolo.",
    "先にプロトコルタイトルを入力してください。",
    "Informe primeiro o título do protocolo.",
    "Geben Sie zuerst den Protokolltitel ein.",
    "Прво унесите наслов протокола.",
    "먼저 프로토콜 제목을 입력하세요."
  ],
  [
    "生成检索式需要先把方案保存到一个任务。将创建新任务《{0}》，是否继续？",
    "Generating search strategies requires saving the protocol to a task first. A new task “{0}” will be created. Continue?",
    "La génération des stratégies exige d’abord d’enregistrer le protocole dans une tâche. La nouvelle tâche « {0} » sera créée. Continuer ?",
    "Для формирования поисковых стратегий протокол сначала нужно сохранить в задачу. Будет создана новая задача «{0}». Продолжить?",
    "Para generar estrategias de búsqueda debe guardar primero el protocolo en una tarea. Se creará la tarea «{0}». ¿Continuar?",
    "検索式の生成には、先にプロトコルをタスクへ保存する必要があります。新しいタスク「{0}」を作成します。続けますか？",
    "Para gerar estratégias de busca, salve primeiro o protocolo em uma tarefa. A nova tarefa “{0}” será criada. Continuar?",
    "Zum Generieren der Suchstrategien muss das Protokoll zuerst in einer Aufgabe gespeichert werden. Die neue Aufgabe „{0}“ wird angelegt. Fortfahren?",
    "За генерисање стратегија претраге протокол прво мора бити сачуван у задатак. Направиће се нови задатак „{0}“. Наставити?",
    "검색식 생성을 위해서는 먼저 프로토콜을 작업에 저장해야 합니다. 새 작업 “{0}”을(를) 생성합니다. 계속할까요?"
  ],
  [
    "已创建任务《{0}》，方案将保存到该任务",
    "Task “{0}” created; the protocol will be saved to it",
    "Tâche « {0} » créée ; le protocole y sera enregistré",
    "Задача «{0}» создана; протокол будет сохранён в неё",
    "Tarea «{0}» creada; el protocolo se guardará en ella",
    "タスク「{0}」を作成しました。プロトコルはこのタスクに保存されます",
    "Tarefa “{0}” criada; o protocolo será salvo nela",
    "Aufgabe „{0}“ angelegt; das Protokoll wird darin gespeichert",
    "Задатак „{0}“ направљен; протокол ће се сачувати у њега",
    "작업 “{0}”을(를) 생성했습니다. 프로토콜은 이 작업에 저장됩니다"
  ],
  [
    "创建任务失败：",
    "Task creation failed: ",
    "Échec de création de la tâche : ",
    "Не удалось создать задачу: ",
    "Error al crear la tarea: ",
    "タスク作成に失敗：",
    "Falha ao criar a tarefa: ",
    "Aufgabe konnte nicht angelegt werden: ",
    "Прављење задатка није успело: ",
    "작업 생성 실패: "
  ],
  [
    "正在生成检索式…",
    "Generating search strategies…",
    "Génération des stratégies de recherche…",
    "Формирование поисковых стратегий…",
    "Generando estrategias de búsqueda…",
    "検索式を生成中…",
    "Gerando estratégias de busca…",
    "Suchstrategien werden generiert…",
    "Генерисање стратегија претраге…",
    "검색식 생성 중…"
  ],
  [
    "响应缺少方案数据。",
    "The response contains no protocol data.",
    "La réponse ne contient pas de données de protocole.",
    "В ответе нет данных протокола.",
    "La respuesta no contiene datos del protocolo.",
    "レスポンスにプロトコルデータがありません。",
    "A resposta não contém dados do protocolo.",
    "Die Antwort enthält keine Protokolldaten.",
    "Одговор не садржи податке протокола.",
    "응답에 프로토콜 데이터가 없습니다."
  ],
  [
    "方案已保存到任务 {0}（{1} 条检索式）。",
    "Protocol saved to task {0}; search strategies: {1}.",
    "Protocole enregistré dans la tâche {0} ({1} stratégies de recherche).",
    "Протокол сохранён в задаче {0}; количество поисковых стратегий: {1}.",
    "Protocolo guardado en la tarea {0} ({1} estrategias de búsqueda).",
    "プロトコルをタスク {0} に保存しました（{1} 件の検索式）。",
    "Protocolo salvo na tarefa {0} ({1} estratégias de busca).",
    "Protokoll in Aufgabe {0} gespeichert; Suchstrategien: {1}.",
    "Протокол је сачуван у задатку {0}; број стратегија претраге: {1}.",
    "프로토콜을 작업 {0}에 저장했습니다(검색식 {1}개)."
  ],
  [
    "✓ 方案已保存并嵌入任务《{0}》",
    "✓ Protocol saved and embedded in task “{0}”",
    "✓ Protocole enregistré et intégré à la tâche « {0} »",
    "✓ Протокол сохранён и встроен в задачу «{0}»",
    "✓ Protocolo guardado e incrustado en la tarea «{0}»",
    "✓ プロトコルを保存してタスク「{0}」に組み込みました",
    "✓ Protocolo salvo e incorporado à tarefa “{0}”",
    "✓ Protokoll gespeichert und in Aufgabe „{0}“ eingebettet",
    "✓ Протокол сачуван и уграђен у задатак „{0}“",
    "✓ 프로토콜을 저장해 작업 “{0}”에 포함했습니다"
  ],
  [
    "所有 PICO 要素均为空，未生成检索式。请在上方至少填写一个要素。",
    "All PICO elements are empty; no search strategy was generated. Fill in at least one element above.",
    "Tous les éléments PICO sont vides ; aucune stratégie générée. Renseignez au moins un élément ci-dessus.",
    "Все элементы PICO пусты; поисковые стратегии не сформированы. Заполните хотя бы один элемент выше.",
    "Todos los elementos PICO están vacíos; no se generó ninguna estrategia. Complete al menos un elemento arriba.",
    "すべての PICO 要素が空のため、検索式を生成しませんでした。上の要素を少なくとも 1 つ入力してください。",
    "Todos os elementos PICO estão vazios; nenhuma estratégia foi gerada. Preencha ao menos um elemento acima.",
    "Alle PICO-Elemente sind leer; es wurde keine Suchstrategie erzeugt. Füllen Sie oben mindestens ein Element aus.",
    "Сви PICO елементи су празни; стратегија није генерисана. Попуните барем један елемент изнад.",
    "모든 PICO 요소가 비어 검색식을 생성하지 못했습니다. 위에서 최소 한 개의 요소를 입력하세요."
  ],
  [
    "各数据库检索式",
    "Search strategies by database",
    "Stratégies de recherche par base",
    "Поисковые стратегии по базам данных",
    "Estrategias de búsqueda por base de datos",
    "データベース別の検索式",
    "Estratégias de busca por base de dados",
    "Suchstrategien je Datenbank",
    "Стратегије претраге по базама",
    "데이터베이스별 검색식"
  ],
  [
    "复制",
    "Copy",
    "Copier",
    "Копировать",
    "Copiar",
    "コピー",
    "Copiar",
    "Kopieren",
    "Копирај",
    "복사"
  ],
  [
    "✓ 已复制 {0} 检索式",
    "✓ {0} search strategy copied",
    "✓ Stratégie de recherche {0} copiée",
    "✓ Поисковая стратегия {0} скопирована",
    "✓ Estrategia de búsqueda {0} copiada",
    "✓ {0} の検索式をコピーしました",
    "✓ Estratégia de busca {0} copiada",
    "✓ Suchstrategie {0} kopiert",
    "✓ Стратегија претраге {0} копирана",
    "✓ {0} 검색식을 복사했습니다"
  ],
  [
    "复制失败，请手动选择文本复制。",
    "Copy failed; select the text manually to copy it.",
    "Échec de la copie ; sélectionnez le texte manuellement.",
    "Не удалось скопировать; выделите текст вручную.",
    "No se pudo copiar; seleccione el texto manualmente.",
    "コピーに失敗しました。テキストを手動で選択してコピーしてください。",
    "Falha ao copiar; selecione o texto manualmente.",
    "Kopieren fehlgeschlagen; markieren Sie den Text manuell.",
    "Копирање није успело; ручно изаберите текст.",
    "복사에 실패했습니다. 텍스트를 직접 선택해 복사하세요."
  ],
  [
    "{0} 行",
    "Lines: {0}",
    "{0} lignes",
    "Количество строк: {0}",
    "{0} líneas",
    "{0} 行",
    "{0} linhas",
    "Zeilen: {0}",
    "Број редова: {0}",
    "{0}행"
  ],
  [
    "在浏览器中打开 ↗",
    "Open in browser ↗",
    "Ouvrir dans le navigateur ↗",
    "Открыть в браузере ↗",
    "Abrir en el navegador ↗",
    "ブラウザーで開く ↗",
    "Abrir no navegador ↗",
    "Im Browser öffnen ↗",
    "Отвори у прегледачу ↗",
    "브라우저에서 열기 ↗"
  ],
  [
    "检索式由本地规则生成（Cochrane Handbook ch.4）；「在浏览器中打开」仅为导航链接，本应用不发起网络请求。Embase 链接仅定位到检索页，需手动粘贴执行。",
    "Search strategies are generated by local rules (Cochrane Handbook ch. 4); “Open in browser” is a navigation link only — the app makes no network requests. The Embase link only opens the search page; paste and run the query there manually.",
    "Les stratégies sont générées par des règles locales (Cochrane Handbook ch. 4) ; « Ouvrir dans le navigateur » n’est qu’un lien de navigation — l’application n’émet aucune requête réseau. Le lien Embase ouvre seulement la page de recherche ; collez et exécutez la requête manuellement.",
    "Поисковые стратегии формируются локальными правилами (Cochrane Handbook, гл. 4); «Открыть в браузере» — только навигационная ссылка, приложение не отправляет сетевых запросов. Ссылка Embase лишь открывает страницу поиска; вставьте и выполните запрос вручную.",
    "Las estrategias se generan con reglas locales (Cochrane Handbook cap. 4); «Abrir en el navegador» es solo un enlace de navegación: la aplicación no hace peticiones de red. El enlace de Embase solo abre la página de búsqueda; pegue y ejecute la consulta manualmente.",
    "検索式はローカルルールで生成されます（Cochrane Handbook 第 4 章）。「ブラウザーで開く」はナビゲーションリンクにすぎず、本アプリはネットワークリクエストを行いません。Embase リンクは検索ページを開くだけで、検索式は手動で貼り付けて実行してください。",
    "As estratégias são geradas por regras locais (Cochrane Handbook cap. 4); “Abrir no navegador” é apenas um link de navegação — o aplicativo não faz requisições de rede. O link do Embase apenas abre a página de busca; cole e execute a consulta manualmente.",
    "Die Suchstrategien werden durch lokale Regeln erzeugt (Cochrane Handbook, Kap. 4); „Im Browser öffnen“ ist nur ein Navigationslink — die App stellt keine Netzwerkanfragen. Der Embase-Link öffnet nur die Suchseite; fügen Sie die Abfrage dort manuell ein und führen Sie sie aus.",
    "Стратегије претраге се генеришу локалним правилима (Cochrane Handbook, погл. 4); „Отвори у прегледачу“ је само навигациони линк — апликација не шаље мрежне захтеве. Embase линк само отвара страницу претраге; налепите и покрените упит ручно.",
    "검색식은 로컬 규칙으로 생성됩니다(Cochrane Handbook 4장). “브라우저에서 열기”는 탐색 링크일 뿐이며 앱은 네트워크 요청을 보내지 않습니다. Embase 링크는 검색 페이지만 열므로 검색식을 직접 붙여넣어 실행하세요."
  ],
  [
    "请先保存方案（导出需要已保存方案的任务）。",
    "Save the protocol first (export requires a task with a saved protocol).",
    "Enregistrez d’abord le protocole (l’export exige une tâche avec protocole enregistré).",
    "Сначала сохраните протокол (для экспорта нужна задача с сохранённым протоколом).",
    "Guarde primero el protocolo (la exportación requiere una tarea con protocolo guardado).",
    "先にプロトコルを保存してください（書き出しには保存済みプロトコルが必要です）。",
    "Salve primeiro o protocolo (a exportação exige uma tarefa com protocolo salvo).",
    "Speichern Sie zuerst das Protokoll (der Export erfordert eine Aufgabe mit gespeichertem Protokoll).",
    "Прво сачувајте протокол (извоз захтева задатак са сачуваним протоколом).",
    "먼저 프로토콜을 저장하세요(내보내려면 저장된 프로토콜이 있는 작업이 필요합니다)."
  ],
  [
    "RCT · 连续结局",
    "RCT · continuous outcome",
    "ECR · critère continu",
    "РКИ · непрерывный исход",
    "ECA · desenlace continuo",
    "RCT・連続アウトカム",
    "ECR · desfecho contínuo",
    "RCT · kontinuierlicher Endpunkt",
    "РЦТ · континуирани исход",
    "RCT · 연속형 결과"
  ],
  [
    "RCT · 二分类结局",
    "RCT · binary outcome",
    "ECR · critère binaire",
    "РКИ · бинарный исход",
    "ECA · desenlace binario",
    "RCT・二値アウトカム",
    "ECR · desfecho binário",
    "RCT · binärer Endpunkt",
    "РЦТ · бинарни исход",
    "RCT · 이분형 결과"
  ],
  [
    "诊断准确性 · 2×2 表",
    "Diagnostic accuracy · 2×2 table",
    "Précision diagnostique · tableau 2×2",
    "Диагностическая точность · таблица 2×2",
    "Exactitud diagnóstica · tabla 2×2",
    "診断精度・2×2 表",
    "Acurácia diagnóstica · tabela 2×2",
    "Diagnostische Genauigkeit · 2×2-Tabelle",
    "Дијагностичка тачност · 2×2 табела",
    "진단 정확도 · 2×2 표"
  ],
  [
    "生存分析 · 风险比 HR",
    "Survival · hazard ratio HR",
    "Survie · rapport de risque HR",
    "Анализ выживаемости · отношение рисков HR",
    "Supervivencia · razón de riesgos HR",
    "生存解析・ハザード比 HR",
    "Sobrevivência · razão de risco HR",
    "Überleben · Hazard Ratio HR",
    "Преживљавање · однос ризика HR",
    "생존 분석 · 위험비 HR"
  ],
  [
    "提取",
    "Extraction",
    "Extraction",
    "Извлечение",
    "Extracción",
    "抽出",
    "Extração",
    "Extraktion",
    "Извлачење",
    "추출"
  ],
  [
    "未载入文献",
    "No references loaded",
    "Aucune référence chargée",
    "Публикации не загружены",
    "No hay referencias cargadas",
    "文献が読み込まれていません",
    "Nenhuma referência carregada",
    "Keine Referenzen geladen",
    "Референце нису учитане",
    "불러온 문헌 없음"
  ],
  [
    "结构化数据提取",
    "Structured data extraction",
    "Extraction structurée des données",
    "Структурированное извлечение данных",
    "Extracción estructurada de datos",
    "構造化データ抽出",
    "Extração estruturada de dados",
    "Strukturierte Datenextraktion",
    "Структурисано извлачење података",
    "구조화된 자료 추출"
  ],
  [
    "研究设计（提取模板）",
    "Study design (extraction template)",
    "Design d’étude (modèle d’extraction)",
    "Дизайн исследования (шаблон извлечения)",
    "Diseño del estudio (plantilla de extracción)",
    "研究デザイン（抽出テンプレート）",
    "Desenho do estudo (modelo de extração)",
    "Studiendesign (Extraktionsvorlage)",
    "Дизајн студије (шаблон извлачења)",
    "연구 설계(추출 템플릿)"
  ],
  [
    "研究设计",
    "Study design",
    "Design d’étude",
    "Дизайн исследования",
    "Diseño del estudio",
    "研究デザイン",
    "Desenho do estudo",
    "Studiendesign",
    "Дизајн студије",
    "연구 설계"
  ],
  [
    "研究编号（与数据分析页一致）",
    "Study ID (same as on the analysis page)",
    "Identifiant d’étude (identique à la page d’analyse)",
    "Идентификатор исследования (как на странице анализа)",
    "ID del estudio (igual que en la página de análisis)",
    "研究番号（データ分析ページと同一）",
    "ID do estudo (igual à página de análise)",
    "Studien-ID (wie auf der Analyseseite)",
    "ID студије (исто као на страни анализе)",
    "연구 ID(데이터 분석 페이지와 동일)"
  ],
  [
    "比较方向，如：治疗组与对照组",
    "Comparison direction, e.g. treatment vs control",
    "Direction de la comparaison, p. ex. traitement vs contrôle",
    "Направление сравнения, напр. лечение vs контроль",
    "Dirección de la comparación, p. ej. tratamiento vs control",
    "比較の方向（例：治療群 vs 対照群）",
    "Direção da comparação, ex. tratamento vs controle",
    "Vergleichsrichtung, z. B. Behandlung vs. Kontrolle",
    "Смер поређења, нпр. третман vs контрола",
    "비교 방향(예: 치료군 대비 대조군)"
  ],
  [
    "原文页码、表或图（可选）",
    "Source page, table or figure (optional)",
    "Page, tableau ou figure du rapport source (facultatif)",
    "Страница, таблица или рисунок источника (необязательно)",
    "Página, tabla o figura del informe fuente (opcional)",
    "原典のページ・表・図（任意）",
    "Página, tabela ou figura do relatório original (opcional)",
    "Seite, Tabelle oder Abbildung der Quelle (optional)",
    "Страна, табела или слика извора (опционо)",
    "원문 쪽수·표·그림(선택)"
  ],
  [
    "校验",
    "Validate",
    "Valider",
    "Проверить",
    "Validar",
    "検証",
    "Validar",
    "Prüfen",
    "Провери",
    "검증"
  ],
  [
    "计算效应量并保存",
    "Compute effect size and save",
    "Calculer la taille d’effet et enregistrer",
    "Вычислить величину эффекта и сохранить",
    "Calcular el tamaño del efecto y guardar",
    "効果量を計算して保存",
    "Calcular o tamanho do efeito e salvar",
    "Effektgröße berechnen und speichern",
    "Израчунај величину ефекта и сачувај",
    "효과 크기 계산 및 저장"
  ],
  [
    "✓ 校验通过，可以计算并保存。",
    "✓ Validation passed; ready to compute and save.",
    "✓ Validation réussie ; prêt à calculer et enregistrer.",
    "✓ Проверка пройдена; можно вычислять и сохранять.",
    "✓ Validación superada; listo para calcular y guardar.",
    "✓ 検証に合格しました。計算して保存できます。",
    "✓ Validação aprovada; pronto para calcular e salvar.",
    "✓ Prüfung bestanden; bereit zum Berechnen und Speichern.",
    "✓ Провера је прошла; спремно за рачунање и чување.",
    "✓ 검증을 통과했습니다. 계산해 저장할 수 있습니다."
  ],
  [
    "选择研究设计",
    "Choose study design",
    "Choisir un design d’étude",
    "Выберите дизайн исследования",
    "Elegir diseño del estudio",
    "研究デザインを選択",
    "Escolher desenho do estudo",
    "Studiendesign wählen",
    "Изабери дизајн студије",
    "연구 설계 선택"
  ],
  [
    "模板暂不可用",
    "Templates temporarily unavailable",
    "Modèles temporairement indisponibles",
    "Шаблоны временно недоступны",
    "Plantillas no disponibles temporalmente",
    "テンプレートを一時的に利用できません",
    "Modelos temporariamente indisponíveis",
    "Vorlagen vorübergehend nicht verfügbar",
    "Шаблони привремено недоступни",
    "템플릿을 일시적으로 사용할 수 없음"
  ],
  [
    "选择研究设计后，按模板字段动态生成提取表单。",
    "After choosing a study design, the extraction form is generated from the template fields.",
    "Après le choix du design, le formulaire d’extraction est généré à partir des champs du modèle.",
    "После выбора дизайна форма извлечения строится по полям шаблона.",
    "Tras elegir el diseño, el formulario de extracción se genera con los campos de la plantilla.",
    "研究デザインを選択すると、テンプレートの項目から抽出フォームを動的に生成します。",
    "Após escolher o desenho, o formulário de extração é gerado a partir dos campos do modelo.",
    "Nach der Wahl des Studiendesigns wird das Extraktionsformular aus den Vorlagenfeldern erzeugt.",
    "Након избора дизајна, образац извлачења се генерише из поља шаблона.",
    "연구 설계를 선택하면 템플릿 필드에 따라 추출 양식이 동적으로 생성됩니다."
  ],
  [
    "不适用（诊断 2×2）",
    "Not applicable (diagnostic 2×2)",
    "Non applicable (2×2 diagnostique)",
    "Не применимо (диагностическая 2×2)",
    "No aplicable (2×2 diagnóstica)",
    "該当なし（診断 2×2）",
    "Não se aplica (2×2 diagnóstico)",
    "Nicht zutreffend (Diagnostik-2×2)",
    "Не примењиво (дијагностички 2×2)",
    "해당 없음(진단 2×2)"
  ],
  [
    "四格合计 N = {0}（患病组 {1} · 非患病组 {2}）",
    "2×2 total N = {0} (diseased {1} · non-diseased {2})",
    "Total 2×2 N = {0} (malades {1} · non malades {2})",
    "Итог 2×2 N = {0} (больные {1} · люди без заболевания {2})",
    "Total 2×2 N = {0} (enfermos {1} · no enfermos {2})",
    "四格の合計 N = {0}（罹患群 {1}・非罹患群 {2}）",
    "Total 2×2 N = {0} (doentes {1} · não doentes {2})",
    "2×2-Gesamt N = {0} (Kranke {1} · Nicht-Kranke {2})",
    "Укупно 2×2 N = {0} (болесни {1} · особе без болести {2})",
    "2×2 합계 N = {0}(환자군 {1} · 비환자군 {2})"
  ],
  [
    "填写四格后显示合计。",
    "Totals appear once the four cells are filled.",
    "Les totaux s’affichent une fois les quatre cases remplies.",
    "Итоги появятся после заполнения четырёх ячеек.",
    "Los totales aparecen al completar las cuatro casillas.",
    "4 つのセルを入力すると合計を表示します。",
    "Os totais aparecem ao preencher as quatro células.",
    "Die Summen erscheinen, sobald die vier Felder gefüllt sind.",
    "Збирови се приказују попуњавањем четири поља.",
    "네 칸을 채우면 합계가 표시됩니다."
  ],
  [
    "请先选择研究设计。",
    "Choose a study design first.",
    "Choisissez d’abord un design d’étude.",
    "Сначала выберите дизайн исследования.",
    "Elija primero el diseño del estudio.",
    "先に研究デザインを選択してください。",
    "Escolha primeiro o desenho do estudo.",
    "Wählen Sie zuerst das Studiendesign.",
    "Прво изаберите дизајн студије.",
    "먼저 연구 설계를 선택하세요."
  ],
  [
    "必填",
    "Required",
    "Obligatoire",
    "Обязательно",
    "Obligatorio",
    "必須",
    "Obrigatório",
    "Erforderlich",
    "Обавезно",
    "필수"
  ],
  [
    "{0} 为必填项。",
    "{0} is required.",
    "{0} est obligatoire.",
    "{0} обязательно к заполнению.",
    "{0} es obligatorio.",
    "{0} は必須です。",
    "{0} é obrigatório.",
    "{0} ist erforderlich.",
    "{0} је обавезно.",
    "{0}은(는) 필수입니다."
  ],
  [
    "必须是数字",
    "Must be a number",
    "Doit être un nombre",
    "Должно быть числом",
    "Debe ser un número",
    "数値である必要があります",
    "Deve ser um número",
    "Muss eine Zahl sein",
    "Мора бити број",
    "숫자여야 합니다"
  ],
  [
    "{0} 必须是数字。",
    "{0} must be a number.",
    "{0} doit être un nombre.",
    "{0} должно быть числом.",
    "{0} debe ser un número.",
    "{0} は数値である必要があります。",
    "{0} deve ser um número.",
    "{0} muss eine Zahl sein.",
    "{0} мора бити број.",
    "{0}은(는) 숫자여야 합니다."
  ],
  [
    "不能小于 {0}",
    "Must be at least {0}",
    "Doit valoir au moins {0}",
    "Должно быть не менее {0}",
    "Debe ser al menos {0}",
    "{0} 以上である必要があります",
    "Deve ser pelo menos {0}",
    "Muss mindestens {0} sein",
    "Мора бити најмање {0}",
    "{0} 이상이어야 합니다"
  ],
  [
    "{0} 不能小于 {1}。",
    "{0} must not be below {1}.",
    "{0} ne peut être inférieur à {1}.",
    "{0} не может быть меньше {1}.",
    "{0} no puede ser menor que {1}.",
    "{0} は {1} 未満にできません。",
    "{0} não pode ser menor que {1}.",
    "{0} darf nicht unter {1} liegen.",
    "{0} не може бити мање од {1}.",
    "{0}은(는) {1} 미만일 수 없습니다."
  ],
  [
    "不能大于 {0}",
    "Must be at most {0}",
    "Doit valoir au plus {0}",
    "Должно быть не более {0}",
    "Debe ser como máximo {0}",
    "{0} 以下である必要があります",
    "Deve ser no máximo {0}",
    "Darf höchstens {0} sein",
    "Мора бити највише {0}",
    "{0} 이하여야 합니다"
  ],
  [
    "{0} 不能大于 {1}。",
    "{0} must not exceed {1}.",
    "{0} ne peut dépasser {1}.",
    "{0} не может превышать {1}.",
    "{0} no puede superar {1}.",
    "{0} は {1} を超えられません。",
    "{0} não pode exceder {1}.",
    "{0} darf {1} nicht überschreiten.",
    "{0} не може бити веће од {1}.",
    "{0}은(는) {1}을(를) 초과할 수 없습니다."
  ],
  [
    "必须是非负整数",
    "Must be a non-negative integer",
    "Doit être un entier non négatif",
    "Должно быть неотрицательным целым",
    "Debe ser un entero no negativo",
    "0 以上の整数である必要があります",
    "Deve ser um inteiro não negativo",
    "Muss eine nicht negative ganze Zahl sein",
    "Мора бити ненегативан цео број",
    "음이 아닌 정수여야 합니다"
  ],
  [
    "{0} 必须是非负整数。",
    "{0} must be a non-negative integer.",
    "{0} doit être un entier non négatif.",
    "{0} должно быть неотрицательным целым.",
    "{0} debe ser un entero no negativo.",
    "{0} は 0 以上の整数である必要があります。",
    "{0} deve ser um inteiro não negativo.",
    "{0} muss eine nicht negative ganze Zahl sein.",
    "{0} мора бити ненегативан цео број.",
    "{0}은(는) 음이 아닌 정수여야 합니다."
  ],
  [
    "CI 须包含 HR",
    "The CI must contain the HR",
    "L’IC doit contenir le HR",
    "Доверительный интервал должен охватывать HR",
    "El IC debe contener el HR",
    "CI は HR を含む必要があります",
    "O IC deve conter o HR",
    "Das Konfidenzintervall muss die HR einschließen",
    "Интервал поверења мора обухватити HR",
    "신뢰구간은 HR을 포함해야 합니다"
  ],
  [
    "HR 必须位于 CI 下限与上限之间。",
    "The HR must lie between the CI lower and upper bounds.",
    "Le HR doit se situer entre la borne inférieure et la borne supérieure de l’IC.",
    "HR должен находиться между нижней и верхней границами доверительного интервала.",
    "El HR debe estar entre el límite inferior y el superior del IC.",
    "HR は CI の下限と上限の間にある必要があります。",
    "O HR deve estar entre o limite inferior e o superior do IC.",
    "Die HR muss zwischen der unteren und oberen KI-Grenze liegen.",
    "HR мора бити између доње и горње границе интервала поверења.",
    "HR은 신뢰구간 하한과 상한 사이에 있어야 합니다."
  ],
  [
    "请填写研究编号（与数据分析页一致）。",
    "Enter the study ID (same as on the analysis page).",
    "Saisissez l’identifiant d’étude (identique à la page d’analyse).",
    "Введите идентификатор исследования (как на странице анализа).",
    "Introduzca el ID del estudio (igual que en la página de análisis).",
    "研究番号を入力してください（データ分析ページと同一）。",
    "Informe o ID do estudo (igual à página de análise).",
    "Geben Sie die Studien-ID ein (wie auf der Analyseseite).",
    "Унесите ID студије (исто као на страни анализе).",
    "연구 ID를 입력하세요(데이터 분석 페이지와 동일)."
  ],
  [
    "提取结果入库需要比较方向、结局与时间点。",
    "Saving an extraction requires the comparison, outcome and time point.",
    "L’enregistrement d’une extraction exige la comparaison, le critère et le moment de mesure.",
    "Для сохранения результата извлечения нужны направление сравнения, исход и момент измерения.",
    "Para guardar una extracción se requieren la comparación, el desenlace y el momento de medición.",
    "抽出結果の保存には比較方向・アウトカム・測定時点が必要です。",
    "Salvar uma extração exige a comparação, o desfecho e o momento da medição.",
    "Zum Speichern einer Extraktion sind Vergleich, Endpunkt und Messzeitpunkt erforderlich.",
    "Чување извлачења захтева поређење, исход и време мерења.",
    "추출 결과를 저장하려면 비교 방향·결과·측정 시점이 필요합니다."
  ],
  [
    "请显式选择效应方向（试验组 vs 对照组）如何对应到比较方向。",
    "Choose explicitly how the effect direction (experimental vs control) maps onto the comparison.",
    "Indiquez explicitement comment la direction de l’effet (expérimental vs contrôle) correspond à la comparaison.",
    "Явно укажите, как направление эффекта (экспериментальная vs контроль) соотносится со сравнением.",
    "Indique explícitamente cómo se corresponde la dirección del efecto (experimental vs control) con la comparación.",
    "効果の方向（試験群 vs 対照群）が比較方向へどう対応するかを明示的に選択してください。",
    "Indique explicitamente como a direção do efeito (experimental vs controle) corresponde à comparação.",
    "Wählen Sie explizit, wie die Effektrichtung (experimentell vs. Kontrolle) dem Vergleich zugeordnet ist.",
    "Изричито изаберите како смер ефекта (експериментална vs контрола) одговара поређењу.",
    "효과 방향(시험군 대비 대조군)이 비교 방향에 어떻게 대응하는지 명시적으로 선택하세요."
  ],
  [
    "请填写研究编号。",
    "Enter the study ID.",
    "Saisissez l’identifiant d’étude.",
    "Введите идентификатор исследования.",
    "Introduzca el ID del estudio.",
    "研究番号を入力してください。",
    "Informe o ID do estudo.",
    "Geben Sie die Studien-ID ein.",
    "Унесите ID студије.",
    "연구 ID를 입력하세요."
  ],
  [
    "诊断 2×2 入库需要 {0}。",
    "Saving a diagnostic 2×2 requires {0}.",
    "L’enregistrement d’un 2×2 diagnostique exige {0}.",
    "Для сохранения диагностической 2×2 требуется {0}.",
    "Guardar una 2×2 diagnóstica requiere {0}.",
    "診断 2×2 の保存には {0} が必要です。",
    "Salvar um 2×2 diagnóstico exige {0}.",
    "Zum Speichern einer Diagnostik-2×2 ist {0} erforderlich.",
    "Чување дијагностичког 2×2 захтева {0}.",
    "진단 2×2를 저장하려면 {0}이(가) 필요합니다."
  ],
  [
    "患病组与非患病组都必须有受试者。",
    "Both the diseased and non-diseased groups must contain participants.",
    "Les groupes malades et non malades doivent tous deux contenir des participants.",
    "В обеих группах — с заболеванием и без него — должны быть участники.",
    "Tanto el grupo de enfermos como el de no enfermos deben tener participantes.",
    "罹患群・非罹患群の両方に参加者が必要です。",
    "Os grupos doentes e não doentes devem ter participantes.",
    "Sowohl die Gruppe der Kranken als auch der Nicht-Kranken muss Teilnehmer enthalten.",
    "И група са болешћу и група без болести морају имати учеснике.",
    "질환군과 비질환군 모두 참여자가 있어야 합니다."
  ],
  [
    "当前没有可提取的文献。",
    "No references available for extraction.",
    "Aucune référence à extraire.",
    "Нет публикаций для извлечения.",
    "No hay referencias para extraer.",
    "抽出できる文献がありません。",
    "Não há referências para extração.",
    "Keine Referenzen für die Extraktion.",
    "Нема референци за извлачење.",
    "추출할 문헌이 없습니다."
  ],
  [
    "校验未通过，请修正标红字段。",
    "Validation failed; fix the fields highlighted in red.",
    "Échec de la validation ; corrigez les champs signalés en rouge.",
    "Проверка не пройдена; исправьте поля, отмеченные красным.",
    "La validación falló; corrija los campos marcados en rojo.",
    "検証に失敗しました。赤く表示された項目を修正してください。",
    "A validação falhou; corrija os campos destacados em vermelho.",
    "Die Prüfung ist fehlgeschlagen; korrigieren Sie die rot markierten Felder.",
    "Провера није прошла; исправите поља обележена црвеним.",
    "검증에 실패했습니다. 빨간색으로 표시된 필드를 수정하세요."
  ],
  [
    "正在计算效应量并保存…",
    "Computing the effect size and saving…",
    "Calcul de la taille d’effet et enregistrement…",
    "Вычисление величины эффекта и сохранение…",
    "Calculando el tamaño del efecto y guardando…",
    "効果量を計算して保存中…",
    "Calculando o tamanho do efeito e salvando…",
    "Effektgröße wird berechnet und gespeichert…",
    "Рачунање величине ефекта и чување…",
    "효과 크기를 계산해 저장하는 중…"
  ],
  [
    "{0}（本次无可计算的单数值估计）",
    "{0} (no single-number estimate computable this time)",
    "{0} (aucune estimation numérique unique calculable cette fois)",
    "{0} (в этот раз единичная числовая оценка не вычисляется)",
    "{0} (esta vez no se puede calcular una estimación numérica única)",
    "{0}（今回、単一の数値推定は計算できません）",
    "{0} (desta vez não há estimativa numérica única calculável)",
    "{0} (diesmal keine berechenbare Einzelschätzung)",
    "{0} (овај пут нема израчунљиве једнобројне процене)",
    "{0}(이번에는 단일 수치 추정을 계산할 수 없음)"
  ],
  [
    "比值类指标（RR/OR/HR/DOR）的 SE 已按对数尺度换算后保存。",
    "For ratio measures (RR/OR/HR/DOR) the SE was converted to the log scale before saving.",
    "Pour les mesures de rapport (RR/OR/HR/DOR), l’ET a été converti en échelle logarithmique avant enregistrement.",
    "Для относительных показателей (RR/OR/HR/DOR) стандартная ошибка пересчитана в логарифмическую шкалу перед сохранением.",
    "Para medidas de razón (RR/OR/HR/DOR), el EE se convirtió a escala logarítmica antes de guardar.",
    "比に関する指標（RR/OR/HR/DOR）の SE は対数スケールに換算して保存しました。",
    "Para medidas de razão (RR/OR/HR/DOR), o EP foi convertido para a escala logarítmica antes de salvar.",
    "Bei Verhältnismaßen (RR/OR/HR/DOR) wurde der SE vor dem Speichern auf die Log-Skala umgerechnet.",
    "За односне мере (RR/OR/HR/DOR) SE је пре чувања претворена на логаритамску скалу.",
    "비율형 지표(RR/OR/HR/DOR)의 표준오차는 로그 척도로 환산해 저장했습니다."
  ],
  [
    "含零单元格：DOR 无定义（未做连续性校正），原始计数已入库供 bivariate 合成。",
    "Zero cell present: the DOR is undefined (no continuity correction); the raw counts were saved for bivariate synthesis.",
    "Cellule nulle : le DOR n’est pas défini (aucune correction de continuité) ; les comptes bruts ont été enregistrés pour la synthèse bivariate.",
    "Есть нулевая ячейка: DOR не определён (без коррекции непрерывности); исходные подсчёты сохранены для двумерного синтеза.",
    "Hay celda cero: el DOR no está definido (sin corrección de continuidad); los recuentos brutos se guardaron para la síntesis bivariada.",
    "ゼロセルがあるため DOR は定義されません（連続性補正なし）。二変量メタ解析用に元の度数データを保存しました。",
    "Há célula zero: o DOR não está definido (sem correção de continuidade); as contagens brutas foram salvas para a síntese bivariada.",
    "Nullzelle vorhanden: Das DOR ist undefiniert (keine Stetigkeitskorrektur); die Rohzählungen wurden für die bivariate Synthese gespeichert.",
    "Постоји нулта ћелија: DOR није дефинисан (без корекције континуитета); сирови бројеви су сачувани за биваријантну синтезу.",
    "0인 셀이 있어 DOR을 정의할 수 없습니다(연속성 보정 없음). 이변량 메타분석에 사용할 원시 빈도 자료를 저장했습니다."
  ],
  [
    "已保存到分析池（review_effects，结果编号 {0}），可在「数据分析」页合并。",
    "Saved to the analysis pool (review_effects, result ID {0}); pool it on the Data analysis page.",
    "Enregistré dans le pool d’analyse (review_effects, résultat n° {0}) ; à regrouper sur la page d’analyse.",
    "Сохранено в аналитический пул (review_effects, номер результата {0}); объединение — на странице анализа.",
    "Guardado en el grupo de análisis (review_effects, resultado n.º {0}); se puede combinar en la página de análisis.",
    "分析プール（review_effects、結果番号 {0}）に保存しました。「データ分析」ページで統合できます。",
    "Salvo no conjunto de análise (review_effects, resultado nº {0}); combine na página de análise.",
    "Im Analysepool gespeichert (review_effects, Ergebnis-Nr. {0}); auf der Analyseseite zusammenfassen.",
    "Сачувано у аналитички фонд (review_effects, број резултата {0}); обједините на страни анализе.",
    "분석 풀에 저장했습니다(review_effects, 결과 번호 {0}). “데이터 분석” 페이지에서 통합할 수 있습니다."
  ],
  [
    "已保存到诊断准确性结果库（review_dta_results），供 bivariate 合成。",
    "Saved to the diagnostic-accuracy results store (review_dta_results) for bivariate synthesis.",
    "Enregistré dans la base de résultats de précision diagnostique (review_dta_results) pour synthèse bivariate.",
    "Сохранено в хранилище результатов диагностической точности (review_dta_results) для двумерного синтеза.",
    "Guardado en el almacén de resultados de exactitud diagnóstica (review_dta_results) para síntesis bivariada.",
    "診断精度結果ライブラリ（review_dta_results）に保存しました。二変量メタ解析に使用します。",
    "Salvo no repositório de resultados de acurácia diagnóstica (review_dta_results) para síntese bivariada.",
    "Im Diagnostik-Ergebnisspeicher (review_dta_results) für die bivariate Synthese gespeichert.",
    "Сачувано у складиште резултата дијагностичке тачности (review_dta_results) за биваријантну синтезу.",
    "진단 정확도 결과 저장소(review_dta_results)에 저장했습니다. 이변량 메타분석에 사용합니다."
  ],
  [
    "提取完成后可到「数据分析」页做偏倚风险（RoB）评估。",
    "After extraction, assess risk of bias (RoB) on the Data analysis page.",
    "Après l’extraction, évaluez le risque de biais (RoB) sur la page d’analyse.",
    "После извлечения оцените риск систематической ошибки (RoB) на странице анализа.",
    "Tras la extracción, evalúe el riesgo de sesgo (RoB) en la página de análisis.",
    "抽出後に「データ分析」ページでバイアスリスク（RoB）を評価できます。",
    "Após a extração, avalie o risco de viés (RoB) na página de análise.",
    "Nach der Extraktion können Sie auf der Analyseseite das Verzerrungsrisiko (RoB) bewerten.",
    "Након извлачења, на страни анализе процените ризик пристрасности (RoB).",
    "추출 후 “데이터 분석” 페이지에서 비뚤림 위험(RoB)을 평가할 수 있습니다."
  ],
  [
    "✓ 效应量已计算并保存（{0}）",
    "✓ Effect size computed and saved ({0})",
    "✓ Taille d’effet calculée et enregistrée ({0})",
    "✓ Величина эффекта вычислена и сохранена ({0})",
    "✓ Tamaño del efecto calculado y guardado ({0})",
    "✓ 効果量を計算して保存しました（{0}）",
    "✓ Tamanho do efeito calculado e salvo ({0})",
    "✓ Effektgröße berechnet und gespeichert ({0})",
    "✓ Величина ефекта израчуната и сачувана ({0})",
    "✓ 효과 크기를 계산해 저장했습니다({0})"
  ],
  [
    "{0} = {1}, SE = {2}", "{0} = {1}, SE = {2}", "{0} = {1}, SE = {2}", "{0} = {1}, SE = {2}", "{0} = {1}, SE = {2}", "{0} = {1}、SE = {2}", "{0} = {1}, SE = {2}", "{0} = {1}, SE = {2}", "{0} = {1}, SE = {2}", "{0} = {1}, SE = {2}"
  ],
  [
    "CI 上限",
    "CI upper bound",
    "Borne supérieure de l’IC",
    "Верхняя граница ДИ",
    "Límite superior del IC",
    "CI 上限",
    "Limite superior do IC",
    "Obere KI-Grenze",
    "Горња граница интервала поверења",
    "신뢰구간 상한"
  ],
  [
    "CI 下限",
    "CI lower bound",
    "Borne inférieure de l’IC",
    "Нижняя граница ДИ",
    "Límite inferior del IC",
    "CI 下限",
    "Limite inferior do IC",
    "Untere KI-Grenze",
    "Доња граница интервала поверења",
    "신뢰구간 하한"
  ],
  [
    "对照组SD",
    "Control-group SD",
    "ET du groupe contrôle",
    "SD контрольной группы",
    "DE del grupo control",
    "対照群 SD",
    "DP do grupo controle",
    "SD der Kontrollgruppe",
    "SD контролне групе",
    "대조군 표준편차"
  ],
  [
    "对照组总数",
    "Control-group total",
    "Effectif total du groupe contrôle",
    "Всего в контрольной группе",
    "Total del grupo control",
    "対照群の総数",
    "Total do grupo controle",
    "Gesamtzahl der Kontrollgruppe",
    "Укупно у контрольној групи",
    "대조군 총 수"
  ],
  [
    "治疗组SD",
    "Treatment-group SD",
    "ET du groupe traitement",
    "SD группы лечения",
    "DE del grupo tratamiento",
    "治療群 SD",
    "DP do grupo tratamento",
    "SD der Behandlungsgruppe",
    "SD групе третмана",
    "치료군 표준편차"
  ],
  [
    "治疗组总数",
    "Treatment-group total",
    "Effectif total du groupe traitement",
    "Всего в группе лечения",
    "Total del grupo tratamiento",
    "治療群の総数",
    "Total do grupo tratamento",
    "Gesamtzahl der Behandlungsgruppe",
    "Укупно у групи третмана",
    "치료군 총 수"
  ],
  [
    "结局单位",
    "Outcome unit",
    "Unité du critère",
    "Единица исхода",
    "Unidad del desenlace",
    "アウトカムの単位",
    "Unidade do desfecho",
    "Einheit des Endpunkts",
    "Јединица исхода",
    "결과 단위"
  ],
  [
    "待评价试验（index_test）",
    "index_test",
    "test évalué (index_test)",
    "оцениваемый тест (index_test)",
    "prueba evaluada (index_test)",
    "評価対象の検査（index_test）",
    "teste avaliado (index_test)",
    "Index-Test (index_test)",
    "тест који се оцењује (index_test)",
    "평가 대상 검사(index_test)"
  ],
  [
    "目标疾病（target_condition）",
    "target_condition",
    "maladie cible (target_condition)",
    "целевое заболевание (target_condition)",
    "enfermedad objetivo (target_condition)",
    "対象疾患（target_condition）",
    "doença-alvo (target_condition)",
    "Zielerkrankung (target_condition)",
    "циљна болест (target_condition)",
    "대상 질환(target_condition)"
  ],
  [
    "诊断阈值（threshold）",
    "threshold",
    "seuil (threshold)",
    "пороговое значение (threshold)",
    "umbral (threshold)",
    "判定閾値（threshold）",
    "limiar (threshold)",
    "Schwellenwert (threshold)",
    "праг (threshold)",
    "역치(threshold)"
  ],
  [
    "参考标准（reference_standard）",
    "reference_standard",
    "standard de référence (reference_standard)",
    "референтный стандарт (reference_standard)",
    "estándar de referencia (reference_standard)",
    "参照標準（reference_standard）",
    "padrão de referência (reference_standard)",
    "Referenzstandard (reference_standard)",
    "референтни стандард (reference_standard)",
    "참조 표준(reference_standard)"
  ]
];
 RFLang.register(rows);
 window.RFInterfaceVocabulary=Object.freeze(rows.map(row=>Object.freeze(row.slice())));
})();
