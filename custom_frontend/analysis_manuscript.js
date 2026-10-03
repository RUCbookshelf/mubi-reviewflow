/* ReviewFlow · 综述撰写器面板（衍生产品规格 2026-09-30 §4.4；API=W12 Batch 段）。
 *
 * 分析页追加「撰写稿件」面板（挂在 .an-workspace 末尾 #anJumpQual 之后，与
 * GRADE 面板同一追加式接入，不改动既有 DOM）：
 *   - 章节勾选（默认全选；Discussion/Limitations/Conclusions 标注「需手动
 *     撰写」——后端 MANUSCRIPT_SECTIONS 中 source=MANUALLY_WRITTEN 的三节，
 *     生成时仅留占位，不自动生成）；
 *   - 格式选择 Word(.docx) / LaTeX(.tex) / Markdown(.md)；
 *   - 参考文献格式 AMA / Vancouver / APA；
 *   - 合并模型 / CI 方法（生成时需重算合成；默认跟随分析页当前选择，
 *     未选时用后端默认 random/normal——显式下拉可见可改，不静默切换）；
 *   - [生成并下载]：POST /api/tasks/{id}/analysis/manuscript/generate →
 *     文件流直接下载（附件名 {task_id}-manuscript.{ext}）；
 *   - [Markdown 预览]：GET .../manuscript/preview → 前端渲染 Markdown
 *     （标题/段落/表格/围栏代码块/data-URI 图像；纯 DOM 构造，不用 innerHTML）。
 *
 * 章节模板与 coscreen/manuscript_builder.MANUSCRIPT_SECTIONS（规格 §4.2 逐字）
 * 对齐：标题为稿件内的实际节名（数据，不翻译）；manual=true 的节生成时仅占位。
 *
 * 事件接入（追加式）：reviewflow:analysis-core-loaded（用分析页当前模型预填，
 * 用户已手改则不打扰）、reviewflow:analysis-task-changed（复位）、
 * reviewflow:language-changed（重填静态文案）。
 *
 * 单点故障：初始化与全部回调自行捕获异常；网络仅经全局 api() 访问本应用 API。
 */
(function () {
  'use strict';
  try {
    if (typeof document === 'undefined' || typeof T !== 'function' || typeof el !== 'function') return;

    const anchor = document.getElementById('anJumpQual');
    const workspace = document.querySelector('.page[data-p="analysis"] .an-workspace');
    if (!anchor || !workspace) return;   // 结构不符：安静退出

    /* ---- 样式（随模块注入） ---- */
    const style = document.createElement('style');
    style.textContent = [
      '.rf-manuscript{margin-top:12px}',
      '.rf-manuscript h3{margin:0 0 4px;font-size:.95rem}',
      '.rf-ms-controls{display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end;margin-top:10px}',
      '.rf-ms-controls label{display:flex;flex-direction:column;gap:3px;font-size:.75rem}',
      '.rf-ms-controls .input{font-size:.8rem;padding:4px 8px;min-width:150px}',
      '.rf-ms-sections{display:grid;grid-template-columns:repeat(auto-fill,minmax(215px,1fr));gap:4px 12px;margin-top:8px;border:1px solid var(--border);border-radius:10px;padding:8px 10px;background:var(--bg)}',
      '.rf-ms-sections label{display:flex;gap:6px;align-items:baseline;font-size:.78rem;margin:0}',
      '.rf-ms-manual{color:var(--danger);font-size:.7rem;white-space:nowrap}',
      '.rf-ms-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;align-items:center}',
      '.rf-ms-status{font-size:.78rem;margin:6px 0 0;white-space:pre-wrap}',
      '.rf-ms-preview{margin-top:10px;border:1px solid var(--border);border-radius:12px;padding:12px 14px;max-height:520px;overflow:auto;background:var(--bg)}',
      '.rf-ms-preview h1{font-size:1.15rem;margin:0 0 8px}',
      '.rf-ms-preview h2{font-size:1rem;margin:14px 0 6px;border-bottom:1px solid var(--border);padding-bottom:3px}',
      '.rf-ms-preview h3{font-size:.9rem;margin:10px 0 4px}',
      '.rf-ms-preview p{font-size:.82rem;margin:6px 0;line-height:1.55}',
      '.rf-ms-preview pre{font-size:.76rem;background:var(--primary-light);border-radius:8px;padding:8px 10px;overflow-x:auto;margin:8px 0}',
      '.rf-ms-preview table{border-collapse:collapse;font-size:.76rem;margin:8px 0;min-width:60%}',
      '.rf-ms-preview th,.rf-ms-preview td{border:1px solid var(--border);padding:4px 8px;text-align:left;vertical-align:top}',
      '.rf-ms-preview th{background:var(--bg);font-weight:600}',
      '.rf-ms-preview img{max-width:100%;height:auto;margin:8px 0;border:1px solid var(--border);border-radius:6px}',
      '.rf-ms-preview hr{border:none;border-top:1px solid var(--border);margin:12px 0}',
    ].join('\n');
    (document.head || document.documentElement).appendChild(style);

    /* ---- 常量：与 MANUSCRIPT_SECTIONS（规格 §4.2 逐字）对齐 ----
       [key, 稿件内节名（数据，不翻译）, manual（source=MANUALLY_WRITTEN：仅留占位）] */
    const SECTIONS = [
      ['title', 'Title', false],
      ['abstract', 'Abstract', false],
      ['introduction', 'Introduction', false],
      ['methods_search', 'Methods: Search Strategy', false],
      ['methods_screening', 'Methods: Study Selection', false],
      ['methods_data', 'Methods: Data Extraction', false],
      ['methods_risk', 'Methods: Risk of Bias', false],
      ['methods_synthesis', 'Methods: Synthesis', false],
      ['results_flow', 'Results: Study Flow', false],
      ['results_characteristics', 'Results: Study Characteristics', false],
      ['results_rob', 'Results: Risk of Bias', false],
      ['results_synthesis', 'Results: Synthesis', false],
      ['results_grade', 'Results: GRADE', false],
      ['results_heterogeneity', 'Results: Heterogeneity', false],
      ['results_publication_bias', 'Results: Publication Bias', false],
      ['discussion', 'Discussion', true],
      ['limitations', 'Limitations', true],
      ['conclusions', 'Conclusions', true],
      ['references', 'References', false],
    ];
    const FORMATS = [
      ['docx', 'Word（.docx）'], ['latex', 'LaTeX（.tex）'], ['markdown', 'Markdown（.md）'],
    ];
    const FORMAT_EXT = { docx: 'docx', latex: 'tex', markdown: 'md' };
    const REF_STYLES = [['ama', 'AMA'], ['vancouver', 'Vancouver'], ['apa', 'APA']];
    const MODELS = [
      ['', '（未选择，将采用默认：随机效应 · DL）'],
      ['fixed', '固定效应 · 共同效应'], ['random', '随机效应 · DL'],
      ['random_pm', '随机效应 · PM'], ['random_reml', '随机效应 · REML'],
    ];
    const CI_METHODS = [['', '（未选择，将采用默认：正态 / Wald）'], ['normal', '正态 / Wald · 95%'], ['hksj', '修正 HKSJ · 95%（随机效应）']];

    function safe(fn) { return function () { try { fn.apply(this, arguments); } catch (e) { /* 单点故障 */ } }; }

    /* ---- 状态 ---- */
    let version = 0;          // 异步票据
    let modelTouched = false; // 用户改过模型/CI 后，预填不再覆盖
    let previewData = null;   // 最近一次预览（语言切换时重渲染标题区）

    /* ---- 面板骨架 ---- */
    const heading = el('h3', { text: T('撰写稿件') });
    const hint = el('p', { class: 'hint', style: 'margin:2px 0 8px', text: T('根据本任务中的方案、效应量、偏倚评估、GRADE 与筛选计数，按 PRISMA 2020 结构生成综述稿件框架（规则生成，非 AI 撰写）。Discussion、Limitations 和 Conclusions 三节需由作者撰写。') });

    const sectionsBox = el('div', { class: 'rf-ms-sections', role: 'group', 'aria-label': T('稿件章节') });
    const sectionChecks = [];
    for (const [key, title, manual] of SECTIONS) {
      const check = el('input', { type: 'checkbox', value: key, checked: '' });
      check.checked = true;   // 默认全选
      sectionChecks.push(check);
      sectionsBox.append(el('label', {},
        check,
        el('span', { translate: 'no', text: title }),
        manual ? el('span', { class: 'rf-ms-manual', text: T('需手动撰写') }) : null));
    }
    const allBtn = el('button', { class: 'btn b-out', type: 'button', style: 'padding:3px 10px;font-size:.75rem', text: T('全不选') });
    allBtn.addEventListener('click', safe(() => {
      const target = !sectionChecks.every(c => c.checked);
      for (const c of sectionChecks) c.checked = target;
      allBtn.textContent = T(target ? '全不选' : '全选');
    }));

    const formatSel = el('select', { class: 'input', 'aria-label': T('稿件格式') },
      FORMATS.map(([v, label]) => el('option', { value: v, text: T(label) })));
    const refSel = el('select', { class: 'input', 'aria-label': T('参考文献格式') },
      REF_STYLES.map(([v, label]) => el('option', { value: v, text: T(label) })));
    const modelSel = el('select', { class: 'input', 'aria-label': T('合并模型（稿件重算合成用）') },
      MODELS.map(([v, label]) => el('option', { value: v, text: T(label) })));
    const ciSel = el('select', { class: 'input', 'aria-label': T('置信区间方法（稿件重算合成用）') },
      CI_METHODS.map(([v, label]) => el('option', { value: v, text: T(label) })));
    const figCheck = el('input', { type: 'checkbox', checked: '' });
    figCheck.checked = true;

    const controls = el('div', { class: 'rf-ms-controls' },
      el('label', {}, el('span', { text: T('格式') }), formatSel),
      el('label', {}, el('span', { text: T('参考文献格式') }), refSel),
      el('label', {}, el('span', { text: T('合并模型') }), modelSel),
      el('label', {}, el('span', { text: T('置信区间方法') }), ciSel),
      el('label', { style: 'flex-direction:row;align-items:center;gap:6px' }, figCheck, el('span', { text: T('嵌入图表（森林图 / PRISMA 流程图）') })));

    const genBtn = el('button', { class: 'btn b-inc', type: 'button', text: T('生成并下载') });
    const previewBtn = el('button', { class: 'btn b-out', type: 'button', text: T('Markdown 预览') });
    const statusLine = el('p', { class: 'rf-ms-status hint', role: 'status' });
    const previewCap = el('p', { class: 'hint', style: 'margin:0 0 6px' });
    const previewBox = el('div', { class: 'rf-ms-preview', hidden: '' });
    const panel = el('section', { class: 'card rf-manuscript', id: 'rfManuscriptPanel', 'aria-label': T('撰写稿件') },
      heading, hint,
      el('div', { style: 'display:flex;gap:8px;align-items:center;flex-wrap:wrap' },
        el('b', { style: 'font-size:.82rem', text: T('稿件章节') }), allBtn),
      sectionsBox, controls,
      el('div', { class: 'rf-ms-actions' }, genBtn, previewBtn),
      statusLine, previewCap, previewBox);
    anchor.after(panel);

    /* ---- 请求参数收集 ---- */
    function selectedSections() {
      const keys = sectionChecks.filter(c => c.checked).map(c => c.value);
      return keys.length === SECTIONS.length ? ['all'] : keys;
    }
    function currentParams() {
      return {
        format: formatSel.value || 'docx',
        reference_style: refSel.value || 'ama',
        model: modelSel.value || 'random',
        ci_method: ciSel.value || 'normal',
      };
    }
    function guardTask() {
      if (typeof S !== 'undefined' && S && S.task && S.task.task_id) return true;
      statusLine.textContent = T('请先选择任务。');
      if (typeof toast === 'function') toast(T('请先选择任务'), 'warn');
      return false;
    }

    /* ---- [生成并下载]：POST generate → 文件流下载 ---- */
    genBtn.addEventListener('click', safe(generate));
    async function generate() {
      let ticket;
      try {
        if (typeof api !== 'function' || typeof taskApi !== 'function') return;
        if (!guardTask()) return;
        const keys = selectedSections();
        if (!keys.length) {
          statusLine.textContent = T('请至少勾选一个章节。');
          return;
        }
        const params = currentParams();
        const body = {
          format: params.format, sections: keys, include_figures: figCheck.checked,
          reference_style: params.reference_style, model: params.model, ci_method: params.ci_method,
        };
        ticket = ++version;
        genBtn.disabled = true;
        genBtn.dataset.requestTicket = String(ticket);
        statusLine.textContent = T('正在生成稿件…');
        try {
          const blob = await api(taskApi() + '/analysis/manuscript/generate', { method: 'POST', body, blob: true });
          if (ticket !== version) return;
          const name = S.task.task_id + '-manuscript.' + FORMAT_EXT[params.format];
          const a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = name;
          document.body.appendChild(a); a.click(); a.remove();
          setTimeout(() => URL.revokeObjectURL(a.href), 3000);
          statusLine.textContent = T('✓ 已生成并下载：') + name;
          if (typeof toast === 'function') toast(T('✓ 已下载：') + name, 'inc');
        } finally {
          if (genBtn.dataset.requestTicket === String(ticket)) genBtn.disabled = false;
        }
      } catch (e) {
        if (ticket !== undefined && ticket !== version) return;
        statusLine.textContent = T('生成失败：') + ((e && e.message) || '');
        if (typeof toast === 'function') toast(T('生成失败：') + ((e && e.message) || ''), 'warn');
      }
    }

    /* ---- [Markdown 预览]：GET preview → 前端渲染 ---- */
    previewBtn.addEventListener('click', safe(preview));
    async function preview() {
      let ticket;
      try {
        if (typeof api !== 'function' || typeof taskApi !== 'function') return;
        if (!guardTask()) return;
        const params = currentParams();
        const q = '?reference_style=' + encodeURIComponent(params.reference_style) +
          '&model=' + encodeURIComponent(params.model) +
          '&ci_method=' + encodeURIComponent(params.ci_method);
        ticket = ++version;
        previewBtn.disabled = true;
        previewBtn.dataset.requestTicket = String(ticket);
        statusLine.textContent = T('正在生成预览…');
        try {
          const d = await api(taskApi() + '/analysis/manuscript/preview' + q);
          if (ticket !== version) return;
          previewData = d;
          renderPreview(d);
          statusLine.textContent = T('✓ 预览已生成（全部章节；含图内联为 SVG）。');
        } finally {
          if (previewBtn.dataset.requestTicket === String(ticket)) previewBtn.disabled = false;
        }
      } catch (e) {
        if (ticket !== undefined && ticket !== version) return;
        previewBox.hidden = true;
        statusLine.textContent = T('预览失败：') + ((e && e.message) || '');
      }
    }

    function renderPreview(d) {
      previewBox.replaceChildren();
      const markdown = (d && typeof d.markdown === 'string') ? d.markdown : '';
      for (const node of markdownNodes(markdown)) previewBox.append(node);
      const meta = (d && d.meta) || {};
      const manual = ((d && d.sections) || []).filter(s => s && s.placeholder).map(s => s.title);
      const capParts = [T('预览为全部章节的 Markdown；下载文件按上方勾选与格式生成。')];
      const software = meta.software ? meta.software + (meta.version ? ' ' + meta.version : '') : '';
      if (software) capParts.push(T('生成器：{0}', String(software)));
      if (meta.generated_at) capParts.push(T('生成时间：{0}', String(meta.generated_at)));
      if (manual.length) capParts.push(T('需手动撰写：{0}', manual.join(' / ')));
      previewCap.textContent = capParts.join('　');
      previewBox.hidden = false;
    }

    /* ---- 极简 Markdown 渲染（纯 DOM 构造，不用 innerHTML） ----
       覆盖 render_markdown 的全部产物：一至三级标题、段落、围栏代码块、
       管道表、内联 data-URI 图像、水平分隔线、行内加粗与斜体。 */
    function markdownNodes(markdown) {
      const nodes = [];
      const lines = String(markdown).split(/\r?\n/);
      let i = 0;
      while (i < lines.length) {
        const line = lines[i];
        if (!line.trim()) { i++; continue; }
        if (line.startsWith('```')) {   // 围栏代码块
          const buf = [];
          i++;
          while (i < lines.length && !lines[i].startsWith('```')) { buf.push(lines[i]); i++; }
          i++;   // 跳过收尾 ```
          nodes.push(el('pre', { translate: 'no', text: buf.join('\n') }));
          continue;
        }
        if (/^\|/.test(line)) {          // 管道表（含 |---| 分隔行）
          const rows = [];
          while (i < lines.length && /^\|/.test(lines[i])) { rows.push(lines[i]); i++; }
          nodes.push(tableNode(rows));
          continue;
        }
        if (/^#{1,6}\s/.test(line)) {
          const m = line.match(/^(#{1,6})\s+(.*)$/);
          const tag = 'h' + Math.min(4, m[1].length);
          nodes.push(el(tag, {}, inlineNodes(m[2])));
          i++; continue;
        }
        if (/^---+$/.test(line.trim())) { nodes.push(el('hr')); i++; continue; }
        const img = imgNode(line);       // 单行 <img .../>（data-URI SVG 图）
        if (img) { nodes.push(img); i++; continue; }
        nodes.push(el('p', {}, inlineNodes(line)));   // 普通段落
        i++;
      }
      return nodes;
    }

    function tableNode(rows) {
      const trs = [];
      for (const row of rows) {
        if (/^\|\s*:?-{2,}/.test(row)) continue;   // 分隔行
        /* \| 是转义竖线：先占位再切分（不用后行断言，兼容旧浏览器） */
        const guarded = row.replace(/^\|/, '').replace(/\|$/, '').replace(/\\\|/g, '\u0000');
        trs.push(guarded.split('|').map(c => c.replace(/\u0000/g, '|').trim()));
      }
      if (!trs.length) return el('p', { class: 'hint', text: '' });
      const head = trs.shift();
      return el('table', { translate: 'no' },
        el('thead', {}, el('tr', {}, head.map(c => el('th', { text: c })))),
        el('tbody', {}, trs.map(r => el('tr', {}, r.map(c => el('td', { text: c }))))));
    }

    function imgNode(line) {
      const m = String(line).trim().match(/^<img\s+([^>]*?)\s*\/?>$/);
      if (!m) return null;
      const attrs = {};
      for (const [, k, v] of m[1].matchAll(/(\w+)="([^"]*)"/g)) attrs[k.toLowerCase()] = v;
      const src = attrs.src || '';
      if (!src.startsWith('data:image/')) return null;   // 只认 data-URI 图像，不加载外部资源
      const img = el('img', { alt: attrs.alt || '', src });
      if (attrs.width && /^\d+$/.test(attrs.width)) img.setAttribute('width', attrs.width);
      return img;
    }

    function inlineNodes(text) {
      const parts = String(text).split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g);
      return parts.filter(Boolean).map(part => {
        if (/^\*\*[^*]+\*\*$/.test(part)) return el('strong', { text: part.slice(2, -2) });
        if (/^\*[^*]+\*$/.test(part)) return el('em', { text: part.slice(1, -1) });
        return document.createTextNode(part);
      });
    }

    /* ---- 模型预填：跟随分析页当前选择（用户改过则不打扰） ---- */
    function syncModelFromAnalysis() {
      try {
        if (modelTouched || typeof anValue !== 'function') return;
        const model = anValue('anModel');
        const ci = anValue('anCiMethod');
        if (['fixed', 'random', 'random_pm', 'random_reml'].includes(model)) modelSel.value = model;
        if (['normal', 'hksj'].includes(ci)) ciSel.value = ci;
      } catch (e) { /* 单点故障 */ }
    }
    modelSel.addEventListener('change', safe(() => { modelTouched = true; }));
    ciSel.addEventListener('change', safe(() => { modelTouched = true; }));

    /* ---- 语言切换：重填静态文案（预览按缓存重渲染标题区） ---- */
    function refreshLang() {
      try {
        heading.textContent = T('撰写稿件');
        hint.textContent = T('根据本任务中的方案、效应量、偏倚评估、GRADE 与筛选计数，按 PRISMA 2020 结构生成综述稿件框架（规则生成，非 AI 撰写）。Discussion、Limitations 和 Conclusions 三节需由作者撰写。');
        allBtn.textContent = T(sectionChecks.every(c => c.checked) ? '全不选' : '全选');
        /* 先记当前值再重建选项（replaceChildren 会清掉选中态） */
        const keep = { format: formatSel.value, ref: refSel.value, model: modelSel.value, ci: ciSel.value };
        formatSel.replaceChildren(...FORMATS.map(([v, label]) => el('option', { value: v, text: T(label) })));
        refSel.replaceChildren(...REF_STYLES.map(([v, label]) => el('option', { value: v, text: T(label) })));
        modelSel.replaceChildren(...MODELS.map(([v, label]) => el('option', { value: v, text: T(label) })));
        ciSel.replaceChildren(...CI_METHODS.map(([v, label]) => el('option', { value: v, text: T(label) })));
        formatSel.value = keep.format || 'docx';
        refSel.value = keep.ref || 'ama';
        if (!modelTouched) syncModelFromAnalysis();
        else { modelSel.value = keep.model || ''; ciSel.value = keep.ci || ''; }
        genBtn.textContent = T('生成并下载');
        previewBtn.textContent = T('Markdown 预览');
        sectionsBox.setAttribute('aria-label', T('稿件章节'));
        panel.setAttribute('aria-label', T('撰写稿件'));
        for (const node of sectionsBox.querySelectorAll('.rf-ms-manual')) node.textContent = T('需手动撰写');
        if (previewData) renderPreview(previewData);
      } catch (e) { /* 单点故障 */ }
    }

    /* ---- 事件接入 ---- */
    document.addEventListener('reviewflow:analysis-loaded', safe(syncModelFromAnalysis));
    document.addEventListener('reviewflow:analysis-core-loaded', safe(syncModelFromAnalysis));
    document.addEventListener('reviewflow:analysis-task-changed', safe(() => {
      version++;
      previewData = null;
      previewBox.hidden = true;
      previewCap.textContent = '';
      statusLine.textContent = '';
      genBtn.disabled = false;
      previewBtn.disabled = false;
      modelTouched = false;
      syncModelFromAnalysis();
    }));
    document.addEventListener('reviewflow:language-changed', safe(refreshLang));

    syncModelFromAnalysis();
  } catch (e) { /* 单点故障：模块初始化失败不影响主应用与 /api/health */ }
})();
