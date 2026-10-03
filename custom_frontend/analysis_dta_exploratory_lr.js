(function () {
  const prefix = 'anDtaExploratoryLr', host = $('#'+prefix+'Result');
  const run = $('#'+prefix+'Run'), download = $('#'+prefix+'Export');
  let generation = 0, record = null;
  const clear = () => { generation++; record = null; host.replaceChildren(); download.disabled = true; run.disabled = false; };
  $('#'+prefix+'Panel').addEventListener('input',clear);
  document.addEventListener('reviewflow:dta-cleared',clear);
  run.addEventListener('click',async () => {
    clear(); const ticket = generation;
    let timer;
    try {
      const body = {index_test:anValue('anDtaTest'),target_condition:anValue('anDtaCondition'),threshold:anValue('anDtaThreshold'),reference_standard:anValue('anDtaReference'),model:anValue(prefix+'Model'),correction:anValue(prefix+'Correction'),confidence:Number(anValue(prefix+'Level'))/100};
      if (!body.index_test || !body.target_condition || !body.threshold || !body.reference_standard || !body.model || !body.correction || !(body.confidence > 0 && body.confidence < 1)) throw new Error('Specify the test, condition, threshold, reference standard, model, correction and confidence level.');
      run.disabled = true;
      const started = performance.now();
      const progress = () => { if (ticket === generation) host.textContent = T("Calculating exploratory likelihood ratios… {0} s elapsed.", ((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress, 500);
      const result = await api(taskApi()+'/analysis/dta/exploratory-likelihood-ratios',{method:'POST',body});
      if (ticket !== generation) return;
      const fmt = value => Number(value).toPrecision(6), table = el('table'), rows = el('tbody');
      table.append(el('caption',{text:T("Exploratory separate pooling — {0}% intervals", body.confidence*100)}),
        el('thead',{},el('tr',{},...['Ratio','Estimate','Interval','Q (fixed-IV; df)','I² (%)','DL τ² (log LR variance scale)'].map(text => el('th',{text})))),rows);
      for (const [key,label] of [['positive','LR+'],['negative','LR−']]) {
        const item = result[key], h = item.heterogeneity;
        rows.append(el('tr',{},...[label,fmt(result['lr_'+key]),item.ci_lr.map(fmt).join(' – '),`${fmt(h.q)} (${h.q_df})`,fmt(h.i2_percent),fmt(h.tau2)].map(text => el('td',{text}))));
      }
      host.replaceChildren(el('p',{text:T("{0}; {1}/{2} studies used. {3}. DL τ² is a diagnostic estimate even when fixed weights are selected.", RFAnalysisLocale.term(result.effect_model),result.n_used,result.n_studies,RFAnalysisLocale.term(result.zero_cell_policy))}),table,
        el('p',{class:'hint',text:'I² = max(0, (Q − df) / Q) × 100% from fixed inverse-variance Q; I² = 0 when Q = 0.'}));
      for (const row of result.excluded_studies) host.append(el('p',{class:'hint',text:T("Excluded {0}: {1}", row.study_id,RFAnalysisLocale.term(row.reason))}));
      for (const text of [...result.warnings,...result.method_sources]) host.append(el('p',{class:'hint',text}));
      host.append(el('p',{class:'hint',text:T('Sources:')+' '+result.source_provenance.map(row => `${row.study_id} (${row.source_key}${row.source_locator ? ', '+row.source_locator : ''})`).join('; ')}));
      host.append(anAuditReportButtons(taskApi()+'/analysis/dta/exploratory-likelihood-ratios',null,
        'reviewflow_dta_exploratory_lr_audit',() => ticket === generation,body));
      record = result; download.disabled = false;
    } catch (error) { if (ticket === generation) host.textContent = error.message; }
    finally { clearInterval(timer); if (ticket === generation) run.disabled = false; }
  });
  download.addEventListener('click',() => {
    if (!record) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(record,null,2)],{type:'application/json'}));
    const link = el('a',{href:url,download:'reviewflow_dta_exploratory_lr.json'});
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
  });
})();
