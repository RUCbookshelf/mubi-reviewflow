/* ReviewFlow 2.1.5 · Table 1 and Summary of Findings export and judgment. */
(function () {
  'use strict';

  const exportPage = document.querySelector('section[data-p="export"]');
  if (!exportPage || !window.AppraisalWorkspace) return;
  const { canAssess, fileResponse, popup, grid, select, toolsSelect, node, raw, tr, button, field, statusLine } = AppraisalWorkspace;
  const scope = () => [S.task?.task_id, S.taskVersion, S.user?.user_id, S.screener].join('|');
  const components = ['rob', 'inconsistency', 'imprecision', 'publication_bias'];
  const cellNames = ['label', 'n_studies', 'effect', ...components, 'certainty'];
  const formats = ['csv', 'docx'];
  const levels = ['none', 'serious', 'very_serious', 'very_very_serious'];
  const grades = ['high', 'moderate', 'low', 'very_low'];
  const controlsFor = (prefix, options, value) => select(options.map(option => [option, prefix + option]), value);
  const heading = (key, ...children) => node('fieldset', null, {}, tr('legend', key), children);
  const expanded = (key, ...children) => node('details', null, {}, tr('summary', key), children);
  const resultMessage = (error, fallback) => error?.message || T(fallback);
  const componentLabel = component => 'tables.sof.judgment.component_' + component;
  const formatEvidence = signal => Object.entries(signal || {}).filter(([key, value]) =>
    key !== 'available' && ['string', 'number', 'boolean'].includes(typeof value)).map(([key, value]) => key + ' = ' + value).join(' · ');

  function choices(items, keyOf, labelOf, changed, ordered = false) {
    const list = node('div');
    const empty = tr('p', 'tables.all_by_default', { class: 'hint' });
    const rank = new Map();
    list.append(empty);
    for (const item of items) {
      const key = keyOf(item);
      const check = node('input', null, { type: 'checkbox', value: key });
      const position = node('span', '');
      check.addEventListener('change', () => {
        if (check.checked) rank.set(key, position);
        else {
          rank.delete(key);
          position.textContent = '';
        }
        if (ordered) [...rank.values()].forEach((marker, index) => { marker.textContent = String(index + 1); });
        changed();
      });
      list.append(node('label', null, { class: 'rf-tables-choice' }, check, position, raw('span', labelOf(item))));
    }
    return { list, values: () => Array.from(rank.keys()) };
  }

  function table1Preview(data) {
    const names = [T('tables.authors_year'), T('appraisal.article_title'), ...(data.columns || []).map(column => column.column)];
    const rows = (data.rows || []).map(row => [row.authors_year, row.title, ...(data.columns || []).map(column => row.values[column.column])]);
    return node('div', null, {}, grid(names, rows), tr('p', 'tables.empty_cell_footnote', { class: 'hint' }), noteList(data.notes));
  }

  function noteList(notes) {
    return node('ul', null, { class: 'hint' }, (notes || []).map(line => raw('li', line)));
  }

  function suggestionsBlock(values, overall, showEvidence) {
    const list = node('dl');
    for (const component of components) {
      const value = values?.[component];
      const level = typeof value === 'string' ? value : value?.level;
      const availability = value?.signal?.available;
      const reason = showEvidence ? formatEvidence(value?.signal) : '';
      list.append(tr('dt', componentLabel(component)), node('dd', level && availability !== false ? T('tables.level.' + level) : '', {},
        reason ? raw('small', ' · ' + reason) : null));
    }
    list.append(tr('dt', 'tables.sof.judgment.overall'), node('dd', overall ? T('appraisal.rating.' + overall) : ''));
    return list;
  }

  function judgmentBlock(judgment, privileged, allowPrevious = true) {
    const area = node('div', null, {}, tr('span', 'tables.sof.judged', { class: 'chip' }),
      tr('span', judgment.source === 'adopted' ? 'tables.sof.judged_by_adopted' : 'tables.sof.judged_by_manual', { class: 'chip' }),
      suggestionsBlock(judgment.components, judgment.overall, false));
    if (judgment.note) area.append(raw('p', judgment.note));
    if (judgment.decided_at) area.append(raw('time', new Date(judgment.decided_at).toLocaleString()));
    const snapshot = judgment.adopted_snapshot;
    if (snapshot) {
      const evidence = privileged && snapshot.evidence_redacted !== true;
      const contents = suggestionsBlock(snapshot.components, snapshot.overall, evidence);
      if (snapshot.initial_design) contents.append(tr('dt', 'tables.sof.design'), tr('dd', 'tables.design.' + snapshot.initial_design));
      if (evidence && snapshot.n_studies != null)
        contents.append(tr('dt', 'tables.sof.n_studies'), raw('dd', snapshot.n_studies));
      if (snapshot.params) {
        const params = snapshot.params;
        if (params.model) contents.append(tr('dt', 'tables.sof.model'), tr('dd', 'tables.model.' + params.model));
        if (params.ci_method) contents.append(tr('dt', 'tables.sof.ci'), tr('dd', 'tables.ci.' + params.ci_method));
        if (params.rob_tool) contents.append(tr('dt', 'appraisal.tool'), raw('dd', params.rob_tool));
      }
      area.append(expanded('tables.sof.judgment.snapshot', contents,
        evidence ? null : tr('p', 'tables.evidence_redacted')));
    }
    if (allowPrevious && judgment.prev)
      area.append(expanded('tables.sof.judgment.prev', judgmentBlock(judgment.prev, privileged, false)));
    return area;
  }

  function openJudgment(row, parameters, wizard) {
    const { dialog, body } = popup('tables.sof.judgment.title');
    const valid = () => wizard.valid() && dialog.open;
    const params = {
      model: parameters.model, ci_method: parameters.ci_method,
      rob_tool: parameters.rob_tool || null, initial_design: parameters.initial_design
    };
    const note = node('textarea', null, { class: 'input', rows: 3, translate: 'no' });
    note.value = row.judgment?.note || '';
    const source = controlsFor('tables.sof.judgment.', ['manual', 'adopt'], 'manual');
    const chosen = Object.fromEntries(components.map(component => [
      component, controlsFor('tables.level.', ['', ...levels], row.judgment?.components?.[component] || '')
    ]));
    const overall = select([['', 'tables.level.'], ...grades.map(value => [value, 'appraisal.rating.' + value])], row.judgment?.overall || '');
    const manual = node('div', null, {}, components.map(key => field(componentLabel(key), chosen[key])), field('tables.sof.judgment.overall', overall));
    const message = statusLine();
    const save = button('tables.sof.judgment.submit', async () => {
      if (!valid() || save.disabled) return;
      const adopt = source.value === 'adopt';
      const payload = { comparison: row.comparison, outcome: row.outcome, timepoint: row.timepoint, measure: row.measure, note: note.value };
      if (adopt) Object.assign(payload, params, { adopt_from_suggestion: true });
      else {
        payload.components = Object.fromEntries(Object.entries(chosen).filter(([, input]) => input.value).map(([key, input]) => [key, input.value]));
        if (overall.value) payload.overall = overall.value;
      }
      const controls = [source, ...Object.values(chosen), overall, note, save];
      controls.forEach(control => { control.disabled = true; });
      message.textContent = T('tables.generating');
      try {
        await api(wizard.path + '/sof/judgment', { method: 'PUT', body: payload });
        if (!wizard.valid()) return;
        if (dialog.open) dialog.close();
        await wizard.refresh();
      } catch (error) {
        if (valid()) message.textContent = resultMessage(error, 'tables.unavailable');
      } finally {
        controls.forEach(control => { control.disabled = false; });
      }
    }, true);
    source.addEventListener('change', () => { manual.hidden = source.value === 'adopt'; message.textContent = ''; });
    const current = row.detail?.grade;
    body.append(raw('b', row.label),
      tr('h3', 'tables.current_suggestion'),
      suggestionsBlock(current?.suggestions, current?.quality?.final_quality, true),
      field('tables.sof.judgment.source', source), manual, field('tables.sof.judgment.note', note),
      tr('p', 'tables.sof.judgment.overwrite_hint', { class: 'hint' }), message,
      node('div', null, { class: 'row' }, save, button('tables.sof.judgment.cancel', () => dialog.close())));
  }

  function sofPreview(data, params, wizard, privileged) {
    const cols = [T('tables.sof.label'), T('tables.sof.n_studies'), T('tables.sof.effect'),
      ...components.map(key => T(componentLabel(key))), T('tables.sof.judgment.overall')];
    const rows = (data.rows || []).map(row => cellNames.map(key => {
      if (!row.judgment || ![...components, 'certainty'].includes(key)) return row.cells?.[key] ?? '';
      const value = key === 'certainty' ? row.judgment.overall : row.judgment.components?.[key];
      return node('span', null, {}, tr('span', (key === 'certainty' ? 'appraisal.rating.' : 'tables.level.') + value),
        tr('span', 'tables.sof.judged', { class: 'chip' }));
    }));
    const area = node('div', null, {}, grid(cols, rows), noteList(data.footnotes));
    for (const row of data.rows || []) {
      const audit = node('section', null, { class: 'card rf-tables-audit' }, raw('h3', row.label));
      audit.append(raw('p', row.comparison), noteList(row.footnotes));
      const current = row.detail?.grade;
      audit.append(expanded('tables.current_suggestion',
        suggestionsBlock(current?.suggestions, current?.quality?.final_quality, privileged)));
      if (row.judgment) audit.append(judgmentBlock(row.judgment, privileged));
      if (privileged) audit.append(button('tables.sof.judgment.title', () => openJudgment(row, params, wizard)));
      area.append(audit);
    }
    return area;
  }

  function openWizard(sof) {
    if (!S.task) { toast(T('请先选择任务。'), 'warn'); return; }
    const identity = scope(), taskId = S.task.task_id;
    const path = taskApi() + '/tables';
    const resource = path + (sof ? '/sof' : '/characteristics');
    const title = sof ? 'tables.sof.title' : 'tables.table1.title';
    const { dialog, body } = popup(title);
    const status = statusLine(), preview = node('div');
    const format = controlsFor('tables.format.', formats, 'csv');
    const valid = () => dialog.open && scope() === identity;
    const formatKey = sof ? 'tables.sof.format' : 'tables.table1.format';
    let payload, privileged = false, busy = false;
    let request = 0;
    const previewButton = button('tables.preview', () => refresh());
    const downloadButton = button('tables.download', () => download(), true);
    const wizard = { path, valid, refresh };
    function changed() {
      ++request;
      preview.replaceChildren();
      status.textContent = '';
    }
    async function run(action) {
      if (!valid() || !payload || busy) return;
      const version = ++request;
      const same = () => valid() && version === request;
      busy = true;
      previewButton.disabled = downloadButton.disabled = true;
      status.textContent = T('tables.generating');
      try {
        await action(payload(), same);
        if (same()) status.textContent = '';
      } catch (error) {
        if (same()) {
          status.textContent = resultMessage(error, 'tables.unavailable');
          if (action.name === 'previewRequest')
            preview.replaceChildren(tr('p', 'tables.unavailable', { class: 'hint', role: 'alert' }));
        }
      } finally {
        busy = false;
        previewButton.disabled = downloadButton.disabled = false;
      }
    }
    function refresh() {
      return run(async function previewRequest(body, same) {
        const data = await api(resource + '?detail=1', { method: 'POST', body });
        if (!same()) return;
        preview.replaceChildren(sof ? sofPreview(data, body, wizard, privileged) : table1Preview(data));
      });
    }
    function download() {
      return run((body, same) => fileResponse(resource, { method: 'POST', body },
        (sof ? 'sof-' : 'table1-') + taskId + '.' + body.format, same));
    }
    async function init() {
      body.replaceChildren(status);
      status.textContent = T('tables.generating');
      try {
        if (sof) {
          const [baseline, canWrite] = await Promise.all([
            api(resource + '?detail=1', { method: 'POST', body: {} }), canAssess()
          ]);
          if (!valid()) return;
          privileged = canWrite;
          const outcomes = [...new Set((baseline.rows || []).map(row => row.outcome))];
          const chosen = choices(outcomes, name => name, name => name, changed);
          const model = controlsFor('tables.model.', ['fixed', 'random', 'random_pm', 'random_reml'], 'fixed');
          const ci = controlsFor('tables.ci.', ['normal', 'hksj'], 'normal');
          const design = controlsFor('tables.design.', ['rct', 'observational', 'diagnostic'], 'rct');
          const params = heading('tables.sof.params', field('tables.sof.model', model), field('tables.sof.ci', ci), field('tables.sof.design', design));
          let rob;
          if (privileged) {
            try {
              const registry = await api(taskApi() + '/appraisal/tools');
              if (!valid()) return;
              if (!registry.available) throw Error(RFServerMessage(registry.message_key, registry.params) || T('appraisal.registry_unavailable'));
              rob = toolsSelect(registry.tools, '', 'tables.rob_none');
              rob.addEventListener('change', changed);
              params.append(field(componentLabel('rob'), rob), tr('p', 'tables.rob_tool_hint', { class: 'hint' }));
            } catch (error) {
              params.append(raw('p', resultMessage(error, 'appraisal.registry_unavailable')));
            }
          }
          if (!valid()) return;
          [model, ci, design].forEach(control => control.addEventListener('change', changed));
          payload = () => ({ outcomes: chosen.values(), model: model.value, ci_method: ci.value,
            initial_design: design.value, ...(rob?.value ? { rob_tool: rob.value } : {}), format: format.value });
          body.append(heading('tables.sof.outcomes', chosen.list), params);
          preview.replaceChildren(sofPreview(baseline, payload(), wizard, privileged));
        } else {
          const scheme = await api(taskApi() + '/coding/scheme' + scrQ());
          if (!valid()) return;
          const chosen = choices(scheme.dimensions || [], item => item.name, item => item.name, changed, true);
          const inclusion = controlsFor('tables.inclusion.', ['stage1_stage2', 'stage1', 'matrix'], 'stage1_stage2');
          const sort = controlsFor('tables.sort.', ['import_order', 'author_year'], 'import_order');
          [inclusion, sort].forEach(control => control.addEventListener('change', changed));
          payload = () => ({ fields: chosen.values(), inclusion: inclusion.value, sort: sort.value, format: format.value });
          body.append(heading('tables.table1.fields', chosen.list), field('tables.table1.inclusion', inclusion),
            field('tables.table1.sort', sort));
        }
        status.textContent = '';
        body.append(field(formatKey, format), node('div', null, { class: 'row' }, previewButton, downloadButton), status, preview);
      } catch (error) {
        if (!valid()) return;
        status.textContent = resultMessage(error, 'tables.unavailable');
        body.append(tr('p', 'tables.unavailable', { class: 'hint' }), button('重试', init));
      }
    }
    init();
  }

  const title = tr('h3', 'tables.title');
  const table1 = button('tables.table1.title', () => openWizard(false));
  const sof = button('tables.sof.title', () => openWizard(true));
  const section = node('section', null, { class: 'card', id: 'rfTablesEntry' }, title,
    node('div', null, { class: 'rf-tables-cards' },
      node('article', null, { class: 'card' }, table1),
      node('article', null, { class: 'card' }, sof)));
  exportPage.append(section);
  document.addEventListener('reviewflow:language-changed', () => {
    title.textContent = T('tables.title');
    table1.textContent = T('tables.table1.title');
    sof.textContent = T('tables.sof.title');
  });
})();
