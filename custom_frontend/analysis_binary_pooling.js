(function () {
const run = $('#anBinaryPoolRun');
const output = $('#anBinaryPoolResult');
let generation = 0;
const clear = () => { generation++; output.replaceChildren(); run.disabled = false; };
['anComparison','anOutcome','anTimepoint','anMeasure','anBinaryPoolMethod']
  .forEach(id => $('#'+id).addEventListener('input',clear));
document.addEventListener('reviewflow:analysis-core-loaded',clear);
document.addEventListener('reviewflow:analysis-task-changed',clear);
const saveArms = $('#anBinaryArmSave'), armStatus = $('#anBinaryArmStatus');
saveArms.addEventListener('click', () => anRunWithProgress(saveArms, armStatus, async () => {
  clear();
  const arms = {};
  for (const [key, id] of Object.entries({
    events_t:'anEventsT', total_t:'anTotalT', events_c:'anEventsC', total_c:'anTotalC'
  })) {
    if (anValue(id) === '') throw new Error(T("Required field missing: {0}", $('#'+id).getAttribute('aria-label') || $('#'+id).placeholder || id));
    arms[key] = Number(anValue(id));
  }
  const body = {
    study_id:anValue('anEffectStudy'), source_key:anValue('anSource'),
    source_locator:anValue('anSourceLocator'),
    comparison:anValue('anComparison'), outcome:anValue('anOutcome'),
    timepoint:anValue('anTimepoint'), arms
  };
  const result = await api(taskApi() + '/analysis/binary-arms', { method:'POST', body });
  return T("Saved {0}; {1} raw arm record(s).", body.study_id, result.arms.length);
}, ['anEffectStudy','anSource','anSourceLocator','anComparison','anOutcome','anTimepoint',
  'anEventsT','anTotalT','anEventsC','anTotalC'], 'Saving arm counts… {0} s elapsed.'));
run.addEventListener('click', async () => {
  clear(); const ticket = generation;
  let timer;
  try {
    const method = anValue('anBinaryPoolMethod');
    const measure = anValue('anMeasure');
    if (!method) throw new Error('Choose a binary pooling method.');
    if (!['RR', 'OR', 'RD'].includes(measure) || (method === 'peto' && measure !== 'OR'))
      throw new Error('Mantel–Haenszel supports RR, OR or RD; Peto supports OR only.');
    const query = new URLSearchParams({
      comparison: anValue('anComparison'), outcome: anValue('anOutcome'),
      timepoint: anValue('anTimepoint'), measure, method
    });
    run.disabled = true;
    const started = performance.now();
    const progress = () => { if (ticket === generation) output.textContent = T("Calculating binary pooling… {0} s elapsed.", ((performance.now()-started)/1000).toFixed(1)); };
    progress(); timer = setInterval(progress, 500);
    const result = await api(taskApi() + '/analysis/binary-pooling?' + query);
    if (ticket !== generation) return;
    output.replaceChildren();
    const number = value => Number(value).toPrecision(4);
    output.append(el('p', { text:T("{0} · {1}: {2} (95% CI {3}–{4}); {5} studies", RFAnalysisLocale.term(result.method), result.measure, number(result.pooled), number(result.ci_low), number(result.ci_high), result.n_studies) }));
    if (result.included_studies?.length) output.append(el('p', {
      class:'hint', text:T('Included:') + ' ' + result.included_studies.map(study =>
        `${study.study_id}${result.sources?.[study.study_id] ? ` (${result.sources[study.study_id]})` : ''}`).join(', ')
    }));
    if (result.excluded_studies?.length) {
      const list = el('ul');
      result.excluded_studies.forEach(study => list.append(el('li', { text:T('{0}: {1}',study.study_id,RFAnalysisLocale.term(study.reason)) })));
      output.append(el('p', { text:'Excluded studies:' }), list);
    }
    if (result.warnings?.length) output.append(el('p', { class:'hint', text:result.warnings.map(text=>RFAnalysisLocale.term(text)).join(' ') }));
    output.append(el('p', { class:'hint', text:(result.method_sources || []).join(' ') }),
      el('p', { class:'hint', text:T('使用该方法撰写论文时，请展开本区参考文献并引用方法来源。') }),
      anAuditReportButtons(taskApi() + '/analysis/binary-pooling', query,
        'reviewflow_binary_pooling_audit', () => ticket === generation));
  } catch (error) {
    if (ticket === generation) { output.textContent = error.message; toast(error.message, 'warn'); }
  }
  finally { clearInterval(timer); if (ticket === generation) run.disabled = false; }
});
})();
