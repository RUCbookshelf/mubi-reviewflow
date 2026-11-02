/* ReviewFlow · R11 ERIC 教育检索 —— 导入页「从 ERIC 检索文献」入口。
 *
 * 治理红线（与 zotero_import.js / living_review.js / ai_suggest.js 同一口径）：
 *   - 十语言词条自包含注册（RFLang.register，列序 zh/en/fr/ru/es/ja/pt/de/sr/ko）；
 *   - 联网默认关 + 显式手动触发：页面加载零网络请求；出站只有两段，且每段都由
 *     一次用户点击触发：
 *       1) 「预览命中」→ POST /api/tasks/{task}/eric/search（只读，不入库）；
 *       2) 「检索并导入」→ POST /api/tasks/{task}/eric/import（受保护导入管线）。
 *   - 隐私披露常驻入口卡：只访问 ERIC 官方 API（api.ies.ed.gov），不向其他
 *     地址发送数据；
 *   - 失败优雅降级：不可达/超时/坏响应/查询语法错误/命中超限/零结果均给可读
 *     提示，绝不中断导入页其他功能；初始化与全部回调自捕获异常；
 *   - 渲染红线：所有动态数据经 mk()/textContent，绝不 innerHTML；
 *   - 导入成功后复用导入页的导入报告（renderImportReport）与进度刷新。
 *
 * 纯逻辑（错误分流/摘要文案/占位填充）经 module.exports 暴露，
 * build/tests/test_r11_eric_frontend.py 以 node 真实加载并钉行为。
 */
(function (root) {
  'use strict';

  /* =====================================================================
   * 一、纯逻辑（node 直接 require；无 DOM 依赖）
   * ===================================================================== */

  /** 连接类失败（不可达/超时/重定向）→ 统一的修复指引。 */
  var CONNECT_FAIL_CODES = ['eric_unreachable', 'eric_timeout', 'eric_redirect'];

  function isNetworkFailure(error) {
    var code = error && error.code;
    return CONNECT_FAIL_CODES.indexOf(code) >= 0;
  }

  /* 后端 message 是中文原文；前端按 code 映射到已注册词条，绝不透传后端
     技术串（非中文界面会泄漏中文）。 */
  var CODE_TEXT = {
    eric_http_error: 'ERIC 返回了无法识别的响应。请稍后重试，或改用导出文件导入。',
    eric_bad_response: 'ERIC 返回了无法识别的响应。请稍后重试，或改用导出文件导入。',
    eric_response_too_large: 'ERIC 返回了无法识别的响应。请稍后重试，或改用导出文件导入。',
    eric_incomplete: 'ERIC 返回的题录不完整，已停止导入。请稍后重试，或收窄检索式。',
    eric_query_error: 'ERIC 拒绝了该检索式。请检查字段名与引号是否合法后重试。',
    eric_bad_query: 'ERIC 拒绝了该检索式。请检查字段名与引号是否合法后重试。',
    eric_too_many_hits: 'ERIC 命中数超过单次导入上限（500 条）。请收窄检索式（如加年份、限定字段）后分批导入。',
    eric_no_results: 'ERIC 检索命中 0 条。请调整检索式后重试。',
    eric_unavailable: '从 ERIC 导入失败，请稍后重试或改用导出文件导入。'
  };

  /** 错误 → 已注册的中文源串（经 T() 取当前语言）。 */
  function errorText(error) {
    var code = error && error.code;
    if (code === 'eric_timeout') {
      return 'ERIC 请求超时。请稍后重试，或改用导出文件导入。';
    }
    if (isNetworkFailure(error)) {
      return '无法连接 ERIC。请确认本机可以访问 api.ies.ed.gov 后重试；离线功能不受影响。';
    }
    if (code && CODE_TEXT[code]) return CODE_TEXT[code];
    if (typeof console !== 'undefined' && console.warn) {
      console.warn('[ERIC] 未登记的错误：', code, error && error.message);
    }
    return '从 ERIC 导入失败，请稍后重试或改用导出文件导入。';
  }

  /** 预览响应 → 状态模板（零命中与有命中两形态；zh 源串经 T() 取词）。 */
  function previewSummary(data) {
    var d = data || {};
    var total = Number(d.total_hits || 0);
    var returned = Number(d.returned || (d.records || []).length || 0);
    if (!total) {
      return { template: 'ERIC 检索命中 0 条。请调整检索式后重试。', args: [] };
    }
    return { template: '命中 {0} 条；以下为前 {1} 条题录。', args: [total, returned] };
  }

  /** 受保护导入响应 → 摘要模板（暂存与直入两形态；zh 源串经 T() 取词）。
   *
   * 受保护导入单文件（batch=false）返回的是 ``n_parsed``/``n_imported``；
   * 批处理形态才是 ``total_parsed``/``imported``。两种键都认，缺字段兜底 0，
   * 绝不把真实导入显示成 0 条。
   */
  function importSummary(data) {
    var d = data || {};
    var parsed = Number(d.total_parsed != null ? d.total_parsed
      : (d.n_parsed != null ? d.n_parsed : 0));
    if (d.staged) {
      return { template: '导入已暂存：解析 {0} 条，待核验后激活。', args: [parsed] };
    }
    var imported = Number(d.imported != null ? d.imported
      : (d.n_imported != null ? d.n_imported : 0));
    return { template: '导入完成：解析 {0} 条，精确去重 {1} 条，导入 {2} 条。',
             args: [parsed, Number(d.exact_duplicate_excess || 0), imported] };
  }

  /** {0}/{1} 占位填充（与主应用 T(模板, 参数) 同语义）。 */
  function fill(template, args) {
    return String(template || '').replace(/\{(\d+)\}/g, function (_, index) {
      var value = (args || [])[Number(index)];
      return value === undefined || value === null ? '' : String(value);
    });
  }

  var lib = {
    isNetworkFailure: isNetworkFailure,
    errorText: errorText,
    previewSummary: previewSummary,
    importSummary: importSummary,
    fill: fill
  };
  root.EricSearch = lib;
  if (typeof module !== 'undefined') module.exports = lib;

  /* =====================================================================
   * 二、浏览器接入（追加式：任何异常都只影响本模块，不影响导入页）
   * ===================================================================== */
  function mountBrowser() {
    if (typeof document === 'undefined' || typeof RFLang === 'undefined' ||
        typeof T !== 'function' || typeof api === 'undefined' ||
        typeof fetch !== 'function') return;

    /* 十语言词条（模块自包含；列序 zh-CN/en/fr/ru/es/ja/pt/de/sr/ko）。
       字面量形态：词典回放/静态守卫只认此处内联的字面量数组。 */
    RFLang.register([
    ["从 ERIC 检索文献", "Search ERIC for literature", "Rechercher dans ERIC", "Поиск литературы в ERIC", "Buscar literatura en ERIC", "ERIC で文献を検索", "Pesquisar literatura no ERIC", "Literatur in ERIC suchen", "Претражи литературу у ERIC-у", "ERIC에서 문헌 검색"],
    ["仅访问 ERIC 官方 API（api.ies.ed.gov）获取题录与摘要；不向其他地址发送数据。检索与导入都由你手动触发。", "Contacts only the official ERIC API (api.ies.ed.gov) for records and abstracts; sends nothing to other addresses. Both search and import are triggered manually by you.", "Ne contacte que l’API officielle ERIC (api.ies.ed.gov) pour les notices et résumés ; n’envoie rien à d’autres adresses. La recherche et l’importation sont déclenchés manuellement par vous.", "Обращается только к официальному API ERIC (api.ies.ed.gov) за записями и аннотациями; на другие адреса ничего не отправляет. Поиск и импорт выполняются только вручную.", "Solo contacta con la API oficial de ERIC (api.ies.ed.gov) para obtener registros y resúmenes; no envía nada a otras direcciones. La búsqueda y la importación las activa usted manualmente.", "ERIC公式API（api.ies.ed.gov）にのみ書誌と抄録を問い合わせ、他のアドレスには何も送信しません。検索と取り込みは毎回手動で実行します。", "Contata apenas a API oficial do ERIC (api.ies.ed.gov) para obter registros e resumos; não envia nada para outros endereços. A busca e a importação são acionadas manualmente por você.", "Kontaktiert nur die offizielle ERIC-API (api.ies.ed.gov) für Titel und Zusammenfassungen; an andere Adressen wird nichts gesendet. Suche und Import lösen Sie jeweils manuell aus.", "Повезује се само са званичним ERIC API-јем (api.ies.ed.gov) ради записа и сажетака; на друге адресе не шаље ништа. Претрагу и увоз покрећете ручно.", "공식 ERIC API(api.ies.ed.gov)에만 서지와 초록을 요청하며 다른 주소로는 아무것도 보내지 않습니다. 검색과 가져오기는 항상 직접 실행합니다."],
    ["ERIC 检索式（例如 title:\"second language\" AND publicationdateyear:2020）", "ERIC query (e.g. title:\"second language\" AND publicationdateyear:2020)", "Équation ERIC (p. ex. title:\"second language\" AND publicationdateyear:2020)", "Запрос ERIC (например, title:\"second language\" AND publicationdateyear:2020)", "Consulta ERIC (p. ej., title:\"second language\" AND publicationdateyear:2020)", "ERIC 検索式（例：title:\"second language\" AND publicationdateyear:2020）", "Consulta ERIC (ex.: title:\"second language\" AND publicationdateyear:2020)", "ERIC-Suchanfrage (z. B. title:\"second language\" AND publicationdateyear:2020)", "ERIC упит (нпр. title:\"second language\" AND publicationdateyear:2020)", "ERIC 검색식(예: title:\"second language\" AND publicationdateyear:2020)"],
    ["预览命中", "Preview hits", "Aperçu des résultats", "Предпросмотр результатов", "Previsualizar resultados", "ヒットをプレビュー", "Pré-visualizar resultados", "Treffer vorschauen", "Прегледај поготке", "검색 결과 미리보기"],
    ["正在检索 ERIC…", "Searching ERIC…", "Recherche dans ERIC…", "Поиск в ERIC…", "Buscando en ERIC…", "ERIC を検索中…", "Pesquisando no ERIC…", "ERIC wird durchsucht…", "Претрага ERIC-а…", "ERIC 검색 중…"],
    ["命中 {0} 条；以下为前 {1} 条题录。", "{0} hits; showing the first {1} records.", "{0} résultats ; affichage des {1} premières notices.", "Найдено записей: {0}; показаны первые {1}.", "{0} resultados; se muestran los primeros {1} registros.", "ヒット {0} 件。先頭 {1} 件を表示しています。", "{0} resultados; exibindo os primeiros {1} registros.", "{0} Treffer; die ersten {1} Datensätze werden angezeigt.", "Погодака: {0}; приказано првих {1} записа.", "검색 결과 {0}건 중 처음 {1}건을 표시합니다."],
    ["ERIC 检索命中 0 条。请调整检索式后重试。", "ERIC returned 0 hits. Adjust the query and try again.", "ERIC n’a renvoyé aucun résultat. Modifiez l’équation et réessayez.", "ERIC не вернул ни одной записи. Измените запрос и повторите.", "ERIC no devolvió resultados. Ajuste la consulta y reintente.", "ERIC のヒットは 0 件です。検索式を調整して再試行してください。", "O ERIC não retornou resultados. Ajuste a consulta e tente novamente.", "ERIC lieferte 0 Treffer. Passen Sie die Suchanfrage an und versuchen Sie es erneut.", "ERIC није вратио ниједан резултат. Прилагодите упит и покушајте поново.", "ERIC 검색 결과가 0건입니다. 검색식을 조정한 뒤 다시 시도하세요."],
    ["检索并导入", "Search and import", "Rechercher et importer", "Найти и импортировать", "Buscar e importar", "検索して取り込む", "Pesquisar e importar", "Suchen und importieren", "Претражи и увези", "검색 후 가져오기"],
    ["正在从 ERIC 导入…", "Importing from ERIC…", "Importation depuis ERIC…", "Импорт из ERIC…", "Importando desde ERIC…", "ERIC から取り込み中…", "Importando do ERIC…", "Import aus ERIC läuft…", "Увоз из ERIC-а…", "ERIC에서 가져오는 중…"],
    ["导入已暂存：解析 {0} 条，待核验后激活。", "Import staged: {0} parsed, pending review before activation.", "Importation mise en attente : {0} notices analysées, activation après vérification.", "Импорт поставлен в очередь: разобрано {0}, активация после проверки.", "Importación en espera: {0} analizados, activación tras la verificación.", "取り込みを一時保存：{0} 件を解析し、確認後に有効化します。", "Importação em espera: {0} analisados, ativação após verificação.", "Import vorgemerkt: {0} verarbeitet, Aktivierung nach Prüfung.", "Увоз је припремљен: обрађено {0}, активација након провере.", "가져오기 대기 중: {0}건 분석됨, 확인 후 활성화됩니다."],
    ["导入完成：解析 {0} 条，精确去重 {1} 条，导入 {2} 条。", "Import finished: {0} parsed, {1} exact duplicates, {2} imported.", "Importation terminée : {0} analysées, {1} doublons exacts, {2} importées.", "Импорт завершён: разобрано {0}, точных дубликатов {1}, импортировано {2}.", "Importación terminada: {0} analizados, {1} duplicados exactos, {2} importados.", "取り込み完了：{0} 件を解析、正確な重複 {1} 件、{2} 件を取り込みました。", "Importação concluída: {0} analisados, {1} duplicatas exatas, {2} importados.", "Import abgeschlossen: {0} verarbeitet, {1} exakte Dubletten, {2} importiert.", "Увоз завршен: обрађено {0}, тачних дупликата {1}, увезено {2}.", "가져오기 완료: {0}건 분석, 정확한 중복 {1}건, {2}건 가져옴."],
    ["请先填写 ERIC 检索式。", "Enter an ERIC query first.", "Saisissez d’abord une équation ERIC.", "Сначала введите запрос ERIC.", "Introduzca primero una consulta ERIC.", "先に ERIC 検索式を入力してください。", "Informe primeiro uma consulta ERIC.", "Geben Sie zuerst eine ERIC-Suchanfrage ein.", "Прво унесите ERIC упит.", "먼저 ERIC 검색식을 입력하세요."],
    ["请先在首页选择或创建任务，再使用 ERIC 检索。", "Pick or create a task on the Home page before using ERIC search.", "Choisissez ou créez d’abord une tâche dans l’accueil avant d’utiliser la recherche ERIC.", "Сначала выберите или создайте задачу на главной странице, затем используйте поиск ERIC.", "Elija o cree primero una tarea en Inicio antes de usar la búsqueda ERIC.", "先にホームでタスクを選択または作成してから、ERIC 検索を使用してください。", "Escolha ou crie primeiro uma tarefa na página inicial antes de usar a busca ERIC.", "Wählen oder erstellen Sie zuerst eine Aufgabe auf der Startseite, bevor Sie die ERIC-Suche verwenden.", "Прво изаберите или направите задатак на почетној, па користите ERIC претрагу.", "먼저 홈에서 작업을 선택하거나 만든 뒤 ERIC 검색을 사용하세요."],
    ["无法连接 ERIC。请确认本机可以访问 api.ies.ed.gov 后重试；离线功能不受影响。", "Cannot reach ERIC. Make sure this computer can access api.ies.ed.gov, then retry; offline features are unaffected.", "Impossible de joindre ERIC. Vérifiez que cet ordinateur peut accéder à api.ies.ed.gov, puis réessayez ; les fonctions hors ligne ne sont pas affectées.", "Не удаётся связаться с ERIC. Убедитесь, что этот компьютер имеет доступ к api.ies.ed.gov, и повторите; офлайн-функции не затронуты.", "No se puede conectar con ERIC. Compruebe que este equipo puede acceder a api.ies.ed.gov y reintente; las funciones sin conexión no se ven afectadas.", "ERIC に接続できません。このコンピューターから api.ies.ed.gov にアクセスできることを確認して再試行してください。オフライン機能は影響を受けません。", "Não é possível acessar o ERIC. Verifique se este computador consegue acessar api.ies.ed.gov e tente novamente; os recursos offline não são afetados.", "ERIC ist nicht erreichbar. Prüfen Sie, ob dieser Computer auf api.ies.ed.gov zugreifen kann, und versuchen Sie es erneut; Offline-Funktionen sind nicht betroffen.", "ERIC није доступан. Проверите да ли овај рачунар може да приступи api.ies.ed.gov, па покушајте поново; офлајн функције нису погођене.", "ERIC에 연결할 수 없습니다. 이 컴퓨터에서 api.ies.ed.gov에 접속할 수 있는지 확인한 뒤 다시 시도하세요. 오프라인 기능은 영향을 받지 않습니다."],
    ["ERIC 请求超时。请稍后重试，或改用导出文件导入。", "The ERIC request timed out. Retry later or import an exported file instead.", "La requête ERIC a expiré. Réessayez plus tard ou importez un fichier exporté.", "Запрос к ERIC истёк по времени. Повторите позже или импортируйте экспортированный файл.", "La solicitud a ERIC agotó el tiempo de espera. Reintente más tarde o importe un archivo exportado.", "ERIC へのリクエストがタイムアウトしました。後で再試行するか、書き出したファイルを取り込んでください。", "A solicitação ao ERIC excedeu o tempo limite. Tente novamente mais tarde ou importe um arquivo exportado.", "Die ERIC-Anfrage hat das Zeitlimit überschritten. Versuchen Sie es später erneut oder importieren Sie eine exportierte Datei.", "Захтев ка ERIC-у је истекао. Покушајте касније или увезите извезену датотеку.", "ERIC 요청 시간이 초과되었습니다. 나중에 다시 시도하거나 내보낸 파일을 가져오세요."],
    ["ERIC 返回了无法识别的响应。请稍后重试，或改用导出文件导入。", "ERIC returned an unrecognised response. Retry later or import an exported file instead.", "ERIC a renvoyé une réponse non reconnue. Réessayez plus tard ou importez un fichier exporté.", "ERIC вернул нераспознанный ответ. Повторите позже или импортируйте экспортированный файл.", "ERIC devolvió una respuesta no reconocida. Reintente más tarde o importe un archivo exportado.", "ERIC が解釈できない応答を返しました。後で再試行するか、書き出したファイルを取り込んでください。", "O ERIC retornou uma resposta não reconhecida. Tente novamente mais tarde ou importe um arquivo exportado.", "ERIC lieferte eine unbekannte Antwort. Versuchen Sie es später erneut oder importieren Sie eine exportierte Datei.", "ERIC је вратио непрепознат одговор. Покушајте касније или увезите извезену датотеку.", "ERIC가 인식할 수 없는 응답을 반환했습니다. 나중에 다시 시도하거나 내보낸 파일을 가져오세요."],
    ["ERIC 拒绝了该检索式。请检查字段名与引号是否合法后重试。", "ERIC rejected the query. Check the field names and quotation marks, then retry.", "ERIC a rejeté l’équation. Vérifiez les noms de champs et les guillemets, puis réessayez.", "ERIC отклонил запрос. Проверьте имена полей и кавычки и повторите.", "ERIC rechazó la consulta. Compruebe los nombres de campo y las comillas y reintente.", "ERIC が検索式を拒否しました。フィールド名と引用符を確認して再試行してください。", "O ERIC rejeitou a consulta. Verifique os nomes dos campos e as aspas e tente novamente.", "ERIC hat die Suchanfrage abgelehnt. Prüfen Sie Feldnamen und Anführungszeichen und versuchen Sie es erneut.", "ERIC је одбио упит. Проверите називе поља и наводнике, па покушајте поново.", "ERIC가 검색식을 거부했습니다. 필드 이름과 따옴표를 확인한 뒤 다시 시도하세요."],
    ["ERIC 命中数超过单次导入上限（500 条）。请收窄检索式（如加年份、限定字段）后分批导入。", "ERIC hits exceed the single-import limit (500). Narrow the query (e.g. add a year or field) and import in batches.", "Les résultats ERIC dépassent la limite d’un seul import (500). Restreignez l’équation (p. ex. ajoutez une année ou un champ) et importez par lots.", "Число результатов ERIC превышает лимит одного импорта (500). Сузьте запрос (например, добавьте год или поле) и импортируйте частями.", "Los resultados de ERIC superan el límite de una sola importación (500). Restrinja la consulta (p. ej., añada un año o un campo) e importe por lotes.", "ERIC のヒット数が 1 回の取り込み上限（500 件）を超えています。検索式を絞り（年やフィールドの追加など）、分割して取り込んでください。", "Os resultados do ERIC excedem o limite de uma única importação (500). Restrinja a consulta (por exemplo, adicione um ano ou campo) e importe em lotes.", "Die ERIC-Treffer überschreiten das Limit für einen Import (500). Schränken Sie die Suchanfrage ein (z. B. Jahr oder Feld ergänzen) und importieren Sie in Stapeln.", "ERIC погодци премашују ограничење једног увоза (500). Сузите упит (нпр. додајте годину или поље) и увезите у деловима.", "ERIC 검색 결과가 한 번 가져오기 한도(500건)를 초과했습니다. 검색식을 좁혀(연도나 필드 추가 등) 나누어 가져오세요."],
    ["ERIC 返回的题录不完整，已停止导入。请稍后重试，或收窄检索式。", "ERIC returned an incomplete record set, so the import stopped. Retry later or narrow the query.", "ERIC a renvoyé un ensemble de notices incomplet ; l’importation a été arrêtée. Réessayez plus tard ou restreignez l’équation.", "ERIC вернул неполный набор записей, поэтому импорт остановлен. Повторите позже или сузьте запрос.", "ERIC devolvió un conjunto incompleto de registros, por lo que se detuvo la importación. Reintente más tarde o restrinja la consulta.", "ERIC の書誌が不完全だったため、取り込みを停止しました。後で再試行するか、検索式を絞ってください。", "O ERIC retornou um conjunto incompleto de registros, então a importação foi interrompida. Tente novamente mais tarde ou restrinja a consulta.", "ERIC lieferte einen unvollständigen Datensatz, daher wurde der Import gestoppt. Versuchen Sie es später erneut oder schränken Sie die Suchanfrage ein.", "ERIC је вратио непотпун скуп записа, па је увоз заустављен. Покушајте касније или сузите упит.", "ERIC가 불완전한 서지 집합을 반환하여 가져오기를 중단했습니다. 나중에 다시 시도하거나 검색식을 좁히세요."],
    ["从 ERIC 导入失败，请稍后重试或改用导出文件导入。", "Import from ERIC failed. Retry later or import an exported file instead.", "L’importation depuis ERIC a échoué. Réessayez plus tard ou importez un fichier exporté.", "Импорт из ERIC не удался. Повторите позже или импортируйте экспортированный файл.", "La importación desde ERIC falló. Reintente más tarde o importe un archivo exportado.", "ERIC からの取り込みに失敗しました。後で再試行するか、書き出したファイルを取り込んでください。", "A importação do ERIC falhou. Tente novamente mais tarde ou importe um arquivo exportado.", "Der Import aus ERIC ist fehlgeschlagen. Versuchen Sie es später erneut oder importieren Sie eine exportierte Datei.", "Увоз из ERIC-а није успео. Покушајте касније или увезите извезену датотеку.", "ERIC에서 가져오지 못했습니다. 나중에 다시 시도하거나 내보낸 파일을 가져오세요."]
    ]);

    var section = document.querySelector('section.rf-import-page');
    if (!section || document.getElementById('rfEricEntry')) return;

    /* XSS 安全 DOM 构造器（textContent only）。 */
    function mk(tag, attrs, children) {
      var node = document.createElement(tag);
      if (attrs) Object.keys(attrs).forEach(function (name) {
        var value = attrs[name];
        if (value === null || value === undefined) return;
        if (name === 'class') node.className = value;
        else if (name === 'text') node.textContent = value;
        else if (name === 'style' && typeof value === 'object') Object.assign(node.style, value);
        else if (name.slice(0, 2) === 'on' && typeof value === 'function')
          node.addEventListener(name.slice(2), value);
        else node.setAttribute(name, value);
      });
      (children || []).forEach(function (child) {
        if (child !== null && child !== undefined) node.append(child);
      });
      return node;
    }

    var style = mk('style', { text:
      '.rf-eric-entry{margin-top:12px}' +
      '.rf-eric-query{width:100%;min-height:64px;font:inherit;font-size:.85rem;padding:8px 10px;' +
      'border:1px solid var(--border);border-radius:8px;background:var(--bg);color:inherit;resize:vertical}' +
      '.rf-eric-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px}' +
      '.rf-eric-status{font-size:.88rem;margin:8px 0 0;min-height:1.2em;white-space:pre-wrap}' +
      '.rf-eric-list{list-style:none;margin:8px 0 0;padding:0;max-height:320px;overflow:auto;' +
      'border:1px solid rgba(125,125,125,.35);border-radius:8px}' +
      '.rf-eric-list li{padding:7px 10px;border-top:1px solid rgba(125,125,125,.2)}' +
      '.rf-eric-list li:first-child{border-top:none}' +
      '.rf-eric-list .t{font-size:.86rem;font-weight:600}' +
      '.rf-eric-list .m{font-size:.76rem;opacity:.75;margin-top:2px}' });
    document.head.append(style);

    var state = { busy: false };

    function taskBase() {
      if (typeof S === 'undefined' || !S.task || !S.task.task_id) return null;
      if (typeof taskApi !== 'function') return null;
      return taskApi();
    }

    function screenerQuery() {
      return typeof scrQ === 'function' && typeof S !== 'undefined' && S.screener
        ? scrQ() : '';
    }

    var input = mk('textarea', {
      class: 'rf-eric-query', id: 'rfEricQuery', rows: '3',
      'aria-label': T('ERIC 检索式（例如 title:"second language" AND publicationdateyear:2020）'),
      placeholder: T('ERIC 检索式（例如 title:"second language" AND publicationdateyear:2020）')
    });
    var previewButton = mk('button', {
      class: 'btn b-out', id: 'rfEricPreview', type: 'button',
      text: T('预览命中'), onclick: function () { runPreview(); }
    });
    var importButton = mk('button', {
      class: 'btn b-inc', id: 'rfEricImport', type: 'button',
      text: T('检索并导入'), onclick: function () { runImport(); }
    });
    var statusBox = mk('div', { class: 'rf-eric-status', id: 'rfEricStatus', role: 'status' });
    var listBox = mk('ul', { class: 'rf-eric-list', id: 'rfEricList', hidden: 'hidden' });

    var entry = mk('div', { class: 'card rf-eric-entry', id: 'rfEricEntry' }, [
      mk('b', { text: T('从 ERIC 检索文献') }),
      mk('p', { class: 'hint', style: { margin: '4px 0 8px' },
                text: T('仅访问 ERIC 官方 API（api.ies.ed.gov）获取题录与摘要；不向其他地址发送数据。检索与导入都由你手动触发。') }),
      input,
      mk('div', { class: 'rf-eric-actions' }, [previewButton, importButton]),
      statusBox,
      listBox
    ]);
    /* 挂载锚点：Zotero 入口之后（存在时），否则上传卡之后；都找不到就追加到页尾。 */
    var anchor = document.getElementById('rfZoteroEntry');
    if (!anchor) {
      var dropCard = section.querySelector('.dz') && section.querySelector('.dz').closest('.card');
      anchor = dropCard || null;
    }
    if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(entry, anchor.nextSibling);
    else section.append(entry);

    function setStatus(text) {
      statusBox.textContent = text || '';
    }

    function setBusy(busy) {
      state.busy = busy;
      previewButton.disabled = busy;
      importButton.disabled = busy;
    }

    function renderPreview(data) {
      var summary = previewSummary(data);
      /* 参数交给 T()（而不是先 T 再 fill）：RFLang.t 会记住「模板+实参」，
         切换界面语言时状态栏可按当前语言重渲染，不会留在旧语言。 */
      setStatus(T(summary.template, ...summary.args));
      var records = (data && Array.isArray(data.records)) ? data.records : [];
      listBox.replaceChildren();
      if (!records.length) { listBox.hidden = true; return; }
      records.forEach(function (record) {
        if (!record || typeof record !== 'object') return;
        var meta = [];
        if (record.authors) meta.push(String(record.authors));
        if (record.year) meta.push(String(record.year));
        if (record.journal) meta.push(String(record.journal));
        listBox.append(mk('li', {}, [
          mk('div', { class: 't', text: String(record.title || '') }),
          meta.length ? mk('div', { class: 'm', text: meta.join(' · ') }) : null
        ]));
      });
      listBox.hidden = false;
    }

    /* 第 1 显式触发：检索预览（只读，不入库）。 */
    function runPreview() {
      if (state.busy) return;
      var query = String(input.value || '').trim();
      if (!query) { setStatus(T('请先填写 ERIC 检索式。')); return; }
      var base = taskBase();
      if (!base) { setStatus(T('请先在首页选择或创建任务，再使用 ERIC 检索。')); return; }
      setBusy(true);
      setStatus(T('正在检索 ERIC…'));
      listBox.replaceChildren();
      listBox.hidden = true;
      api(base + '/eric/search', { method: 'POST', body: { query: query, rows: 20 } })
        .then(function (data) { renderPreview(data); })
        /* errorText 返回 zh 源串：必须经 T() 才能落到当前界面语言。 */
        .catch(function (error) { setStatus(T(errorText(error))); })
        .then(function () { setBusy(false); });
    }

    /* 第 2 显式触发：检索并导入（受保护导入管线；成功后复用导入报告）。 */
    function runImport() {
      if (state.busy) return;
      var query = String(input.value || '').trim();
      if (!query) { setStatus(T('请先填写 ERIC 检索式。')); return; }
      var base = taskBase();
      if (!base) { setStatus(T('请先在首页选择或创建任务，再使用 ERIC 检索。')); return; }
      setBusy(true);
      setStatus(T('正在从 ERIC 导入…'));
      api(base + '/eric/import' + screenerQuery(), {
        method: 'POST', body: { query: query, limit: 500 }
      }).then(function (result) {
        var summary = importSummary(result);
        var text = T(summary.template, ...summary.args);
        setStatus(text);
        if (typeof toast === 'function') toast(text, 'inc');
        /* 复用导入页报告卡 + 既有完成事件/进度刷新（存在才用，缺失只影响展示）。 */
        try {
          if (typeof renderImportReport === 'function') renderImportReport(result);
        } catch (error) { /* 报告渲染失败不影响导入结果展示 */ }
        try { document.dispatchEvent(new CustomEvent('reviewflow:import-completed')); } catch (error) {}
        try {
          if (typeof refreshProgress === 'function') refreshProgress();
        } catch (error) {}
      }).catch(function (error) {
        /* 同 runPreview：zh 源串经 T() 取当前语言，非中文界面不泄漏中文。 */
        setStatus(T(errorText(error)));
      }).then(function () { setBusy(false); });
    }
  }

  try { mountBrowser(); } catch (error) { /* 单点故障：绝不影响导入页 */ }
})(typeof window === 'undefined' ? globalThis : window);
