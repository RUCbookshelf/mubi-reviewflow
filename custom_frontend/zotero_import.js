/* ReviewFlow · W1 从本机 Zotero 导入（B5）—— 导入页入口 + 集合选择对话框。
 *
 * 治理红线（空白格计划 W1，与 ai_suggest.js / living_review.js 同一口径）：
 *   - 十语言词条自包含注册（RFLang.register，列序 zh/en/fr/ru/es/ja/pt/de/sr/ko）；
 *   - 联网默认关 + 显式手动触发：页面加载零网络请求。出站恰好两段，且每段都由
 *     一次用户点击触发：
 *       1) 入口卡/对话框的「连接本机 Zotero…」按钮（#rfZoteroOpen 只打开对话框、
 *          零请求；#rfZoteroConnect 一次点击 → POST /zotero-local/test +
 *          GET /zotero-local/collections 两个请求）；
 *       2) 对话框的「导入所选集合」按钮（#rfZoteroImport）→ POST /zotero-local/import。
 *     打开对话框本身不是网络动作（复审 R5#4：此处曾误写成三次点击）。
 *   - 隐私披露常驻对话框：仅连接本机回环 127.0.0.1:23119（Zotero 7 本地 API），
 *     不访问 Zotero 云端，也不向任何外部地址发送数据；
 *   - 失败优雅降级：连接不可达/超时给修复指引，绝不中断导入页其他功能；
 *     初始化与全部回调自捕获异常（单点故障，追加式接入）；
 *   - 渲染红线：所有动态数据经 mk()/textContent，绝不 innerHTML；
 *   - 导入走后端 /api/tasks/{task}/zotero-local/* 端点（受保护导入管线），
 *     成功后复用导入页的导入报告（renderImportReport）与进度刷新。
 *
 * 纯逻辑（错误分流/层级缩进/摘要文案/选中计数）经 module.exports 暴露，
 * build/tests/zotero_import.test.cjs 以 node --test 钉行为。
 */
(function (root) {
  'use strict';

  /* =====================================================================
   * 一、十语言词条（模块自包含；列序 zh-CN/en/fr/ru/es/ja/pt/de/sr/ko）
   * ===================================================================== */


  /* =====================================================================
   * 二、纯逻辑（node --test 钉行为；无 DOM 依赖）
   * ===================================================================== */

  /** 连接类失败（不可达/超时/拒绝重定向）→ 前端给修复指引而非原始错误串。 */
  var CONNECT_FAIL_CODES = ['zotero_unreachable', 'zotero_timeout', 'zotero_redirect'];

  function isConnectFailure(error) {
    var code = error && error.code;
    return CONNECT_FAIL_CODES.indexOf(code) >= 0;
  }

  /* 复审 R4#3：后端 message 是**中文原文**（zotero_local_api.py 的 str(exc)），
     直接拼进 T() 模板会在英/法/西等界面泄漏中文。这里按 code 映射到已注册的
     源串——与连接类/no_items 同一模式，绝不透传未注册的技术串。 */
  var CODE_TEXT = {
    zotero_no_items: '所选集合没有可导入的文献条目。',
    zotero_collection_not_found: '所选集合在本机 Zotero 中已不存在，请刷新集合列表后重试。',
    zotero_too_many_items: '所选集合条目过多，无法一次导入。请只选择部分集合（或改用子集合）分批导入。',
    zotero_bad_response: '本机 Zotero 返回了无法识别的响应。请确认 127.0.0.1:23119 指向 Zotero 7（该端口可能被其他程序占用）。',
    zotero_http_error: '本机 Zotero 拒绝了这次请求（HTTP 错误）。请确认该端口确是 Zotero 7 本地 API。',
    zotero_response_too_large: '本机 Zotero 的响应超出读取上限，已停止读取。请改用子集合分批导入。',
    zotero_bad_collection_key: '集合标识无效，请刷新集合列表后重试。',
    zotero_bad_path: '请求地址无效，已拒绝发送。'
  };

  /** 错误 → 对话框正文（zh 源串，经 T() 取当前语言；**绝不透传后端中文**）。 */
  function errorText(error, fallback) {
    if (isConnectFailure(error)) return '无法连接本机 Zotero。请确认 Zotero 7 正在本机运行；若仍失败，请在 Zotero 的 设置 → 高级 中勾选「允许本计算机上的其他应用程序与 Zotero 通信」后重试。';
    var code = error && error.code;
    if (code && CODE_TEXT[code]) return CODE_TEXT[code];
    /* 未登记的 code：只用通用外壳（本身是注册词条），不拼接后端原文——
       原文是中文技术串，非中文界面会泄漏。原始错误保留在控制台便于排查。 */
    if (typeof console !== 'undefined' && console.warn) {
      console.warn('[Zotero] 未登记的错误：', code, error && error.message);
    }
    return '从本机 Zotero 读取失败，请稍后重试或改用导出文件导入。';
  }

  /** 集合层级深度：parent 链长度（坏数据/环按深度 0 兜底，绝不抛错）。 */
  function collectionDepths(collections) {
    var byKey = {};
    (collections || []).forEach(function (row) {
      if (row && row.key) byKey[row.key] = row;
    });
    var depthOf = function (key, seen) {
      var depth = 0;
      var current = byKey[key];
      while (current && current.parent && byKey[current.parent] &&
             (seen || {}).indexOf(current.parent) < 0) {
        seen = (seen || []).concat(current.parent);
        current = byKey[current.parent];
        depth += 1;
        if (depth > 12) break;  /* 环防御 */
      }
      return depth;
    };
    var out = {};
    Object.keys(byKey).forEach(function (key) { out[key] = depthOf(key, [key]); });
    return out;
  }

  /** 选中集合 → 摘要行（已选 N 个集合 · M 条文献；未知条数按集合计不累计）。 */
  function selectionSummary(selectedKeys, collections) {
    var byKey = {};
    (collections || []).forEach(function (row) { if (row && row.key) byKey[row.key] = row; });
    var keys = (selectedKeys || []).filter(function (key) { return !!byKey[key]; });
    var items = keys.reduce(function (sum, key) {
      var count = byKey[key] && typeof byKey[key].num_items === 'number'
        ? byKey[key].num_items : 0;
      return sum + count;
    }, 0);
    return { count: keys.length, items: items, keys: keys };
  }

  /** 受保护导入响应 → 摘要模板（暂存与直入两形态；zh 源串经 T() 取词）。 */
  function importSummary(data) {
    var d = data || {};
    if (d.staged) {
      return { template: '导入已暂存：解析 {0} 条，待核验后激活。',
               args: [Number(d.total_parsed || 0)] };
    }
    return { template: '导入完成：解析 {0} 条，精确去重 {1} 条，导入 {2} 条。',
             args: [Number(d.total_parsed || 0), Number(d.exact_duplicate_excess || 0),
                    Number(d.imported || 0)] };
  }

  /** {0}/{1} 占位填充（与主应用 T(模板, 参数) 同语义）。 */
  function fill(template, args) {
    return String(template || '').replace(/\{(\d+)\}/g, function (_, index) {
      var value = (args || [])[Number(index)];
      return value === undefined || value === null ? '' : String(value);
    });
  }

  var lib = {
    isConnectFailure: isConnectFailure,
    errorText: errorText,
    collectionDepths: collectionDepths,
    selectionSummary: selectionSummary,
    importSummary: importSummary,
    fill: fill
  };
  root.ZoteroLocalImport = lib;
  if (typeof module !== 'undefined') module.exports = lib;

  /* =====================================================================
   * 三、浏览器接入（追加式：任何异常都只影响本模块，不影响导入页）
   * ===================================================================== */
  function mountBrowser() {
    if (typeof document === 'undefined' || typeof RFLang === 'undefined' ||
        typeof T !== 'function' || typeof api === 'undefined' ||
        typeof fetch !== 'function') return;
    /* 十语言词条（模块自包含；列序 zh-CN/en/fr/ru/es/ja/pt/de/sr/ko）。
       字面量形态：词典回放/静态守卫只认此处内联的字面量数组。 */
    RFLang.register([
    ["从本机 Zotero 导入", "Import from local Zotero", "Importer depuis Zotero local", "Импорт из локального Zotero", "Importar desde Zotero local", "このPCのZoteroから取り込む", "Importar do Zotero local", "Aus lokalem Zotero importieren", "Увоз из локалног Zotera", "로컬 Zotero에서 가져오기"],
    ["仅连接本机 Zotero（127.0.0.1:23119）读取你选择的集合；不访问 Zotero 云端，也不向任何外部地址发送数据。每次连接都由你手动触发。", "Connects only to Zotero on this computer (127.0.0.1:23119) to read the collections you choose; it never reaches the Zotero cloud and sends nothing to any external address. Every connection is triggered manually by you.", "Se connecte uniquement au Zotero de cet ordinateur (127.0.0.1:23119) pour lire les collections choisies ; jamais au cloud Zotero, et n’envoie rien à une adresse externe. Chaque connexion est déclenchée manuellement.", "Подключается только к Zotero на этом компьютере (127.0.0.1:23119) и читает только выбранные вами коллекции; облачный Zotero не используется, на внешние адреса ничего не отправляется. Каждое подключение выполняется вручную.", "Se conecta solo al Zotero de este equipo (127.0.0.1:23119) para leer las colecciones que elija; nunca accede a la nube de Zotero ni envía datos a direcciones externas. Cada conexión se inicia manualmente.", "このPC上のZotero（127.0.0.1:23119）にのみ接続し、選択したコレクションだけを読み取ります。Zoteroクラウドには接続せず、外部アドレスへデータを送信しません。接続は毎回手動で行います。", "Conecta-se apenas ao Zotero deste computador (127.0.0.1:23119) para ler as coleções escolhidas; nunca acessa a nuvem do Zotero nem envia dados a endereços externos. Cada conexão é disparada manualmente.", "Verbindet sich nur mit Zotero auf diesem Computer (127.0.0.1:23119), um die gewählten Sammlungen zu lesen; niemals mit der Zotero-Cloud, und es werden keine Daten an externe Adressen gesendet. Jede Verbindung wird manuell ausgelöst.", "Повезује се само са Zotero-ом на овом рачунару (127.0.0.1:23119) и чита само изабране збирке; никад не приступа Zotero облаку и не шаље податке на спољне адресе. Свака веза се покреће ручно.", "이 컴퓨터의 Zotero(127.0.0.1:23119)에만 연결하여 선택한 컬렉션을 읽습니다. Zotero 클라우드에 접속하지 않고 외부 주소로 데이터를 보내지 않습니다. 연결은 매번 수동으로 시작합니다."],
    ["连接本机 Zotero…", "Connect to local Zotero…", "Se connecter à Zotero local…", "Подключиться к локальному Zotero…", "Conectar con Zotero local…", "このPCのZoteroに接続…", "Conectar ao Zotero local…", "Mit lokalem Zotero verbinden…", "Повежи се са локалним Zotero-ом…", "로컬 Zotero에 연결…"],
    ["正在连接本机 Zotero…", "Connecting to local Zotero…", "Connexion à Zotero local…", "Подключение к локальному Zotero…", "Conectando con Zotero local…", "このPCのZoteroに接続中…", "Conectando ao Zotero local…", "Verbindung zum lokalen Zotero wird hergestellt…", "Повезивање са локалним Zotero-ом…", "로컬 Zotero에 연결하는 중…"],
    ["已连接本机 Zotero（{0} ms）。", "Connected to local Zotero ({0} ms).", "Connecté à Zotero local ({0} ms).", "Локальный Zotero подключён ({0} мс).", "Conectado a Zotero local ({0} ms).", "このPCのZoteroに接続しました（{0} ms）。", "Conectado ao Zotero local ({0} ms).", "Mit lokalem Zotero verbunden ({0} ms).", "Повезан са локалним Zotero-ом ({0} ms).", "로컬 Zotero에 연결되었습니다({0}ms)."],
    ["无法连接本机 Zotero。请确认 Zotero 7 正在本机运行；若仍失败，请在 Zotero 的 设置 → 高级 中勾选「允许本计算机上的其他应用程序与 Zotero 通信」后重试。", "Cannot connect to local Zotero. Make sure Zotero 7 is running on this computer; if it still fails, enable “Allow other applications on this computer to communicate with Zotero” under Zotero Settings → Advanced, then retry.", "Impossible de se connecter à Zotero local. Vérifiez que Zotero 7 est en cours d’exécution sur cet ordinateur ; en cas d’échec persistant, activez « Autoriser d’autres applications de cet ordinateur à communiquer avec Zotero » dans Paramètres → Avancé, puis réessayez.", "Не удаётся подключиться к локальному Zotero. Убедитесь, что Zotero 7 запущен на этом компьютере; если ошибка остаётся, включите в Zotero «Настройки → Дополнительно» пункт «Разрешить другим приложениям на этом компьютере обмениваться данными с Zotero» и повторите.", "No se puede conectar con Zotero local. Asegúrese de que Zotero 7 se esté ejecutando en este equipo; si sigue fallando, active «Permitir que otras aplicaciones de este equipo se comuniquen con Zotero» en Ajustes → Avanzado y reintente.", "このPCのZoteroに接続できません。Zotero 7 が起動しているか確認してください。それでも失敗する場合は、Zotero の「設定 → 詳細」で「このコンピューター上の他のアプリケーションとの通信を許可する」を有効にして再試行してください。", "Não é possível conectar ao Zotero local. Verifique se o Zotero 7 está em execução neste computador; se ainda falhar, ative “Permitir que outros aplicativos deste computador se comuniquem com o Zotero” em Configurações → Avançado e tente novamente.", "Verbindung zum lokalen Zotero nicht möglich. Stellen Sie sicher, dass Zotero 7 auf diesem Computer läuft; falls es weiterhin fehlschlägt, aktivieren Sie „Anderen Anwendungen auf diesem Computer die Kommunikation mit Zotero erlauben“ unter Einstellungen → Erweitert und versuchen Sie es erneut.", "Није могуће повезати се са локалним Zotero-ом. Уверите се да је Zotero 7 покренут на овом рачунару; ако и даље не успева, укључите „Дозволи другим апликацијама на овом рачунару да комуницирају са Zotero-ом“ у Подешавања → Напредно, па покушајте поново.", "로컬 Zotero에 연결할 수 없습니다. Zotero 7이 이 컴퓨터에서 실행 중인지 확인하세요. 계속 실패하면 Zotero 설정 → 고급에서 ‘이 컴퓨터의 다른 애플리케이션이 Zotero와 통신하도록 허용’을 켜고 다시 시도하세요."],
    ["本机 Zotero 库中没有集合。请先在 Zotero 中建立集合。", "No collections in the local Zotero library. Create collections in Zotero first.", "Aucune collection dans la bibliothèque Zotero locale. Créez d’abord des collections dans Zotero.", "В локальной библиотеке Zotero нет коллекций. Сначала создайте коллекции в Zotero.", "No hay colecciones en la biblioteca Zotero local. Cree primero colecciones en Zotero.", "ローカルのZoteroライブラリにコレクションがありません。先にZoteroでコレクションを作成してください。", "Não há coleções na biblioteca Zotero local. Crie coleções no Zotero primeiro.", "Keine Sammlungen in der lokalen Zotero-Bibliothek. Legen Sie zuerst Sammlungen in Zotero an.", "Нема збирки у локалној Zotero библиотеци. Прво направите збирке у Zotero-у.", "로컬 Zotero 라이브러리에 컬렉션이 없습니다. 먼저 Zotero에서 컬렉션을 만드세요."],
    ["选择要导入的集合", "Choose collections to import", "Choisir les collections à importer", "Выберите коллекции для импорта", "Elegir colecciones para importar", "取り込むコレクションを選択", "Escolher coleções para importar", "Zu importierende Sammlungen wählen", "Изаберите збирке за увоз", "가져올 컬렉션 선택"],
    ["{0} 条", "{0} items", "{0} notices", "{0} записей", "{0} registros", "{0} 件", "{0} itens", "{0} Einträge", "{0} ставки", "{0}건"],
    ["导入所选集合", "Import selected collections", "Importer les collections choisies", "Импортировать выбранные коллекции", "Importar las colecciones elegidas", "選択したコレクションを取り込む", "Importar coleções selecionadas", "Ausgewählte Sammlungen importieren", "Увези изабране збирке", "선택한 컬렉션 가져오기"],
    ["已选 {0} 个集合 · {1} 条文献", "{0} collections selected · {1} items", "{0} collections choisies · {1} notices", "Выбрано коллекций: {0} · записей: {1}", "{0} colecciones elegidas · {1} registros", "選択中：コレクション {0} ・ 文献 {1} 件", "{0} coleções selecionadas · {1} itens", "{0} Sammlungen ausgewählt · {1} Einträge", "Изабрано збирки: {0} · ставки: {1}", "컬렉션 {0}개 선택 · 문헌 {1}건"],
    ["请先选择至少一个集合。", "Select at least one collection first.", "Sélectionnez d’abord au moins une collection.", "Сначала выберите хотя бы одну коллекцию.", "Seleccione primero al menos una colección.", "先にコレクションを1つ以上選択してください。", "Selecione primeiro pelo menos uma coleção.", "Wählen Sie zuerst mindestens eine Sammlung.", "Прво изаберите бар једну збирку.", "먼저 컬렉션을 하나 이상 선택하세요."],
    ["正在从本机 Zotero 导入…", "Importing from local Zotero…", "Importation depuis Zotero local…", "Импорт из локального Zotero…", "Importando desde Zotero local…", "このPCのZoteroから取り込み中…", "Importando do Zotero local…", "Import aus lokalem Zotero läuft…", "Увоз из локалног Zotera…", "로컬 Zotero에서 가져오는 중…"],
    ["请先在首页选择或创建任务。", "Pick or create a task on the Home page first.", "Choisissez ou créez d'abord une tâche dans l'accueil.", "Сначала выберите или создайте задачу на главной.", "Elija o cree primero una tarea en Inicio.", "先にホームでタスクを選択または作成してください。", "Escolha ou crie uma tarefa na Início primeiro.", "Wählen oder erstellen Sie zuerst eine Aufgabe auf der Startseite.", "Прво изаберите или направите задатак на почетној.", "먼저 홈에서 작업을 선택하거나 만들어 주세요."],
    ["刷新集合", "Refresh collections", "Actualiser les collections", "Обновить коллекции", "Actualizar colecciones", "コレクションを更新", "Atualizar coleções", "Sammlungen aktualisieren", "Освежи збирке", "컬렉션 새로 고침"],
    ["关闭", "Close", "Fermer", "Закрыть", "Cerrar", "閉じる", "Fechar", "Schließen", "Затвори", "닫기"],
    ["导入已暂存：解析 {0} 条，待核验后激活。", "Import staged: {0} parsed, pending review before activation.", "Importation mise en attente : {0} notices analysées, activation après vérification.", "Импорт поставлен в очередь: разобрано {0}, активация после проверки.", "Importación en espera: {0} analizados, activación tras la verificación.", "取り込みを一時保存：{0} 件を解析し、確認後に有効化します。", "Importação em espera: {0} analisados, ativação após verificação.", "Import vorgemerkt: {0} verarbeitet, Aktivierung nach Prüfung.", "Увоз је припремљен: обрађено {0}, активација након провере.", "가져오기 대기 중: {0}건 분석됨, 확인 후 활성화됩니다."],
    ["导入完成：解析 {0} 条，精确去重 {1} 条，导入 {2} 条。", "Import finished: {0} parsed, {1} exact duplicates, {2} imported.", "Importation terminée : {0} analysées, {1} doublons exacts, {2} importées.", "Импорт завершён: разобрано {0}, точных дубликатов {1}, импортировано {2}.", "Importación terminada: {0} analizados, {1} duplicados exactos, {2} importados.", "取り込み完了：{0} 件を解析、正確な重複 {1} 件、{2} 件を取り込みました。", "Importação concluída: {0} analisados, {1} duplicatas exatas, {2} importados.", "Import abgeschlossen: {0} verarbeitet, {1} exakte Dubletten, {2} importiert.", "Увоз завршен: обрађено {0}, тачних дупликата {1}, увезено {2}.", "가져오기 완료: {0}건 분석, 정확한 중복 {1}건, {2}건 가져옴."],
    ["所选集合没有可导入的文献条目。", "The selected collections contain no importable items.", "Les collections choisies ne contiennent aucune notice importable.", "В выбранных коллекциях нет записей для импорта.", "Las colecciones elegidas no contienen registros importables.", "選択したコレクションに取り込める文献がありません。", "As coleções selecionadas não contêm itens importáveis.", "Die ausgewählten Sammlungen enthalten keine importierbaren Einträge.", "Изабране збирке не садрже уносне ставке.", "선택한 컬렉션에 가져올 수 있는 문헌이 없습니다."],
    ["所选集合在本机 Zotero 中已不存在，请刷新集合列表后重试。", "The selected collections no longer exist in the local Zotero library. Refresh the list and retry.", "Les collections choisies n’existent plus dans la bibliothèque Zotero locale. Actualisez la liste puis réessayez.", "Выбранных коллекций больше нет в локальной библиотеке Zotero. Обновите список и повторите.", "Las colecciones elegidas ya no existen en la biblioteca Zotero local. Actualice la lista y reintente.", "選択したコレクションはローカルのZoteroライブラリに存在しません。一覧を更新して再試行してください。", "As coleções selecionadas não existem mais na biblioteca Zotero local. Atualize a lista e tente novamente.", "Die gewählten Sammlungen sind in der lokalen Zotero-Bibliothek nicht mehr vorhanden. Aktualisieren Sie die Liste und versuchen Sie es erneut.", "Изабране збирке више не постоје у локалној Zotero библиотеци. Освежите листу и покушајте поново.", "선택한 컬렉션이 로컬 Zotero 라이브러리에 더 이상 없습니다. 목록을 새로 고친 뒤 다시 시도하세요."],
    ["所选集合条目过多，无法一次导入。请只选择部分集合（或改用子集合）分批导入。", "The selected collections contain too many items for one import. Select fewer collections (or use subcollections) and import in batches.", "Les collections choisies contiennent trop de notices pour un seul import. Sélectionnez moins de collections (ou utilisez des sous-collections) et importez par lots.", "В выбранных коллекциях слишком много записей для одного импорта. Выберите меньше коллекций (или используйте подколлекции) и импортируйте частями.", "Las colecciones elegidas contienen demasiados registros para una sola importación. Seleccione menos colecciones (o use subcolecciones) e importe por lotes.", "選択したコレクションの文献が多すぎて一度に取り込めません。コレクションを減らす（またはサブコレクションを使う）か、分割して取り込んでください。", "As coleções selecionadas contêm itens demais para uma única importação. Selecione menos coleções (ou use subcoleções) e importe em lotes.", "Die gewählten Sammlungen enthalten zu viele Einträge für einen Import. Wählen Sie weniger Sammlungen (oder nutzen Sie Untersammlungen) und importieren Sie in Stapeln.", "Изабране збирке садрже превише ставки за један увоз. Изаберите мање збирки (или користите подзбирке) и увезите у деловима.", "선택한 컬렉션의 문헌이 너무 많아 한 번에 가져올 수 없습니다. 컬렉션을 줄이거나(또는 하위 컬렉션 사용) 나누어 가져오세요."],
    ["本机 Zotero 返回了无法识别的响应。请确认 127.0.0.1:23119 指向 Zotero 7（该端口可能被其他程序占用）。", "Local Zotero returned an unrecognised response. Make sure 127.0.0.1:23119 is Zotero 7 (another program may have taken the port).", "Zotero local a renvoyé une réponse non reconnue. Vérifiez que 127.0.0.1:23119 correspond bien à Zotero 7 (un autre programme a peut-être pris ce port).", "Локальный Zotero вернул нераспознанный ответ. Убедитесь, что 127.0.0.1:23119 — это Zotero 7 (порт мог занять другое приложение).", "Zotero local devolvió una respuesta no reconocida. Compruebe que 127.0.0.1:23119 sea Zotero 7 (otro programa puede haber ocupado el puerto).", "このPCのZoteroが解釈できない応答を返しました。127.0.0.1:23119 が Zotero 7 であることを確認してください（別のプログラムがポートを使用している可能性があります）。", "O Zotero local devolveu uma resposta não reconhecida. Verifique se 127.0.0.1:23119 é o Zotero 7 (outro programa pode ter ocupado a porta).", "Das lokale Zotero lieferte eine unbekannte Antwort. Prüfen Sie, ob 127.0.0.1:23119 wirklich Zotero 7 ist (möglicherweise hat ein anderes Programm den Port belegt).", "Локални Zotero је вратио непрепознат одговор. Проверите да ли је 127.0.0.1:23119 Zotero 7 (порт је можда заузела друга апликација).", "로컬 Zotero가 인식할 수 없는 응답을 반환했습니다. 127.0.0.1:23119가 Zotero 7인지 확인하세요(다른 프로그램이 포트를 점유했을 수 있습니다)."],
    ["本机 Zotero 拒绝了这次请求（HTTP 错误）。请确认该端口确是 Zotero 7 本地 API。", "Local Zotero rejected the request (HTTP error). Confirm the port really is the Zotero 7 local API.", "Zotero local a rejeté la requête (erreur HTTP). Vérifiez que ce port est bien l’API locale de Zotero 7.", "Локальный Zotero отклонил запрос (ошибка HTTP). Убедитесь, что на этом порту действительно локальный API Zotero 7.", "Zotero local rechazó la solicitud (error HTTP). Confirme que ese puerto es realmente la API local de Zotero 7.", "このPCのZoteroがリクエストを拒否しました（HTTPエラー）。そのポートが Zotero 7 のローカルAPIであることを確認してください。", "O Zotero local recusou a solicitação (erro HTTP). Confirme que essa porta é realmente a API local do Zotero 7.", "Das lokale Zotero hat die Anfrage abgelehnt (HTTP-Fehler). Prüfen Sie, ob dieser Port wirklich die lokale API von Zotero 7 ist.", "Локални Zotero је одбио захтев (HTTP грешка). Проверите да ли је тај порт заиста локални API програма Zotero 7.", "로컬 Zotero가 요청을 거부했습니다(HTTP 오류). 해당 포트가 Zotero 7 로컬 API인지 확인하세요."],
    ["本机 Zotero 的响应超出读取上限，已停止读取。请改用子集合分批导入。", "The local Zotero response exceeded the read limit, so reading stopped. Use subcollections to import in batches.", "La réponse de Zotero local a dépassé la limite de lecture ; la lecture a été arrêtée. Utilisez des sous-collections pour importer par lots.", "Ответ локального Zotero превысил предел чтения — чтение остановлено. Используйте подколлекции и импортируйте частями.", "La respuesta de Zotero local superó el límite de lectura, así que se detuvo la lectura. Use subcolecciones para importar por lotes.", "このPCのZoteroの応答が読み取り上限を超えたため、読み取りを停止しました。サブコレクションを使って分割して取り込んでください。", "A resposta do Zotero local excedeu o limite de leitura, então a leitura foi interrompida. Use subcoleções para importar em lotes.", "Die Antwort des lokalen Zotero überschritt das Leselimit; das Lesen wurde beendet. Nutzen Sie Untersammlungen für den gestaffelten Import.", "Одговор локалног Zotero-а премашио је ограничење читања, па је читање заустављено. Користите подзбирке за увоз у деловима.", "로컬 Zotero 응답이 읽기 한도를 초과하여 읽기를 중단했습니다. 하위 컬렉션으로 나누어 가져오세요."],
    ["集合标识无效，请刷新集合列表后重试。", "The collection identifier is invalid. Refresh the list and retry.", "L’identifiant de collection est invalide. Actualisez la liste puis réessayez.", "Идентификатор коллекции недействителен. Обновите список и повторите.", "El identificador de colección no es válido. Actualice la lista y reintente.", "コレクション識別子が無効です。一覧を更新して再試行してください。", "O identificador da coleção é inválido. Atualize a lista e tente novamente.", "Die Sammlungs-ID ist ungültig. Aktualisieren Sie die Liste und versuchen Sie es erneut.", "Идентификатор збирке није важећи. Освежите листу и покушајте поново.", "컬렉션 식별자가 유효하지 않습니다. 목록을 새로 고친 뒤 다시 시도하세요."],
    ["请求地址无效，已拒绝发送。", "The request address is invalid, so nothing was sent.", "L’adresse de la requête est invalide ; rien n’a été envoyé.", "Адрес запроса недействителен; отправка не выполнялась.", "La dirección de la solicitud no es válida; no se envió nada.", "リクエスト先が無効なため、送信していません。", "O endereço da solicitação é inválido; nada foi enviado.", "Die Anfrageadresse ist ungültig; es wurde nichts gesendet.", "Адреса захтева није важећа; ништа није послато.", "요청 주소가 유효하지 않아 전송하지 않았습니다."],
    ["从本机 Zotero 读取失败，请稍后重试或改用导出文件导入。", "Reading from local Zotero failed. Retry later or import an exported file instead.", "La lecture depuis Zotero local a échoué. Réessayez plus tard ou importez un fichier exporté.", "Не удалось прочитать данные из локального Zotero. Повторите позже или импортируйте экспортированный файл.", "No se pudo leer desde Zotero local. Reintente más tarde o importe un archivo exportado.", "このPCのZoteroからの読み取りに失敗しました。後で再試行するか、書き出したファイルを取り込んでください。", "Falha ao ler do Zotero local. Tente novamente mais tarde ou importe um arquivo exportado.", "Das Lesen aus dem lokalen Zotero ist fehlgeschlagen. Versuchen Sie es später erneut oder importieren Sie eine exportierte Datei.", "Читање из локалног Zotero-а није успело. Покушајте касније или увезите извезену датотеку.", "로컬 Zotero에서 읽지 못했습니다. 나중에 다시 시도하거나 내보낸 파일을 가져오세요."]
    ]);

    var section = document.querySelector('section.rf-import-page');
    if (!section || document.getElementById('rfZoteroEntry')) return;

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
      '.rf-zotero-overlay{position:fixed;inset:0;background:rgba(15,23,42,.45);display:flex;' +
      'align-items:center;justify-content:center;z-index:1200;padding:16px}' +
      '.rf-zotero-card{background:var(--bg,#fff);color:inherit;border-radius:12px;box-shadow:0 12px 40px rgba(0,0,0,.25);' +
      'max-width:640px;width:100%;max-height:86vh;display:flex;flex-direction:column;padding:18px}' +
      '.rf-zotero-card h3{margin:0 0 6px}' +
      '.rf-zotero-privacy{font-size:.8rem;opacity:.75;margin:0 0 12px}' +
      '.rf-zotero-status{font-size:.9rem;margin:8px 0;min-height:1.2em}' +
      '.rf-zotero-list{overflow:auto;border:1px solid rgba(125,125,125,.35);border-radius:8px;' +
      'margin:8px 0;padding:4px;flex:1;min-height:120px;max-height:46vh}' +
      '.rf-zotero-row{display:flex;align-items:center;gap:8px;padding:5px 6px;border-radius:6px}' +
      '.rf-zotero-row:hover{background:rgba(125,125,125,.08)}' +
      '.rf-zotero-count{font-size:.78rem;opacity:.7;margin-left:auto;white-space:nowrap}' +
      '.rf-zotero-actions{display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap;margin-top:10px}' +
      '.rf-zotero-entry{margin-top:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}' });
    document.head.append(style);

    var state = { collections: [], busy: false };

    function taskBase() {
      if (typeof S === 'undefined' || !S.task || !S.task.task_id) return null;
      if (typeof taskApi !== 'function') return null;
      return taskApi();
    }

    function screenerQuery() {
      return typeof scrQ === 'function' && typeof S !== 'undefined' && S.screener
        ? scrQ() : '';
    }

    /* -- 入口卡（插在上传区卡片之后） -------------------------------- */
    var entry = mk('div', { class: 'card', id: 'rfZoteroEntry' }, [
      mk('b', { text: T('从本机 Zotero 导入') }),
      mk('p', { class: 'hint', style: { margin: '4px 0 0' },
                text: T('仅连接本机 Zotero（127.0.0.1:23119）读取你选择的集合；不访问 Zotero 云端，也不向任何外部地址发送数据。每次连接都由你手动触发。') }),
      mk('div', { class: 'rf-zotero-entry' }, [
        mk('button', { class: 'btn b-inc', id: 'rfZoteroOpen',
                       text: T('连接本机 Zotero…'),
                       onclick: function () { openDialog(); } })
      ])
    ]);
    var dropCard = section.querySelector('.dz') && section.querySelector('.dz').closest('.card');
    if (dropCard && dropCard.parentNode) dropCard.parentNode.insertBefore(entry, dropCard.nextSibling);
    else section.append(entry);

    /* -- 对话框 ------------------------------------------------------ */
    var overlay = null;

    function closeDialog() {
      if (overlay) { overlay.remove(); overlay = null; }
    }

    function openDialog() {
      if (overlay) { overlay.remove(); overlay = null; }
      overlay = mk('div', { class: 'rf-zotero-overlay', id: 'rfZoteroOverlay' }, [
        mk('div', { class: 'card rf-zotero-card', role: 'dialog', 'aria-modal': 'true' }, [
          mk('h3', { text: T('从本机 Zotero 导入') }),
          mk('p', { class: 'hint rf-zotero-privacy',
                    text: T('仅连接本机 Zotero（127.0.0.1:23119）读取你选择的集合；不访问 Zotero 云端，也不向任何外部地址发送数据。每次连接都由你手动触发。') }),
          mk('div', { class: 'row', style: { justifyContent: 'space-between', margin: '0' } }, [
            mk('button', { class: 'btn b-inc', id: 'rfZoteroConnect',
                           text: T('连接本机 Zotero…'), onclick: function () { connect(); } }),
            mk('button', { class: 'btn b-out', id: 'rfZoteroRefresh',
                           text: T('刷新集合'), style: { display: 'none' },
                           onclick: function () { connect(); } })
          ]),
          mk('div', { class: 'rf-zotero-status', id: 'rfZoteroStatus', role: 'status' }),
          mk('div', { id: 'rfZoteroBody' }),
          mk('div', { class: 'rf-zotero-actions' }, [
            mk('span', { class: 'hint', id: 'rfZoteroPick', style: { marginRight: 'auto' } }),
            mk('button', { class: 'btn b-out', text: T('关闭'), onclick: function () { closeDialog(); } }),
            mk('button', { class: 'btn b-inc', id: 'rfZoteroImport', style: { display: 'none' },
                           text: T('导入所选集合'), onclick: function () { runImport(); } })
          ])
        ])
      ]);
      overlay.addEventListener('click', function (event) {
        if (event.target === overlay) closeDialog();
      });
      document.body.append(overlay);
      var connectButton = overlay.querySelector('#rfZoteroConnect');
      if (connectButton) connectButton.focus();
    }

    function status(node) {
      var box = document.getElementById('rfZoteroStatus');
      if (box) box.replaceChildren(node || '');
    }

    function setStatusText(text) {
      status(mk('span', { text: text || '' }));
    }

    /* 第 1 显式触发：连接 + 拉取集合（一次用户点击，一次连接测试 + 一次列表）。 */
    function connect() {
      if (state.busy) return;
      var base = taskBase();
      if (!base) { setStatusText(T('请先在首页选择或创建任务。')); return; }
      state.busy = true;
      setStatusText(T('正在连接本机 Zotero…'));
      var body = document.getElementById('rfZoteroBody');
      if (body) body.replaceChildren();
      Promise.resolve()
        .then(function () { return api(base + '/zotero-local/test', { method: 'POST' }); })
        .then(function (result) {
          setStatusText(T('已连接本机 Zotero（{0} ms）。', result && result.latency_ms));
          return api(base + '/zotero-local/collections');
        })
        .then(function (result) { renderCollections((result && result.collections) || []); })
        .catch(function (error) {
          setStatusText(T(errorText(error, '')) || '');
          var refresh = document.getElementById('rfZoteroRefresh');
          if (refresh) refresh.style.display = '';
        })
        .then(function () { state.busy = false; });
    }

    function renderCollections(collections) {
      state.collections = collections || [];
      var refresh = document.getElementById('rfZoteroRefresh');
      if (refresh) refresh.style.display = '';
      var importButton = document.getElementById('rfZoteroImport');
      var pick = document.getElementById('rfZoteroPick');
      if (!state.collections.length) {
        setStatusText(T('本机 Zotero 库中没有集合。请先在 Zotero 中建立集合。'));
        if (importButton) importButton.style.display = 'none';
        if (pick) pick.textContent = '';
        return;
      }
      status(mk('b', { text: T('选择要导入的集合') }));
      var depths = collectionDepths(state.collections);
      var body = document.getElementById('rfZoteroBody');
      if (!body) return;
      body.replaceChildren(mk('div', { class: 'rf-zotero-list' },
        state.collections.map(function (row) {
          var depth = depths[row.key] || 0;
          return mk('label', { class: 'rf-zotero-row' }, [
            mk('input', { type: 'checkbox', value: row.key,
                          style: { marginLeft: (depth * 18) + 'px' },
                          onchange: function () { updatePick(); } }),
            mk('span', { text: row.name || row.key, translate: 'no' }),
            mk('span', { class: 'rf-zotero-count',
                         text: typeof row.num_items === 'number'
                           ? T('{0} 条', row.num_items) : '' })
          ]);
        })));
      if (importButton) importButton.style.display = '';
      updatePick();
    }

    function selectedKeys() {
      var body = document.getElementById('rfZoteroBody');
      if (!body) return [];
      return Array.prototype.slice.call(body.querySelectorAll('input[type=checkbox]:checked'))
        .map(function (node) { return node.value; });
    }

    function updatePick() {
      var pick = document.getElementById('rfZoteroPick');
      if (!pick) return;
      var summary = selectionSummary(selectedKeys(), state.collections);
      pick.textContent = summary.count
        ? T('已选 {0} 个集合 · {1} 条文献', summary.count, summary.items) : '';
    }

    /* 第 2 显式触发：导入所选集合（受保护导入管线；成功后复用导入报告）。 */
    function runImport() {
      if (state.busy) return;
      var keys = selectedKeys();
      if (!keys.length) { setStatusText(T('请先选择至少一个集合。')); return; }
      var base = taskBase();
      if (!base) { setStatusText(T('请先在首页选择或创建任务。')); return; }
      state.busy = true;
      setStatusText(T('正在从本机 Zotero 导入…'));
      var importButton = document.getElementById('rfZoteroImport');
      if (importButton) importButton.disabled = true;
      api(base + '/zotero-local/import' + screenerQuery(), {
        method: 'POST', body: { collection_keys: keys }
      }).then(function (result) {
        var summary = importSummary(result);
        setStatusText(T(summary.template, summary.args[0], summary.args[1], summary.args[2]));
        if (typeof toast === 'function') toast(T(summary.template, summary.args[0], summary.args[1], summary.args[2]), 'inc');
        /* 复用导入页报告卡 + 既有完成事件/进度刷新（存在才用，缺失只影响展示）。 */
        try {
          if (typeof renderImportReport === 'function') renderImportReport(result);
        } catch (error) { /* 报告渲染失败不影响导入结果展示 */ }
        try { document.dispatchEvent(new CustomEvent('reviewflow:import-completed')); } catch (error) {}
        try {
          if (typeof refreshProgress === 'function') refreshProgress();
        } catch (error) {}
      }).catch(function (error) {
        setStatusText(T(errorText(error, '')) || '');
      }).then(function () {
        state.busy = false;
        if (importButton) importButton.disabled = false;
      });
    }
  }

  try { mountBrowser(); } catch (error) { /* 单点故障：绝不影响导入页 */ }
})(typeof window === 'undefined' ? globalThis : window);
