(function () {
  const host = $('#anBayesResult'), button = $('#anBayesRun');
  let generation = 0, timer;
  const clear = () => { generation++; clearInterval(timer); host.replaceChildren(); button.disabled = false; };
  ['anComparison', 'anOutcome', 'anTimepoint', 'anMeasure', 'anBayesMuMean', 'anBayesMuSd', 'anBayesTauScale']
    .forEach(id => $('#'+id).addEventListener('input', clear));
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', () => {
    ['anBayesMuMean', 'anBayesMuSd', 'anBayesTauScale'].forEach(id => $('#'+id).value = '');
    clear();
  });
  $('#anBayesRun').addEventListener('click', async () => {
    clear(); const ticket = generation;
    try {
      const prior = id => {
        const raw = anValue(id);
        if (raw === '') throw new Error(T('先验参数须填写有限数值；尺度和自由度须为正数。'));
        return Number(raw);
      };
      const body = { comparison:anValue('anComparison'), outcome:anValue('anOutcome'),
        timepoint:anValue('anTimepoint'), measure:anValue('anMeasure'),
        mu_prior_mean:prior('anBayesMuMean'), mu_prior_sd:prior('anBayesMuSd'),
        tau_prior_scale:prior('anBayesTauScale') };
      button.disabled = true;
      const started = performance.now();
      const progress = () => { if (ticket === generation) host.textContent = T('正在计算贝叶斯模型…已等待 {0} 秒。',((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress,500);
      const result = await api(taskApi() + '/analysis/bayesian/normal-meta', { method:'POST', body });
      if (ticket !== generation) return;
      clearInterval(timer); host.replaceChildren();
      const number = value => Number(value).toPrecision(5);
      const interval = values => `[${values.map(number).join(', ')}]`;
      host.append(el('p', { text:T('{0} 项研究 · {1} · μ 位于 {2} 分析尺度 · 正态/半正态先验',result.study_count,result.measure,result.analysis_scale) }),
        el('p', { text:T('μ 后验均值 {0}，中位数 {1}，95% 可信区间 {2}；P(μ > 0) = {3}。',number(result.mu.mean),number(result.mu.median),interval(result.mu.credible_interval_95),number(result.mu.probability_gt_zero)) }),
        el('p', { text:T('τ 后验均值 {0}，中位数 {1}，95% 可信区间 {2}。',number(result.tau.mean),number(result.tau.median),interval(result.tau.credible_interval_95)) }));
      if (result.ratio_summary) host.append(el('p', { text:T('比值中位数 {0}，95% 可信区间 {1}；P(比值 > 1) = {2}。',number(result.ratio_summary.median),interval(result.ratio_summary.credible_interval_95),number(result.ratio_summary.probability_gt_one)) }));
      host.append(anAuditReportButtons(taskApi()+'/analysis/bayesian/normal-meta',null,
        'reviewflow_bayesian_synthesis_audit',() => ticket === generation,body),
        ...(result.method_sources || []).map(text => el('p',{class:'hint',text})),
        el('p', { class:'hint', text:T('请比较合理的先验设定，并引用方法、先验依据和研究报告。') }),
        el('p', { class:'hint', lang:'en', text:`Sources: ${result.sources.map(row => `${row.study_id} (${row.source_key}${row.source_locator ? `, ${row.source_locator}` : ''}, result ${row.result_id})`).join('; ')}` }));
    } catch (error) { if (ticket === generation) { host.replaceChildren(el('span',{lang:'en',text:error.message})); toast(error.message, 'warn'); } }
    finally { if (ticket === generation) { clearInterval(timer); button.disabled = false; } }
  });
})();
