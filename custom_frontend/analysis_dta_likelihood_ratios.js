(function () {
  const host = $('#anDtaLrResult'), output = $('#anDtaLrExport');
  let record = null;
  function clear() { record = null; host.replaceChildren(); output.disabled = true; }
  document.addEventListener('reviewflow:dta-cleared', clear);
  document.addEventListener('reviewflow:dta-synthesized', event => {
    clear();
    const result = event.detail.likelihood_ratios;
    if (!result?.available) { host.textContent = result?.reason || 'Likelihood ratios unavailable for this response.'; return; }
    const fmt = value => Number(value).toPrecision(6), table = el('table'), rows = el('tbody');
    table.append(el('thead', {}, el('tr', {}, ...['Summary-point ratio','Estimate','Approximate 95% CI'].map(text => el('th',{text})))), rows);
    for (const [key,label] of [['positive','LR+'],['negative','LR−']]) {
      const limits = result['ci_lr_'+key];
      rows.append(el('tr', {}, el('th',{scope:'row',text:label}), el('td',{text:fmt(result['lr_'+key])}),
        el('td',{text:result.display_intervals && limits ? limits.map(fmt).join(' to ') : 'Unavailable — see explanation'})));
    }
    host.append(el('p',{text:result.interval_display_note}),table,
      ...result.warnings.map(text => el('p',{class:'hint',text})),
      el('p',{class:'hint',text:result.method_sources.join(' ') + ' Verify the original text and cite the method and study sources when publishing.'}));
    record = result; output.disabled = false;
  });
  output.addEventListener('click', () => {
    if (!record) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(record,null,2)],{type:'application/json'}));
    const link = el('a',{href:url,download:'reviewflow_dta_likelihood_ratios.json'});
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
  });
})();
