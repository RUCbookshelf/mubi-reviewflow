(function () {
  const host = $('#anDtaThresholdResult'), run = $('#anDtaThresholdRun');
  let generation = 0, timer = null;
  function curve(result, request) {
    const fixed = result.fixed_effects, range = result.threshold_range;
    if (!Array.isArray(range) || range.length !== 2 || !(range[1] > range[0])) return null;
    const citations = (result.method_sources || []).map(source => {
      const name = source.match(/^.+?\(\d{4}\)/)?.[0] || source.split(';')[0];
      const doi = source.match(/\bdoi:([^;\s]+)/i)?.[1].replace(/[.,;]+$/, '');
      return name + (doi ? ` · DOI ${doi}` : '');
    });
    const sources = result.studies.map(row => {
      const doi = anSourceFields(row.source_key).source_doi;
      const label = `${row.study_id} (${row.source_locator || row.source_key}${doi ? '; DOI ' + doi : ''})`;
      return label.length > 48 ? label.slice(0, 47) + '…' : label;
    });
    const height = 298 + citations.length * 18 + sources.length * 14;
    const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', `0 0 520 ${height}`); svg.setAttribute('width', '100%');
    svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', 'Modelled sensitivity and specificity across the observed threshold range');
    const add = (tag, attrs) => {
      const node = document.createElementNS(ns, tag);
      Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
      svg.append(node); return node;
    };
    add('metadata', { 'data-reviewflow-audit':'dta-threshold' }).textContent = JSON.stringify({
      request, result:{...result, studies:result.studies.map(row => ({...row,...anSourceFields(row.source_key)}))},
    });
    add('rect', { x:0, y:0, width:520, height, fill:'#fff' });
    add('line', { x1:45, y1:175, x2:490, y2:175, stroke:'#64748b' });
    add('line', { x1:45, y1:15, x2:45, y2:175, stroke:'#64748b' });
    for (const [prefix, color] of [['sensitivity','#0f766e'], ['specificity','#b45309']]) {
      const intercept = fixed[`logit_${prefix}_at_mean_threshold`].estimate;
      const slope = fixed[`logit_${prefix}_slope_per_threshold_unit`].estimate;
      const points = Array.from({ length:41 }, (_, i) => {
        const threshold = range[0] + (range[1] - range[0]) * i / 40;
        const probability = 1 / (1 + Math.exp(-(intercept + slope * (threshold - result.threshold_center))));
        return `${i ? 'L' : 'M'}${45 + i * 445 / 40},${175 - probability * 160}`;
      }).join(' ');
      add('path', { d:points, fill:'none', stroke:color, 'stroke-width':2.5 });
      add('text', { x:prefix === 'sensitivity' ? 330 : 420, y:12, fill:color, 'font-size':12 }).textContent = prefix;
    }
    add('text', { x:45, y:195, fill:'#475569', 'font-size':11 }).textContent = Number(range[0]).toPrecision(4);
    add('text', { x:445, y:195, fill:'#475569', 'font-size':11 }).textContent = Number(range[1]).toPrecision(4);
    add('text', { x:225, y:210, fill:'#475569', 'font-size':11 }).textContent = 'Observed threshold range';
    add('text', { x:45, y:12, fill:'#475569', 'font-size':10 }).textContent = 'Probability (0–1)';
    add('text', { x:45, y:229, fill:'#475569', 'font-size':10 }).textContent = 'Study-level association; not a causal effect of changing cutoff.';
    if (citations.length) add('text', { x:45, y:246, fill:'#475569', 'font-size':10 }).textContent = 'Method sources:';
    citations.forEach((source, i) => { add('text', { x:45, y:263 + i * 18, fill:'#475569', 'font-size':10 }).textContent = source; });
    add('text', { x:45, y:263 + citations.length * 18, fill:'#475569', 'font-size':10 }).textContent = 'Study reports (full details in SVG metadata):';
    sources.forEach((source, i) => { add('text', { x:45, y:279 + citations.length * 18 + i * 14, fill:'#475569', 'font-size':9 }).textContent = source; });
    add('text', { x:45, y:height-10, fill:'#475569', 'font-size':10 }).textContent = T('Check and cite the original methods. Inputs and references are saved in this SVG.');
    return svg;
  }
  const clear = () => { generation++; clearInterval(timer); run.disabled = false; host.replaceChildren(); };
  ['anDtaTest', 'anDtaCondition', 'anDtaReference', 'anDtaThresholdChoices']
    .forEach(id => $('#'+id).addEventListener('input', clear));
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', clear);
  $('#anDtaSave').addEventListener('click',clear);
  run.addEventListener('click', async () => {
    clear(); const ticket = generation;
    try {
      const choice = anValue('anDtaThresholdChoices');
      const body = { index_test:anValue('anDtaTest'), target_condition:anValue('anDtaCondition'),
        reference_standard:anValue('anDtaReference') || null,
        threshold_by_study:choice ? JSON.parse(choice) : null };
      run.disabled = true;
      const started = performance.now();
      const progress = () => { if (ticket === generation) host.textContent = T("Fitting threshold model… {0} s elapsed.", ((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress,500);
      const result = await api(taskApi() + '/analysis/dta/threshold-meta-regression', { method:'POST', body });
      if (ticket !== generation) return;
      clearInterval(timer); host.replaceChildren();
      const fmt = value => value == null ? T('unavailable') : Number(value).toPrecision(5);
      host.append(el('p', { text:T("{0} studies · {1} numeric thresholds · mean threshold {2} · {3}.", result.n_studies,result.n_distinct_thresholds,fmt(result.threshold_center),result.reference_standard) }),
        el('p', { text:T("Sensitivity {0}; specificity {1} at the mean study threshold.", fmt(result.sensitivity_at_mean_threshold),fmt(result.specificity_at_mean_threshold)) }));
      const table = el('table');
      table.append(el('thead', {}, el('tr', {}, ...['Logit coefficient', 'Estimate', 'SE', '95% CI'].map(text => el('th', { text })))));
      const rows = el('tbody');
      for (const [name, value] of Object.entries(result.fixed_effects)) {
        rows.append(el('tr', {}, ...[name.replaceAll('_', ' '), fmt(value.estimate), fmt(value.se),
          value.ci_95 ? `[${value.ci_95.map(fmt).join(', ')}]` : T('unavailable')].map(text => el('td', { text }))));
      }
      table.append(rows); host.append(table);
      const plot = curve(result, body);
      if (plot) {
        const download = el('button', { class:'btn b-out', type:'button', text:T('Export threshold SVG') });
        download.addEventListener('click', () => {
          const blob = new Blob([new XMLSerializer().serializeToString(plot)], { type:'image/svg+xml;charset=utf-8' });
          const url = URL.createObjectURL(blob), link = el('a', { href:url, download:'reviewflow_dta_threshold.svg' });
          document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
        });
        host.append(plot, download);
      }
      host.append(el('p', { class:'hint', text:T('Study sources: {0}',result.studies.map(row=>T('{0} ({1}; threshold {2})',row.study_id,row.source_key,row.threshold)).join('; ')) }),
        el('p', { class:'hint', text:result.warnings.join(' ') + ' Verify the original methods and cite the model and study reports.' }));
      host.append(anAuditReportButtons(taskApi()+'/analysis/dta/threshold-meta-regression',null,
        'reviewflow_dta_threshold_audit',() => ticket === generation,body),
        ...(result.method_sources || []).map(text => el('p',{class:'hint',text})));
    } catch (error) { if (ticket === generation) { host.textContent = error.message; toast(error.message, 'warn'); } }
    finally { if (ticket === generation) { clearInterval(timer); run.disabled = false; } }
  });
})();
