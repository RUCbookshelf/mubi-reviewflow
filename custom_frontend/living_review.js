/* ReviewFlow · 活体综述监控面板（衍生产品规格 2026-09-30 §5.5；API=W13 Batch 段）。
 *
 * 分析页追加「活体综述」面板（挂在 .an-workspace 内 #rfManuscriptPanel 之后，
 * 兜底 #anJumpQual，与 GRADE/撰写稿件面板同一追加式接入）：
 *   - 检索式管理：已保存检索式列表（数据库/检索式/上次日期/命中数），
 *     PubMed 记录给「一键打开 PubMed ↗」外链（仅 target=_blank 导航，
 *     本模块不发起任何网络请求）；表单保存/更新检索式（POST save-search，
 *     URL 由后端拼字符串生成）；
 *   - 手动导入更新（第一层，100% 离线）：选择 RIS/CSV 文件 + 结局层四要素
 *     （比较组/结局/时间点/指标，预填分析页当前范围）→ POST import-update
 *     （multipart）→ 展示变更报告；
 *   - 变更报告：上次更新时间、新增/去重移除/待新筛（AI 热启动队列）数、
 *     合并结果前后对比表（合并效应 / 95%CI / 研究数 / I²）、结论是否翻转
 *     徽章（CI 跨 null 状态变化）、人可读影响评估（change_report）；
 *     历次更新列表可点选回看（GET history，最新在前）；
 *   - 自动监控（可选插件，默认收起且关闭）：显式开关（POST enable-auto 只写
 *     task.json，不启动后台线程）+ 检查间隔天数；开启后才显示「立即检查」
 *     （POST check-now 手动触发；网络不可达时后端返回降级提示，离线功能不受影响）。
 *
 * 事件接入（追加式）：reviewflow:analysis-loaded（刷新检索式与历史）、
 * reviewflow:analysis-core-loaded（结局层预填，用户改过则不打扰）、
 * reviewflow:analysis-task-changed（复位）、reviewflow:language-changed（重渲染）。
 *
 * 单点故障：初始化与全部回调自行捕获异常；网络仅经全局 api() 访问本应用 API。
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

    /* ---- 面板骨架（静态文案节点集中持有，语言切换时重填） ---- */
    const heading = el('h3', { text: T('活体综述') });
    const hint = el('p', { class: 'hint', style: 'margin:2px 0 8px', text: T('保存检索式，导入新检索到的文献，再查看去重与结果变化。PubMed 检查需单独开启；手动导入仍可使用。') });

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
    const importBtn = el('button', { class: 'btn b-inc', type: 'button', text: T('导入并评估影响') });
    const importHint = el('p', { class: 'hint', style: 'margin:4px 0 0', text: T('结局层四要素用于重跑合并分析并对比前后结论；文件先经安全校验，与库内文献自动去重。') });
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
    const reportEmpty = el('p', { class: 'hint', text: T('尚无更新记录——导入一次增量文献或运行一次检查后，这里展示前后对比与影响评估。') });
    const reportBox = el('div', { class: 'rf-lr-report' });
    const histDetails = disclosure('rfLivingHistory', '历次更新');
    const histBox = el('div', { class: 'rf-lr-hist' });
    histDetails.lastElementChild.append(histBox);

    /* 自动监控（可选，默认收起且关闭） */
    const autoDetails = disclosure('rfLivingAutomation', '自动监控（可选，默认关闭）');
    const autoCheck = el('input', { type: 'checkbox' });
    const autoLabel = el('label', { style: 'display:flex;gap:6px;align-items:center;font-size:.8rem' },
      autoCheck, el('span', { text: T('开启自动监控（PubMed 插件，显式开启）') }));
    const intervalInput = el('input', { class: 'input', type: 'number', min: '1', max: '3650', value: '30',
      style: 'max-width:110px', 'aria-label': T('检查间隔（天）') });
    const checkBtn = el('button', { class: 'btn b-out', type: 'button', text: T('立即检查'), hidden: '' });
    const autoNote = el('p', { class: 'hint', style: 'margin:4px 0 0', text: T('开关只写入任务设置，不启动后台线程；「立即检查」为手动触发的 PubMed 增量拉取（本应用唯一联网入口），失败时自动降级为手动导入模式。') });
    autoDetails.lastElementChild.append(
      autoLabel,
      el('div', { style: 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:6px' },
        el('label', { style: 'font-size:.78rem' }, el('span', { text: T('检查间隔（天）') }), intervalInput),
        checkBtn),
      autoNote);

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
    async function importUpdate() {
      let ticket;
      try {
        if (typeof api !== 'function' || typeof taskApi !== 'function') return;
        if (!guardTask()) return;
        const file = fileInput.files && fileInput.files[0];
        if (!file) { statusLine.textContent = T('请先选择增量文献文件（RIS/CSV）。'); return; }
        const layer = outcomeLayer();
        for (const [name, value] of [['比较组', layer.comparison], ['结局', layer.outcome], ['时间点', layer.timepoint], ['指标代码', layer.measure]]) {
          if (!value) { statusLine.textContent = T('请填写结局层的「{0}」（重跑合并分析需要）。', name); return; }
        }
        const form = new FormData();
        form.append('file', file, file.name);
        form.append('comparison', layer.comparison);
        form.append('outcome', layer.outcome);
        form.append('timepoint', layer.timepoint);
        form.append('measure', layer.measure);
        ticket = ++version;
        importBtn.disabled = true;
        importBtn.dataset.requestTicket = String(ticket);
        statusLine.textContent = T('正在导入并重算…（去重、AI 热启动、重跑合并分析）');
        try {
          const d = await api(taskApi() + '/living-review/import-update', { method: 'POST', form });
          if (ticket !== version) return;
          applyResult(d, 'manual', file.name);
          statusLine.textContent = T('✓ 增量导入完成。');
          if (typeof toast === 'function') toast(T('✓ 增量导入完成：新增 {0} 篇，去重移除 {1} 篇',
            String((d && d.new_articles || []).length), String((d && d.duplicates_removed) || 0)), 'inc');
        } finally {
          if (importBtn.dataset.requestTicket === String(ticket)) importBtn.disabled = false;
        }
        if (ticket === version) await refreshLists();
      } catch (e) {
        if (ticket !== undefined && ticket !== version) return;
        statusLine.textContent = T('导入失败：') + ((e && e.message) || '');
        if (typeof toast === 'function') toast(T('导入失败：') + ((e && e.message) || ''), 'warn');
      }
    }

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
        el('span', { class: 'chip', text: T('去重移除 {0} 篇', String(e.duplicates_removed)) }));
      if (e.n_screen != null) stats.append(el('span', { class: 'chip', text: T('待新筛 {0} 篇（AI 热启动排序）', String(e.n_screen)) }));
      else if (e.al_available === false) stats.append(el('span', { class: 'chip', text: T('AI 排序不可用') }));
      reportBox.append(stats);
      if (e.al_available === false && e.al_note) {
        reportBox.append(el('p', { class: 'hint', style: 'margin:4px 0 0', text: T('AI 排序不可用：') + String(e.al_note) }));
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
        checkBtn.hidden = !autoCheck.checked;
        history = (histRes && histRes.history) || [];
        renderRecords();
        renderHistory();
        if (!viewEntry && history.length) {
          viewEntry = normalizeEntry(history[0], history[0].source, history[0].label);
          renderEntry();
        }
      } catch (e) { /* 面板可选数据加载失败不阻塞；详情看状态行 */ }
    }

    /* ---- 自动监控（显式开关；只写设置，不起线程） ---- */
    async function enableAuto(enabled) {
      let ticket;
      try {
        if (typeof api !== 'function' || typeof taskApi !== 'function') return false;
        if (!guardTask()) return false;
        const body = { enabled, interval_days: Math.max(1, Math.min(3650, Number(intervalInput.value) || 30)) };
        ticket = ++version;
        await api(taskApi() + '/living-review/enable-auto', { method: 'POST', body });
        if (ticket !== version) return null;
        if (typeof toast === 'function') toast(enabled ? T('✓ 自动监控已开启（无后台线程，需手动「立即检查」）') : T('自动监控已关闭'), 'inc');
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
      checkBtn.hidden = !enabled;
    }));
    intervalInput.addEventListener('change', safe(async () => {
      if (autoCheck.checked) await enableAuto(true);   // 间隔变化即时写回设置
    }));

    /* ---- 立即检查（手动触发 PubMed 增量拉取；失败返回降级提示） ---- */
    checkBtn.addEventListener('click', safe(checkNow));
    async function checkNow() {
      let ticket;
      try {
        if (typeof api !== 'function' || typeof taskApi !== 'function') return;
        if (!guardTask()) return;
        const layer = outcomeLayer();
        const complete = layer.comparison && layer.outcome && layer.timepoint && layer.measure;
        const opts = { method: 'POST' };
        if (complete) opts.body = layer;   // 结局层完整才带（缺省跳过 Meta 对比）
        ticket = ++version;
        checkBtn.disabled = true;
        checkBtn.dataset.requestTicket = String(ticket);
        statusLine.textContent = T('正在通过 PubMed 插件拉取增量…');
        try {
          const d = await api(taskApi() + '/living-review/check-now', opts);
          if (ticket !== version) return;
          if (d && d.degraded) {
            statusLine.textContent = T('网络不可达，请手动导出文件后导入。') + ((d.error ? '（' + d.error + '）' : ''));
            if (typeof toast === 'function') toast(T('网络不可达，请手动导出文件后导入。'), 'warn');
            return;
          }
          applyResult(d, 'pubmed', '');
          statusLine.textContent = T('✓ 检查完成：PubMed 拉回 {0} 篇。', String(d && d.fetched != null ? d.fetched : ''));
          if (typeof toast === 'function') toast(T('✓ 检查完成'), 'inc');
        } finally {
          if (checkBtn.dataset.requestTicket === String(ticket)) checkBtn.disabled = false;
        }
        if (ticket === version) await refreshLists();
      } catch (e) {
        if (ticket !== undefined && ticket !== version) return;
        statusLine.textContent = T('检查失败：') + ((e && e.message) || '');
        if (typeof toast === 'function') toast(T('检查失败：') + ((e && e.message) || ''), 'warn');
      }
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
        hint.textContent = T('保存检索式，导入新检索到的文献，再查看去重与结果变化。PubMed 检查需单独开启；手动导入仍可使用。');
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
        importBtn.textContent = T('导入并评估影响');
        importHint.textContent = T('结局层四要素用于重跑合并分析并对比前后结论；文件先经安全校验，与库内文献自动去重。');
        reportCaption.textContent = T('变更报告');
        reportEmpty.textContent = T('尚无更新记录——导入一次增量文献或运行一次检查后，这里展示前后对比与影响评估。');
        histDetails.querySelector(':scope > button').textContent = T('历次更新');
        autoDetails.querySelector(':scope > button').textContent = T('自动监控（可选，默认关闭）');
        autoLabel.querySelector('span').textContent = T('开启自动监控（PubMed 插件，显式开启）');
        intervalInput.setAttribute('aria-label', T('检查间隔（天）'));
        checkBtn.textContent = T('立即检查');
        autoNote.textContent = T('开关只写入任务设置，不启动后台线程；「立即检查」为手动触发的 PubMed 增量拉取（本应用唯一联网入口），失败时自动降级为手动导入模式。');
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
      records = []; history = []; viewEntry = null;
      renderRecords(); renderHistory(); renderEntry();
      recEmpty.hidden = false;
      histDetails.hidden = true;
      autoCheck.checked = false;
      intervalInput.value = '30';
      checkBtn.hidden = true;
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
