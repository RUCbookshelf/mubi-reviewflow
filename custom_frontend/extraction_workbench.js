/* ReviewFlow · 结构化数据提取工作台（衍生产品规格 2026-09-30 §2.5；API=W6 Batch 段）。
 *
 * 编码页每篇文献追加「提取」标签页（挂在 .bench-form 内、#cbForms 之后的自有
 * 容器，renderBench 只清空 #cbForms，不影响本面板）：
 *   - 研究设计下拉（GET /api/tasks/{id}/extraction/templates，会话内缓存）；
 *   - 按模板 fields 动态渲染表单：数字字段 min/max 校验、必填标注、单位显示；
 *   - 字段联动：diagnostic_2x2 的 tp+fp+fn+tn 实时显示合计；survival_hr 校验
 *     CI 包含关系（hr ∈ [ci_low, ci_high]）；
 *   - [校验]：本地校验并标红不合格字段（与后端 map_to_effect 同口径：必填/
 *     数字/min/max/非负整数计数）；
 *   - [计算效应量并保存]：POST /api/tasks/{id}/extraction/{article_key}/apply
 *     → 展示 "MD = 2.3, SE = 0.45" 式结果与入库去向；比值类指标（RR/OR/HR）
 *     后端按 log 尺度换算 SE，界面如实提示；
 *   - 未保存的草稿按 任务|文献键 暂存于内存，切篇往返不丢失。
 *
 * 事件接入（追加式）：
 *   - reviewflow:coding-loaded：规格 §2.5 指定的事件名（主应用将来分发即生效）；
 *   - reviewflow:workspace-changed(detail='bench')：既有桥接——进入编码页后
 *     轮询 S.cbQueue 就绪即激活；
 *   - #cbForms childList 变化（renderBench 每次重绘/切篇）→ 同步当前文献；
 *   - reviewflow:task-loaded / task-context-changed：任务切换 → 复位；
 *   - reviewflow:language-changed：重填静态文案。
 *
 * 单点故障：初始化与全部回调自行捕获异常；网络仅经全局 api() 访问本应用 API。
 */
(function () {
  'use strict';
  try {
    if (typeof document === 'undefined' || typeof T !== 'function' || typeof el !== 'function') return;

    const forms = document.getElementById('cbForms');
    const benchForm = forms ? forms.closest('.bench-form') : null;
    const benchPage = document.querySelector('.page[data-p="bench"]');
    if (!forms || !benchForm || !benchPage) return;   // 结构不符：安静退出

    /* ---- 样式（随模块注入） ---- */
    const style = document.createElement('style');
    style.textContent = [
      '.rf-extract{border:1px solid var(--border);border-radius:12px;margin-top:10px;flex-shrink:0;background:var(--surface);display:flex;flex-direction:column;max-height:55%}',
      '.rf-extract-head{display:flex;align-items:center;gap:8px;padding:8px 12px;width:100%;text-align:left;background:none;color:var(--ink);font:inherit;border:0;cursor:pointer;user-select:none;border-bottom:1px solid var(--border)}',
      '.rf-extract-head .t{font-weight:600;font-size:.85rem}',
      '.rf-extract-head .sub{font-size:.75rem;color:var(--muted);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '.rf-extract-body{padding:10px 12px;overflow-y:auto}',
      '.rf-extract .input{font-size:.82rem;padding:4px 8px;max-width:100%}',
      '.rf-x-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px;margin:8px 0}',
      '.rf-x-field label{display:block;font-size:.75rem;margin-bottom:2px}',
      '.rf-x-field .unit{color:var(--muted)}',
      '.rf-x-field .req{color:var(--danger)}',
      '.rf-x-field .err{display:block;color:var(--danger);font-size:.72rem;margin-top:2px}',
      '.rf-extract .rf-x-invalid{border-color:var(--danger);outline:1px solid var(--danger)}',
      '.rf-x-status{font-size:.8rem;margin:6px 0 0;white-space:pre-wrap}',
      '.rf-x-result{border:1px dashed var(--border);border-radius:10px;padding:8px 10px;margin-top:8px;font-size:.8rem}',
      '.rf-x-result b{font-size:.85rem}',
      '.rf-x-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:6px 0}',
    ].join('\n');
    (document.head || document.documentElement).appendChild(style);

    /* ---- 模板元数据（前端展示层：模板下拉文案 / 指标覆盖选项 / 方向性） ---- */
    const TEMPLATE_LABELS = {
      rct_continuous: 'RCT · 连续结局',
      rct_binary: 'RCT · 二分类结局',
      diagnostic_2x2: '诊断准确性 · 2×2 表',
      survival_hr: '生存分析 · 风险比 HR',
    };
    /* 声明支持 values.measure 覆盖的模板（W6：仅接受 measure 参数的计算函数） */
    const MEASURE_OVERRIDES = {
      rct_continuous: ['MD', 'SMD'],
      rct_binary: ['RR', 'OR', 'RD'],
    };
    const DTA_TEMPLATE = 'diagnostic_2x2';
    const DTA_COUNT_KEYS = ['tp', 'fp', 'fn', 'tn'];

    function safe(fn) { return function () { try { fn.apply(this, arguments); } catch (e) { /* 单点故障 */ } }; }

    /* ---- 状态 ---- */
    let version = 0;              // 异步票据
    let templates = null;         // [{template_id,study_type,outcome_type,fields[...],effect_measure,...}]
    let currentTemplateId = '';
    let currentTaskId = typeof S !== 'undefined' ? S.task?.task_id : null;
    let currentKey = null;        // 当前文献 zotero_key
    let expanded = false;
    const drafts = new Map();     // `${taskVersion}|${key}` -> 草稿（模板选择+字段值+分析层输入）

    /* ---- 面板骨架 ---- */
    const headTitle = el('span', { class: 't', text: T('提取') });
    const headSub = el('span', { class: 'sub', text: T('未载入文献') });

    const head = el('button', { type: 'button', class: 'rf-extract-head', 'aria-expanded': 'false', 'aria-controls': 'rfExtractBody' },
      headTitle, headSub);
    const body = el('div', { id: 'rfExtractBody', class: 'rf-extract-body', hidden: '' });
    const panel = el('section', { class: 'rf-extract', id: 'rfExtractPanel', 'aria-label': T('结构化数据提取') },
      head, body);
    forms.after(panel);

    head.addEventListener('click', safe(() => setExpanded(!expanded)));
    function setExpanded(open) {
      expanded = open;
      body.hidden = !open;
      head.setAttribute('aria-expanded', String(open));
    }

    /* ---- 静态文案节点（语言切换时重填） ---- */
    const L = {};

    /* ---- 模板下拉 + 公共分析层输入 ---- */
    const tplSelect = el('select', { class: 'input', id: 'rfXTpl', 'aria-label': T('研究设计（提取模板）') });
    L.tplCap = el('span', { style: 'font-size:.78rem', text: T('研究设计') });
    tplSelect.addEventListener('change', safe(() => { version++; applyBtn.disabled = false; currentTemplateId = tplSelect.value; renderFields(true); }));

    const studyInput = el('input', { class: 'input', placeholder: T('研究编号（与数据分析页一致）') });
    const cmpInput = el('input', { class: 'input', placeholder: T('比较方向，如：治疗组与对照组') });
    const outInput = el('input', { class: 'input', placeholder: T('结局') });
    const tpInput = el('input', { class: 'input', placeholder: T('时间点') });
    const dirSelect = el('select', { class: 'input' });
    const locInput = el('input', { class: 'input', placeholder: T('原文页码、表或图（可选）') });
    const measureSelect = el('select', { class: 'input' });
    const dtaInputs = {
      index_test: el('input', { class: 'input', placeholder: T('index_test') }),
      target_condition: el('input', { class: 'input', placeholder: T('target_condition') }),
      threshold: el('input', { class: 'input', placeholder: T('threshold') }),
      reference_standard: el('input', { class: 'input', placeholder: T('reference_standard') }),
    };

    const fieldsBox = el('div', {});
    const linkLine = el('p', { class: 'hint', style: 'margin:4px 0 0', hidden: '' });   // 字段联动行（DTA 合计）
    const statusLine = el('p', { class: 'rf-x-status hint', role: 'status' });
    const resultBox = el('div', { class: 'rf-x-result', hidden: '' });

    const checkBtn = el('button', { class: 'btn b-out', type: 'button', text: T('校验') });
    const applyBtn = el('button', { class: 'btn b-inc', type: 'button', text: T('计算效应量并保存') });
    checkBtn.addEventListener('click', safe(() => { const r = validate(); statusLine.textContent = r.ok ? T('✓ 校验通过，可以计算并保存。') : r.messages.join('\n'); }));
    applyBtn.addEventListener('click', safe(() => applyExtraction()));

    const dtaRow = el('div', { class: 'rf-x-row', hidden: '' },   // DTA 定位行（按模板显示）
      dtaInputs.index_test, dtaInputs.target_condition, dtaInputs.threshold, dtaInputs.reference_standard);

    body.append(
      el('div', { class: 'rf-x-row' }, L.tplCap, tplSelect, measureSelect),
      el('div', { class: 'rf-x-row' },
        el('span', { style: 'font-size:.78rem', text: T('研究编号') }), studyInput,
        dirSelect),
      el('div', { class: 'rf-x-row' },
        cmpInput, outInput, tpInput, locInput),
      dtaRow,
      fieldsBox, linkLine,
      el('div', { class: 'rf-x-row' }, checkBtn, applyBtn),
      statusLine, resultBox);

    /* ---- 模板载入（GET /extraction/templates，会话内缓存） ---- */
    async function loadTemplates() {
      if (templates || typeof api !== 'function' || typeof taskApi !== 'function') return;
      const ticket = ++version;
      try {
        const d = await api(taskApi() + '/extraction/templates');
        if (ticket !== version) return;
        templates = (d && Array.isArray(d.templates)) ? d.templates : [];
      } catch (e) { templates = []; /* 拉取失败：占位提示，不影响编码页 */ }
      renderTemplateOptions();
    }
    function renderTemplateOptions() {
      tplSelect.textContent = '';
      tplSelect.append(el('option', { value: '', text: T('选择研究设计') }));
      for (const t of templates || []) {
        if (!t || !t.template_id) continue;
        tplSelect.append(el('option', { value: t.template_id, text: T(TEMPLATE_LABELS[t.template_id] || t.template_id) }));
      }
      if (!tplSelect.options.length) tplSelect.append(el('option', { value: '', text: T('模板暂不可用') }));
      if (currentTemplateId) tplSelect.value = currentTemplateId;
      renderFields(false);
    }

    function currentTemplate() {
      return (templates || []).find(t => t && t.template_id === currentTemplateId) || null;
    }

    /* ---- 按模板动态渲染字段 ---- */
    function renderFields(resetValues) {
      const tpl = currentTemplate();
      fieldsBox.replaceChildren();
      linkLine.hidden = true;
      resultBox.hidden = true;
      measureSelect.textContent = '';
      measureSelect.disabled = false;
      dtaRow.hidden = !(tpl && tpl.template_id === DTA_TEMPLATE);

      if (!tpl) {
        measureSelect.hidden = true;
        dirSelect.hidden = true;
        fieldsBox.append(el('p', { class: 'hint', text: T('选择研究设计后，按模板字段动态生成提取表单。') }));
        return;
      }

      /* 指标覆盖（仅声明支持的模板显示下拉，其余显示固定指标徽标） */
      const overrides = MEASURE_OVERRIDES[tpl.template_id];
      if (overrides) {
        measureSelect.hidden = false;
        measureSelect.setAttribute('aria-label', T('效应指标'));
        for (const m of overrides) measureSelect.append(el('option', { value: m, text: m, translate: 'no' }));
        measureSelect.value = overrides[0];
      } else {
        measureSelect.hidden = false;
        measureSelect.setAttribute('aria-label', T('效应指标'));
        measureSelect.append(el('option', { value: tpl.effect_measure, text: tpl.effect_measure, translate: 'no' }));
        measureSelect.value = tpl.effect_measure;
        measureSelect.disabled = true;
      }

      /* 效应方向：诊断 2×2 不适用（后端要求 null/not_applicable），其余模板必选 */
      const isDta = tpl.template_id === DTA_TEMPLATE;
      dirSelect.hidden = false;
      dirSelect.textContent = '';
      dirSelect.setAttribute('aria-label', T('效应方向'));
      if (isDta) {
        dirSelect.append(el('option', { value: 'not_applicable', text: T('不适用（诊断 2×2）') }));
        dirSelect.value = 'not_applicable';
        dirSelect.disabled = true;
      } else {
        dirSelect.append(el('option', { value: '', text: T('选择组间比较的方向') }));
        dirSelect.append(el('option', { value: 'first_vs_second', text: T('比较组前者相对于后者') }));
        dirSelect.append(el('option', { value: 'second_vs_first', text: T('比较组后者相对于前者') }));
        dirSelect.disabled = false;
      }

      const draft = resetValues ? null : draftFor(currentKey);
      const grid = el('div', { class: 'rf-x-fields' });
      for (const f of tpl.fields || []) {
        if (!f || !f.key) continue;
        const v = (draft && draft.values && f.key in draft.values) ? draft.values[f.key] : '';
        let ctrl;
        if (f.input_type === 'number') {
          ctrl = el('input', { class: 'input', type: 'number', step: 'any', value: String(v == null ? '' : v) });
          const min = f.validation && f.validation.min;
          const max = f.validation && f.validation.max;
          if (typeof min === 'number') ctrl.min = String(min);
          if (typeof max === 'number') ctrl.max = String(max);
          ctrl.addEventListener('input', safe(() => { clearInvalid(ctrl); updateLinkage(); }));
        } else {
          ctrl = el('input', { class: 'input', value: String(v == null ? '' : v) });
          ctrl.addEventListener('input', safe(() => clearInvalid(ctrl)));
        }
        ctrl.dataset.rfKey = f.key;
        ctrl.id = 'rfX-field-' + f.key;
        const cap = el('label', { for: ctrl.id },
          el('span', { text: T(f.label || f.key) }),
          f.unit ? el('span', { class: 'unit', text: '（' + f.unit + '）' }) : null,
          f.required ? el('span', { class: 'req', text: ' *' }) : null);
        grid.append(el('div', { class: 'rf-x-field' }, cap, ctrl));
      }
      fieldsBox.append(grid);
      if (draft) restoreCommonInputs(draft);
      updateLinkage();
    }

    /* ---- 字段联动 ---- */
    function numberValue(key) {
      const node = fieldsBox.querySelector('[data-rf-key="' + key + '"]');
      if (!node || node.value.trim() === '') return null;
      const n = Number(node.value);
      return Number.isFinite(n) ? n : null;
    }
    function updateLinkage() {
      const tpl = currentTemplate();
      if (tpl && tpl.template_id === DTA_TEMPLATE) {
        const nums = DTA_COUNT_KEYS.map(k => numberValue(k));
        const known = nums.filter(n => n !== null);
        linkLine.hidden = false;
        linkLine.textContent = known.length
          ? T('四格合计 N = {0}（患病组 {1} · 非患病组 {2}）',
            String(known.reduce((a, b) => a + b, 0)),
            String((nums[0] == null ? 0 : nums[0]) + (nums[2] == null ? 0 : nums[2])),
            String((nums[1] == null ? 0 : nums[1]) + (nums[3] == null ? 0 : nums[3])))
          : T('填写四格后显示合计。');
      } else {
        linkLine.hidden = true;
      }
    }

    /* ---- 校验（与后端 map_to_effect 同口径；标红不合格字段） ---- */
    function markInvalid(ctrl, msg) {
      ctrl.classList.add('rf-x-invalid');
      let err = ctrl.parentElement && ctrl.parentElement.querySelector('.err');
      if (!err) { err = el('span', { class: 'err' }); ctrl.after(err); }
      err.textContent = msg;
    }
    function clearInvalid(ctrl) {
      ctrl.classList.remove('rf-x-invalid');
      const err = ctrl.parentElement && ctrl.parentElement.querySelector('.err');
      if (err) err.textContent = '';
    }
    function clearAllInvalid() {
      fieldsBox.querySelectorAll('.rf-x-invalid').forEach(n => n.classList.remove('rf-x-invalid'));
      fieldsBox.querySelectorAll('.err').forEach(n => { n.textContent = ''; });
    }

    function validate() {
      clearAllInvalid();
      const tpl = currentTemplate();
      const messages = [];
      if (!tpl) { messages.push(T('请先选择研究设计。')); return { ok: false, messages, values: {} }; }
      const values = {};
      const isDta = tpl.template_id === DTA_TEMPLATE;
      for (const f of tpl.fields || []) {
        if (!f || !f.key) continue;
        const ctrl = fieldsBox.querySelector('[data-rf-key="' + f.key + '"]');
        if (!ctrl) continue;
        const raw = ctrl.value.trim();
        if (raw === '') {
          if (f.required) { markInvalid(ctrl, T('必填')); messages.push(T('{0} 为必填项。', T(f.label || f.key))); }
          continue;
        }
        if (f.input_type === 'number') {
          const n = Number(raw);
          if (!Number.isFinite(n)) {
            markInvalid(ctrl, T('必须是数字')); messages.push(T('{0} 必须是数字。', T(f.label || f.key)));
            continue;
          }
          const min = f.validation && f.validation.min;
          const max = f.validation && f.validation.max;
          if (typeof min === 'number' && n < min) {
            markInvalid(ctrl, T('不能小于 {0}', String(min)));
            messages.push(T('{0} 不能小于 {1}。', T(f.label || f.key), String(min)));
            continue;
          }
          if (typeof max === 'number' && n > max) {
            markInvalid(ctrl, T('不能大于 {0}', String(max)));
            messages.push(T('{0} 不能大于 {1}。', T(f.label || f.key), String(max)));
            continue;
          }
          if (isDta && DTA_COUNT_KEYS.includes(f.key) && (Number.isInteger(n) === false || n < 0)) {
            markInvalid(ctrl, T('必须是非负整数'));
            messages.push(T('{0} 必须是非负整数。', T(f.label || f.key)));
            continue;
          }
          values[f.key] = n;   /* 整数值浮点由后端自动转 int（如 "3e2"→300，不能 parseInt） */
        } else {
          values[f.key] = raw;
        }
      }
      /* 联动校验：survival_hr 的 CI 须包含 HR（后端 hazard_ratio_ci 同口径拒绝） */
      if (tpl.template_id === 'survival_hr') {
        const hr = numberValue('hr'), lo = numberValue('ci_low'), hi = numberValue('ci_high');
        if (hr !== null && lo !== null && hi !== null && !(lo <= hr && hr <= hi)) {
          const ctrl = fieldsBox.querySelector('[data-rf-key="hr"]');
          if (ctrl) markInvalid(ctrl, T('CI 须包含 HR'));
          messages.push(T('HR 必须位于 CI 下限与上限之间。'));
        }
      }
      if (!isDta) {
        if (!studyInput.value.trim()) { messages.push(T('请填写研究编号（与数据分析页一致）。')); }
        if (!cmpInput.value.trim() || !outInput.value.trim() || !tpInput.value.trim()) {
          messages.push(T('提取结果入库需要比较方向、结局与时间点。'));
        }
        if (!dirSelect.value) messages.push(T('请显式选择效应方向（试验组 vs 对照组）如何对应到比较方向。'));
      } else {
        if (!studyInput.value.trim()) messages.push(T('请填写研究编号。'));
        for (const [name, node] of Object.entries(dtaInputs)) {
          if (!node.value.trim()) messages.push(T('诊断 2×2 入库需要 {0}。', name));
        }
        const nums = DTA_COUNT_KEYS.map(k => numberValue(k));
        if (nums.every(n => n !== null)) {
          if (nums[0] + nums[2] <= 0 || nums[1] + nums[3] <= 0) {
            messages.push(T('患病组与非患病组都必须有受试者。'));
          }
        }
      }
      return { ok: messages.length === 0, messages, values };
    }

    /* ---- 草稿（切篇暂存，内存级） ---- */
    function draftKey(key) {
      return (currentTaskId || '') + '|' + (key || '');
    }
    function draftFor(key) { return drafts.get(draftKey(key)) || null; }
    function stashDraft(key) {
      if (!key || !currentTemplateId) return;
      const values = {};
      fieldsBox.querySelectorAll('[data-rf-key]').forEach(node => { values[node.dataset.rfKey] = node.value; });
      drafts.set(draftKey(key), {
        templateId: currentTemplateId,
        values,
        common: commonInputState(),
      });
    }
    function commonInputState() {
      return {
        study_id: studyInput.value, comparison: cmpInput.value, outcome: outInput.value,
        timepoint: tpInput.value, effect_direction: dirSelect.value, source_locator: locInput.value,
        measure: measureSelect.value,
        index_test: dtaInputs.index_test.value, target_condition: dtaInputs.target_condition.value,
        threshold: dtaInputs.threshold.value, reference_standard: dtaInputs.reference_standard.value,
      };
    }
    function restoreCommonInputs(draft) {
      if (!draft || !draft.common) return;
      const c = draft.common;
      studyInput.value = c.study_id || '';
      cmpInput.value = c.comparison || '';
      outInput.value = c.outcome || '';
      tpInput.value = c.timepoint || '';
      if (!dirSelect.disabled) dirSelect.value = c.effect_direction || '';
      locInput.value = c.source_locator || '';
      if (c.measure && Array.from(measureSelect.options).some(o => o.value === c.measure) && !measureSelect.disabled) {
        measureSelect.value = c.measure;
      }
      dtaInputs.index_test.value = c.index_test || '';
      dtaInputs.target_condition.value = c.target_condition || '';
      dtaInputs.threshold.value = c.threshold || '';
      dtaInputs.reference_standard.value = c.reference_standard || '';
    }

    /* ---- 当前文献（编码队列） ---- */
    function currentArticle() {
      if (typeof S === 'undefined' || !Array.isArray(S.cbQueue)) return null;
      const cur = S.cbQueue[S.cbIdx];
      return cur || null;
    }
    function onArticleChanged() {
      try {
        const cur = currentArticle();
        const key = cur ? cur.zotero_key : null;
        if (key === currentKey) { headSub.textContent = cur ? (cur.title || key) : T('未载入文献'); return; }
        if (currentKey) stashDraft(currentKey);
        version++;
        applyBtn.disabled = false;
        currentKey = key;
        restoreCommonInputs({common: {}});
        currentTemplateId = '';
        tplSelect.value = '';
        headSub.textContent = cur ? (cur.title || key) : T('未载入文献');
        statusLine.textContent = '';
        resultBox.hidden = true;
        const draft = draftFor(key);
        if (draft && draft.templateId && (templates || []).some(t => t && t.template_id === draft.templateId)) {
          currentTemplateId = draft.templateId;
          tplSelect.value = draft.templateId;
        }
        renderFields(false);
      } catch (e) { /* 单点故障 */ }
    }

    /* ---- 应用提取：校验 → POST → 展示结果 ---- */
    async function applyExtraction() {
      let ticket;
      try {
        resultBox.hidden = true;
        const cur = currentArticle();
        if (!cur || !cur.zotero_key) { statusLine.textContent = T('当前没有可提取的文献。'); return; }
        if (typeof api !== 'function' || typeof taskApi !== 'function') return;
        const check = validate();
        if (!check.ok) { statusLine.textContent = check.messages.join('\n'); toast(T('校验未通过，请修正标红字段。'), 'warn'); return; }
        const tpl = currentTemplate();
        const isDta = tpl.template_id === DTA_TEMPLATE;
        const values = check.values;
        const overrides = MEASURE_OVERRIDES[tpl.template_id];
        if (overrides && measureSelect.value) values.measure = measureSelect.value;

        const body = {
          template_id: tpl.template_id,
          study_id: studyInput.value.trim(),
          values: values,
          source_locator: locInput.value.trim(),
        };
        if (isDta) {
          body.effect_direction = 'not_applicable';
          body.index_test = dtaInputs.index_test.value.trim();
          body.target_condition = dtaInputs.target_condition.value.trim();
          body.threshold = dtaInputs.threshold.value.trim();
          body.reference_standard = dtaInputs.reference_standard.value.trim();
        } else {
          body.comparison = cmpInput.value.trim();
          body.outcome = outInput.value.trim();
          body.timepoint = tpInput.value.trim();
          body.effect_direction = dirSelect.value;
        }

        ticket = ++version;
        applyBtn.disabled = true;
        statusLine.textContent = T('正在计算效应量并保存…');
        try {
          const d = await api(taskApi() + '/extraction/' + encodeURIComponent(cur.zotero_key) + '/apply',
            { method: 'POST', body: body });
          if (ticket !== version) return;
          showResult(d, tpl);
          statusLine.textContent = '';
          stashDraft(cur.zotero_key);
        } finally {
          if (ticket === version) applyBtn.disabled = false;
        }
      } catch (e) {
        if (ticket !== undefined && ticket !== version) return;
        statusLine.textContent = T('保存失败：') + ((e && e.message) || '');
        toast(T('保存失败：') + ((e && e.message) || ''), 'warn');
      }
    }

    function fmtNum(v) {
      const n = Number(v);
      return Number.isFinite(n) ? String(Number(n.toPrecision(4))) : '—';
    }

    function showResult(d, tpl) {
      const lines = [];
      const measure = d && d.measure ? d.measure : (tpl ? tpl.effect_measure : '');
      if (d && d.estimate != null && d.se != null) {
        lines.push(el('b', { text: T('{0} = {1}, SE = {2}', measure, fmtNum(d.estimate), fmtNum(d.se)) }));
      } else {
        lines.push(el('b', { text: T('{0}（本次无可计算的单数值估计）', measure) }));
      }
      if (d && d.se_scale === 'log') {
        lines.push(el('p', { class: 'hint', style: 'margin:4px 0 0', text: T('比值类指标（RR/OR/HR/DOR）的 SE 已按对数尺度换算后保存。') }));
      }
      if (d && d.dor_unavailable) {
        lines.push(el('p', { class: 'hint', style: 'margin:4px 0 0', text: T('含零单元格：DOR 无定义（未做连续性校正），原始计数已入库供 bivariate 合成。') }));
      }
      if (d && d.storage === 'review_effects') {
        lines.push(el('p', { class: 'hint', style: 'margin:4px 0 0', text: T('已保存到分析池（review_effects，结果编号 {0}），可在「数据分析」页合并。', String(d.result_id == null ? '—' : d.result_id)) }));
      } else if (d && d.storage === 'review_dta_results') {
        lines.push(el('p', { class: 'hint', style: 'margin:4px 0 0', text: T('已保存到诊断准确性结果库（review_dta_results），供 bivariate 合成。') }));
      }
      lines.push(el('p', { class: 'hint', style: 'margin:4px 0 0', text: T('提取完成后可到「数据分析」页做偏倚风险（RoB）评估。') }));
      resultBox.replaceChildren.apply(resultBox, lines);
      resultBox.hidden = false;
      toast(T('✓ 效应量已计算并保存（{0}）', measure), 'inc');
    }

    /* ---- 语言切换：重填静态文案 ---- */
    function refreshLang() {
      try {
        headTitle.textContent = T('提取');
        panel.setAttribute('aria-label', T('结构化数据提取'));
        L.tplCap.textContent = T('研究设计');
        tplSelect.setAttribute('aria-label', T('研究设计（提取模板）'));
        checkBtn.textContent = T('校验');
        applyBtn.textContent = T('计算效应量并保存');
        studyInput.placeholder = T('研究编号（与数据分析页一致）');
        cmpInput.placeholder = T('比较方向，如：治疗组与对照组');
        outInput.placeholder = T('结局');
        tpInput.placeholder = T('时间点');
        locInput.placeholder = T('原文页码、表或图（可选）');
        renderTemplateOptions();
      } catch (e) { /* 单点故障 */ }
    }

    /* ---- 桥接：进入编码页后等待队列就绪（loadBench 完成） ---- */
    function waitForCoding() {
      let tries = 0;
      (function poll() {
        try {   /* setTimeout 回调脱离 safe() 包裹，自行捕获 */
          if (typeof S !== 'undefined' && S.task && Array.isArray(S.cbQueue) && S.cbQueue.length) {
            loadTemplates().then(onArticleChanged).catch(() => {});
            return;
          }
          if (++tries > 60) return;                 // ~15s：无任务/加载失败则放弃
          setTimeout(poll, 250);
        } catch (e) { /* 单点故障 */ }
      })();
    }

    /* ---- renderBench 重绘钩子：#cbForms childList 变化 → 同步当前文献 ---- */
    if (typeof MutationObserver !== 'undefined') {
      new MutationObserver(() => {
        try { onArticleChanged(); } catch (e) { /* 单点故障 */ }
      }).observe(forms, { childList: true });
    }

    /* ---- 事件接入：规格事件 + 既有桥接 ---- */
    document.addEventListener('reviewflow:coding-loaded', safe(() => { loadTemplates().then(onArticleChanged).catch(() => {}); }));
    document.addEventListener('reviewflow:workspace-changed', safe(e => { if (e && e.detail === 'bench') waitForCoding(); }));
    function onTaskChanged() {
      const taskId = S.task?.task_id || null;
      if (taskId === currentTaskId) return;
      stashDraft(currentKey);
      version++;
      currentTaskId = taskId;
      if (!taskId) drafts.clear();
      currentKey = null; currentTemplateId = '';
      restoreCommonInputs({common: {}});
      applyBtn.disabled = false;
      statusLine.textContent = ''; resultBox.hidden = true;
      fieldsBox.replaceChildren(); tplSelect.value = '';
      headSub.textContent = T('未载入文献');
    }
    document.addEventListener('reviewflow:task-loaded', safe(onTaskChanged));
    document.addEventListener('reviewflow:task-context-changed', safe(onTaskChanged));
    document.addEventListener('reviewflow:language-changed', safe(refreshLang));

    /* 首次加载时已停在编码页（hash 直达）：立即激活 */
    if (typeof S !== 'undefined' && S.task && S.page === 'bench') waitForCoding();
  } catch (e) { /* 单点故障：模块初始化失败不影响主应用与 /api/health */ }
})();
