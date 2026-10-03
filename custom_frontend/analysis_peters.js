(function () {
  const host = $('#anPetersResult'), run = $('#anPetersRun'), output = $('#anPetersExport');
  let record = null;
  const task = RFAnalysisTask.create({
    host, run,
    reset() { record = null; output.disabled = true; host.replaceChildren(); },
    progress: seconds => T("Calculating Peters regression… {0} s elapsed.", seconds.toFixed(1)),
    async execute(ticket) {
      if (anValue('anSingleKind') !== 'proportion')
        throw new Error(T('Choose proportion counts above; this Peters variant requires events and total participants.'));
      const query = new URLSearchParams({comparison:anValue('anComparison'), outcome:anValue('anOutcome'), timepoint:anValue('anTimepoint')});
      const result = await api(taskApi() + '/analysis/single-group/peters?' + query);
      if (ticket !== task.generation()) return;
      const fmt = value => Number(value).toPrecision(6), slope = result.coefficients.slope_on_inverse_total;
      host.replaceChildren(el('p', {text:T("{0} of {1} studies included · t={2} · df={3} · p={4}", result.n_included,result.n_studies,fmt(result.statistic),result.df,fmt(result.p_value))}),
        el('p', {text:T("Logit proportion versus 1/total: slope {0} (SE {1}), 95% CI [{2}, {3}].", fmt(slope.estimate),fmt(slope.se),fmt(slope.ci_low),fmt(slope.ci_high))}),
        el('p', {class:'hint',text:result.direction_interpretation}));
      if (result.excluded_studies.length) {
        host.append(el('p', {text:T('Excluded boundary studies (zero regression weight):')}));
        const list = el('ul');
        for (const row of result.excluded_studies) {
          const source = result.input_data.find(input => input.study_id === row.study_id);
          list.append(el('li', {text:`${row.study_id}: ${row.events}/${row.total} · ${row.reason} · ${source?.source_key || ''} ${source?.source_locator || ''}`}));
        }
        host.append(list);
      }
      host.append(...result.warnings.map(text => el('p', {class:'hint',text})),
        el('p', {class:'hint',text:result.limitation}), el('p', {class:'hint',text:result.source}),
        anAuditReportButtons(taskApi() + '/analysis/single-group/peters', query,
          'reviewflow_peters_audit', () => ticket === task.generation()));
      record = {request:Object.fromEntries(query), result}; output.disabled = false;
    },
  });
  ['anComparison', 'anOutcome', 'anTimepoint', 'anSingleKind'].forEach(id => $('#'+id).addEventListener('input', task.clear));
  document.addEventListener('reviewflow:analysis-core-loaded', task.clear);
  document.addEventListener('reviewflow:analysis-task-changed', task.clear);
  document.addEventListener('reviewflow:single-counts-loaded', task.clear);
  run.addEventListener('click', task.submit);
  output.addEventListener('click', () => {
    if (!record) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2)], {type:'application/json'}));
    const link = el('a', {href:url, download:'reviewflow_peters.json'});
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
