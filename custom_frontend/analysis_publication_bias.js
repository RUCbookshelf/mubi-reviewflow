(function () {
  const host = $('#anBiasResult'), audit = $('#anBiasAudit'), run = $('#anBiasRun');
  let generation = 0;
  const notes = {
    'two-sided asymptotic Kendall tau-b with tie adjustment':'双侧渐近 Kendall τ-b 检验，已调整并列秩。',
    'Effects share one comparison, outcome, time point, and measure; each row is an independent study.':'所有效应值对应同一比较、结局、时间点和指标；每行是一项独立研究。',
    'Standard errors are on the analyzed effect scale and give the sampling variances.':'标准误与所分析的效应值使用同一尺度，并用于计算抽样方差。',
    'Effects are standardized around their inverse-variance weighted mean using adjusted variances; the test uses an asymptotic null approximation.':'效应值以逆方差加权均值为中心，按校正后的方差标准化；检验使用渐近零假设近似。',
    "The observed study Z scores are combined with Stouffer's unweighted sum under independence.":'在研究相互独立的假设下，按 Stouffer 非加权方法合并已观察研究的 Z 值。',
    'Each missing study is assumed to have exactly Z=0; significance uses a two-sided alpha of 0.05.':'假定每项缺失研究的 Z 值恰为 0；显著性水平为双侧 α=0.05。',
    'null_effect is on the analyzed scale (log for ratios, Fisher z for FISHER_Z, natural otherwise); standard errors are on that same scale.':'零效应值与标准误均在分析尺度上：比值类为对数尺度，相关系数为 Fisher z，其余为自然尺度。',
    'Fewer than 10 studies: interpret cautiously because power and calibration may be limited; 10 is a rule-of-thumb warning, not a validity cutoff.':'研究数少于 10：检验效能和校准可能不足，请谨慎解读；10 只是经验提醒，不是有效性门槛。',
    "Begg's conventional asymptotic p-value can be miscalibrated; asymmetry or a fail-safe N does not prove publication bias or identify why studies are missing.":'Begg 检验的常规渐近 p 值可能校准不佳。漏斗图不对称或失效安全数不能证明发表偏倚，也不能解释研究缺失原因。',
  };
  const note = (raw, method) => method === 'rosenthal_fail_safe_n' && raw === "Begg's conventional asymptotic p-value can be miscalibrated; asymmetry or a fail-safe N does not prove publication bias or identify why studies are missing." ?
    T('失效安全数不能证明发表偏倚，也不能估计实际缺失研究数或解释缺失原因。') : T(notes[raw] || raw);
  const addLine = (value, source = false) => host.append(el('span', { lang:source ? 'en' : null, text:value }), document.createTextNode('\n'));
  const clear = () => { generation++; host.textContent = ''; audit.replaceChildren(); run.disabled = false; };
  ['anComparison', 'anOutcome', 'anTimepoint', 'anMeasure', 'anBiasMethod']
    .forEach(id => $('#'+id).addEventListener('input', clear));
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', clear);
  run.addEventListener('click', async () => {
    clear(); const ticket = generation;
    let timer;
    try {
      if (!anValue('anBiasMethod')) throw new Error(T('请选择发表偏倚诊断方法。'));
      const params = anAnalysisQuery(); params.set('method', anValue('anBiasMethod'));
      run.disabled = true;
      const started = performance.now();
      const progress = () => { if (ticket === generation) host.textContent = T('正在计算诊断…已等待 {0} 秒。', ((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress, 500);
      const result = await api(taskApi() + '/analysis/publication-bias?' + params);
      if (ticket !== generation) return;
      host.replaceChildren();
      addLine(T(result.method === 'rank_correlation' ? 'Begg–Mazumdar 等级相关检验' : 'Rosenthal 失效安全数 N'));
      const scale = result.effect_scale === 'log' ? T('对数尺度') : result.effect_scale === 'fisher_z' ? T('Fisher z 尺度') : T('自然尺度');
      addLine(T('{0} 项研究 · {1}', result.n_studies, scale));
      addLine(result.statistic_name ? T('Kendall τ-b={0}；p={1}。', Number(result.statistic).toPrecision(4), Number(result.p_value).toPrecision(4)) :
        T('失效安全数 N={0}；合并 p={1}。', result.fail_safe_n, Number(result.combined_p_value).toPrecision(4)));
      for (const raw of [result.p_value_method, ...(result.assumptions || []), result.small_study_warning, result.limitation].filter(Boolean)) {
        const translated = note(raw, result.method); addLine(translated, translated === raw);
      }
      for (const source of result.method_sources || []) addLine(source, true);
      addLine(T('使用该方法撰写论文时，请展开本区参考文献并引用方法来源。'));
      audit.append(anAuditReportButtons(taskApi() + '/analysis/publication-bias', params,
        'reviewflow_publication_bias_audit', () => ticket === generation));
    } catch (error) { if (ticket === generation) { host.textContent = error.message; toast(error.message, 'warn'); } }
    finally { clearInterval(timer); if (ticket === generation) run.disabled = false; }
  });
})();
(function () {
  const host = $('#anOrwinResult'), audit = $('#anOrwinAudit'), run = $('#anOrwinRun');
  let generation = 0;
  const notes = {
    'Each row is one independent study and contributes equally to the arithmetic observed mean.':'每行是一项独立研究，计算已观察效应值的算术平均值时各研究权重相同。',
    'Every missing study has the explicitly supplied missing_effect; target_effect and missing_effect are on the analyzed scale.':'假定每项缺失研究的效应值都等于所填的缺失研究平均效应值；目标值和假定值都使用分析尺度。',
    'Ratio estimates are log transformed, FISHER_Z estimates are already Fisher z, and other measures stay on their natural scale.':'原始比值效应在计算前取对数；FISHER_Z 输入已是 Fisher z；其他效应保持自然尺度。',
    'Standard errors are checked for finite positive values but are not used in this formula.':'标准误会检查为有限正数，但不参与 Orwin 公式计算。',
    'This sensitivity count does not estimate how many unpublished studies exist and does not prove publication bias.':'此敏感性指标不能估计实际未发表研究数，也不能证明发表偏倚。',
    'The arithmetic-mean calculation does not model study precision, heterogeneity, or dependencies beyond one selected effect per independent study.':'算术平均值未考虑研究精度、异质性或研究间依赖；每项独立研究只取一个效应值。',
    "Card's worked example uses raw r; FISHER_Z analyses must use Fisher z targets and missing-effect values, so its raw-r thresholds do not transfer directly.":'Card 书中算例使用原始 r；FISHER_Z 分析的目标值和假定缺失值必须用 Fisher z，不能直接套用书中的 r 阈值。',
  };
  const addLine = (value, source = false) => host.append(el('span', { lang:source ? 'en' : null, text:value }), document.createTextNode('\n'));
  const clear = () => { generation++; host.textContent = ''; audit.replaceChildren(); run.disabled = false; };
  ['anComparison', 'anOutcome', 'anTimepoint', 'anMeasure', 'anOrwinTarget', 'anOrwinMissing']
    .forEach(id => $('#'+id).addEventListener('input', clear));
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', clear);
  run.addEventListener('click', async () => {
    clear(); const ticket = generation;
    let timer;
    try {
      if (anValue('anOrwinTarget') === '' || anValue('anOrwinMissing') === '')
        throw new Error(T('请填写目标效应值和假定缺失研究的平均效应值。'));
      const body = {comparison:anValue('anComparison'), outcome:anValue('anOutcome'),
        timepoint:anValue('anTimepoint'), measure:anValue('anMeasure'),
        target_effect:Number(anValue('anOrwinTarget')), missing_effect:Number(anValue('anOrwinMissing'))};
      if (!Number.isFinite(body.target_effect) || !Number.isFinite(body.missing_effect))
        throw new Error(T('目标值和假定缺失值都必须是有限数值。'));
      run.disabled = true;
      const started = performance.now();
      const progress = () => { if (ticket === generation) host.textContent = T('正在计算 Orwin 失效安全数…已等待 {0} 秒。', ((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress, 500);
      const result = await api(taskApi() + '/analysis/publication-bias/orwin', {method:'POST', body});
      if (ticket !== generation) return;
      host.replaceChildren();
      addLine(T('Orwin 失效安全数 N'));
      const scale = result.effect_scale === 'log' ? T('对数尺度') : result.effect_scale === 'fisher_z' ? T('Fisher z 尺度') : T('自然尺度');
      addLine(T('{0} 项研究 · {1}', result.n_studies, scale));
      addLine(T('已观察效应值的算术平均值：{0}；目标值：{1}；假定缺失研究均值：{2}。', Number(result.observed_mean).toPrecision(4), result.target_effect, result.missing_effect));
      addLine(T('失效安全数 N={0}。', result.fail_safe_n));
      for (const raw of [...(result.assumptions || []), ...(result.limitations || [])]) {
        const translated = T(notes[raw] || raw); addLine(translated, translated === raw);
      }
      for (const source of result.method_sources || []) addLine(source, true);
      addLine(T('使用该方法撰写论文时，请展开本区参考文献并引用方法来源。'));
      audit.append(anAuditReportButtons(taskApi() + '/analysis/publication-bias/orwin', null,
        'reviewflow_orwin_audit', () => ticket === generation, body));
    } catch (error) { if (ticket === generation) { host.textContent = error.message; toast(error.message, 'warn'); } }
    finally { clearInterval(timer); if (ticket === generation) run.disabled = false; }
  });
})();
