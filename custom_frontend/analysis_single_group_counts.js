(function () {
let counts = [];
const base = () => taskApi() + '/analysis/single-group/counts';
const resultHost = $('#anSingleResult'), auditHost = $('#anSingleAudit'), run = $('#anSingleRun');
let generation = 0, loadVersion = 0, timer = null;
const clearResult = () => { generation++; clearInterval(timer); resultHost.textContent = ''; auditHost.replaceChildren(); run.disabled = false; };
['anComparison','anOutcome','anTimepoint','anSingleKind'].forEach(id =>
  $('#'+id).addEventListener('input', clearResult));
document.addEventListener('reviewflow:analysis-core-loaded', clearResult);

function render() {
  const holder = $('#anSingleVariants'); holder.replaceChildren();
  const context = [anValue('anEffectStudy'), anValue('anComparison'),
    anValue('anOutcome'), anValue('anTimepoint'), anValue('anSingleKind')];
  if (context.some(value => !value)) return;
  const rows = counts.filter(row => [row.study_id, row.comparison, row.outcome,
    row.timepoint, row.kind].every((value, index) => value === context[index]));
  if (!rows.length) return;
  const table = el('table');
  table.append(el('thead', {}, el('tr', {}, ...['Use','ID','Events','Exposure','Unit','Report','Location'].map(label => el('th', { text:T(label) })))));
  const body = el('tbody');
  rows.forEach(row => {
    const action = row.selected ? el('strong', { text:T('Selected') }) :
      el('button', { class:'btn b-out', type:'button', text:T('Select'),
        'aria-label':T("Select count result {0}", row.result_id), onclick:() => anButtonWithProgress(action, async () => {
            clearResult();
            await api(base() + `/${row.result_id}/select`, { method:'POST' });
            await loadCounts();
        }) });
    body.append(el('tr', {}, el('td', {}, action),
      ...[row.result_id, row.events, row.kind === 'proportion' ? row.total : row.person_time,
        row.time_unit || '—', row.source_key, row.source_locator || '—']
        .map(value => el('td', { text:String(value) }))));
  });
  table.append(body); holder.append(table);
}
async function loadCounts() {
  const taskId = S.task?.task_id, version = ++loadVersion;
  try {
    const response = await api(base());
    if (version !== loadVersion || S.task?.task_id !== taskId) return;
    counts = response.results;
    render();
    document.dispatchEvent(new CustomEvent('reviewflow:single-counts-loaded'));
  } catch (error) { if (version === loadVersion && S.task?.task_id === taskId) $('#anSingleVariants').textContent = error.message; }
}
function updateKind() {
  const kind = anValue('anSingleKind'), rate = kind === 'rate';
  $('#anSingleTotal').style.display = kind === 'proportion' ? '' : 'none';
  $('#anSingleTime').style.display = rate ? '' : 'none';
  $('#anSingleUnit').style.display = rate ? '' : 'none';
  render();
}
$('#anSingleKind').addEventListener('change', updateKind);
document.addEventListener('reviewflow:analysis-task-changed', () => {
  loadVersion++; counts = [];
  for (const id of ['anSingleKind','anSingleEvents','anSingleTotal','anSingleTime','anSingleUnit']) $('#'+id).value = '';
  $('#anSingleAppend').checked = false;
  $('#anSingleVariants').replaceChildren();
  clearResult(); updateKind();
});
for (const id of ['anEffectStudy','anComparison','anOutcome','anTimepoint'])
  $('#'+id).addEventListener('input', render);
$('#anSingleSave').addEventListener('click', () => anButtonWithProgress($('#anSingleSave'), async () => {
  clearResult(); const ticket = generation;
  try {
    if (!anValue('anSingleKind')) throw new Error(T('Choose a one-group count type.'));
    const rate = anValue('anSingleKind') === 'rate';
    const events = anValue('anSingleEvents');
    const exposure = anValue(rate ? 'anSingleTime' : 'anSingleTotal');
    if (events === '' || exposure === '') throw new Error(T('Events and exposure are required'));
    const body = { study_id:anValue('anEffectStudy'), comparison:anValue('anComparison'),
      outcome:anValue('anOutcome'), timepoint:anValue('anTimepoint'),
      kind:anValue('anSingleKind'), events:Number(events),
      source_key:anValue('anSource'), source_locator:anValue('anSourceLocator'),
      append:$('#anSingleAppend').checked };
    if (rate) { body.person_time = Number(exposure); body.time_unit = anValue('anSingleUnit'); }
    else body.total = Number(exposure);
    await api(base(), { method:'POST', body });
    await loadCounts();
    if (ticket === generation) resultHost.textContent = T('原始计数已保存；请检查来源报告和分析单位。');
  } catch (error) { if (ticket === generation) { resultHost.textContent = error.message; toast(error.message, 'warn'); } }
}));
run.addEventListener('click', async () => {
  clearResult(); const ticket = generation;
  try {
    if (!anValue('anSingleKind')) throw new Error(T('Choose a one-group count type.'));
    const q = new URLSearchParams({ comparison:anValue('anComparison'), outcome:anValue('anOutcome'),
      timepoint:anValue('anTimepoint'), kind:anValue('anSingleKind') });
    run.disabled = true;
    const started = performance.now();
    const progress = () => { if (ticket === generation) resultHost.textContent =
      T("Fitting single-group count models… {0} s elapsed. The random model uses numerical integration; no intermediate progress is available.", ((performance.now()-started)/1000).toFixed(1)); };
    progress(); timer = setInterval(progress,500);
    const result = await api(taskApi() + '/analysis/single-group/synthesis?' + q);
    if (ticket !== generation) return;
    const fmt = value => value == null ? T('unavailable') : Number(value).toPrecision(5);
    const interval = item => item.ci_95 ? item.ci_95.map(fmt).join(' – ') : T('unavailable');
    resultHost.textContent = [
      T("{0} independent studies · {1}{2}", result.n_studies,RFAnalysisLocale.term(result.kind),result.time_unit ? T(' per {0}',result.time_unit) : ''),
      T("{0}: {1} (95% CI {2}).", RFAnalysisLocale.term(result.fixed.method),fmt(result.fixed.estimate),interval(result.fixed)),
      T("{0}: {1}; conditional estimate {2} (95% CI {3}); marginal mean {4}; τ² {5} (ML; {6} variance scale).", RFAnalysisLocale.term(result.random.model),RFAnalysisLocale.term(result.random.status),fmt(result.random.estimate),interval(result.random),fmt(result.random.marginal_mean),fmt(result.random.tau2),T(result.kind === 'proportion' ? 'logit' : 'log')),
      ...(result.warnings || []).map(text=>RFAnalysisLocale.term(text)), ...(result.method_sources || []),
      T('Study sources: {0}',result.source_data.map(row=>`${row.study_id} (${row.source_key}${row.source_locator?', '+row.source_locator:''})`).join('; ')),
      T('撰写论文或报告时，请引用实际使用的方法原始文献及参考书籍，并按期刊格式核对；仅引用本软件不足以说明方法来源。')
    ].join('\n');
    auditHost.append(anAuditReportButtons(taskApi() + '/analysis/single-group/synthesis', q,
      'reviewflow_single_group_audit', () => ticket === generation));
  } catch (error) { if (ticket === generation) { resultHost.textContent = error.message; toast(error.message, 'warn'); } }
  finally { if (ticket === generation) { clearInterval(timer); run.disabled = false; } }
});
document.addEventListener('reviewflow:analysis-loaded', loadCounts);
updateKind();
})();
