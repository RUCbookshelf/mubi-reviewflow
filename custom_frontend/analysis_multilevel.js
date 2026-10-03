(function () {
  const candidates = $('#anMultilevelCandidates'), host = $('#anMultilevelResult');
  const button = $('#anMultilevelRun');
  let generation = 0, timer;
  const clearResult = () => { generation++; clearInterval(timer); host.replaceChildren(); button.disabled = false; };
  candidates.addEventListener('change', clearResult);
  ['anMultilevelComparable','anMultilevelCovariances'].forEach(id => $('#'+id).addEventListener('input',clearResult));
  const clear = () => { candidates.replaceChildren(); clearResult(); $('#anMultilevelComparable').checked = false; };
  ['anComparison', 'anMeasure'].forEach(id => $('#'+id).addEventListener('input', clear));
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', () => {
    clear(); $('#anMultilevelSampling').value = ''; $('#anMultilevelCovariances').value = '';
    $('#anMultilevelCovarianceLabel').hidden = true;
  });
  $('#anMultilevelSampling').addEventListener('change', () => {
    $('#anMultilevelCovarianceLabel').hidden = anValue('anMultilevelSampling') !== 'supplied_known_within_study_covariance';
    clearResult();
  });
  $('#anMultilevelPrepare').addEventListener('click', () => {
    candidates.replaceChildren(); clearResult();
    const rows = anEffects.filter(row => row.selected && row.comparison === anValue('anComparison') && row.measure === anValue('anMeasure'))
      .sort((a, b) => a.study_id.localeCompare(b.study_id) || a.result_id - b.result_id);
    if (!rows.length) { candidates.textContent = T('没有符合当前比较和效应指标的已选结果。'); return; }
    for (const row of rows) {
      const input = el('input', { type:'checkbox', value:String(row.result_id), 'data-multilevel-result':'' });
      candidates.append(el('label', { style:'display:block' }, input,
        T('研究 {0} · 结果 {1} · 结局 {2} · 时间点 {3} · 来源 {4} · 已存储 {5} 效应 {6}；分析尺度 SE {7}',
          row.study_id, row.result_id, row.outcome, row.timepoint, row.source_key, row.measure,
          Number(row.estimate).toPrecision(5), Number(row.se).toPrecision(5))));
    }
    candidates.append(el('p', { class:'hint', text:T('输入协方差时，矩阵行列顺序按研究内勾选结果的顺序排列。RR、OR、HR 和 RATE_RATIO 在库中存为自然比值，拟合时取对数；LOG_ROM、LOG_RATE 和 PAIRED_OR 已存为对数值。协方差须与已存 SE 使用相同尺度。') }));
  });
  $('#anMultilevelRun').addEventListener('click', async () => {
    clearResult(); const ticket = generation;
    try {
      const result_ids = [...candidates.querySelectorAll('[data-multilevel-result]:checked')].map(input => Number(input.value));
      if (!$('#anMultilevelComparable').checked) throw new Error(T('请确认纳入的效应代表相同的临床构念、单位和方向。'));
      const sampling_error_assumption = anValue('anMultilevelSampling');
      if (!sampling_error_assumption) throw new Error(T('请选择研究内抽样误差假设。'));
      let within_study_covariances = null;
      if (sampling_error_assumption === 'supplied_known_within_study_covariance') within_study_covariances = JSON.parse(anValue('anMultilevelCovariances'));
      const body = { result_ids, comparison:anValue('anComparison'), measure:anValue('anMeasure'),
        construct_confirmed:true, sampling_error_assumption, within_study_covariances };
      button.disabled = true;
      const started = performance.now();
      const progress = () => { if (ticket === generation) host.textContent =
        T('正在拟合三层 REML…已等待 {0} 秒。', ((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress,500);
      const result = await api(taskApi() + '/analysis/multilevel/random-intercept', { method:'POST', body });
      if (ticket !== generation) return;
      clearInterval(timer); host.replaceChildren();
      const fmt = value => Number(value).toPrecision(5);
      const analysisScale = result.analysis_scale === 'log' ? '对数尺度'
        : result.analysis_scale === 'fisher_z' ? 'Fisher z 尺度'
        : result.analysis_scale === 'logit' ? 'logit 分析尺度' : '自然尺度';
      const assumption = result.sampling_error_assumption === 'independent_within_studies'
        ? '假定研究内抽样误差相互独立' : '使用已知的研究内抽样协方差';
      host.append(el('p', { text:T('{0} 项效应，来自 {1} 项研究 · {2} · REML 合并均值 {3}；SE {4}；正态 95% CI [{5}, {6}]。',
        result.n_effects, result.n_studies, result.measure,
        fmt(result.estimate), fmt(result.se), fmt(result.ci_95[0]), fmt(result.ci_95[1])) }),
        el('p', {}, T('分析尺度：'), T(analysisScale)),
        el('p', { text:T('方差分量：研究间 {0}；研究内效应 {1}。',
          fmt(result.variance_components.study), fmt(result.variance_components.effect_within_study)) }),
        el('p', {}, T('抽样误差假设：'), T(assumption)),
        el('p', { class:'hint', text:T('核对协方差假设，并引用所用方法和研究报告。') }),
        el('p', { class:'hint', lang:'en', text:'Sources: ' + result.included_results.map(row =>
          `${row.study_id} / ${row.result_id} (${row.source_key}${row.source_locator ? '; ' + row.source_locator : ''})`).join('; ') }),
        el('p', { class:'hint', lang:'en', text:(result.method_sources || []).join(' ') }),
        anAuditReportButtons(taskApi() + '/analysis/multilevel/random-intercept', null,
          'reviewflow_multilevel_audit', () => ticket === generation, body));
    } catch (error) { if (ticket === generation) { host.textContent = error.message; toast(error.message, 'warn'); } }
    finally { if (ticket === generation) { clearInterval(timer); button.disabled = false; } }
  });
})();
