(function () {
  const labels = $('#anRobSensitivityLabels'), host = $('#anRobSensitivityResult'), run = $('#anRobSensitivityRun');
  let generation = 0, timer;
  const clear = () => { generation++; clearInterval(timer); host.replaceChildren(); run.disabled = false; };
  const choices = { 'RoB 2':['low','some concerns','high'],
    'ROBINS-I':['low','moderate','serious','critical','no information'] };
  function render() {
    labels.replaceChildren(); clear();
    for (const value of choices[anValue('anRobSensitivityFramework')] || []) {
      const input = el('input', { type:'checkbox', value, 'data-rob-exclude':'' });
      labels.append(el('label', { style:'display:inline-block;margin-right:1rem' }, input, ' '+T('Exclude {0}',T(value))));
    }
  }
  $('#anRobSensitivityFramework').addEventListener('change', render);
  labels.addEventListener('change', clear);
  ['anComparison', 'anOutcome', 'anTimepoint', 'anMeasure', 'anRobSensitivityModel', 'anRobSensitivityCi']
    .forEach(id => $('#'+id).addEventListener('input', clear));
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', () => {
    ['anRobSensitivityFramework', 'anRobSensitivityModel', 'anRobSensitivityCi']
      .forEach(id => $('#'+id).value = '');
    render();
  });
  run.addEventListener('click', async () => {
    clear(); const ticket = generation;
    try {
      if (!anValue('anRobSensitivityFramework')) throw new Error(T('Choose a risk-of-bias framework.'));
      if (!anValue('anRobSensitivityModel')) throw new Error(T('Choose a synthesis model.'));
      if (!anValue('anRobSensitivityCi')) throw new Error(T('Choose a confidence interval method.'));
      if (anValue('anRobSensitivityModel') === 'fixed' && anValue('anRobSensitivityCi') === 'hksj')
        throw new Error(T('Modified HKSJ requires a random-effects model.'));
      const exclude_judgements = [...labels.querySelectorAll('[data-rob-exclude]:checked')].map(input => input.value);
      if (!exclude_judgements.length) throw new Error(T('Choose at least one overall risk-of-bias judgment to exclude.'));
      const body = { comparison:anValue('anComparison'), outcome:anValue('anOutcome'),
        timepoint:anValue('anTimepoint'), measure:anValue('anMeasure'),
        framework:anValue('anRobSensitivityFramework'), exclude_judgements,
        model:anValue('anRobSensitivityModel'), ci_method:anValue('anRobSensitivityCi') };
      run.disabled = true;
      const started = performance.now();
      const progress = () => { if (ticket === generation) host.textContent =
        T("Calculating risk-of-bias sensitivity… {0} s elapsed. No intermediate progress is available.", ((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress, 500);
      const result = await api(taskApi() + '/analysis/risk-of-bias/sensitivity', { method:'POST', body });
      if (ticket !== generation) return;
      host.replaceChildren();
      const fmt = value => Number(value).toPrecision(5);
      const table = el('table');
      table.append(el('thead', {}, el('tr', {}, ...['Analysis', 'Studies', 'Pooled', '95% CI', 'Excluded IDs'].map(text => el('th', { text:T(text) })))));
      const rows = el('tbody');
      for (const [label, item] of [[T('All assessed studies'), result.all_studies], [T('After exclusion'), result.exclusion_sensitivity]]) {
        rows.append(el('tr', {}, ...[label, String(item.n_studies), fmt(item.pooled),
          `[${fmt(item.ci_low)}, ${fmt(item.ci_high)}]`, item.excluded_study_ids.join(', ') || '—'].map(text => el('td', { text }))));
      }
      table.append(rows); host.append(table);
      host.append(el('p', { class:'hint', text:T("Framework {0}; excluded overall labels: {1}. Sources: {2}. Do not interpret exclusion as proof of bias; cite the assessment and synthesis methods.", result.framework,result.excluded_judgements.map(v=>T(v)).join(', '),Object.entries(result.all_studies.study_sources).map(([id,source])=>`${id} (${source.effect_source_key})`).join('; ')) }));
      host.append(el('p', { class:'hint', text:(result.method_sources || []).join(' ') }),
        anAuditReportButtons(taskApi() + '/analysis/risk-of-bias/sensitivity', null,
          'reviewflow_rob_sensitivity_audit', () => ticket === generation, body));
    } catch (error) { if (ticket === generation) { host.textContent = error.message; toast(error.message, 'warn'); } }
    finally { if (ticket === generation) { clearInterval(timer); run.disabled = false; } }
  });
  render();
})();
