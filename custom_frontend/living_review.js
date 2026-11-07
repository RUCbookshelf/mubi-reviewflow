/* ReviewFlow · 活体综述监控面板（衍生产品规格 2026-09-30 §5.5；API=W13 Batch 段；
 * W3 检索订阅复活：check-now 真拉取 PubMed → 虚拟 RIS → 受保护导入待审）。
 *
 * 分析页追加「活体综述」面板（挂在 .an-workspace 内 #rfManuscriptPanel 之后，
 * 兜底 #anJumpQual，与 GRADE/撰写稿件面板同一追加式接入）：
 *   - 检索式管理：已保存检索式列表（数据库/检索式/上次日期/命中数），
 *     PubMed 记录给「一键打开 PubMed ↗」外链（仅 target=_blank 导航，
 *     本模块自身不直连外网）；表单保存/更新检索式（POST save-search，
 *     URL 由后端拼字符串生成）；PubMed 记录行内「检查更新」；
 *   - 检查更新（W3）：「立即检查更新（PubMed）」POST check-now——由后端
 *     （全仓唯一联网出口 living_review_plugin）访问 PubMed E-utilities，
 *     增量合成虚拟 RIS 文件进入受保护导入待审；失败优雅降级为提示
 *     （绝不让面板不可用），手动导出文件导入的离线路径保持可用；
 *   - 变更报告：上次更新时间、新增/去重移除/待新筛（AI 热启动队列）数、
 *     合并结果前后对比表（合并效应 / 95%CI / 研究数 / I²）、结论是否翻转
 *     徽章（CI 跨 null 状态变化）、人可读影响评估（change_report）；
 *     历次更新列表可点选回看（GET history，最新在前）；
 *   - 自动监控（可选插件，默认收起且关闭）：显式开关（POST enable-auto 只写
 *     task.json，后端不起后台线程）+ 检查间隔天数；开启后由**本页面会话内**
 *     定时器自动触发检查（应用关闭即停；到期补检一次 + 按间隔轮询）。
 *
 * 事件接入（追加式）：reviewflow:analysis-loaded（刷新检索式与历史）、
 * reviewflow:analysis-core-loaded（结局层预填，用户改过则不打扰）、
 * reviewflow:analysis-task-changed（复位并停自动定时器）、
 * reviewflow:language-changed（重渲染）。
 *
 * 单点故障：初始化与全部回调自行捕获异常；网络仅经全局 api() 访问本应用 API
 * （PubMed 出站全部由后端代发，页面不直连 NCBI）。
 * 十语言词条自包含注册（RFLang.register，中文为源串）。
 */
(function () {
  'use strict';
  try {
    if (typeof document === 'undefined' || typeof T !== 'function' || typeof el !== 'function') return;

    const anchor = document.getElementById('rfManuscriptPanel') || document.getElementById('anJumpQual');
    const workspace = document.querySelector('.page[data-p="analysis"] .an-workspace');
    if (!anchor || !workspace) return;   // 结构不符：安静退出

    /* ---- 样式（随模块注入） ---- */
    const style = document.createElement('style');
    style.textContent = [
      '.rf-living{margin-top:12px}',
      '.rf-lr-disclosure{padding:16px 0;border-top:1px solid var(--border);margin-top:16px}',
      '.rf-lr-disclosure>button[aria-expanded=true]{background:var(--primary-light);color:var(--primary)}',
      '.rf-living h3{margin:0 0 4px;font-size:.95rem}',
      '.rf-living b.sub{display:block;font-size:.85rem;margin:12px 0 4px}',
      '.rf-lr-record{border:1px solid var(--border);border-radius:10px;padding:8px 10px;margin-top:8px;background:var(--bg)}',
      '.rf-lr-record .head{display:flex;flex-wrap:wrap;gap:6px;align-items:center}',
      '.rf-lr-record pre{margin:6px 0 0;font-size:.76rem;white-space:pre-wrap;word-break:break-word}',
      '.rf-living .input{font-size:.8rem;padding:4px 8px}',
      '.rf-lr-form{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin-top:8px}',
      '.rf-lr-form label{display:flex;flex-direction:column;gap:3px;font-size:.75rem}',
      '.rf-lr-form .wide{grid-column:1/-1}',
      '.rf-lr-report{border:1px solid var(--border);border-radius:12px;padding:10px 12px;margin-top:8px;background:var(--bg)}',
      '.rf-lr-report table{border-collapse:collapse;font-size:.76rem;margin:8px 0;min-width:340px}',
      '.rf-lr-report th,.rf-lr-report td{border:1px solid var(--border);padding:4px 8px;text-align:left}',
      '.rf-lr-report th{background:var(--bg);font-weight:600}',
      '.rf-lr-report pre{font-size:.76rem;white-space:pre-wrap;margin:8px 0 0}',
      '.rf-lr-flip{display:inline-block;border-radius:999px;padding:2px 10px;font-size:.72rem;font-weight:700;color:#fff}',
      '.rf-lr-flip-yes{background:var(--danger)}',
      '.rf-lr-flip-no{background:#2e7d32}',
      '.rf-lr-hist{margin-top:8px;max-height:220px;overflow:auto}',
      '.rf-lr-hist button{display:block;width:100%;text-align:left;border:1px solid var(--border);border-radius:8px;background:var(--bg);padding:5px 8px;margin-top:4px;font-size:.74rem;cursor:pointer}',
      '.rf-lr-hist button:hover{border-color:var(--primary)}',
      '.rf-lr-status{font-size:.78rem;margin:6px 0 0;white-space:pre-wrap}',
    ].join('\n');
    (document.head || document.documentElement).appendChild(style);

    const DB_LABELS = { pubmed: 'PubMed', embase: 'Embase', cochrane: 'Cochrane Library', manual: '手动 / 其他来源' };
    const SOURCE_LABELS = { manual: '手动导入', pubmed: 'PubMed 插件' };

    /* ---- 十语言词条（模块自包含；列序 zh-CN/en/fr/ru/es/ja/pt/de/sr/ko） ---- */
    if (typeof RFLang !== 'undefined' && typeof RFLang.register === 'function') {
      RFLang.register([
        ["立即检查更新（PubMed）", "Check now (PubMed)", "Vérifier maintenant (PubMed)", "Проверить сейчас (PubMed)", "Comprobar ahora (PubMed)", "今すぐ確認（PubMed）", "Verificar agora (PubMed)", "Jetzt prüfen (PubMed)", "Провери одмах (PubMed)", "지금 확인(PubMed)"],
        ["自动检查中…", "Automatic check running…", "Vérification automatique en cours…", "Выполняется автоматическая проверка…", "Comprobación automática en curso…", "自動確認を実行中…", "Verificação automática em andamento…", "Automatische Prüfung läuft…", "Аутоматска провера у току…", "자동 확인 실행 중…"],
        ["✓ 已拉取 {0} 条，进入导入核验待审。", "✓ Fetched {0} records; staged in import review.", "✓ {0} notices récupérées ; en attente dans la révision d’importation.", "✓ Получено записей: {0}; переданы в импорт-проверку.", "✓ {0} registros obtenidos; en espera en la revisión de importación.", "✓ {0} 件を取得し、インポート審査で待機中です。", "✓ {0} registros obtidos; pendentes na revisão de importação.", "✓ {0} Datensätze abgerufen; zur Importprüfung vorgemerkt.", "✓ Преузето {0} записа; чекају у прегледу увоза.", "✓ {0}건을 가져와 임포트 검토에서 대기 중입니다."],
        ["✓ 自 {0} 起无新增。", "✓ No new records since {0}.", "✓ Aucune nouvelle notice depuis {0}.", "✓ Нет новых записей после {0}.", "✓ Sin registros nuevos desde {0}.", "✓ {0} 以降の新規はありません。", "✓ Nenhum registro novo desde {0}.", "✓ Keine neuen Datensätze seit {0}.", "✓ Нема нових записа од {0}.", "✓ {0} 이후 새 레코드가 없습니다."],
        ["PubMed 检查失败：", "PubMed check failed: ", "Échec de la vérification PubMed : ", "Проверка PubMed не удалась: ", "Fallo al comprobar PubMed: ", "PubMedの確認に失敗：", "Falha na verificação do PubMed: ", "PubMed-Prüfung fehlgeschlagen: ", "Провера PubMed није успела: ", "PubMed 확인 실패: "],
        ["网络不可达，请手动导出文件后导入。", "Network unreachable; export the file manually and import it.", "Réseau injoignable ; exportez le fichier manuellement puis importez-le.", "Сеть недоступна; экспортируйте файл вручную и импортируйте его.", "Red inaccesible; exporte el archivo manualmente e impórtelo.", "ネットワークに到達できません。手動でファイルをエクスポートしてインポートしてください。", "Rede inacessível; exporte o arquivo manualmente e importe-o.", "Netzwerk nicht erreichbar; exportieren Sie die Datei manuell und importieren Sie sie.", "Мрежа недоступна; извезите датотеку ручно и увезите је.", "네트워크에 접근할 수 없습니다. 파일을 수동으로 내보낸 뒤 가져오세요."],
        ["已开启会话内自动检查（每 {0} 天）；关闭应用即停，服务器不会后台联网。", "In-session automatic checks enabled (every {0} days); stops when the app closes. The server never connects in the background.", "Vérifications automatiques de session activées (tous les {0} jours) ; elles s’arrêtent à la fermeture de l’application. Le serveur ne se connecte jamais en arrière-plan.", "Включена автопроверка в рамках сеанса (каждые {0} дн.); останавливается при закрытии приложения. Сервер не подключается в фоновом режиме.", "Comprobaciones automáticas de sesión activadas (cada {0} días); se detienen al cerrar la aplicación. El servidor nunca se conecta en segundo plano.", "セッション内の自動確認を有効にしました（{0} 日ごと）。アプリを閉じると停止し、サーバーがバックグラウンドで通信することはありません。", "Verificações automáticas na sessão ativadas (a cada {0} dias); param quando o aplicativo fecha. O servidor nunca se conecta em segundo plano.", "Automatische Prüfungen innerhalb der Sitzung aktiviert (alle {0} Tage); enden beim Schließen der App. Der Server verbindet sich nie im Hintergrund.", "Аутоматске провере у сесији укључене (сваких {0} дана); стају кад се апликација затвори. Сервер се никад не повезује у позадини.", "세션 내 자동 확인이 활성화되었습니다({0}일마다). 앱을 닫으면 중지되며 서버는 백그라운드에서 연결하지 않습니다."],
        ["仅访问 PubMed 官方接口（eutils.ncbi.nlm.nih.gov），且仅在手动检查或会话内自动检查时发起。", "Contacts only the official PubMed API (eutils.ncbi.nlm.nih.gov), and only on a manual or in-session automatic check.", "Ne contacte que l’API officielle de PubMed (eutils.ncbi.nlm.nih.gov), et uniquement lors d’une vérification manuelle ou automatique en session.", "Обращается только к официальному API PubMed (eutils.ncbi.nlm.nih.gov) и только при ручной или автоматической проверке внутри сеанса.", "Solo contacta con la API oficial de PubMed (eutils.ncbi.nlm.nih.gov), y únicamente al comprobar manualmente o automáticamente en la sesión.", "PubMed公式API（eutils.ncbi.nlm.nih.gov）のみに、手動確認またはセッション内自動確認の時だけ接続します。", "Contata apenas a API oficial do PubMed (eutils.ncbi.nlm.nih.gov), e somente em verificações manuais ou automáticas na sessão.", "Kontaktiert nur die offizielle PubMed-API (eutils.ncbi.nlm.nih.gov), und nur bei einer manuellen oder automatischen Prüfung in der Sitzung.", "Контактира само званични PubMed API (eutils.ncbi.nlm.nih.gov), и само при ручној или аутоматској провери у сесији.", "공식 PubMed API(eutils.ncbi.nlm.nih.gov)에만, 수동 확인 또는 세션 내 자동 확인 시에만 접속합니다."],
        ["请先保存检索式。", "Save a search strategy first.", "Enregistrez d’abord une équation de recherche.", "Сначала сохраните поисковый запрос.", "Guarde primero una estrategia de búsqueda.", "先に検索式を保存してください。", "Salve primeiro uma estratégia de busca.", "Speichern Sie zuerst eine Suchstrategie.", "Прво сачувајте претраживачки израз.", "먼저 검색식을 저장하세요."],
        ["导入待审 {0} 篇", "{0} records staged for review", "{0} notices en attente de révision", "{0} записей ожидают проверки", "{0} registros en espera de revisión", "{0} 件が審査待ち", "{0} registros aguardando revisão", "{0} Datensätze zur Prüfung vorgemerkt", "{0} записа чека преглед", "{0}건 검토 대기 중"],
        ["PubMed 拉回 {0} 篇", "{0} records retrieved from PubMed", "{0} notices récupérées de PubMed", "Из PubMed получено записей: {0}", "{0} registros recuperados de PubMed", "PubMed から {0} 件取得", "{0} registros obtidos do PubMed", "{0} Datensätze aus PubMed abgerufen", "Са PubMed-а преузето {0} записа", "PubMed에서 {0}건 가져옴"],
        ["库内已存在 {0} 篇", "{0} already present in the library", "{0} déjà présentes dans la bibliothèque", "{0} уже есть в библиотеке", "{0} ya presentes en la biblioteca", "ライブラリに既存：{0} 件", "{0} já existentes na biblioteca", "{0} bereits in der Bibliothek vorhanden", "{0} већ постоји у библиотеци", "라이브러리에 이미 {0}건 존재"],
      ]);
    }

    function safe(fn) { return function () { try { fn.apply(this, arguments); } catch (e) { /* 单点故障 */ } }; }
    function fmtNum(v) {
      if (v === null || v === undefined || v === '') return '—';
      const n = Number(v);
      return Number.isFinite(n) ? String(Number(n.toPrecision(4))) : '—';
    }

    /* ---- 状态 ---- */
    let version = 0;              // 异步票据
    let layerTouched = false;     // 用户改过结局层后，预填不再覆盖
    let records = [];             // 已保存检索式
    let history = [];             // 更新历史（最新在前）
    let viewEntry = null;         // 当前展示的变更报告条目
    let checkBusy = false;        // 手动/自动检查互斥（防重叠拉取）

    /* ---- 面板骨架（静态文案节点集中持有，语言切换时重填） ---- */
    const heading = el('h3', { text: T('活体综述') });
    const hint = el('p', { class: 'hint', style: 'margin:2px 0 8px', text: T('Living Review imports now use protected Import review. Export or select the original source file there, review metadata and candidates, then activate resolved records. Saved history is retained; analysis is run separately.') });

    /* 检索式管理 */
    const recCaption = el('b', { class: 'sub', text: T('检索式管理') });
    const recEmpty = el('p', { class: 'hint', text: T('尚未保存检索式。') });
    const recBox = el('div', {});

    function disclosure(id, label) {
      const content = el('div', { id, hidden: true });
      const toggle = el('button', { type: 'button', class: 'btn b-out', text: T(label), 'aria-expanded': 'false', 'aria-controls': id });
      toggle.addEventListener('click', () => {
        content.hidden = !content.hidden;
        toggle.setAttribute('aria-expanded', String(!content.hidden));
      });
      return el('section', { class: 'rf-lr-disclosure' }, toggle, content);
    }
    const saveDetails = disclosure('rfLivingSearchForm', '保存 / 更新检索式');
    const dbSel = el('select', { class: 'input', 'aria-label': T('数据库') },
      Object.entries(DB_LABELS).map(([v, label]) => el('option', { value: v, text: T(label) })));
    const queryInput = el('textarea', { class: 'input', rows: '3', 'aria-label': T('检索式'),
      placeholder: T('粘贴数据库检索式（如 PubMed 检索框中的完整检索式）') });
    const dateInput = el('input', { class: 'input', type: 'date', 'aria-label': T('上次检索日期（可选）') });
    const hitsInput = el('input', { class: 'input', type: 'number', min: '0', 'aria-label': T('上次命中数（可选）') });
    const saveBtn = el('button', { class: 'btn b-inc', type: 'button', text: T('保存检索式') });
    saveDetails.lastElementChild.append(
      el('div', { class: 'rf-lr-form' },
        el('label', { class: 'wide' }, el('span', { text: T('检索式（必填）') }), queryInput),
        el('label', {}, el('span', { text: T('数据库') }), dbSel),
        el('label', {}, el('span', { text: T('上次检索日期') }), dateInput),
        el('label', {}, el('span', { text: T('上次命中数') }), hitsInput),
        el('label', {}, el('span', { text: ' ' }), saveBtn)));

    /* 手动导入更新 */
    const importCaption = el('b', { class: 'sub', text: T('手动导入更新（离线）') });
    const fileInput = el('input', { type: 'file', accept: '.ris,.csv,.txt', 'aria-label': T('增量文献文件（RIS/CSV）') });
    const cmpInput = el('input', { class: 'input', placeholder: T('比较组'), 'aria-label': T('比较组') });
    const outInput = el('input', { class: 'input', placeholder: T('结局'), 'aria-label': T('结局') });
    const tpInput = el('input', { class: 'input', placeholder: T('时间点'), 'aria-label': T('时间点') });
    const msrInput = el('input', { class: 'input', placeholder: T('指标代码（如 MD、OR）'), 'aria-label': T('指标代码') });
    const importBtn = el('button', { class: 'btn b-inc', type: 'button', text: T('Open import review') });
    const importHint = el('p', { class: 'hint', style: 'margin:4px 0 0', text: T('Living Review imports now use protected Import review. Export or select the original source file there, review metadata and candidates, then activate resolved records. Saved history is retained; analysis is run separately.') });
    const importForm = el('div', { class: 'rf-lr-form' },
      el('label', {}, el('span', { text: T('增量文献文件') }), fileInput),
      el('label', {}, el('span', { text: T('比较组') }), cmpInput),
      el('label', {}, el('span', { text: T('结局') }), outInput),
      el('label', {}, el('span', { text: T('时间点') }), tpInput),
      el('label', {}, el('span', { text: T('指标代码') }), msrInput),
      el('label', {}, el('span', { text: ' ' }), importBtn));
    importForm.append(importHint);

    /* 变更报告 */
    const reportCaption = el('b', { class: 'sub', text: T('变更报告') });
    const reportEmpty = el('p', { class: 'hint', text: T('No records yet.') });
    const reportBox = el('div', { class: 'rf-lr-report' });
    const histDetails = disclosure('rfLivingHistory', '历次更新');
    const histBox = el('div', { class: 'rf-lr-hist' });
    histDetails.lastElementChild.append(histBox);

    /* 自动监控（可选，默认收起且关闭）+ 立即检查（真实拉取，W3） */
    const autoDetails = disclosure('rfLivingAutomation', '自动监控（可选，默认关闭）');
    const autoCheck = el('input', { type: 'checkbox' });
    const autoLabel = el('label', { style: 'display:flex;gap:6px;align-items:center;font-size:.8rem' },
      autoCheck, el('span', { text: T('开启自动监控（PubMed 插件，显式开启）') }));
    const intervalInput = el('input', { class: 'input', type: 'number', min: '1', max: '3650', value: '30',
      style: 'max-width:110px', 'aria-label': T('检查间隔（天）') });
    const checkBtn = el('button', { class: 'btn b-out', type: 'button', text: T('立即检查更新（PubMed）') });
    const autoNote = el('p', { class: 'hint', style: 'margin:4px 0 0', text: T('仅访问 PubMed 官方接口（eutils.ncbi.nlm.nih.gov），且仅在手动检查或会话内自动检查时发起。') });
    const autoImportNote = el('p', { class: 'hint', style: 'margin:4px 0 0', text: T('Living Review imports now use protected Import review. Export or select the original source file there, review metadata and candidates, then activate resolved records. Saved history is retained; analysis is run separately.') });
    autoDetails.lastElementChild.append(
      autoLabel,
      el('div', { style: 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:6px' },
        el('label', { style: 'font-size:.78rem' }, el('span', { text: T('检查间隔（天）') }), intervalInput),
        checkBtn),
      autoNote,
      autoImportNote);

    const statusLine = el('p', { class: 'rf-lr-status hint', role: 'status' });
    const panel = el('section', { class: 'card rf-living', id: 'rfLivingReviewPanel', 'aria-label': T('活体综述') },
      heading, hint,
      recCaption, recBox, recEmpty, saveDetails,
      importCaption, importForm,
      reportCaption, reportEmpty, reportBox, histDetails,
      autoDetails, statusLine);
    anchor.after(panel);

    function guardTask() {
      if (typeof S !== 'undefined' && S && S.task && S.task.task_id) return true;
      statusLine.textContent = T('请先选择任务。');
      if (typeof toast === 'function') toast(T('请先选择任务'), 'warn');
      return false;
    }

    /* ---- 检索式列表渲染 ---- */
    function renderRecords() {
      recBox.replaceChildren();
      recEmpty.hidden = records.length > 0;
      for (const rec of records) {
        if (!rec || typeof rec !== 'object') continue;
        const db = String(rec.database || '');
        const head = el('div', { class: 'head' },
          el('span', { class: 'chip', translate: 'no', text: DB_LABELS[db] || db }),
          rec.last_run_date ? el('span', { class: 'chip', text: T('上次检索：{0}', String(rec.last_run_date)) }) : null,
          rec.total_hits_last ? el('span', { class: 'chip', text: T('命中 {0}', String(rec.total_hits_last)) }) : null);
        const url = String(rec.url || '');
        if (db === 'pubmed' && /^https:\/\//.test(url)) {
          head.append(el('a', { class: 'btn b-out', href: url, target: '_blank', rel: 'noopener noreferrer',
            text: T('一键打开 PubMed ↗'), style: 'padding:3px 10px;font-size:.75rem' }));
        }
        if (db === 'pubmed' && String(rec.query_string || '').trim()) {
          head.append(el('button', { type: 'button', class: 'btn b-out', text: T('检查更新'),
            style: 'padding:3px 10px;font-size:.75rem',
            onclick: safe(() => { runCheck(rec, {}); }) }));
        }
        recBox.append(el('div', { class: 'rf-lr-record' }, head,
          el('pre', { translate: 'no', text: String(rec.query_string || '') })));
      }
    }

    /* ---- 保存检索式 ---- */
    saveBtn.addEventListener('click', safe(saveSearch));
    async function saveSearch() {
      let ticket;
      try {
        if (typeof api !== 'function' || typeof taskApi !== 'function') return;
        if (!guardTask()) return;
        const query = queryInput.value.trim();
        if (!query) { statusLine.textContent = T('请先填写检索式。'); return; }
        const body = {
          database: dbSel.value || 'pubmed',
          query_string: query,
          last_run_date: dateInput.value || '',
          total_hits_last: Number(hitsInput.value) || 0,
        };
        ticket = ++version;
        saveBtn.disabled = true;
        saveBtn.dataset.requestTicket = String(ticket);
        statusLine.textContent = T('正在保存检索式…');
        try {
          const d = await api(taskApi() + '/living-review/save-search', { method: 'POST', body });
          if (ticket !== version) return;
          records = (d && d.search_records) || records;
          renderRecords();
          statusLine.textContent = T('✓ 检索式已保存（PubMed 链接由本地规则生成）。');
          if (typeof toast === 'function') toast(T('✓ 检索式已保存'), 'inc');
        } finally {
          if (saveBtn.dataset.requestTicket === String(ticket)) saveBtn.disabled = false;
        }
      } catch (e) {
        if (ticket !== undefined && ticket !== version) return;
        statusLine.textContent = T('保存失败：') + ((e && e.message) || '');
        if (typeof toast === 'function') toast(T('保存失败：') + ((e && e.message) || ''), 'warn');
      }
    }

    /* ---- 手动导入更新（multipart：文件 + 结局层四要素） ---- */
    importBtn.addEventListener('click', safe(importUpdate));
    async function protectedImportRoute() {
      if (!guardTask()) return;
      await WorkflowControls.refresh('entry', false);
      if (!WorkflowControls.settings()?.can_manage) {
        statusLine.textContent = T('Only the task owner can import sources.');
        return;
      }
      statusLine.replaceChildren(el('span', {text:T('Living Review imports now use protected Import review. Export or select the original source file there, review metadata and candidates, then activate resolved records. Saved history is retained; analysis is run separately.')}),
        el('button', {type:'button', class:'btn b-out sm', text:T('Open import review'), onclick:async()=>{
          if (await go('import')) await WorkflowImports.list();
        }}));
    }
    async function importUpdate() { try { await protectedImportRoute(); } catch (error) { WorkflowControls.error(error); } }

    function outcomeLayer() {
      return {
        comparison: cmpInput.value.trim(), outcome: outInput.value.trim(),
        timepoint: tpInput.value.trim(), measure: msrInput.value.trim(),
      };
    }

    /* ---- 变更报告渲染（import/check-now 响应与历史条目共用） ---- */
    function applyResult(d, source, label) {
      viewEntry = normalizeEntry(d, source, label);
      renderEntry();
    }
    function normalizeEntry(d, source, label) {
      d = d || {};
      const entry = d.history_entry || {};
      const nNew = d.new_articles ? d.new_articles.length : (entry.n_new != null ? entry.n_new : null);
      return {
        when: entry.when || '',
        source: entry.source || source || '',
        label: entry.label || label || '',
        n_new: nNew == null ? 0 : nNew,
        n_screen: d.al_prioritized ? d.al_prioritized.length : null,
        fetched: d.fetched != null ? d.fetched : (entry.fetched != null ? entry.fetched : null),
        pending_new: (d.pending_new != null ? d.pending_new : entry.pending_new) || 0,
        already_present: (d.already_present != null ? d.already_present
          : (entry.duplicates_removed || 0)),
        duplicates_removed: d.duplicates_removed != null ? d.duplicates_removed : (entry.duplicates_removed || 0),
        al_available: d.al_available != null ? d.al_available : entry.al_available,
        al_note: d.al_note || '',
        conclusion_changed: !!(d.conclusion_changed != null ? d.conclusion_changed : entry.conclusion_changed),
        meta_before: d.meta_before || entry.meta_before || null,
        meta_after: d.meta_after || entry.meta_after || null,
        change_report: d.change_report || entry.change_report || '',
        comparison: entry.comparison || '', outcome: entry.outcome || '',
        timepoint: entry.timepoint || '', measure: entry.measure || '',
      };
    }

    function metaRow(label, before, after) {
      return el('tr', {}, el('th', { text: label }),
        el('td', { translate: 'no', text: before }), el('td', { translate: 'no', text: after }));
    }
    function ciText(meta) {
      if (!meta || meta.ci_low == null || meta.ci_high == null) return '—';
      return fmtNum(meta.ci_low) + ' ~ ' + fmtNum(meta.ci_high);
    }

    function renderEntry() {
      reportBox.replaceChildren();
      const e = viewEntry;
      if (!e) { reportEmpty.hidden = false; reportBox.hidden = true; return; }
      reportEmpty.hidden = true; reportBox.hidden = false;

      const head = el('div', { style: 'display:flex;gap:6px;flex-wrap:wrap;align-items:center' },
        el('b', { style: 'font-size:.85rem', text: e.when ? T('更新时间：{0}', e.when) : T('本次更新') }));
      if (e.source) head.append(el('span', { class: 'chip', text: T(SOURCE_LABELS[e.source] || e.source) }));
      if (e.label) head.append(el('span', { class: 'chip', translate: 'no', text: e.label }));
      reportBox.append(head);

      const stats = el('div', { style: 'display:flex;gap:6px;flex-wrap:wrap;margin-top:6px' },
        el('span', { class: 'chip', text: T('新增 {0} 篇', String(e.n_new)) }),
        el('span', { class: 'chip', text: T('去重移除 {0} 篇', String(e.duplicates_removed)) }),
        // PubMed 拉取口径（r8 评审 medium）：拉回条数 / 待审条数 / 库内已存在条数
        // 各自独立显示，不再把「已存在」算成「新增」。
        e.pending_new ? el('span', { class: 'chip', text: T('导入待审 {0} 篇', String(e.pending_new)) }) : null,
        e.fetched != null ? el('span', { class: 'chip', text: T('PubMed 拉回 {0} 篇', String(e.fetched)) }) : null,
        e.already_present ? el('span', { class: 'chip', text: T('库内已存在 {0} 篇', String(e.already_present)) }) : null);
      if (e.n_screen != null) stats.append(el('span', { class: 'chip', text: T('待新筛 {0} 篇（机器学习热启动排序）', String(e.n_screen)) }));
      else if (e.al_available === false) stats.append(el('span', { class: 'chip', text: T('机器学习排序不可用') }));
      reportBox.append(stats);
      if (e.al_available === false && e.al_note) {
        reportBox.append(el('p', { class: 'hint', style: 'margin:4px 0 0', text: T('机器学习排序不可用：') + String(e.al_note) }));
      }

      if (e.comparison || e.outcome || e.timepoint || e.measure) {
        const layer = el('p', { class: 'hint', style: 'margin:6px 0 0' });
        layer.append(document.createTextNode(T('结局层') + '：'));
        for (const v of [e.comparison, e.outcome, e.timepoint, e.measure]) {
          if (v) layer.append(el('span', { class: 'chip', translate: 'no', text: String(v) }));
        }
        reportBox.append(layer);
      }

      const before = e.meta_before, after = e.meta_after;
      if (before && Object.keys(before).length || after && Object.keys(after).length) {
        const caption = [after && after.measure, after && after.model, after && after.ci_method]
          .filter(Boolean).map(String).join(' · ');
        reportBox.append(el('table', {},
          el('thead', {}, el('tr', {},
            el('th', { text: T('合并结果对比') }),
            el('th', { text: T('更新前') }), el('th', { text: T('更新后') }))),
          el('tbody', {},
            metaRow(T('研究数'), before ? fmtNum(before.n_studies) : '—', fmtNum(after && after.n_studies)),
            metaRow(T('合并效应'), before ? fmtNum(before.pooled) : '—', fmtNum(after && after.pooled)),
            metaRow(T('95% 置信区间'), ciText(before), ciText(after)),
            metaRow(T('I²（%）'), before ? fmtNum(before.i2_percent) : '—', fmtNum(after && after.i2_percent))),
          caption ? el('caption', { style: 'font-size:.72rem;color:var(--muted);caption-side:bottom;text-align:left', translate: 'no', text: caption }) : null));
      } else {
        reportBox.append(el('p', { class: 'hint', style: 'margin:6px 0 0', text: T('本次更新未重算合并结果（未提供完整结局层或研究数不足）。') }));
      }

      reportBox.append(el('p', { style: 'margin:8px 0 0' },
        el('span', { class: 'rf-lr-flip ' + (e.conclusion_changed ? 'rf-lr-flip-yes' : 'rf-lr-flip-no'),
          text: e.conclusion_changed ? T('⚠ 结论已翻转（CI 跨 null 状态变化）') : T('结论未翻转') })));
      if (e.change_report) {
        reportBox.append(el('p', { class: 'hint', style: 'margin:6px 0 0', text: T('影响评估') }),
          el('pre', { translate: 'no', text: String(e.change_report) }));
      }
    }

    /* ---- 历史列表 ---- */
    function renderHistory() {
      histBox.replaceChildren();
      histDetails.hidden = history.length === 0;
      for (const entry of history.slice(0, 30)) {
        if (!entry || typeof entry !== 'object') continue;
        const row = el('button', { type: 'button' });
        row.append(el('span', { translate: 'no',
          text: [entry.when, SOURCE_LABELS[entry.source] || entry.source,
            T('新增 {0}', String(entry.n_new == null ? 0 : entry.n_new)),
            entry.conclusion_changed ? T('结论已翻转') : ''].filter(Boolean).join(' · ') }));
        row.addEventListener('click', safe(() => {
          viewEntry = normalizeEntry(entry, entry.source, entry.label);
          renderEntry();
        }));
        histBox.append(row);
      }
    }

    /* ---- 列表刷新 ---- */
    async function refreshLists() {
      try {
        if (typeof api !== 'function' || typeof taskApi !== 'function') return;
        const ticket = ++version;
        const [recRes, histRes] = await Promise.all([
          api(taskApi() + '/living-review/search-records'),
          api(taskApi() + '/living-review/history'),
        ]);
        if (ticket !== version) return;
        records = (recRes && recRes.search_records) || [];
        const auto = recRes?.living_review_auto || {enabled: false, interval_days: 30};
        autoCheck.checked = !!auto.enabled;
        intervalInput.value = String(auto.interval_days || 30);
        scheduleAutoCheck();   // 开关开启 → 本会话内自动检查（关闭即 clearAutoTimer）
        history = (histRes && histRes.history) || [];
        renderRecords();
        renderHistory();
        if (!viewEntry && history.length) {
          viewEntry = normalizeEntry(history[0], history[0].source, history[0].label);
          renderEntry();
        }
      } catch (e) { /* 面板可选数据加载失败不阻塞；详情看状态行 */ }
    }

    /* ---- 自动监控（显式开关；后端只写设置；自动检查由本页会话内定时器实现） ---- */
    async function enableAuto(enabled) {
      let ticket;
      try {
        if (typeof api !== 'function' || typeof taskApi !== 'function') return false;
        if (!guardTask()) return false;
        const body = { enabled, interval_days: Math.max(1, Math.min(3650, Number(intervalInput.value) || 30)) };
        ticket = ++version;
        await api(taskApi() + '/living-review/enable-auto', { method: 'POST', body });
        if (ticket !== version) return null;
        if (typeof toast === 'function') toast(enabled
          ? T('已开启会话内自动检查（每 {0} 天）；关闭应用即停，服务器不会后台联网。', String(body.interval_days))
          : T('自动监控已关闭'), 'inc');
        return true;
      } catch (e) {
        if (ticket !== undefined && ticket !== version) return null;
        statusLine.textContent = T('设置自动监控失败：') + ((e && e.message) || '');
        if (typeof toast === 'function') toast(T('设置自动监控失败：') + ((e && e.message) || ''), 'warn');
        return false;
      }
    }
    autoCheck.addEventListener('change', safe(async () => {
      const enabled = autoCheck.checked;
      const ok = await enableAuto(enabled);
      if (ok === null) return;
      if (!ok) { autoCheck.checked = !enabled; return; }
      if (enabled) scheduleAutoCheck(); else clearAutoTimer();
    }));
    intervalInput.addEventListener('change', safe(async () => {
      if (autoCheck.checked) {   // 间隔变化即时写回设置并重排会话内定时器
        await enableAuto(true);
        scheduleAutoCheck();
      }
    }));

    /* ---- 检查更新（W3 真拉取：后端 PubMed 插件 → 虚拟 RIS → 受保护导入待审） ---- */
    checkBtn.addEventListener('click', safe(() => { runCheck(null, {}); }));

    function firstPubmedRecord() {
      return records.find(rec => rec && typeof rec === 'object' && rec.database === 'pubmed'
        && String(rec.query_string || '').trim()) || null;
    }
    function intervalDays() {
      return Math.max(1, Math.min(3650, Number(intervalInput.value) || 30));
    }
    function computeSince(rec) {
      const last = String((rec && rec.last_run_date) || '').trim();
      if (/^\d{4}-\d{2}-\d{2}$/.test(last)) return last;
      const past = new Date(Date.now() - intervalDays() * 86400000);
      return past.toISOString().slice(0, 10);
    }

    async function runCheck(record, opts) {
      opts = opts || {};
      try {
        if (typeof api !== 'function' || typeof taskApi !== 'function') return;
        if (!guardTask() || checkBusy) return;
        const rec = record || firstPubmedRecord();
        const query = String((rec && rec.query_string) || '').trim();
        if (!query) {
          statusLine.textContent = T('请先保存检索式。');
          if (!opts.auto && typeof toast === 'function') toast(T('请先保存检索式。'), 'warn');
          return;
        }
        const since = computeSince(rec);
        const ticket = ++version;
        checkBusy = true;
        checkBtn.disabled = true;
        try {
          statusLine.textContent = T(opts.auto ? '自动检查中…' : '正在通过 PubMed 插件拉取增量…');
          const d = await api(taskApi() + '/living-review/check-now',
            { method: 'POST', body: { query_string: query, since_date: since } });
          if (ticket !== version) return;
          if (d && Array.isArray(d.search_records)) { records = d.search_records; renderRecords(); }
          applyResult(d, 'pubmed', (d && d.virtual_file) || '');
          statusLine.textContent = (d && d.fetched)
            ? T('✓ 已拉取 {0} 条，进入导入核验待审。', String(d.fetched))
            : T('✓ 自 {0} 起无新增。', since);
          refreshLists();  // 同步服务端推进的检索式与历史（内部自带票据与兜错）
        } finally {
          checkBusy = false;
          checkBtn.disabled = false;
        }
      } catch (e) {
        // 优雅降级：检查失败只提示（含 409 pubmed_unavailable 的手动导入指引），
        // 面板与离线手动导入路径不受影响，异常绝不冒泡。
        const code = (e && e.code) || '';
        const msg = String((e && e.message) || e || '');
        statusLine.textContent = T('PubMed 检查失败：') + msg
          + (code === 'pubmed_unavailable' ? ' ' + T('网络不可达，请手动导出文件后导入。') : '');
        if (!opts.auto && typeof toast === 'function') toast(T('PubMed 检查失败：') + msg, 'warn');
      }
    }

    /* ---- 会话内自动检查（纯前端定时器；应用关闭即停，后端不起任何服务） ----
     * 溢出红线（r8 评审 blocking）：setTimeout/setInterval 的延迟在浏览器与
     * Node 都按 32 位有符号整数处理（上限 MAX_TIMER_MS = 2^31-1）。
     * days * 86400000 在 days >= 25 时即溢出（默认 30 天 = 2,592,000,000），
     * 溢出后延迟被钳到 1ms；若直接 setInterval(…, intervalMs) 就变成**每秒
     * 数百次联网**（Node 实测 300ms 内 5 次 tick、TimeoutOverflowWarning），
     * 直接违背 NCBI「无并发轰炸」礼仪并污染导入核验队列。
     * 因此改为「单次 setTimeout 分片推进」：每片 <= MAX_TIMER_MS 且 <= 剩余
     * 时间，累计到目标时刻才真正检查一次；每次重新计算剩余时间，天然吸收
     * 定时器漂移与休眠唤醒。 */
    const MAX_TIMER_MS = 2147483647;   // 2^31-1：32 位定时器上限
    let autoTimer = null;         // 当前分片 timer（interval/timeout 共用句柄）
    let autoDueAt = 0;            // 下一次自动检查的目标时刻（epoch ms）
    let autoCatchup = null;       // 到期补检的一次性定时器（与 autoTimer 同生共死）
    function clearAutoTimer() {
      if (autoTimer !== null) { clearTimeout(autoTimer); autoTimer = null; }
      if (autoCatchup !== null) { clearTimeout(autoCatchup); autoCatchup = null; }
      autoDueAt = 0;
    }
    /* 一片的延迟：既不超过 32 位上限，也不越过目标时刻 */
    function nextTimerSlice() {
      const remaining = autoDueAt - Date.now();
      return Math.max(1, Math.min(MAX_TIMER_MS, remaining));
    }
    function armAutoSlice() {
      if (autoTimer !== null) { clearTimeout(autoTimer); autoTimer = null; }
      if (!autoCheck.checked) return;
      const remaining = autoDueAt - Date.now();
      if (remaining > MAX_TIMER_MS) {          // 还早：再睡一片
        autoTimer = setTimeout(armAutoSlice, nextTimerSlice());
        return;
      }
      autoTimer = setTimeout(() => {           // 到期：检查一次并排下一轮
        autoTimer = null;
        if (autoCheck.checked && !checkBusy) runCheck(null, { auto: true });
        autoDueAt = Date.now() + autoIntervalMs();
        armAutoSlice();
      }, Math.max(0, remaining));
    }
    function autoIntervalMs() {
      return Math.max(1, intervalDays()) * 86400000;
    }
    function scheduleAutoCheck() {
      clearAutoTimer();
      if (!autoCheck.checked || !firstPubmedRecord()) return;
      const intervalMs = autoIntervalMs();
      // 会话开始时已到期（上次检查距今 ≥ 间隔）→ 短延迟补检一次，随后按间隔排下一轮
      const last = Date.parse(String((firstPubmedRecord() || {}).last_run_date || '')) || 0;
      if (last && Date.now() - last >= intervalMs) {
        autoDueAt = Date.now();
        autoCatchup = setTimeout(() => {
          autoCatchup = null;
          if (!autoCheck.checked) return;
          if (!checkBusy) runCheck(null, { auto: true });
          autoDueAt = Date.now() + intervalMs;
          armAutoSlice();
        }, 5000);
        return;
      }
      autoDueAt = (last || Date.now()) + intervalMs;
      armAutoSlice();
    }

    /* ---- 结局层预填：跟随分析页当前范围（用户改过则不打扰） ---- */
    function syncLayerFromAnalysis() {
      try {
        if (layerTouched || typeof anValue !== 'function') return;
        const pairs = [[cmpInput, 'anComparison'], [outInput, 'anOutcome'], [tpInput, 'anTimepoint'], [msrInput, 'anMeasure']];
        for (const [input, id] of pairs) {
          const v = anValue(id);
          if (v) input.value = v;
        }
      } catch (e) { /* 单点故障 */ }
    }
    for (const input of [cmpInput, outInput, tpInput, msrInput]) {
      input.addEventListener('input', safe(() => { layerTouched = true; }));
    }

    /* ---- 语言切换：重填静态文案并重渲染动态区 ---- */
    function refreshLang() {
      try {
        heading.textContent = T('活体综述');
        hint.textContent = T('Living Review imports now use protected Import review. Export or select the original source file there, review metadata and candidates, then activate resolved records. Saved history is retained; analysis is run separately.');
        recCaption.textContent = T('检索式管理');
        recEmpty.textContent = T('尚未保存检索式。');
        saveDetails.querySelector(':scope > button').textContent = T('保存 / 更新检索式');
        const keepDb = dbSel.value || 'pubmed';
        dbSel.replaceChildren(...Object.entries(DB_LABELS).map(([v, label]) => el('option', { value: v, text: T(label) })));
        dbSel.value = keepDb;
        queryInput.placeholder = T('粘贴数据库检索式（如 PubMed 检索框中的完整检索式）');
        queryInput.setAttribute('aria-label', T('检索式'));
        dateInput.setAttribute('aria-label', T('上次检索日期（可选）'));
        hitsInput.setAttribute('aria-label', T('上次命中数（可选）'));
        saveBtn.textContent = T('保存检索式');
        importCaption.textContent = T('手动导入更新（离线）');
        importBtn.textContent = T('Open import review');
        importHint.textContent = T('Living Review imports now use protected Import review. Export or select the original source file there, review metadata and candidates, then activate resolved records. Saved history is retained; analysis is run separately.');
        reportCaption.textContent = T('变更报告');
        reportEmpty.textContent = T('No records yet.');
        histDetails.querySelector(':scope > button').textContent = T('历次更新');
        autoDetails.querySelector(':scope > button').textContent = T('自动监控（可选，默认关闭）');
        autoLabel.querySelector('span').textContent = T('开启自动监控（PubMed 插件，显式开启）');
        intervalInput.setAttribute('aria-label', T('检查间隔（天）'));
        checkBtn.textContent = T('立即检查更新（PubMed）');
        autoNote.textContent = T('仅访问 PubMed 官方接口（eutils.ncbi.nlm.nih.gov），且仅在手动检查或会话内自动检查时发起。');
        autoImportNote.textContent = T('Living Review imports now use protected Import review. Export or select the original source file there, review metadata and candidates, then activate resolved records. Saved history is retained; analysis is run separately.');
        panel.setAttribute('aria-label', T('活体综述'));
        renderRecords();
        renderEntry();
        renderHistory();
      } catch (e) { /* 单点故障 */ }
    }

    /* ---- 事件接入 ---- */
    document.addEventListener('reviewflow:analysis-loaded', safe(refreshLists));
    document.addEventListener('reviewflow:analysis-core-loaded', safe(syncLayerFromAnalysis));
    document.addEventListener('reviewflow:analysis-task-changed', safe(() => {
      version++;
      clearAutoTimer();   // 换任务：停旧任务的会话内自动检查
      records = []; history = []; viewEntry = null;
      renderRecords(); renderHistory(); renderEntry();
      recEmpty.hidden = false;
      histDetails.hidden = true;
      autoCheck.checked = false;
      intervalInput.value = '30';
      saveBtn.disabled = false;
      importBtn.disabled = false;
      checkBtn.disabled = false;
      layerTouched = false;
      statusLine.textContent = '';
    }));
    document.addEventListener('reviewflow:language-changed', safe(refreshLang));

    renderRecords();
    renderHistory();
    renderEntry();
  } catch (e) { /* 单点故障：模块初始化失败不影响主应用与 /api/health */ }
})();
