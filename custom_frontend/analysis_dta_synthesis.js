(function () {
  const host = $('#anDtaHsrocResult'), button = $('#anDtaSynthesize');
  let generation = 0, record = null, timer = null;
  const fmt = value => Number(value).toPrecision(6);
  function clear() {
    generation++; record = null; clearInterval(timer); button.disabled = false;
    ['anDtaSummary', 'anDtaResult', 'anDtaPlot', 'anDtaHsrocResult'].forEach(id => $('#'+id).replaceChildren());
    $('#anDtaPlotExport').disabled = true;
    $('#anDtaHsrocExport').disabled = true;
    document.dispatchEvent(new CustomEvent('reviewflow:dta-cleared'));
  }
  ['anDtaTest', 'anDtaCondition', 'anDtaThreshold', 'anDtaReference', 'anTp', 'anFp', 'anFn', 'anTn']
    .forEach(id => $('#'+id).addEventListener('input', clear));
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', clear);

  function plot(result) {
    const region = result.prediction_region;
    const curve = result.hsroc?.available && result.hsroc.summary_sroc_curve;
    const citations = [...new Set([
      ...(result.method_sources || []), ...(curve ? result.hsroc.sources || [] : []),
      ...(region?.available ? region.method_sources || [] : []),
    ].map(source => {
      const name = source.match(/^.+?\(\d{4}\)/)?.[0] || source.split(/[;:]/)[0];
      const doi = source.match(/\bdoi:\s*([^;\s]+)/i)?.[1].replace(/[.,;]+$/, '');
      return name + (doi ? ` · DOI ${doi}` : '');
    }))];
    const height = 416 + citations.length * 16;
    const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', `0 0 440 ${height}`); svg.setAttribute('width', '100%');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-labelledby', 'anDtaPlotTitle anDtaPlotDescription');
    const add = (tag, attrs, text) => {
      const node = document.createElementNS(ns, tag);
      Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
      if (text) node.textContent = text;
      svg.append(node); return node;
    };
    add('title', {id:'anDtaPlotTitle'}, 'Diagnostic accuracy: sensitivity against false-positive rate');
    add('desc', {id:'anDtaPlotDescription'}, 'Study points, pooled point, optional descriptive SROC, and conditional between-study prediction region. The region is not a confidence region for the pooled mean. Full parameters, study tables and method sources are in SVG metadata.');
    add('metadata', {id:'anDtaPlotAudit',type:'application/json'}, JSON.stringify({
      ...result, studies:result.studies.map(row => ({...row,...anSourceFields(row.source_key)})),
    }));
    add('rect', {x:0,y:0,width:440,height,fill:'#ffffff'});
    add('line', {x1:55,y1:265,x2:405,y2:265,stroke:'#334155'});
    add('line', {x1:55,y1:265,x2:55,y2:25,stroke:'#334155'});
    for (const p of [0, .5, 1]) {
      add('text', {x:55+350*p,y:283,'text-anchor':'middle',fill:'#334155','font-size':12}, String(p));
      add('text', {x:45,y:269-240*p,'text-anchor':'end',fill:'#334155','font-size':12}, String(p));
    }
    add('text', {x:230,y:305,'text-anchor':'middle',fill:'#334155','font-size':12}, 'False-positive rate (1 − specificity)');
    add('text', {transform:'translate(16 150) rotate(-90)','text-anchor':'middle',fill:'#334155','font-size':12}, 'Sensitivity');
    if (region?.available && region.points?.length) add('path', {
      d:region.points.map((point, index) => `${index ? 'L' : 'M'}${55+350*(1-point.specificity)},${265-240*point.sensitivity}`).join(' ') + ' Z',
      fill:'#2563eb','fill-opacity':'.12',stroke:'#2563eb','stroke-width':1.5,
      'data-prediction-region':'true'});
    if (curve) {
      const points = Array.from({length:201}, (_, i) => {
        const fpr = i / 200;
        const se = i === 0 ? 0 : i === 200 ? 1 : 1 / (1 + Math.exp(-(
          curve.logit_intercept + curve.logit_slope_fpr * Math.log(fpr / (1-fpr)))));
        return `${i ? 'L' : 'M'}${55+350*fpr},${265-240*se}`;
      }).join(' ');
      add('path', {d:points,fill:'none',stroke:'#b45309','stroke-width':2,'data-hsroc-curve':'true'});
    }
    result.studies.forEach(row => add('circle', {cx:55+350*(1-row.specificity),cy:265-240*row.sensitivity,
      r:4,fill:'#475569',opacity:.5}));
    add('circle', {cx:55+350*(1-result.specificity),cy:265-240*result.sensitivity,r:7,fill:'#27866c'});
    add('text', {x:55,y:327,fill:'#334155','font-size':11},
      curve ? 'Grey: studies · Green: pooled · Brown: SROC (no CI band)' : 'Grey: studies · Green: pooled');
    add('text', {x:55,y:348,fill:'#334155','font-size':10},
      region?.available ? 'Blue: conditional 95% between-study prediction region, not pooled-mean CI.' :
        'Prediction region unavailable; see model result for reason.');
    if (citations.length) add('text', {x:20,y:374,fill:'#475569','font-size':9}, 'Method sources:');
    citations.forEach((source, i) => add('text', {x:20,y:390+i*16,fill:'#475569','font-size':9}, source));
    add('text', {x:20,y:height-9,fill:'#475569','font-size':9},
      'Check and cite the original methods. Inputs and reports are saved in this SVG.');
    $('#anDtaPlot').replaceChildren(svg);
    $('#anDtaPlotExport').disabled = false;
  }

  button.addEventListener('click', async () => {
    clear(); const ticket = generation; button.disabled = true;
    const started = performance.now();
    const progress = () => { if (ticket === generation) $('#anDtaSummary').textContent = T("Fitting bivariate model… {0} s elapsed.", ((performance.now()-started)/1000).toFixed(1)); };
    progress(); timer = setInterval(progress,500);
    try {
      const query = new URLSearchParams({index_test:anValue('anDtaTest'), target_condition:anValue('anDtaCondition')});
      if (anValue('anDtaThreshold').trim()) query.set('threshold', anValue('anDtaThreshold').trim());
      if (anValue('anDtaReference').trim()) query.set('reference_standard', anValue('anDtaReference').trim());
      const result = await api(taskApi() + '/analysis/dta/synthesis?' + query);
      if (ticket !== generation) return;
      $('#anDtaResult').textContent = JSON.stringify(result, null, 2);
      $('#anDtaSummary').replaceChildren(el('p', {text:T("{0} studies · sensitivity {1} · specificity {2}", result.n_studies,fmt(result.sensitivity),fmt(result.specificity))}),
        el('p', {class:'hint',text:T("Between-study SD on logit sensitivity and logit specificity scales: {0} and {1}.", fmt(result.tau_sensitivity),fmt(result.tau_specificity))}),
        ...result.warnings.map(text => el('p', {class:'hint', text})),
        anAuditReportButtons(taskApi()+'/analysis/dta/synthesis',query,
          'reviewflow_dta_synthesis_audit',() => ticket === generation),
        ...(result.method_sources || []).map(text => el('p',{class:'hint',text})));
      $('#anDtaSummary').append(el('p', {class:'hint',text:result.prediction_region?.available
        ? 'Blue region shows conditional between-study variation in latent sensitivity and specificity under the fitted bivariate model. It excludes sampling and fitted-parameter uncertainty; it is not a confidence region for the pooled mean. Verify Cochrane DTA Handbook ch. 10 and Reitsma et al. (2005), then cite the original methods.'
        : (result.prediction_region?.reason || 'Prediction region unavailable for this fit.')}));
      plot(result);
      document.dispatchEvent(new CustomEvent('reviewflow:dta-synthesized', {detail:result}));
      const hsroc = result.hsroc;
      if (!hsroc?.available) {
        host.textContent = hsroc?.reason || 'HSROC presentation unavailable for this response.';
        return;
      }
      const table = el('table'), body = el('tbody');
      table.append(el('thead', {}, el('tr', {}, el('th', {text:'HSROC parameter'}), el('th', {text:'Estimate'}))), body);
      for (const [key, label] of [['lambda','Λ · accuracy'], ['theta','Θ · threshold'], ['beta','β · shape'],
        ['variance_alpha','σα² · accuracy variance'], ['variance_theta','σθ² · threshold variance']]) {
        body.append(el('tr', {}, el('th', {scope:'row', text:label}), el('td', {text:fmt(hsroc[key])})));
      }
      host.append(table, el('p', {class:'hint',text:'Accuracy and threshold variances are on their latent HSROC parameter scales. Descriptive HSROC curve and parameters only: no HSROC parameter CI or shape significance test. The separate bivariate prediction region is shown on the plot when available.'}),
        ...hsroc.warnings.map(text => el('p', {class:'hint',text})),
        el('p', {class:'hint',text:hsroc.sources.join(' ') + ' Verify the original text and cite these methods when publishing.'}));
      record = {task_id:S.task.task_id, query:Object.fromEntries(query), result:hsroc};
      $('#anDtaHsrocExport').disabled = false;
    } catch (error) {
      if (ticket === generation) $('#anDtaSummary').textContent = error.message;
    } finally { if (ticket === generation) { clearInterval(timer); button.disabled = false; } }
  });
  $('#anDtaHsrocExport').addEventListener('click', () => {
    if (!record) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2)], {type:'application/json'}));
    const link = el('a', {href:url, download:'reviewflow_hsroc.json'});
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  $('#anDtaPlotExport').addEventListener('click', () => {
    const svg = $('#anDtaPlot svg');
    if (!svg) return;
    const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)], {type:'image/svg+xml;charset=utf-8'}));
    const link = el('a', {href:url, download:'reviewflow_dta_plot.svg'});
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
