(function () {
  const host = $('#anRosenbergResult'), run = $('#anRosenbergRun'), output = $('#anRosenbergExport');
  let generation = 0, record = null;
  const notes = {
    'Fewer than 10 studies: interpret cautiously because power and calibration may be limited; 10 is a rule-of-thumb warning, not a validity cutoff.':'研究数少于 10：检验效能和校准可能不足，请谨慎解读；10 只是经验提醒，不是有效性门槛。',
    'The count depends on all effects and standard errors sharing one consistent analysis scale (log for ratios, Fisher z for correlations) and on the zero-cell or continuity corrections applied upstream; changing the scale or corrections changes the fail-safe number.':'所有效应值和标准误须使用同一分析尺度（比值取对数，相关系数用 Fisher z）；上游零单元格或连续性校正也会改变失效安全数。',
    'A fail-safe number is a sensitivity indicator: it does not prove publication bias, does not estimate how many studies are actually unpublished, and does not explain why studies are missing (Rosenberg 2005, p. 467).':'失效安全数不能证明发表偏倚，也不能估计实际缺失研究数或解释缺失原因。',
    'Each row is one independent study weighted by the inverse of its sampling variance, w = 1/se^2 (Rosenberg 2005, equations 3-4).':'每行是一项独立研究，按抽样方差的倒数加权：w=1/SE²（Rosenberg 2005，式 3–4）。',
    'Missing studies are assumed to have mean effect exactly zero and the mean observed weight sum(w)/k (Rosenberg 2005, after equation 10); the count targets a two-sided alpha.':'假定缺失研究的平均效应恰为零、平均权重为 Σw/k；该数值以双侧 α 为目标（Rosenberg 2005，式 10 后）。',
    "The fixed-effects critical value is used: test='z' takes the two-sided standard normal quantile (metafor convention; identical to this package's Rosenthal fail-safe N), test='t' takes Rosenberg's Student-t quantile with df = k + N - 1 solved iteratively.":'使用固定效应临界值：z 选项采用双侧标准正态分位数（metafor 惯例）；t 选项采用 Rosenberg 的 Student t 分位数，并迭代求解自由度 k+N−1。',
    'This sensitivity count does not prove publication bias, does not estimate how many unpublished studies exist, and is not a method for accounting for publication bias (Rosenberg 2005, p. 467).':'此敏感性指标不能证明发表偏倚、估计实际未发表研究数，也不能校正已存在的发表偏倚（Rosenberg 2005，第 467 页）。',
    "Only the fixed-effects version is implemented; the paper's random-effects variant (equations 11-13) re-weights by 1/(se^2 + tau^2_pooled) iteratively and often collapses to the fixed-effects count.":'此处仅实现固定效应版本；原文随机效应版本需迭代按 1/(SE²+合并 τ²) 重新加权，且经常退化为固定效应结果（式 11–13）。',
    'The classic robustness threshold N > 5k + 10 (Rosenthal 1991, cited in Rosenberg 2005, p. 466) is an arbitrary rule of thumb, not a validity criterion.':'N>5k+10 是任意的经验阈值，不是有效性标准（Rosenthal 1991；见 Rosenberg 2005，第 466 页）。',
  };
  function clear() { generation++; record = null; output.disabled = true; host.replaceChildren(); run.disabled = false; }
  ['anComparison', 'anOutcome', 'anTimepoint', 'anMeasure', 'anRosenbergTest', 'anRosenbergAlpha']
    .forEach(id => $('#'+id).addEventListener('input', clear));
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', clear);
  run.addEventListener('click', async () => {
    clear(); const ticket = generation;
    let timer;
    try {
      const test = anValue('anRosenbergTest'), alpha = anValue('anRosenbergAlpha');
      if (!test || !alpha || !(Number(alpha) > 0 && Number(alpha) < 1))
        throw new Error(T('请选择临界值方法，并填写大于 0 且小于 1 的双侧 α。'));
      const body = {comparison:anValue('anComparison'), outcome:anValue('anOutcome'),
        timepoint:anValue('anTimepoint'), measure:anValue('anMeasure'), test, target_alpha:Number(alpha)};
      run.disabled = true;
      const started = performance.now();
      const progress = () => { if (ticket === generation) host.textContent = T('正在计算 Rosenberg 敏感性指标…已等待 {0} 秒。', ((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress, 500);
      const result = await api(taskApi() + '/analysis/publication-bias/rosenberg', {method:'POST', body});
      if (ticket !== generation) return;
      const fmt = value => Number(value).toPrecision(6);
      const scale = result.effect_scale === 'log' ? T('对数尺度') : result.effect_scale === 'Fisher z' ? T('Fisher z 尺度') : T('自然尺度');
      const observedMethod = result.test === 't' ? T('双侧 Student t（自由度 k−1）') : T('双侧标准正态');
      const criticalMethod = result.test === 't' ? T('双侧 Student t（迭代自由度 {0}）',fmt(result.critical_df)) : T('双侧标准正态');
      host.replaceChildren(el('p', {text:T('加权失效安全数 N={0} · {1} 项研究 · {2}', result.fail_safe_n, result.n_studies, scale)}),
        el('p', {text:T('固定效应加权均值 {0}；已观察 p={1}（{2}）。', fmt(result.weighted_mean_effect), fmt(result.observed_p_value), observedMethod)}),
        el('p', {text:T('目标 α={0}；临界值 {1}（{2}）。公式值 {3} 向上取整，最低为零。', result.alpha, fmt(result.critical_value), criticalMethod, fmt(result.formula_value))}),
        ...[...(result.assumptions || []), ...(result.warnings || []), ...(result.limitations || [])].map(raw => { const translated = T(notes[raw] || raw); return el('p', {class:'hint',lang:translated === raw ? 'en' : null,text:translated}); }),
        el('p', {class:'hint',lang:'en',text:result.source_equations}),
        el('p', {class:'hint',lang:'en',text:(result.method_sources || []).join(' ')}),
        anAuditReportButtons(taskApi() + '/analysis/publication-bias/rosenberg', null,
          'reviewflow_rosenberg_audit', () => ticket === generation, body));
      record = {request:body, result}; output.disabled = false;
    } catch (error) { if (ticket === generation) host.textContent = error.message; }
    finally { clearInterval(timer); if (ticket === generation) run.disabled = false; }
  });
  output.addEventListener('click', () => {
    if (!record) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2)], {type:'application/json'}));
    const link = el('a', {href:url, download:'reviewflow_rosenberg.json'});
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
