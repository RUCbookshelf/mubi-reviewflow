(function () {
  const panel = $('#anStudyIntervalPanel'), host = $('#anIntervalResult');
  const run = $('#anIntervalRun'), exportButton = $('#anIntervalExport');
  const methods = {
    proportion:[['clopper_pearson','Clopper–Pearson (conservative)'], ['wilson','Wilson score'],
      ['mid_p','mid-P'], ['jeffreys','Jeffreys equal-tailed credible interval']],
    rate:[['exact','Garwood exact (conservative; recommended for ≤2 events)'], ['byar','Byar approximation']],
    risk_difference:[['newcombe_hybrid_score','Newcombe hybrid score (recommended study-level interval; no correction)'], ['wald','Wald (unpooled; comparison only)']],
    rate_ratio:[['exact','Conditional exact (conservative)'], ['mid_p','Conditional mid-P']]
  };
  let record = null, generation = 0;
  function clear() {
    generation++; record = null; host.replaceChildren(); exportButton.disabled = true;
    run.disabled = false;
  }
  panel.addEventListener('input', clear);
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', clear);
  $('#anIntervalKind').addEventListener('change', () => {
    clear();
    const kind = anValue('anIntervalKind');
    const twoArms = ['risk_difference','rate_ratio'].includes(kind), rates = ['rate','rate_ratio'].includes(kind);
    anOptions('anIntervalMethod', [['','Choose interval method'], ...(methods[kind] || [])]);
    $('#anIntervalUnitLabel').hidden = !rates;
    $('#anIntervalControl').hidden = !twoArms;
    $('#anIntervalRdHint').hidden = kind !== 'risk_difference';
    $('#anIntervalIrrHint').hidden = kind !== 'rate_ratio';
    $('#anIntervalSingleHint').hidden = twoArms;
    $('#anIntervalEventsLabel').textContent = twoArms ? 'Treatment events' : 'Events';
    $('#anIntervalExposureLabel').textContent = twoArms ? (rates ? 'Treatment total person-time' : 'Treatment total people') : (rates ? 'Total person-time' : 'Total people');
    $('#anIntervalControlExposureLabel').textContent = rates ? 'Control total person-time' : 'Control total people';
    $('#anIntervalExposure').step = rates ? 'any' : '1';
    $('#anIntervalControlTotal').step = rates ? 'any' : '1';
  });
  run.addEventListener('click', async () => {
    clear();
    const request = generation;
    let timer;
    try {
      const kind = anValue('anIntervalKind'), method = anValue('anIntervalMethod');
      const twoArms = ['risk_difference','rate_ratio'].includes(kind), rates = ['rate','rate_ratio'].includes(kind);
      if (!kind || !method) throw new Error('Choose a quantity and an interval method.');
      const number = id => {
        const value = anValue(id);
        if (!value || !Number.isFinite(Number(value))) throw new Error('Enter the event count, exposure and interval level.');
        return Number(value);
      };
      const events = number('anIntervalEvents'), exposure = number('anIntervalExposure');
      const level = number('anIntervalLevel') / 100;
      if (!Number.isSafeInteger(events) || events < 0) throw new Error('Events must be a nonnegative safe integer.');
      if (!(exposure > 0) || (!rates && (!Number.isSafeInteger(exposure) || events > exposure)))
        throw new Error('Exposure must be positive; total people must be an integer at least as large as events.');
      if (!(level > 0 && level < 1)) throw new Error('Interval level must be between 0 and 100, excluding the endpoints.');
      const time_unit = anValue('anIntervalUnit').trim();
      if (rates && !time_unit) throw new Error('Enter the person-time unit.');
      let body = { kind, method, events, level, source_note:anValue('anIntervalSource'),
        ...(kind === 'rate' ? { person_time:exposure, time_unit } : { total:exposure }) };
      if (twoArms) {
        const events_c = number('anIntervalControlEvents'), n_c = number('anIntervalControlTotal');
        if (!Number.isSafeInteger(events_c) || events_c < 0 || n_c <= 0 || (!rates && (!Number.isSafeInteger(n_c) || events_c > n_c)))
          throw new Error('Control events must be a nonnegative integer. Exposure must be positive; total people must be an integer at least as large as events.');
        body = { events_t:events, events_c, method, level, source_note:body.source_note,
          ...(rates ? { person_time_t:exposure, person_time_c:n_c, time_unit } : { n_t:exposure, n_c }) };
      }
      run.disabled = true;
      const started = performance.now();
      const progress = () => { if (request === generation) host.textContent = T("Calculating study interval… {0} s elapsed.", ((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress, 500);
      const endpoint = kind === 'risk_difference' ? '/risk-difference' : kind === 'rate_ratio' ? '/rate-ratio' : '';
      const result = await api(taskApi() + '/analysis/study-interval' + endpoint, { method:'POST', body });
      if (request !== generation) return;
      host.replaceChildren();
      const intervalType = result.method === 'jeffreys' ? 'credible interval' : 'confidence interval';
      const fmt = value => value === 'Infinity' ? '∞' : value === '-Infinity' ? '−∞' : Number(value).toPrecision(7);
      const estimate = kind === 'risk_difference' ? result.rd : kind === 'rate_ratio' ? result.irr : result.estimate;
      const interval = twoArms ? result.ci : [result.lower, result.upper];
      const scale = kind === 'rate' ? T(' events per {0}',result.time_unit) : kind === 'rate_ratio' ? T('(rate ratio, treatment / control)') : kind === 'risk_difference' ? T('(risk difference, treatment − control)') : T('(proportion)');
      host.append(el('p', { text:T("{0} · {1}% {2}", RFAnalysisLocale.term(result.method_label),result.level*100,T(intervalType)) }),
        el('p', { text:T("Estimate {0}; interval [{1}]{2}.", fmt(estimate),interval.map(fmt).join(', '),scale) }));
      if (kind === 'rate_ratio') host.append(el('p', { class:'hint', text:T("Rates per {0}: treatment {1}, control {2}. Infinite bounds are shown as ∞ and preserved as explicit strings in the JSON record.", result.time_unit,fmt(result.rate_t),fmt(result.rate_c)) }));
      if (result.direction_note) host.append(el('p', { class:'hint', text:result.direction_note }));
      for (const warning of result.warnings) host.append(el('p', { class:'hint', role:'note', text:warning }));
      for (const source of result.method_sources) host.append(el('p', { class:'hint', text:source }));
      host.append(el('p', { class:'hint', text:T("Study source: {0}", result.source_note || T('Not recorded; add the study report before publishing.')) }));
      record = { request:body, result, citation_reminder:'Check the original method source and cite it together with the study report.' };
      exportButton.disabled = false;
    } catch (error) {
      if (request === generation) host.textContent = error.message;
    } finally {
      clearInterval(timer);
      if (request === generation) run.disabled = false;
    }
  });
  exportButton.addEventListener('click', () => {
    if (!record) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2)], { type:'application/json' }));
    const link = el('a', { href:url, download:'reviewflow_study_interval.json' });
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
