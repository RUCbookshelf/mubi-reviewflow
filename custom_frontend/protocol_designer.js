/* ReviewFlow · 方案设计器前端（衍生产品规格 2026-09-30 §1.5；API=W5 Batch 段）。
 *
 * 任务列表页（首页「任务管理」卡）追加「设计方案」入口：
 *   - 方案表单：研究问题 + 四张 PICO 卡片（主输入 + 动态同义词行 + MeSH 本地
 *     建议词典，纯前端硬编码、零网络）+ 纳入/排除标准标签式输入（可增删）；
 *   - [生成检索式]：POST /api/tasks/{id}/protocol（W5）→ 展示 PubMed/Embase/
 *     Cochrane/通用四库检索式（代码块 + 复制按钮；带 url 的库附
 *     target=_blank 的「在浏览器中打开」链接——仅导航，非本模块发起的请求）；
 *   - [保存并创建任务]：确保目标任务存在（目标为「新任务」时按正常创建流程
 *     POST /api/tasks + loadTasks + pickTask）→ 方案嵌入 task.json → 显示
 *     「下一步导入文献」提示；
 *   - [导出方案]：GET .../protocol/export?format=markdown → 下载 Markdown。
 *
 * 目标任务选择显式化（无静默默认变更）：默认「新任务（保存时创建）」，也可
 * 指向任一既有任务（此时读取 GET /protocol 回填表单）；[生成检索式] 在需要
 * 新建任务时先经 confirm 确认。
 *
 * 事件接入（追加式，不改任何既有脚本）：
 *   - reviewflow:task-loaded：规格 §1.5 指定的事件名（主应用将来分发即生效）；
 *   - reviewflow:task-context-changed：既有桥接（pickTask 已分发）→ 刷新目标
 *     任务下拉；
 *   - reviewflow:language-changed：重填静态文案。
 *
 * 单点故障：初始化与全部回调自行捕获异常，任何失败只影响本面板，不波及主
 * 应用与 /api/health。网络仅经全局 api() 访问本应用 API。
 */
(function () {
  'use strict';
  try {
    if (typeof document === 'undefined' || typeof T !== 'function' || typeof el !== 'function') return;

    const anchorBtn = document.getElementById('newTaskBtn');
    const taskCard = anchorBtn ? anchorBtn.closest('.card') : null;
    if (!anchorBtn || !taskCard || !document.querySelector('.page[data-p="home"]')) return;   // 结构不符：安静退出

    /* ---- 样式（随模块注入，不占用 index.html 既有样式段） ---- */
    const style = document.createElement('style');
    style.textContent = [
      '.rf-protocol-card{margin-top:10px}',
      '.rf-protocol-card h3{margin:0 0 6px;font-size:.95rem}',
      '.rf-pd-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 8px}',
      '.rf-pd-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px;margin:10px 0}',
      '.rf-pico-card{border:1px solid var(--border);border-radius:12px;padding:10px 12px;margin:0;background:var(--bg)',
      '.rf-pico-card b{font-size:.85rem}',
      '.rf-pico-card .input{max-width:100%}',
      '.rf-syn-row{display:flex;gap:6px;align-items:center;margin-top:4px}',
      '.rf-syn-row .input{flex:1;min-width:0;font-size:.8rem;padding:4px 8px}',
      '.rf-chipbox{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}',
      '.rf-chip{display:inline-flex;align-items:center;gap:4px;border:1px solid var(--border);border-radius:999px;padding:2px 10px;font-size:.78rem;background:var(--bg)',
      '.rf-chip button{border:none;background:none;color:var(--danger);cursor:pointer;font-weight:700;padding:0 2px}',
      '.rf-strategy{border:1px dashed var(--border);border-radius:10px;padding:8px 10px;margin-top:8px}',
      '.rf-strategy pre{margin:6px 0 0;padding:8px 10px;background:var(--bg);border-radius:8px;white-space:pre-wrap;word-break:break-all;font-size:.78rem}',
      '.rf-status{margin:6px 0 0;font-size:.8rem}',
    ].join('\n');
    (document.head || document.documentElement).appendChild(style);

    /* ---- MeSH 本地建议词典（纯前端硬编码常用主题词，零网络；仅为建议，
       用户可自由输入任意 MeSH 词） ---- */
    const MESH_HINTS = {
      population: ['Humans', 'Adult', 'Middle Aged', 'Aged', 'Young Adult', 'Adolescent', 'Child',
        'Infant', 'Female', 'Male', 'Pregnancy', 'Comorbidity', 'Inpatients', 'Outpatients', 'Risk Factors'],
      intervention: ['Therapeutics', 'Drug Therapy', 'Exercise Therapy', 'Diet', 'Counseling',
        'Physical Therapy Modalities', 'Surgical Procedures, Operative', 'Patient Education as Topic',
        'Telemedicine', 'Mind-Body Therapies', 'Vaccines'],
      comparator: ['Placebos', 'Placebo Effect', 'Comparative Study', 'Watchful Waiting'],
      outcome: ['Treatment Outcome', 'Patient Outcome Assessment', 'Quality of Life', 'Mortality',
        'Morbidity', 'Incidence', 'Prevalence', 'Severity of Illness Index', 'Pain',
        'Drug-Related Side Effects and Adverse Reactions', 'Biomarkers', 'Cost-Benefit Analysis'],
    };
    const PICO_SLOTS = [
      { key: 'population', labelKey: '人群（P）' },
      { key: 'intervention', labelKey: '干预（I）' },
      { key: 'comparator', labelKey: '对照（C）' },
      { key: 'outcome', labelKey: '结局（O）' },
    ];
    const DB_LABELS = {
      pubmed: 'PubMed', embase: 'Embase', cochrane: 'Cochrane Library', wos: 'Web of Science',
      generic: '通用格式',
    };

    /* ---- 状态 ---- */
    let formRevision = 0;
    let version = 0;            // 异步票据
    let anchoredTaskId = null;  // 已把方案保存到的任务（生成/保存流程产物）
    const picoState = {};       // key -> {synonyms:[], meshTerms:[]}
    const inclState = { tags: [] };
    const exclState = { tags: [] };

    function safe(fn) { return function () { try { fn.apply(this, arguments); } catch (e) { /* 单点故障 */ } }; }

    /* 目标任务的 API 前缀：当前选中任务走全局 taskApi()，其余任务显式拼路径 */
    function taskBase(taskId) {
      if (typeof S !== 'undefined' && S.task && S.task.task_id === taskId && typeof taskApi === 'function') return taskApi();
      return '/api/tasks/' + encodeURIComponent(taskId);
    }

    /* ---- 复制到剪贴板（clipboard API 优先，失败退回 execCommand；均本地操作） ---- */
    function copyText(text) {
      return new Promise(resolve => {
        const fallback = () => {
          try {
            const ta = document.createElement('textarea');
            ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
            document.body.appendChild(ta); ta.select();
            const ok = document.execCommand('copy');
            ta.remove(); return !!ok;
          } catch (e2) { return false; }
        };
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(() => resolve(true), () => resolve(fallback()));
            return;
          }
        } catch (e) { /* 落入回退 */ }
        resolve(fallback());
      });
    }

    /* ---- 小构件 ---- */
    function smallBtn(label, kind) {
      return el('button', { class: 'btn ' + (kind || 'b-out'), type: 'button', text: T(label), style: 'padding:4px 10px;font-size:.78rem' });
    }
    function chip(text, onRemove) {
      const x = el('button', { type: 'button', text: '✕', title: T('移除'), 'aria-label': T('移除 {0}', text) });
      x.addEventListener('click', safe(onRemove));
      return el('span', { class: 'rf-chip', translate: 'no' }, el('span', { text: text }), x);
    }

    /* ---- 静态文案节点（语言切换时重填） ---- */
    const L = {};
    function setText(node, tpl, ...args) { if (node) node.textContent = T(tpl, ...args); return node; }

    /* ---- 表单骨架 ---- */
    const panel = el('section', { class: 'card rf-protocol-card', id: 'rfProtocolPanel', hidden: '' });
    const toggleBtn = el('button', { class: 'btn b-out', id: 'rfProtocolToggle', type: 'button', 'aria-controls': 'rfProtocolPanel', 'aria-expanded': 'false', text: T('设计方案') });
    anchorBtn.after(toggleBtn);

    L.heading = el('h3', { text: T('设计方案（Protocol Designer）') });
    L.hint = el('p', { class: 'hint', style: 'margin:2px 0 8px', text: T('综述开始前结构化定义 PICO 要素，自动生成各数据库检索式；方案保存进任务的 task.json，可导出为 Markdown（可直接粘贴到 PROSPERO 注册表单）。全部计算在本地完成。') });

    const targetSel = el('select', { class: 'input', id: 'rfProtocolTarget', style: 'max-width:320px', 'aria-label': T('目标任务') });
    L.targetCap = el('span', { class: 'hint', style: 'margin:0', text: T('目标任务') });
    const targetRow = el('div', { class: 'rf-pd-row' }, L.targetCap, targetSel, (L.targetHint = el('span', { class: 'hint', style: 'margin:0' })));

    L.titleCap = el('span', { class: 'hint', style: 'margin:0', text: T('方案标题（必填，创建新任务时即任务名）') });
    const titleInput = el('input', { class: 'input', id: 'rfProtocolTitle', style: 'max-width:360px', placeholder: T('如：有氧运动对 2 型糖尿病患者血糖控制的影响') });
    const titleRow = el('div', { class: 'rf-pd-row' }, L.titleCap, titleInput);

    L.rqCap = el('span', { class: 'hint', style: 'align-self:flex-start;margin:4px 6px 0 0', text: T('研究问题') });
    const rqInput = el('textarea', { class: 'input', id: 'rfProtocolQuestion', rows: 2, style: 'flex:1;min-width:240px', placeholder: T('结构化研究问题（自由文本）') });
    const rqRow = el('div', { class: 'rf-pd-row' }, L.rqCap, rqInput);

    const picoGrid = el('div', { class: 'rf-pd-grid' });
    for (const slot of PICO_SLOTS) {
      picoState[slot.key] = { synonyms: [], meshTerms: [] };
      const valueInput = el('input', { class: 'input', style: 'max-width:100%', placeholder: T('自然语言描述（如 adults with type 2 diabetes）') });
      const synBox = el('div', {});
      const meshInput = el('input', { class: 'input', list: 'rfMesh-' + slot.key, style: 'flex:1;min-width:0;font-size:.8rem;padding:4px 8px', placeholder: T('输入或从建议中选择 MeSH 主题词') });
      const meshList = el('datalist', { id: 'rfMesh-' + slot.key },
        (MESH_HINTS[slot.key] || []).map(m => el('option', { value: m })));
      const meshChips = el('div', { class: 'rf-chipbox' });
      const addMesh = smallBtn('添加 MeSH');
      addMesh.addEventListener('click', safe(() => {
        const v = meshInput.value.trim();
        if (!v) return;
        if (!picoState[slot.key].meshTerms.includes(v)) picoState[slot.key].meshTerms.push(v);
        meshInput.value = '';
        renderMeshChips();
      }));
      meshInput.addEventListener('keydown', safe(e => { if (e.key === 'Enter') { e.preventDefault(); addMesh.click(); } }));

      function renderMeshChips() {
        meshChips.replaceChildren();
        for (const term of picoState[slot.key].meshTerms) {
          meshChips.append(chip(term, () => {
            const i = picoState[slot.key].meshTerms.indexOf(term);
            if (i >= 0) picoState[slot.key].meshTerms.splice(i, 1);
            renderMeshChips();
          }));
        }
      }

      const addSyn = smallBtn('添加同义词');
      addSyn.addEventListener('click', safe(() => {
        picoState[slot.key].synonyms.push('');
        renderSynRows();
      }));
      function renderSynRows() {
        synBox.replaceChildren();
        picoState[slot.key].synonyms.forEach((_, idx) => {
          const inp = el('input', { class: 'input', value: picoState[slot.key].synonyms[idx], placeholder: T('同义词/变体（如 T2DM）'), style: 'flex:1;min-width:0' });
          inp.addEventListener('input', safe(() => { picoState[slot.key].synonyms[idx] = inp.value; }));
          const rm = el('button', { class: 'btn b-exc', type: 'button', text: '✕', title: T('删除该行'), style: 'padding:2px 8px' });
          rm.addEventListener('click', safe(() => {
            picoState[slot.key].synonyms.splice(idx, 1);
            renderSynRows();
          }));
          synBox.append(el('div', { class: 'rf-syn-row' }, inp, rm));
        });
      }
      renderSynRows();

      picoGrid.append(el('article', { class: 'rf-pico-card' },
        el('b', { text: T(slot.labelKey) }),
        el('div', { style: 'margin-top:6px' }, valueInput),
        el('div', { class: 'rf-pd-row', style: 'margin:6px 0 0' }, addSyn),
        synBox,
        el('div', { class: 'rf-pd-row', style: 'margin:6px 0 0' }, meshInput, addMesh),
        meshList, meshChips));
      picoState[slot.key].valueInput = valueInput;
      picoState[slot.key].renderers = [renderSynRows, renderMeshChips];
    }

    /* 纳入/排除标准（标签式输入） */
    function tagInput(labelTpl, state) {
      const input = el('input', { class: 'input', style: 'flex:1;min-width:180px', placeholder: T('输入一条标准后按回车或点击添加') });
      const add = smallBtn('添加');
      const chips = el('div', { class: 'rf-chipbox' });
      function renderChips() {
        chips.replaceChildren();
        for (const tag of state.tags) {
          chips.append(chip(tag, () => {
            const i = state.tags.indexOf(tag);
            if (i >= 0) state.tags.splice(i, 1);
            renderChips();
          }));
        }
      }
      function addTag() {
        const v = input.value.trim();
        if (!v) return;
        if (!state.tags.includes(v)) state.tags.push(v);
        input.value = '';
        renderChips();
      }
      add.addEventListener('click', safe(addTag));
      input.addEventListener('keydown', safe(e => { if (e.key === 'Enter') { e.preventDefault(); addTag(); } }));
      const root = el('div', { class: 'rf-pico-card' }, el('b', { text: T(labelTpl) }),
        el('div', { class: 'rf-pd-row', style: 'margin:6px 0 0' }, input, add), chips);
      state.render = renderChips;
      return root;
    }
    picoGrid.append(tagInput('纳入标准', inclState), tagInput('排除标准', exclState));

    /* ---- 操作按钮 ---- */
    const genBtn = el('button', { class: 'btn b-out', type: 'button', text: T('生成检索式') });
    const saveBtn = el('button', { class: 'btn b-inc', type: 'button', text: T('保存并创建任务') });
    const exportBtn = el('button', { class: 'btn b-out', type: 'button', text: T('导出方案') });
    const statusLine = el('p', { class: 'rf-status hint', 'role': 'status' });
    const nextBox = el('div', { class: 'rf-pd-row', hidden: '' });
    const nextNav = smallBtn('导入文献', 'b-inc');
    nextNav.addEventListener('click', safe(() => { if (typeof go === 'function') go('import'); }));
    nextBox.append(el('span', { class: 'hint', text: T('方案已保存并嵌入任务。下一步导入文献，然后开始初筛。') }), nextNav);

    const strategyBox = el('div', { id: 'rfProtocolStrategies' });
    genBtn.addEventListener('click', safe(() => runFlow(false)));
    saveBtn.addEventListener('click', safe(() => runFlow(true)));
    exportBtn.addEventListener('click', safe(exportMarkdown));

    panel.append(L.heading, L.hint, targetRow, titleRow, rqRow, picoGrid,
      el('div', { class: 'rf-pd-row' }, genBtn, saveBtn, exportBtn),
      statusLine, nextBox, strategyBox);
    taskCard.after(panel);
    panel.addEventListener('input', () => { formRevision++; setText(L.targetHint, ''); });

    toggleBtn.addEventListener('click', safe(() => {
      panel.hidden = !panel.hidden;
      toggleBtn.setAttribute('aria-expanded', String(!panel.hidden));
      if (!panel.hidden) refreshTargetOptions();
    }));

    /* ---- 目标任务下拉 ---- */
    function refreshTargetOptions() {
      const tasks = (typeof S !== 'undefined' && Array.isArray(S.tasks)) ? S.tasks : [];
      const prev = targetSel.value || '__new__';
      targetSel.textContent = '';
      targetSel.append(el('option', { value: '__new__', text: T('新任务（保存时创建）') }));
      for (const t of tasks) {
        if (!t || !t.task_id) continue;
        targetSel.append(el('option', { value: t.task_id, translate: 'no', text: t.name + (t.archived ? T('（已归档）') : '') }));
      }
      targetSel.value = Array.from(targetSel.options).some(o => o.value === prev) ? prev : '__new__';
    }
    targetSel.addEventListener('change', safe(() => {
      version++;
      anchoredTaskId = null;
      nextBox.hidden = true;
      const v = targetSel.value;
      if (v === '__new__') { setText(L.targetHint, ''); return; }
      loadProtocolIntoForm(v);
    }));

    /* ---- 读取已保存方案回填表单（GET /protocol；未保存过 -> 404 安静提示） ---- */
    function loadProtocolIntoForm(taskId) {
      if (typeof api !== 'function') return;
      const revision = formRevision;
      const ticket = ++version;
      setText(L.targetHint, T('正在读取已保存方案…'));
      api(taskBase(taskId) + '/protocol').then(d => {
        if (ticket !== version || revision !== formRevision) return;
        const p = d && d.protocol;
        if (!p) { setText(L.targetHint, T('该任务尚未保存方案。')); return; }
        fillForm(p);
        setText(L.targetHint, T('已载入该任务保存的方案（{0}）', p.created_at || '—'));
        renderStrategies(p.search_strategies || []);
      }).catch(e => {
        if (ticket !== version || revision !== formRevision) return;
        setText(L.targetHint, (e && e.status === 404) ? T('该任务尚未保存方案。') : ((e && e.message) || ''));
      });
    }

    function fillForm(p) {
      titleInput.value = typeof p.title === 'string' ? p.title : '';
      rqInput.value = typeof p.research_question === 'string' ? p.research_question : '';
      for (const slot of PICO_SLOTS) {
        const st = picoState[slot.key];
        const item = (p.pico || []).find(x => x && x.key === slot.key) || {};
        st.valueInput.value = typeof item.value === 'string' ? item.value : '';
        st.synonyms = Array.isArray(item.synonyms) ? item.synonyms.map(String) : [];
        st.meshTerms = Array.isArray(item.mesh_terms) ? item.mesh_terms.map(String) : [];
        st.renderers.forEach(fn => { try { fn(); } catch (e) { /* 单点故障 */ } });
      }
      inclState.tags = Array.isArray(p.inclusion_criteria) ? p.inclusion_criteria.map(String) : [];
      exclState.tags = Array.isArray(p.exclusion_criteria) ? p.exclusion_criteria.map(String) : [];
      inclState.render(); exclState.render();
    }

    /* ---- 收集表单 ---- */
    function collectBody() {
      const title = titleInput.value.trim();
      if (!title) throw new Error(T('请先填写方案标题。'));
      return {
        title: title,
        research_question: rqInput.value.trim(),
        pico: PICO_SLOTS.map(slot => ({
          key: slot.key,
          label: slot.labelKey,
          value: picoState[slot.key].valueInput.value.trim(),
          synonyms: picoState[slot.key].synonyms.map(s => String(s || '').trim()).filter(Boolean),
          mesh_terms: picoState[slot.key].meshTerms.slice(),
        })),
        inclusion_criteria: inclState.tags.slice(),
        exclusion_criteria: exclState.tags.slice(),
      };
    }

    /* ---- 主流程：生成检索式 / 保存并创建任务 ---- */
    async function runFlow(isSave) {
      let ticket;
      try {
        if (typeof api !== 'function') return;
        let body;
        try { body = collectBody(); } catch (e) { toast(e.message, 'warn'); return; }
        ticket = ++version;
        genBtn.disabled = true; saveBtn.disabled = true; targetSel.disabled = true;

        let taskId = targetSel.value === '__new__' ? null : targetSel.value;
        if (!taskId && anchoredTaskId) taskId = anchoredTaskId;
        if (!taskId) {
          if (!isSave) {
            const ok = confirm(T('生成检索式需要先把方案保存到一个任务。将创建新任务《{0}》，是否继续？', body.title));
            if (!ok) return;
          }
          try {
            const t = await api('/api/tasks', { method: 'POST', body: { name: body.title, description: body.research_question } });
            if (typeof loadTasks === 'function') await loadTasks();
            if (typeof pickTask === 'function') pickTask(t.task_id, { silent: true });
            taskId = t.task_id;
            anchoredTaskId = taskId;
            refreshTargetOptions();
            targetSel.value = taskId;
            if (typeof toast === 'function') toast(T('已创建任务《{0}》，方案将保存到该任务', t.name), 'inc');
          } catch (e) { if (ticket === version) toast(T('创建任务失败：') + ((e && e.message) || ''), 'warn'); return; }
        }

        genBtn.disabled = true; saveBtn.disabled = true;
        statusLine.textContent = T('正在生成检索式…');
        try {
          const d = await api(taskBase(taskId) + '/protocol', { method: 'POST', body: body });
          if (ticket !== version) return;
          const p = d && d.protocol;
          if (!p) throw new Error(T('响应缺少方案数据。'));
          anchoredTaskId = taskId;
          renderStrategies(p.search_strategies || []);
          statusLine.textContent = T('方案已保存到任务 {0}（{1} 条检索式）。', taskId, String((p.search_strategies || []).length));
          if (isSave) {
            nextBox.hidden = false;
            if (typeof toast === 'function') toast(T('✓ 方案已保存并嵌入任务《{0}》', body.title), 'inc');
          }
        } finally {
          if (ticket === version) { genBtn.disabled = false; saveBtn.disabled = false; }
        }
      } catch (e) {
        if (ticket !== undefined && ticket !== version) return;
        statusLine.textContent = T('保存失败：') + ((e && e.message) || '');
        if (typeof toast === 'function') toast(T('保存失败：') + ((e && e.message) || ''), 'warn');
      } finally {
        if (ticket === version) { genBtn.disabled = false; saveBtn.disabled = false; targetSel.disabled = false; }
      }
    }

    /* ---- 检索式展示（代码块 + 复制 + 打开链接） ---- */
    function renderStrategies(strategies) {
      strategyBox.replaceChildren();
      if (!strategies.length) {
        strategyBox.append(el('p', { class: 'hint', text: T('所有 PICO 要素均为空，未生成检索式。请在上方至少填写一个要素。') }));
        return;
      }
      strategyBox.append(el('b', { text: T('各数据库检索式') }));
      for (const st of strategies) {
        if (!st || typeof st.query_string !== 'string') continue;
        const db = String(st.database || '');
        const pre = el('pre', { translate: 'no', text: st.query_string });
        const copy = smallBtn('复制');
        copy.addEventListener('click', safe(async () => {
          const ok = await copyText(st.query_string);
          if (typeof toast === 'function') toast(ok ? T('✓ 已复制 {0} 检索式', DB_LABELS[db] || db) : T('复制失败，请手动选择文本复制。'), ok ? 'inc' : 'warn');
        }));
        const head = el('div', { class: 'rf-pd-row', style: 'margin:0' },
          el('b', { text: DB_LABELS[db] || db }),
          el('span', { class: 'hint', style: 'margin:0', text: T('{0} 行', String(st.line_count == null ? '?' : st.line_count)) }),
          copy);
        if (st.url) {
          head.append(el('a', { class: 'btn b-out', href: st.url, target: '_blank', rel: 'noopener noreferrer',
            text: T('在浏览器中打开 ↗'), style: 'padding:4px 10px;font-size:.78rem' }));
        }
        strategyBox.append(el('div', { class: 'rf-strategy' }, head, pre));
      }
      strategyBox.append(el('p', { class: 'hint', text: T('检索式由本地规则生成（Cochrane Handbook ch.4）；「在浏览器中打开」仅为导航链接，本应用不发起网络请求。Embase 链接仅定位到检索页，需手动粘贴执行。') }));
    }

    /* ---- 导出方案（Markdown 下载） ---- */
    async function exportMarkdown() {
      try {
        if (typeof api !== 'function') return;
        const taskId = targetSel.value === '__new__' ? anchoredTaskId : targetSel.value;
        if (!taskId) { toast(T('请先保存方案（导出需要已保存方案的任务）。'), 'warn'); return; }
        const blob = await api(taskBase(taskId) + '/protocol/export?format=markdown', { blob: true });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = taskId + '-protocol.md';
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 3000);
        if (typeof toast === 'function') toast(T('✓ 已下载：') + a.download, 'inc');
      } catch (e) {
        toast(T('导出失败：') + ((e && e.message) || ''), 'warn');
      }
    }

    /* ---- 语言切换：重填静态文案 ---- */
    function refreshLang() {
      try {
        toggleBtn.textContent = T('设计方案');
        L.heading.textContent = T('设计方案（Protocol Designer）');
        L.hint.textContent = T('综述开始前结构化定义 PICO 要素，自动生成各数据库检索式；方案保存进任务的 task.json，可导出为 Markdown（可直接粘贴到 PROSPERO 注册表单）。全部计算在本地完成。');
        L.targetCap.textContent = T('目标任务');
        L.titleCap.textContent = T('方案标题（必填，创建新任务时即任务名）');
        L.rqCap.textContent = T('研究问题');
        genBtn.textContent = T('生成检索式');
        saveBtn.textContent = T('保存并创建任务');
        exportBtn.textContent = T('导出方案');
        nextNav.textContent = T('导入文献');
        targetSel.setAttribute('aria-label', T('目标任务'));
        refreshTargetOptions();
      } catch (e) { /* 单点故障 */ }
    }

    /* ---- 事件接入：规格事件 + 既有桥接 ---- */
    document.addEventListener('reviewflow:task-loaded', safe(() => { anchoredTaskId = null; refreshTargetOptions(); }));
    document.addEventListener('reviewflow:task-context-changed', safe(e => {
      const id = e && e.detail;
      if (!id) { anchoredTaskId = null; }
      refreshTargetOptions();
    }));
    document.addEventListener('reviewflow:language-changed', safe(refreshLang));

    refreshTargetOptions();
  } catch (e) { /* 单点故障：模块初始化失败不影响主应用与 /api/health */ }
})();
