/* ReviewFlow · 证据地图面板（衍生产品规格 2026-09-30 §6.4；API=W8 Batch 段）。
 *
 * 分析页追加「证据地图」面板（挂在 .an-workspace 内 #rfLivingReviewPanel 之后，
 * 兜底 #rfManuscriptPanel / #anJumpQual，与 GRADE/撰写稿件/活体综述同一追加式接入）：
 *   - X/Y 维度选择器（年份 / 期刊 / 研究设计 / 人群 / 主题簇，x≠y 前端先校验）
 *     + 主题聚类数（1–64，默认 8，与后端约束一致）；
 *   - [生成地图]：GET /api/tasks/{id}/evidence-map?x=&y=&clusters= → SVG 热力图
 *     （行 = x_labels、列 = y_labels、matrix[i][j] 为该组合文献数；颜色深浅 =
 *     数量多少）；计数 0 的单元格以虚线红框标注「研究缺口」；
 *   - 点击单元格 → 弹出该组合下的文献列表：年份/期刊/研究设计/人群四维按与
 *     coscreen/evidence_mapper.py 完全相同的推导规则（正则精确优先、期刊前 20
 *     长尾并入 other、年份 1800–2099、缺失归 unknown）在本地对已加载文献
 *     （anArticles，与端点同一筛选员库、同样不含重复条目）复筛；主题簇维度由
 *     服务端 TF-IDF+KMeans 分配、前端无法逐篇复现，此时按另一维度筛选并如实说明；
 *   - 主题簇列表：每簇前 5 个代表词 + 研究数量；
 *   - 研究缺口汇总：空白单元格计数与组合明细（前若干条）。
 *
 * 维度取值是语言中立的稳定代码（后端文档约定），显示名经 T() 映射；
 * 期刊名/年份/代表词为题录数据，原样显示不翻译。
 *
 * 事件接入（追加式）：reviewflow:analysis-loaded（文献就绪后可点格）、
 * reviewflow:analysis-task-changed（复位）、reviewflow:language-changed（重渲染）。
 *
 * 单点故障：初始化与全部回调自行捕获异常；网络仅经全局 api() 访问本应用 API。
 */
(function () {
  'use strict';
  try {
    if (typeof document === 'undefined' || typeof T !== 'function' || typeof el !== 'function') return;

    const anchor = document.getElementById('rfLivingReviewPanel') ||
      document.getElementById('rfManuscriptPanel') || document.getElementById('anJumpQual');
    const workspace = document.querySelector('.page[data-p="analysis"] .an-workspace');
    if (!anchor || !workspace) return;   // 结构不符：安静退出

    /* ---- 样式（随模块注入） ---- */
    const style = document.createElement('style');
    style.textContent = [
      '.rf-em{margin-top:12px}',
      '.rf-em h3{margin:0 0 4px;font-size:.95rem}',
      '.rf-em-controls{display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end;margin-top:8px}',
      '.rf-em-controls label{display:flex;flex-direction:column;gap:3px;font-size:.75rem}',
      '.rf-em-controls .input{font-size:.8rem;padding:4px 8px;min-width:140px}',
      '.rf-em-status{font-size:.78rem;margin:6px 0 0;white-space:pre-wrap}',
      '.rf-em-heat{margin-top:10px;overflow:auto;border:1px solid var(--border);border-radius:12px;background:var(--bg);max-height:560px}',
      '.rf-em-heat svg{display:block}',
      '.rf-em-heat svg text{font-size:11px;fill:var(--ink)}',
      '.rf-em-heat svg .gapcell{fill:none;stroke:var(--danger);stroke-dasharray:3 2}',
      '.rf-em-heat svg .countcell{cursor:pointer}',
      '.rf-em-heat svg .countcell:hover{stroke:var(--primary-dark);stroke-width:2}',
      '.rf-em-clusters{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}',
      '.rf-em-cluster{border:1px solid var(--border);border-radius:10px;padding:6px 10px;background:var(--bg);font-size:.75rem;max-width:100%}',
      '.rf-em-cluster .terms{color:var(--muted);margin-top:2px;word-break:break-word}',
      '.rf-em-gaps{margin-top:8px;font-size:.78rem}',
      '.rf-em-gaps .chip{font-size:.7rem}',
      '.rf-em-pop{position:fixed;inset:0;width:100%;height:100%;max-width:none;max-height:none;margin:0;padding:0;border:0;display:flex;align-items:center;justify-content:center;background:transparent}',
      '.rf-em-pop::backdrop{background:rgba(0,0,0,.35)}',
      '.rf-em-pop .box{background:var(--bg);color:var(--ink);border:1px solid var(--border);border-radius:12px;max-width:min(680px,92vw);max-height:80vh;display:flex;flex-direction:column;padding:12px 16px;box-shadow:0 10px 30px rgba(0,0,0,.25)}',
      '.rf-em-pop .box header{display:flex;gap:8px;align-items:center;border-bottom:1px solid var(--border);padding-bottom:8px;margin-bottom:8px}',
      '.rf-em-pop .list{overflow:auto;min-height:0}',
      '.rf-em-pop .item{border-bottom:1px dashed var(--border);padding:6px 0;font-size:.78rem}',
      '.rf-em-pop .item small{color:var(--muted);display:block;margin-top:2px;word-break:break-all}',
    ].join('\n');
    (document.head || document.documentElement).appendChild(style);

    /* ---- 常量（与后端 DIMENSIONS / 规则一致） ---- */
    const DIMENSIONS = [
      ['year', '年份'], ['journal', '期刊'], ['study_design', '研究设计'],
      ['population', '人群'], ['cluster', '主题簇'],
    ];
    /* 维度取值（稳定代码）→ 显示名：题录类（期刊名/年份/cluster N）原样显示 */
    const VALUE_LABELS = {
      study_design: {
        meta_analysis: 'Meta 分析', systematic_review: '系统综述', rct: '随机对照试验',
        cohort: '队列研究', case_control: '病例对照', cross_sectional: '横断面研究',
        diagnostic: '诊断准确性', other: '其他',
      },
      population: {
        children: '儿童', older_adults: '老年人群', animals: '动物',
        patients: '患者', adults: '成人', other: '其他',
      },
      year: { unknown: '未知年份' },
      journal: { unknown: '未知期刊', other: '其他期刊' },
    };
    const MAX_JOURNAL_LABELS = 20;   // 与后端 MAX_CATEGORY_LABELS 一致

    /* 关键词规则（精确优先、先命中先归类）——逐条镜像
       coscreen/evidence_mapper.py 的 _STUDY_DESIGN_RULES / _POPULATION_RULES */
    const STUDY_RULES = [
      ['meta_analysis', [/meta-?analys/i]],
      ['systematic_review', [/systematic\s+review/i]],
      ['rct', [/randomi[sz]ed\s+(controlled\s+)?trial/i, /randomly\s+(assigned|allocated)/i]],
      ['cohort', [/\bcohorts?\b/i]],
      ['case_control', [/case-?\s?control/i]],
      ['cross_sectional', [/cross-?\s?sectional/i]],
      ['diagnostic', [/diagnostic\s+accuracy/i, /sensitivity\s+and\s+specificity/i]],
    ];
    const POPULATION_RULES = [
      ['children', [/\b(child(ren|hood)?|infants?|adolescen\w*|p[ae]diatric\w*|neonat\w*|boys|girls)\b/i]],
      ['older_adults', [/\b(older\s+adults?|elderly|geriatric\w*|nursing\s+home|postmenopausal)\b/i]],
      ['animals', [/\b(mice|mouse|murine|rats?|rabbits?|guinea\s+pigs?|dogs?|cats?|swine|pigs?|piglets?|sheep|goats?|hamsters?|zebrafish|cattle|cows?|horses?|monkeys|primates|in\s+vivo|animals?)\b/i]],
      ['patients', [/\bpatients?\b/i]],
      ['adults', [/\badults?\b/i]],
    ];
    const YEAR_RE = /\b(1[89]\d{2}|20\d{2})\b/;

    function safe(fn) { return function () { try { fn.apply(this, arguments); } catch (e) { /* 单点故障 */ } }; }

    /* ---- 本地维度推导（与后端同一规则；仅用于点击单元格后的文献列表） ---- */
    function ruleLabel(text, rules) {
      for (const [label, patterns] of rules) {
        for (const re of patterns) if (re.test(text)) return label;
      }
      return 'other';
    }
    function dimensionValue(article, dim, journalKept) {
      article = article || {};
      if (dim === 'cluster') return null;   // 服务端 KMeans 分配，前端不可复现
      if (dim === 'year') {
        if (article.year === null || article.year === undefined) return 'unknown';
        const m = YEAR_RE.exec(String(article.year));
        return m ? m[1] : 'unknown';
      }
      if (dim === 'journal') {
        const journal = String(article.journal || '').split(/\s+/).filter(Boolean).join(' ') || 'unknown';
        if (journalKept) return journalKept.has(journal) ? journal : 'other';
        return journal;
      }
      const text = String(article.title || '') + ' ' + String(article.abstract || '');
      return dim === 'study_design' ? ruleLabel(text, STUDY_RULES) : ruleLabel(text, POPULATION_RULES);
    }
    /* journal 维度前 20 标签集合（按文献数降序、并列按名称升序）——镜像 _journal_labels */
    function journalKeptSet(articles) {
      const counts = new Map();
      for (const a of articles) {
        const j = String((a || {}).journal || '').split(/\s+/).filter(Boolean).join(' ') || 'unknown';
        counts.set(j, (counts.get(j) || 0) + 1);
      }
      const ranked = Array.from(counts.keys()).sort((x, y) =>
        (counts.get(y) - counts.get(x)) || (x < y ? -1 : x > y ? 1 : 0));
      return new Set(ranked.length <= MAX_JOURNAL_LABELS ? ranked : ranked.slice(0, MAX_JOURNAL_LABELS));
    }

    /* 取值代码 → 显示文本 */
    function dimLabel(dim) {
      return T((DIMENSIONS.find(d => d[0] === dim) || [, dim])[1]);
    }
    function valueLabel(dim, code) {
      const mapped = (VALUE_LABELS[dim] || {})[code];
      if (mapped) return T(mapped);
      if (dim === 'cluster') return T('主题簇 {0}', String(code).replace(/^cluster\s+/i, ''));
      return code;   // 年份数字 / 期刊名：题录数据，原样
    }

    /* ---- 状态 ---- */
    let version = 0;      // 异步票据
    let mapData = null;   // 最近一次 /evidence-map 响应
    let mapDims = { x: '', y: '' };
    let popover = null;   // 当前弹层

    /* ---- 面板骨架 ---- */
    const heading = el('h3', { text: T('证据地图') });
    const hint = el('p', { class: 'hint', style: 'margin:2px 0 8px', text: T('按已导入文献生成分布地图，点击单元格查看文献。空白格只表示当前文献库没有记录，不代表该领域没有研究。主题聚类在本地计算。') });

    const xSel = el('select', { class: 'input', 'aria-label': T('X 轴维度') },
      DIMENSIONS.map(([v, label]) => el('option', { value: v, text: T(label) })));
    const ySel = el('select', { class: 'input', 'aria-label': T('Y 轴维度') },
      DIMENSIONS.map(([v, label]) => el('option', { value: v, text: T(label) })));
    xSel.value = 'cluster'; ySel.value = 'study_design';
    const clusterInput = el('input', { class: 'input', type: 'number', min: '1', max: '64', value: '8', style: 'max-width:110px', 'aria-label': T('主题聚类数') });
    const runBtn = el('button', { class: 'btn b-inc', type: 'button', text: T('生成地图') });
    const statusLine = el('p', { class: 'rf-em-status hint', role: 'status' });
    const heatBox = el('div', { class: 'rf-em-heat', hidden: '' });
    const clustersCap = el('b', { style: 'font-size:.82rem;margin-top:10px;display:none', text: T('主题簇') });
    const clustersBox = el('div', { class: 'rf-em-clusters' });
    const gapsBox = el('div', { class: 'rf-em-gaps' });
    const panel = el('section', { class: 'card rf-em', id: 'rfEvidenceMapPanel', 'aria-label': T('证据地图') },
      heading, hint,
      el('div', { class: 'rf-em-controls' },
        el('label', {}, el('span', { text: T('X 轴维度') }), xSel),
        el('label', {}, el('span', { text: T('Y 轴维度') }), ySel),
        el('label', {}, el('span', { text: T('主题聚类数') }), clusterInput),
        el('label', {}, el('span', { text: ' ' }), runBtn)),
      statusLine, heatBox, clustersCap, clustersBox, gapsBox);
    anchor.after(panel);

    function guardTask() {
      if (typeof S !== 'undefined' && S && S.task && S.task.task_id) return true;
      statusLine.textContent = T('请先选择任务。');
      if (typeof toast === 'function') toast(T('请先选择任务'), 'warn');
      return false;
    }

    /* ---- [生成地图] ---- */
    runBtn.addEventListener('click', safe(runMap));
    async function runMap() {
      let ticket;
      try {
        if (typeof api !== 'function' || typeof taskApi !== 'function') return;
        if (!guardTask()) return;
        const x = xSel.value, y = ySel.value;
        if (x === y) { statusLine.textContent = T('X 轴与 Y 轴维度不能相同。'); return; }
        const clusters = Math.max(1, Math.min(64, Math.round(Number(clusterInput.value) || 8)));
        clusterInput.value = String(clusters);
        ticket = ++version;
        runBtn.disabled = true;
        runBtn.dataset.requestTicket = String(ticket);
        statusLine.textContent = T('正在生成证据地图…（TF-IDF 聚类 + 交叉计数）');
        try {
          const d = await api(taskApi() + '/evidence-map?x=' + encodeURIComponent(x) +
            '&y=' + encodeURIComponent(y) + '&clusters=' + clusters);
          if (ticket !== version) return;
          mapData = d; mapDims = { x, y };
          render();
          const total = ((d && d.clusters) || []).reduce((sum, c) => sum + (c.count || 0), 0);
          statusLine.textContent = T('✓ 已生成：{0} 篇文献 · {1} × {2}。点击单元格查看该组合下的文献。',
            String(total), dimLabel(x), dimLabel(y));
        } finally {
          if (runBtn.dataset.requestTicket === String(ticket)) runBtn.disabled = false;
        }
      } catch (e) {
        if (ticket !== undefined && ticket !== version) return;
        statusLine.textContent = T('生成失败：') + ((e && e.message) || '');
        if (typeof toast === 'function') toast(T('生成失败：') + ((e && e.message) || ''), 'warn');
      }
    }

    /* ---- 渲染（热力图 + 主题簇 + 缺口） ---- */
    function render() {
      renderHeat();
      renderClusters();
      renderGaps();
    }

    function renderHeat() {
      heatBox.replaceChildren();
      const d = mapData;
      if (!d || !Array.isArray(d.matrix) || !d.matrix.length) {
        heatBox.hidden = false;
        heatBox.append(el('p', { class: 'hint', style: 'margin:8px', text: T('当前库没有可聚类的文献（或全部文本为空）。') }));
        return;
      }
      const xLabels = d.x_labels || [], yLabels = d.y_labels || [];
      const rows = xLabels.length, cols = yLabels.length;
      const cellW = cols > 14 ? 26 : 36, cellH = rows > 14 ? 24 : 32;
      const leftW = 150, topH = 92, pad = 10;
      const svgW = leftW + cols * cellW + pad;
      const svgH = topH + rows * cellH + pad + 26;
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('viewBox', '0 0 ' + svgW + ' ' + svgH);
      svg.setAttribute('width', String(svgW));
      svg.setAttribute('height', String(svgH));
      svg.setAttribute('role', 'img');
      svg.setAttribute('aria-label', T('证据地图热力图：{0} × {1}', dimLabel(mapDims.x), dimLabel(mapDims.y)));

      const max = Math.max(1, ...d.matrix.flat().filter(v => Number.isFinite(v)));
      const mk = (tag, attrs, text) => {
        const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
        for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, String(v));
        if (text !== undefined) node.textContent = String(text);
        svg.appendChild(node);
        return node;
      };

      /* 列标签（Y 轴，顶部斜排） */
      yLabels.forEach((label, j) => {
        const t = mk('text', {
          x: leftW + j * cellW + cellW / 2, y: topH - 6,
          transform: 'rotate(-40 ' + (leftW + j * cellW + cellW / 2) + ' ' + (topH - 6) + ')',
          'text-anchor': 'end', style: 'font-size:10px',
        }, shorten(valueLabel(mapDims.y, label)));
        const full = valueLabel(mapDims.y, label);
        if (full.length > shorten(full).length) { const tt = mk('title', {}, ''); tt.textContent = full; t.appendChild(tt); }
      });
      /* 行标签（X 轴，左侧） */
      xLabels.forEach((label, i) => {
        const full = valueLabel(mapDims.x, label);
        const t = mk('text', { x: leftW - 6, y: topH + i * cellH + cellH / 2 + 4, 'text-anchor': 'end' }, shorten(full, 20));
        t.setAttribute('style', 'font-size:10px');
        if (full.length > shorten(full, 20).length) { const tt = mk('title', {}, ''); tt.textContent = full; t.appendChild(tt); }
      });

      /* 单元格 */
      for (let i = 0; i < rows; i++) {
        for (let j = 0; j < cols; j++) {
          const count = Number(d.matrix[i][j]) || 0;
          const x = leftW + j * cellW, y = topH + i * cellH;
          const rect = mk('rect', {
            x, y, width: cellW - 2, height: cellH - 2, rx: 3,
            class: count > 0 ? 'countcell' : 'gapcell',
          });
          if (count > 0) {
            rect.setAttribute('fill', 'var(--primary)');
            rect.setAttribute('fill-opacity', String(0.18 + 0.82 * (count / max)));
            rect.setAttribute('stroke', 'var(--border)');
            rect.setAttribute('tabindex', '0');
            rect.setAttribute('role', 'button');
          }
          const tip = mk('title', {}, '');
          tip.textContent = valueLabel(mapDims.x, xLabels[i]) + ' × ' + valueLabel(mapDims.y, yLabels[j]) +
            '：' + (count > 0 ? T('{0} 篇文献', String(count)) : T('研究缺口：无研究覆盖'));
          rect.appendChild(tip);
          if (count > 0) {
            const label = mk('text', {
              x: x + (cellW - 2) / 2, y: y + (cellH - 2) / 2 + 4, 'text-anchor': 'middle',
            }, String(count));
            label.setAttribute('style', 'font-size:11px;fill:' + (count / max > 0.45 ? '#fff' : 'var(--ink)'));
            rect.addEventListener('click', safe(() => openCell(i, j)));
            rect.addEventListener('keydown', safe(ev => {
              if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openCell(i, j); }
            }));
          } else {
            rect.addEventListener('click', safe(() => openCell(i, j)));
          }
        }
      }

      /* 图例 */
      const legendY = topH + rows * cellH + 14;
      mk('rect', { x: leftW, y: legendY, width: 14, height: 10, rx: 2, fill: 'var(--primary)', 'fill-opacity': '0.18', stroke: 'var(--border)' });
      mk('text', { x: leftW + 20, y: legendY + 9 }, T('少'));
      mk('rect', { x: leftW + 38, y: legendY, width: 14, height: 10, rx: 2, fill: 'var(--primary)', 'fill-opacity': '1', stroke: 'var(--border)' });
      mk('text', { x: leftW + 58, y: legendY + 9 }, T('多'));
      mk('rect', { x: leftW + 84, y: legendY, width: 14, height: 10, rx: 2, class: 'gapcell' });
      mk('text', { x: leftW + 104, y: legendY + 9 }, T('研究缺口'));

      heatBox.append(svg);
      heatBox.hidden = false;
    }

    function shorten(text, cap) {
      const limit = cap || 14;
      const s = String(text);
      return s.length > limit ? s.slice(0, limit - 1) + '…' : s;
    }

    function renderClusters() {
      clustersBox.replaceChildren();
      const clusters = (mapData && mapData.clusters) || [];
      clustersCap.style.display = clusters.length ? '' : 'none';
      clustersCap.textContent = T('主题簇（每簇前 {0} 个代表词）', '5');
      for (const cluster of clusters) {
        if (!cluster) continue;
        const terms = (cluster.top_terms || []).slice(0, 5);
        clustersBox.append(el('div', { class: 'rf-em-cluster' },
          el('div', {},
            el('b', { text: T('主题簇 {0}', String(cluster.id == null ? '—' : cluster.id)) }),
            el('span', { class: 'chip', text: T('{0} 篇', String(cluster.count == null ? 0 : cluster.count)) })),
          terms.length ? el('div', { class: 'terms', translate: 'no', text: terms.join(' · ') })
            : el('div', { class: 'terms', text: T('（文本为空，无代表词）') })));
      }
    }

    function renderGaps() {
      gapsBox.replaceChildren();
      const gaps = (mapData && mapData.gaps) || [];
      if (!gaps.length) return;
      const line = el('p', { style: 'margin:4px 0' },
        el('b', { text: T('研究缺口') }), document.createTextNode('：' + T('{0} 个空白单元格（图中虚线框）', String(gaps.length))));
      gapsBox.append(line);
      const chips = el('div', {});
      for (const gap of gaps.slice(0, 12)) {
        if (!gap) continue;
        chips.append(el('span', { class: 'chip', translate: 'no',
          text: valueLabel(mapDims.x, gap.x) + ' × ' + valueLabel(mapDims.y, gap.y) }));
      }
      gapsBox.append(chips);
      if (gaps.length > 12) {
        gapsBox.append(el('p', { class: 'hint', style: 'margin:2px 0 0', text: T('仅列出前 12 个；完整清单见图内虚线单元格。') }));
      }
    }

    /* ---- 点击单元格 → 弹出该组合下的文献列表 ---- */
    function openCell(i, j) {
      const d = mapData;
      if (!d || !Array.isArray(d.matrix) || !d.matrix[i]) return;
      const count = Number(d.matrix[i][j]) || 0;
      const xLabel = (d.x_labels || [])[i] || '', yLabel = (d.y_labels || [])[j] || '';
      closePopover();
      const articles = (typeof anArticles !== 'undefined' && Array.isArray(anArticles)) ? anArticles : null;
      const usesCluster = mapDims.x === 'cluster' || mapDims.y === 'cluster';
      const memberKeys = d.cell_members?.[i]?.[j];
      const exactMembers = Array.isArray(memberKeys) ? new Set(memberKeys) : null;

      const list = el('div', { class: 'list' });
      let matched = [];
      if (articles && articles.length) {
        const kept = {
          x: mapDims.x === 'journal' ? journalKeptSet(articles) : null,
          y: mapDims.y === 'journal' ? journalKeptSet(articles) : null,
        };
        matched = articles.filter(a => {
          if (exactMembers) return exactMembers.has(a.zotero_key);
          const vx = dimensionValue(a, mapDims.x, kept.x);
          const vy = dimensionValue(a, mapDims.y, kept.y);
          const okX = mapDims.x === 'cluster' || vx === xLabel;
          const okY = mapDims.y === 'cluster' || vy === yLabel;
          return okX && okY;
        });
        if (matched.length) {
          for (const a of matched.slice(0, 50)) {
            list.append(el('div', { class: 'item' },
              el('div', { translate: 'no', text: String(a.title || a.zotero_key || '—') }),
              el('small', { translate: 'no',
                text: [a.journal, a.year, a.doi ? 'doi: ' + a.doi : ''].filter(Boolean).join(' · ') })));
          }
          if (matched.length > 50) {
            list.append(el('p', { class: 'hint', text: T('仅显示前 50 篇，共 {0} 篇。', String(matched.length)) }));
          }
        } else {
          list.append(el('p', { class: 'hint', text: T('该组合下没有匹配的文献。') }));
        }
      } else {
        list.append(el('p', { class: 'hint', text: T('文献列表不可用：分析页文献尚未加载完成，请稍后重试。') }));
      }

      const notes = [];
      if (usesCluster && !exactMembers) {
        notes.push(T('主题簇根据当前文献库使用 TF-IDF+KMeans 自动分配。此处按另一维度（{0}）筛选文献；单篇归属不单独重算。', dimLabel(mapDims.x === 'cluster' ? mapDims.y : mapDims.x)));
      } else if (articles && articles.length && matched.length !== count) {
        notes.push(T('按相同规则从当前文献库筛出 {0} 篇，与地图计数 {1} 不一致（生成地图后，文献库可能已变化）。', String(matched.length), String(count)));
      }

      const box = el('div', { class: 'box', tabindex: '-1' },
        el('header', {},
          el('b', { text: valueLabel(mapDims.x, xLabel) + ' × ' + valueLabel(mapDims.y, yLabel) }),
          el('span', { class: 'chip', text: count > 0 ? T('{0} 篇', String(count)) : T('研究缺口') }),
          el('button', { class: 'btn b-out', type: 'button', style: 'margin-left:auto;padding:2px 10px', text: T('关闭') })),
        notes.length ? el('div', { class: 'hint', style: 'margin-bottom:6px' }, notes.map(n => el('p', { style: 'margin:2px 0', text: n }))) : null,
        list);
      const closeBtn = box.querySelector('button');
      popover = el('dialog', { class: 'rf-em-pop', 'aria-label': valueLabel(mapDims.x, xLabel) + ' × ' + valueLabel(mapDims.y, yLabel) }, box);
      popover.addEventListener('click', safe(ev => { if (ev.target === popover) closePopover(); }));
      document.body.append(popover);
      closeBtn.addEventListener('click', safe(closePopover));
      popover.addEventListener('cancel', safe(ev => { ev.preventDefault(); closePopover(); }));
      popover.showModal();
      closeBtn.focus();
    }
    function closePopover() {
      try {
        if (popover) { popover.close(); popover.remove(); popover = null; }
      } catch (e) { /* 单点故障 */ }
    }

    /* ---- 语言切换：重填静态文案并按缓存重渲染 ---- */
    function refreshLang() {
      try {
        heading.textContent = T('证据地图');
        hint.textContent = T('按已导入文献生成分布地图，点击单元格查看文献。空白格只表示当前文献库没有记录，不代表该领域没有研究。主题聚类在本地计算。');
        runBtn.textContent = T('生成地图');
        const keepX = xSel.value, keepY = ySel.value;
        xSel.replaceChildren(...DIMENSIONS.map(([v, label]) => el('option', { value: v, text: T(label) })));
        ySel.replaceChildren(...DIMENSIONS.map(([v, label]) => el('option', { value: v, text: T(label) })));
        xSel.value = keepX || 'cluster'; ySel.value = keepY || 'study_design';
        clusterInput.setAttribute('aria-label', T('主题聚类数'));
        panel.setAttribute('aria-label', T('证据地图'));
        if (mapData) render();
      } catch (e) { /* 单点故障 */ }
    }

    /* ---- 事件接入 ---- */
    document.addEventListener('reviewflow:analysis-task-changed', safe(() => {
      version++;
      mapData = null; mapDims = { x: '', y: '' };
      heatBox.hidden = true;
      heatBox.replaceChildren();
      clustersBox.replaceChildren();
      clustersCap.style.display = 'none';
      gapsBox.replaceChildren();
      statusLine.textContent = '';
      runBtn.disabled = false;
      closePopover();
    }));
    document.addEventListener('reviewflow:language-changed', safe(refreshLang));
  } catch (e) { /* 单点故障：模块初始化失败不影响主应用与 /api/health */ }
})();
