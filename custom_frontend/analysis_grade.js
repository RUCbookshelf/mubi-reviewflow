/* ReviewFlow · GRADE 证据质量评估面板（衍生产品规格 2026-09-30 §3.5；API=W7 Batch 段）。
 *
 * 分析页「效应量与合并分析」卡下方追加「GRADE 评估」面板：
 *   - 结局行来自已保存的效应量（anEffects 去重 comparison/outcome/timepoint 组合），
 *     每个 outcome 一行：初始质量（RCT/诊断=高、观察性=低）、五个降级下拉
 *     （无/严重(-1)/非常严重(-2)/极其严重(-3)）、三个升级下拉（仅观察性显示，
 *     +1/+2）、自动计算最终质量彩色徽章（高=绿 中=黄 低=橙 极低=红，
 *     与 coscreen/grade_assessor.final_quality 同口径：初始−总降级+总升级，夹 [1,4]）；
 *   - [自动建议]：GET /api/tasks/{id}/analysis/grade/auto-suggest 预填降级因子
 *     并展示推导依据（signal_sources：规则与关键数字；合成口径取分析页当前
 *     选中的合并模型，未选时不传参、由后端默认 random 并在响应中透明记录）；
 *   - 用户修改预填值 → 该行标记「已覆盖」，自动记录 override_notes（可附备注）；
 *   - [保存评估]：POST .../grade/save（服务端按同一规则复算最终质量）；
 *   - [导出 SoF 表]：GET .../grade/export?format=sof_table → 渲染 GRADE 标准
 *     Summary of Findings 表（列/行/图例来自后端），并可下载 CSV（本地生成）。
 *
 * 事件接入（追加式）：
 *   - reviewflow:analysis-loaded：规格 §3.5 指定的事件名（loadAnalysis 已分发）；
 *   - reviewflow:analysis-core-loaded：既有桥接（anEffects 赋值后即分发，可选
 *     端点失败也能建行）；
 *   - reviewflow:analysis-task-changed：任务切换 → 清空行；
 *   - reviewflow:language-changed：重填静态文案并重渲染行。
 *
 * 单点故障：初始化与全部回调自行捕获异常；网络仅经全局 api() 访问本应用 API。
 */
(function () {
  'use strict';
  try {
    if (typeof document === 'undefined' || typeof T !== 'function' || typeof el !== 'function') return;

    const synthesisCard = document.getElementById('anJumpEffects');
    const analysisPage = document.querySelector('.page[data-p="analysis"]');
    if (!synthesisCard || !analysisPage) return;   // 结构不符：安静退出

    /* ---- 样式（随模块注入；高=绿 中=黄 低=橙 极低=红） ---- */
    const style = document.createElement('style');
    style.textContent = [
      '.rf-grade{margin-top:12px}',
      '.rf-grade h3{margin:0 0 4px;font-size:.95rem}',
      '.rf-grade-row{border:1px solid var(--border);border-radius:12px;padding:10px 12px;margin-top:10px;background:var(--bg)}',
      '.rf-grade-row .head{display:flex;flex-wrap:wrap;gap:8px;align-items:center}',
      '.rf-grade-row .head b{font-size:.85rem}',
      '.rf-grade-row .ctl{display:flex;flex-wrap:wrap;gap:10px;align-items:flex-start;margin-top:8px}',
      '.rf-grade-row .factor{display:flex;flex-direction:column;gap:2px;font-size:.75rem}',
      '.rf-grade-row .factor .input{font-size:.78rem;padding:3px 6px;min-width:130px;max-width:150px}',
      '.rf-grade-row .factor select.big{min-width:240px;max-width:none}',
      '.rf-gq{display:inline-block;border-radius:999px;padding:3px 12px;font-weight:700;font-size:.8rem;color:#fff}',
      '.rf-gq-high{background:#2e7d32}',
      '.rf-gq-moderate{background:#b8860b}',
      '.rf-gq-low{background:#d2691e}',
      '.rf-gq-very_low{background:#c62828}',
      '.rf-grade-row .chip-ovr{display:none;border:1px solid var(--border);border-radius:999px;padding:2px 10px;font-size:.72rem;background:var(--bg)}',
      '.rf-grade-row.has-override .chip-ovr{display:inline-block}',
      '.rf-grade-rationale{margin-top:8px;font-size:.78rem;white-space:pre-wrap}',
      '.rf-grade-row .factor .rf-grade-note{font-size:.78rem;padding:3px 8px;min-width:340px;max-width:none}',
      '.rf-sof{margin-top:12px;overflow-x:auto}',
      '.rf-sof table{border-collapse:collapse;font-size:.76rem;min-width:720px}',
      '.rf-sof th,.rf-sof td{border:1px solid var(--border);padding:5px 8px;text-align:left;vertical-align:top}',
      '.rf-sof th{background:var(--bg);font-weight:600}',
    ].join('\n');
    (document.head || document.documentElement).appendChild(style);

    /* ---- 常量（与 coscreen/grade_assessor 同口径） ---- */
    const DOWNGRADE_FACTORS = ['rob', 'inconsistency', 'indirectness', 'imprecision', 'publication_bias'];
    const UPGRADE_FACTORS = ['large_effect', 'dose_response', 'plausible_confounding'];
    const FACTOR_LABELS = {
      rob: '偏倚风险', inconsistency: '不一致性', indirectness: '间接性',
      imprecision: '不精确性', publication_bias: '发表偏倚',
      large_effect: '大效应', dose_response: '剂量反应', plausible_confounding: '合理混杂',
    };
    const DOWNGRADE_LEVELS = [
      ['none', '无降级'], ['serious', '严重(-1)'], ['very_serious', '非常严重(-2)'], ['very_very_serious', '极其严重(-3)'],
    ];
    const UPGRADE_LEVELS = [['none', '无升级'], ['plus_one', '升级(+1)'], ['plus_two', '升级(+2)']];
    const DESIGNS = [
      ['rct', 'RCT（初始质量：高）'],
      ['observational', '观察性研究（初始质量：低）'],
      ['diagnostic', '诊断准确性（初始质量：高）'],
    ];
    const INITIAL_QUALITY = { rct: 'high', observational: 'low', diagnostic: 'high' };
    const LEVEL_SCORE = { very_low: 1, low: 2, moderate: 3, high: 4 };
    const LEVEL_LABEL = { high: '高', moderate: '中', low: '低', very_low: '极低' };
    const DOWN_STEP = { none: 0, serious: 1, very_serious: 2, very_very_serious: 3 };
    const UP_STEP = { none: 0, plus_one: 1, plus_two: 2 };
    const OUTCOME_TYPES = [['critical', '关键结局'], ['important', '重要结局'], ['not_critical', '不重要结局']];

    function safe(fn) { return function () { try { fn.apply(this, arguments); } catch (e) { /* 单点故障 */ } }; }
    function fmtNum(v) {
      const n = Number(v);
      return Number.isFinite(n) ? String(Number(n.toPrecision(4))) : '—';
    }

    /* 最终质量 = 初始 − 总降级 + 总升级（仅观察性可升级），夹 [1,4]（镜像
       grade_assessor.final_quality，保存时服务端再复算一遍为准） */
    function computeFinal(design, downgrades, upgrades) {
      const initial = LEVEL_SCORE[INITIAL_QUALITY[design] || 'high'];
      let totalDown = 0;
      for (const f of DOWNGRADE_FACTORS) totalDown += DOWN_STEP[downgrades[f]] || 0;
      let totalUp = 0;
      if (design === 'observational') {
        for (const f of UPGRADE_FACTORS) totalUp += UP_STEP[upgrades[f]] || 0;
      }
      const score = Math.min(4, Math.max(1, initial - totalDown + totalUp));
      for (const [level, bound] of Object.entries(LEVEL_SCORE)) {
        if (score === bound) return { level, score, initial, totalDown, totalUp };
      }
      return { level: 'very_low', score, initial, totalDown, totalUp };
    }

    /* ---- 状态 ---- */
    let version = 0;                 // 异步票据
    let restoredTask = null;
    let assessmentReadReady = false;
    let assessmentReadError = '';
    const rowState = new Map();      // combo key -> 状态（跨重渲染保留）
    function comboKey(c) { return (c.comparison || '') + '\u0001' + (c.outcome || '') + '\u0001' + (c.timepoint || ''); }

    function stateFor(combo) {
      const key = comboKey(combo);
      let st = rowState.get(key);
      if (!st) {
        st = {
          combo: combo,
          initial_design: 'rct', outcome_type: 'important',
          downgrades: { rob: 'none', inconsistency: 'none', indirectness: 'none', imprecision: 'none', publication_bias: 'none' },
          upgrades: { large_effect: 'none', dose_response: 'none', plausible_confounding: 'none' },
          suggested: null, signals: {}, synthesis: null,
          overridden: {},   // factor -> {auto, user}
          note: '',
        };
        rowState.set(key, st);
      }
      return st;
    }

    /* ---- 面板骨架 ---- */
    const heading = el('h3', { text: T('GRADE 评估') });
    const hint = el('p', { class: 'hint', style: 'margin:2px 0 8px', text: T('按 GRADE 框架评估每个结局的证据质量（高→中→低→极低）。[自动建议] 从已保存的合成/RoB/发表偏倚结果推导降级因子；间接性无法自动推导，须人工判断。修改预填值会标记「已覆盖」并记入备注。') });
    const sofBtn = el('button', { class: 'btn b-out', type: 'button', text: T('导出 SoF 表') });
    const sofBox = el('div', { class: 'rf-sof', hidden: '' });
    const rowsBox = el('div', { id: 'rfGradeRows' });
    const emptyLine = el('p', { class: 'hint', text: T('尚无已保存的效应量——先在「效应量录入」保存结果，再回到这里做 GRADE 评估。') });
    const panel = el('section', { class: 'card rf-grade', id: 'rfGradePanel', 'aria-label': T('GRADE 评估') },
      heading, hint, el('div', { style: 'display:flex;gap:8px;flex-wrap:wrap' }, sofBtn),
      sofBox, emptyLine, rowsBox);
    synthesisCard.after(panel);

    /* ---- 从 anEffects 取结局组合（去重，稳定排序） ---- */
    function outcomeCombos() {
      const effects = (typeof anEffects !== 'undefined' && Array.isArray(anEffects)) ? anEffects : [];
      const seen = new Map();
      for (const eff of effects) {
        if (!eff) continue;
        const combo = {
          comparison: String(eff.comparison || ''), outcome: String(eff.outcome || ''),
          timepoint: String(eff.timepoint || ''),
        };
        const key = comboKey(combo);
        if (!seen.has(key)) seen.set(key, { combo, measures: new Set(), studies: new Set() });
        const rec = seen.get(key);
        if (eff.measure) rec.measures.add(String(eff.measure));
        if (eff.study_id) rec.studies.add(String(eff.study_id));
      }
      return Array.from(seen.values());
    }

    /* ---- 行渲染 ---- */
    function renderRows() {
      const combos = outcomeCombos();
      emptyLine.hidden = combos.length > 0;
      rowsBox.replaceChildren();
      for (const rec of combos) rowsBox.append(buildRow(rec));
    }

    function buildRow(rec) {
      const st = stateFor(rec.combo);
      const rowEl = el('div', { class: 'rf-grade-row', 'data-combo': comboKey(rec.combo) });
      rowEl.addEventListener('input', () => { st.dirty = true; });
      rowEl.addEventListener('change', () => { st.dirty = true; });

      /* 头部：结局 + 时间点 + 比较 + 指标 + 已覆盖标记 */
      const overrideChip = el('span', { class: 'chip-ovr', text: T('已覆盖') });
      const head = el('div', { class: 'head' },
        el('b', { text: rec.combo.outcome || T('（未命名结局）') }),
        rec.combo.timepoint ? el('span', { class: 'chip', text: T('时间点：{0}', rec.combo.timepoint) }) : null,
        rec.combo.comparison ? el('span', { class: 'chip', translate: 'no', text: rec.combo.comparison }) : null,
        Array.from(rec.measures).length ? el('span', { class: 'chip', translate: 'no', text: Array.from(rec.measures).join('/') }) : null,
        el('span', { class: 'chip', text: T('{0} 项研究', String(rec.studies.size)) }),
        overrideChip);

      /* 控件区 */
      const designSel = el('select', { class: 'input big', 'aria-label': T('研究设计与初始质量') },
        DESIGNS.map(([v, label]) => el('option', { value: v, text: T(label) })));
      designSel.value = st.initial_design;
      const typeSel = el('select', { class: 'input', 'aria-label': T('结局重要性') },
        OUTCOME_TYPES.map(([v, label]) => el('option', { value: v, text: T(label) })));
      typeSel.value = st.outcome_type;

      const factorSelects = {};
      const factorInfo = {};   // factor -> ⓘ（推导依据 title）
      function factorNode(factor, levels) {
        const sel = el('select', { class: 'input', 'aria-label': T(FACTOR_LABELS[factor]) },
          levels.map(([v, label]) => el('option', { value: v, text: T(label) })));
        factorSelects[factor] = sel;
        const info = el('span', { text: 'ⓘ', title: '', style: 'cursor:help;color:var(--muted)', hidden: '' });
        factorInfo[factor] = info;
        return el('div', { class: 'factor' },
          el('span', { text: T(FACTOR_LABELS[factor]) }), el('span', { style: 'display:flex;gap:4px;align-items:center' }, sel, info));
      }

      const badge = el('span', { class: 'rf-gq', role: 'status' });
      const autoBtn = el('button', { class: 'btn b-out', type: 'button', text: T('自动建议') });
      const saveBtn = el('button', { class: 'btn b-inc', type: 'button', text: T('保存评估') });
      const noteInput = el('input', { class: 'input rf-grade-note', 'aria-label': T('覆盖备注'), placeholder: T('覆盖备注（可选）'), value: st.note });

      const ctl = el('div', { class: 'ctl' },
        el('div', { class: 'factor' }, el('span', { text: T('研究设计') }), designSel),
        el('div', { class: 'factor' }, el('span', { text: T('结局重要性') }), typeSel),
        DOWNGRADE_FACTORS.map(f => factorNode(f, DOWNGRADE_LEVELS)),
        el('div', { class: 'factor rf-upgrades' }, el('span', { text: T('观察性升级因子') }),
          el('span', { style: 'display:flex;flex-direction:column;gap:3px' },
            UPGRADE_FACTORS.map(f => factorNode(f, UPGRADE_LEVELS)))),
        el('div', { class: 'factor' }, el('span', { text: T('最终质量') }), badge),
        el('div', { class: 'factor' }, el('span', { text: ' ' }),
          el('span', { style: 'display:flex;gap:6px;flex-wrap:wrap' }, autoBtn, saveBtn)),
        el('div', { class: 'factor', style: 'flex:1;min-width:200px' }, el('span', { text: T('覆盖备注') }), noteInput));

      const statusLine = el('p', { class: 'hint', style: 'margin:6px 0 0', role: 'status' });
      const rationaleBox = el('div', { class: 'rf-grade-rationale hint', hidden: '' });

      rowEl.append(head, ctl, statusLine, rationaleBox);

      /* ---- 同步状态 → 控件 ---- */
      function syncFromState() {
        for (const f of DOWNGRADE_FACTORS) factorSelects[f].value = st.downgrades[f];
        for (const f of UPGRADE_FACTORS) factorSelects[f].value = st.upgrades[f];
        syncUpgradeVisibility();
        refreshBadge();
        refreshOverrideMark();
      }
      function syncUpgradeVisibility() {
        const upgradeBox = rowEl.querySelector('.rf-upgrades');
        if (upgradeBox) upgradeBox.style.display = st.initial_design === 'observational' ? '' : 'none';
      }
      function refreshBadge() {
        const outcome = computeFinal(st.initial_design, st.downgrades, st.upgrades);
        badge.className = 'rf-gq rf-gq-' + outcome.level;
        badge.textContent = T(LEVEL_LABEL[outcome.level]);
        badge.title = T('初始 {0} − 降级 {1} + 升级 {2} → 最终 {3}',
          T(LEVEL_LABEL[INITIAL_QUALITY[st.initial_design] || 'high']),
          String(outcome.totalDown), String(outcome.totalUp), T(LEVEL_LABEL[outcome.level]));
      }
      function refreshOverrideMark() {
        const any = Object.keys(st.overridden).length > 0;
        rowEl.classList.toggle('has-override', any);
        if (any) {
          const parts = Object.entries(st.overridden).map(([f, o]) => T('{0}: {1}→{2}', T(FACTOR_LABELS[f]), T(levelLabel('down', o.auto)), T(levelLabel('down', o.user))));
          overrideChip.title = parts.join('；');
        }
      }
      function levelLabel(kind, v) {
        const list = kind === 'up' ? UPGRADE_LABEL_TEXT : DOWN_LABEL_TEXT;
        return list[v] || v;
      }

      /* ---- 控件 → 状态 ---- */
      designSel.addEventListener('change', safe(() => {
        st.initial_design = designSel.value;
        if (st.initial_design !== 'observational') {
          for (const f of UPGRADE_FACTORS) st.upgrades[f] = 'none';
        }
        syncFromState();
      }));
      typeSel.addEventListener('change', safe(() => { st.outcome_type = typeSel.value; }));
      for (const f of DOWNGRADE_FACTORS) {
        factorSelects[f].addEventListener('change', safe(() => {
          st.downgrades[f] = factorSelects[f].value;
          trackOverride(f, factorSelects[f].value);
          refreshBadge(); refreshOverrideMark();
        }));
      }
      for (const f of UPGRADE_FACTORS) {
        factorSelects[f].addEventListener('change', safe(() => {
          st.upgrades[f] = factorSelects[f].value;
          refreshBadge();
        }));
      }
      noteInput.addEventListener('input', safe(() => { st.note = noteInput.value; }));

      function trackOverride(factor, value) {
        if (!st.suggested) return;
        const auto = st.suggested[factor];
        if (auto !== undefined && auto !== value) st.overridden[factor] = { auto: auto, user: value };
        else delete st.overridden[factor];
      }

      /* ---- [自动建议]：预填 + 推导依据 ---- */
      autoBtn.addEventListener('click', safe(() => runAutoSuggest(st, rowEl, {
        autoBtn, statusLine, rationaleBox, factorSelects, factorInfo, syncFn: syncFromState,
      })));

      /* ---- [保存评估] ---- */
      saveBtn.addEventListener('click', safe(() => runSave(st, rowEl, { saveBtn, statusLine, badge, refreshBadge })));

      syncFromState();
      if (!assessmentReadReady) {
        rowEl.querySelectorAll('input,select,button').forEach(control => { control.disabled = true; });
        statusLine.textContent = assessmentReadError;
      }
      return rowEl;
    }

    const DOWN_LABEL_TEXT = { none: '无降级', serious: '严重(-1)', very_serious: '非常严重(-2)', very_very_serious: '极其严重(-3)' };
    const UPGRADE_LABEL_TEXT = { none: '无升级', plus_one: '升级(+1)', plus_two: '升级(+2)' };

    /* ---- 推导依据格式化（signal_sources：规则 + 关键数字；纯本地展示） ---- */
    function formatSignal(factor, signal) {
      if (!signal || typeof signal !== 'object') return T('{0}：无信号。', T(FACTOR_LABELS[factor]));
      if (signal.available === false) {
        return T('{0}：{1}', T(FACTOR_LABELS[factor]), T(String(signal.reason || '无数据，需人工判断')));
      }
      const parts = [];
      if (signal.rule) parts.push(T('规则 {0}', String(signal.rule)));
      for (const [k, v] of Object.entries(signal)) {
        if (k === 'available' || k === 'rule' || v === null || v === undefined || v === '') continue;
        parts.push(k + '=' + (typeof v === 'number' ? fmtNum(v) : String(v)));
      }
      return T('{0}：{1}', T(FACTOR_LABELS[factor]), parts.join(' · ') || T('可用'));
    }

    async function runAutoSuggest(st, rowEl, ui) {
      st.dirty = true;
      let ticket;
      try {
        if (typeof api !== 'function' || typeof taskApi !== 'function') return;
        const combo = st.combo;
        if (!combo.outcome || !combo.comparison || !combo.timepoint) {
          ui.statusLine.textContent = T('该行缺少比较方向、结局或时间点，无法生成建议。');
          return;
        }
        let q = '?comparison=' + encodeURIComponent(combo.comparison) +
          '&outcome=' + encodeURIComponent(combo.outcome) +
          '&timepoint=' + encodeURIComponent(combo.timepoint) +
          '&initial_design=' + encodeURIComponent(st.initial_design);
        /* 合成口径跟随分析页当前模型选择（未选/非法则不传，由后端默认 random 并透明记录） */
        if (typeof anValue === 'function') {
          const model = anValue('anModel');
          if (['fixed', 'random', 'random_pm', 'random_reml'].includes(model)) q += '&model=' + encodeURIComponent(model);
        }
        ticket = ++version;
        ui.autoBtn.disabled = true;
        ui.autoBtn.dataset.requestTicket = String(ticket);
        ui.statusLine.textContent = T('正在推导自动建议…');
        try {
          const d = await api(taskApi() + '/analysis/grade/auto-suggest' + q);
          if (ticket !== version) return;
          const downgrades = (d && d.downgrades) || {};
          st.suggested = {};
          for (const f of DOWNGRADE_FACTORS) {
            if (f === 'indirectness') continue;   // 无法自动推导：保留用户当前选择
            const level = downgrades[f];
            if (level) { st.downgrades[f] = level; st.suggested[f] = level; }
          }
          st.signals = (d && d.signal_sources) || {};
          st.synthesis = (d && d.synthesis) || null;
          st.overridden = {};
          ui.syncFn();
          /* 每个预填因子旁的 ⓘ 显示推导依据 */
          for (const f of DOWNGRADE_FACTORS) {
            const info = ui.factorInfo[f];
            if (!info) continue;
            const text = formatSignal(f, st.signals[f]);
            info.title = text;
            info.hidden = !st.signals[f];
            info.setAttribute('aria-label', text);
          }
          const lines = DOWNGRADE_FACTORS.map(f => formatSignal(f, st.signals[f]));
          if (st.synthesis) {
            const sy = st.synthesis;
            lines.push(T('合成口径：{0} 模型 · {1} 项研究 · 合并效应 {2}（95% CI {3} 至 {4}）· I²={5}%',
              String(sy.model || 'random'), String(sy.n_studies == null ? '—' : sy.n_studies),
              fmtNum(sy.pooled), fmtNum(sy.ci_low), fmtNum(sy.ci_high), fmtNum(sy.i2_percent)));
            if (sy.publication_bias_test_available === false) {
              lines.push(T('发表偏倚检验不可用（研究数不足或单组指标），该因子需人工评估。'));
            }
          }
          lines.push(T('以上为自动建议；间接性完全依赖人工判断。请逐项确认或覆盖。'));
          ui.rationaleBox.replaceChildren(el('span', { text: lines.join('\n') }));
          ui.rationaleBox.hidden = false;
          ui.statusLine.textContent = T('已按自动建议预填（初始质量：{0}，建议最终质量：{1}）。',
            T(LEVEL_LABEL[(d && d.initial_quality) || 'high']), T(LEVEL_LABEL[(d && d.final_quality) || 'high']));
        } finally {
          if (ui.autoBtn.dataset.requestTicket === String(ticket)) ui.autoBtn.disabled = false;
        }
      } catch (e) {
        if (ticket !== undefined && ticket !== version) return;
        ui.statusLine.textContent = T('自动建议失败：') + ((e && e.message) || '');
      }
    }

    function overrideNotesText(st) {
      const parts = [];
      for (const [f, o] of Object.entries(st.overridden)) {
        parts.push(T('{0}: {1} → {2}', T(FACTOR_LABELS[f]),
          T(DOWN_LABEL_TEXT[o.auto] || o.auto), T(DOWN_LABEL_TEXT[o.user] || o.user)));
      }
      if (st.note && st.note.trim()) parts.push(st.note.trim());
      return parts.join('；');
    }

    async function runSave(st, rowEl, ui) {
      st.dirty = true;
      let ticket;
      try {
        if (typeof api !== 'function' || typeof taskApi !== 'function') return;
        const combo = st.combo;
        if (!combo.outcome || !combo.comparison || !combo.timepoint) {
          ui.statusLine.textContent = T('该行缺少比较方向、结局或时间点，无法保存。请补全后重试。');
          return;
        }
        const body = {
          comparison: combo.comparison, outcome: combo.outcome, timepoint: combo.timepoint,
          outcome_type: st.outcome_type, initial_design: st.initial_design,
          rob_downgrade: st.downgrades.rob,
          inconsistency_downgrade: st.downgrades.inconsistency,
          indirectness_downgrade: st.downgrades.indirectness,
          imprecision_downgrade: st.downgrades.imprecision,
          publication_bias_downgrade: st.downgrades.publication_bias,
          large_effect_upgrade: st.upgrades.large_effect,
          dose_response_upgrade: st.upgrades.dose_response,
          plausible_confounding_upgrade: st.upgrades.plausible_confounding,
          signal_sources: st.signals && Object.keys(st.signals).length ? st.signals : { initial_design: st.initial_design },
          override_notes: overrideNotesText(st),
        };
        ticket = ++version;
        ui.saveBtn.disabled = true;
        ui.saveBtn.dataset.requestTicket = String(ticket);
        ui.statusLine.textContent = T('正在保存 GRADE 评估…');
        try {
          const d = await api(taskApi() + '/analysis/grade/save', { method: 'POST', body: body });
          if (ticket !== version) return;
          ui.statusLine.textContent = T('✓ 已保存（最终质量已重新计算：{0}）。', T(LEVEL_LABEL[(d && d.final_quality) || 'high']));
          ui.refreshBadge();
          if (typeof toast === 'function') toast(T('✓ 已保存「{0}」的 GRADE 评估', combo.outcome), 'inc');
        } finally {
          if (ui.saveBtn.dataset.requestTicket === String(ticket)) ui.saveBtn.disabled = false;
        }
      } catch (e) {
        if (ticket !== undefined && ticket !== version) return;
        ui.statusLine.textContent = T('保存失败：') + ((e && e.message) || '');
        if (typeof toast === 'function') toast(T('保存失败：') + ((e && e.message) || ''), 'warn');
      }
    }

    /* ---- 导出 SoF 表（GET .../grade/export → 渲染 + 本地 CSV 下载） ---- */
    sofBtn.addEventListener('click', safe(sofExport));
    async function sofExport() {
      let ticket;
      try {
        if (typeof api !== 'function' || typeof taskApi !== 'function') return;
        ticket = ++version;
        sofBtn.disabled = true;
        sofBtn.dataset.requestTicket = String(ticket);
        const d = await api(taskApi() + '/analysis/grade/export?format=sof_table');
        if (ticket !== version) return;
        renderSof(d);
        if (typeof toast === 'function') toast(T('✓ 已生成 SoF 表（{0} 行）', String((d && d.rows || []).length)), 'inc');
      } catch (e) {
        if (ticket !== undefined && ticket !== version) return;
        sofBox.hidden = true;
        if (typeof toast === 'function') toast(T('导出失败：') + ((e && e.message) || ''), 'warn');
      } finally {
        if (sofBtn.dataset.requestTicket === String(ticket)) sofBtn.disabled = false;
      }
    }
    function renderSof(d) {
      sofBox.replaceChildren();
      if (!d || !Array.isArray(d.columns) || !Array.isArray(d.rows)) {
        sofBox.append(el('p', { class: 'hint', text: T('SoF 表数据格式异常。') }));
        sofBox.hidden = false;
        return;
      }
      const table = el('table', {},
        el('thead', {}, el('tr', {}, d.columns.map(c => el('th', { text: String(c) })))),
        el('tbody', {}, d.rows.map(r => el('tr', {}, (r || []).map(cell => el('td', { text: String(cell == null ? '' : cell) }))))));
      sofBox.append(el('b', { text: String(d.title || T('GRADE 证据概要（Summary of Findings）')) }), table);
      for (const line of d.legend || []) sofBox.append(el('p', { class: 'hint', style: 'margin:4px 0 0', text: String(line) }));
      const csvBtn = el('button', { class: 'btn b-out', type: 'button', text: T('下载 SoF CSV'), style: 'margin-top:8px' });
      csvBtn.addEventListener('click', safe(() => downloadSofCsv(d)));
      sofBox.append(csvBtn);
      sofBox.hidden = false;
    }
    function downloadSofCsv(d) {
      try {
        const esc = v => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
        const lines = [d.columns.map(esc).join(',')];
        for (const r of d.rows || []) lines.push((r || []).map(esc).join(','));
        const blob = new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'grade-sof.csv';
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 3000);
        if (typeof toast === 'function') toast(T('✓ 已下载：') + a.download, 'inc');
      } catch (e) { /* 单点故障 */ }
    }

    /* ---- 语言切换：重填静态文案并重渲染 ---- */
    function refreshLang() {
      try {
        heading.textContent = T('GRADE 评估');
        hint.textContent = T('按 GRADE 框架评估每个结局的证据质量（高→中→低→极低）。[自动建议] 从已保存的合成/RoB/发表偏倚结果推导降级因子；间接性无法自动推导，须人工判断。修改预填值会标记「已覆盖」并记入备注。');
        sofBtn.textContent = T('导出 SoF 表');
        panel.setAttribute('aria-label', T('GRADE 评估'));
        renderRows();
      } catch (e) { /* 单点故障 */ }
    }

    /* ---- 事件接入：规格事件 + 既有桥接 ---- */
    async function restoreAssessments() {
      const taskId = S.task?.task_id;
      if (!taskId || restoredTask === taskId) return;
      restoredTask = taskId;
      const taskVersion = S.taskVersion;
      try {
        const data = await api(taskApi() + '/analysis/grade/export?format=sof_table');
        if (taskVersion !== S.taskVersion || taskId !== S.task?.task_id) return;
        for (const assessment of data.assessments || []) {
          if (assessment.assessor && assessment.assessor !== S.user?.username) continue;
          const combo = {comparison: assessment.comparison, outcome: assessment.outcome, timepoint: assessment.timepoint};
          const st = stateFor(combo);
          if (st.dirty) continue;
          st.initial_design = assessment.initial_design || 'rct';
          st.outcome_type = assessment.outcome_type || 'important';
          Object.assign(st.downgrades, assessment.downgrades || {});
          Object.assign(st.upgrades, assessment.upgrades || {});
          st.signals = assessment.signal_sources || {};
          st.note = assessment.override_notes || '';
        }
        assessmentReadReady = true;
        renderRows();
      } catch (error) {
        if (taskVersion !== S.taskVersion || taskId !== S.task?.task_id) return;
        assessmentReadReady = error.status === 404;
        if (!assessmentReadReady) {
          restoredTask = null;
          assessmentReadError = T('载入失败：') + (error.message || '');
        }
        renderRows();
      }
    }
    document.addEventListener('reviewflow:analysis-loaded', safe(() => { renderRows(); restoreAssessments(); }));
    document.addEventListener('reviewflow:analysis-core-loaded', safe(renderRows));
    document.addEventListener('reviewflow:analysis-task-changed', safe(() => {
      version++;
      restoredTask = null;
      assessmentReadReady = false; assessmentReadError = '';
      rowState.clear();
      for (const button of rowsBox.querySelectorAll('button')) button.disabled = false;
      rowsBox.replaceChildren();
      emptyLine.hidden = false;
      sofBox.hidden = true;
      sofBtn.disabled = false;
    }));
    document.addEventListener('reviewflow:language-changed', safe(refreshLang));

    renderRows();
  } catch (e) { /* 单点故障：模块初始化失败不影响主应用与 /api/health */ }
})();
