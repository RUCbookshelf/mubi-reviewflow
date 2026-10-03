(function () {
  const host = $('#anPetPeeseResult');
  const run = $('#anPetPeeseRun');
  const notes = {
    'normal 95% Wald; known-sampling-variance WLS':'已知抽样方差的 WLS 正态 95% Wald 区间。',
    'Fewer than 10 studies: interpret cautiously because regression power and calibration may be limited; 10 is a rule of thumb, not a validity cutoff.':'研究少于 10 项时请谨慎解读：回归效能和校准可能有限。10 项只是经验参考，不是有效性门槛。',
    'Each row is one independent study effect for the same comparison, outcome, time point, and measure.':'每行代表同一比较、结局、时间点和效应指标中的一项独立研究效应。',
    'PET regresses effect on SE; PEESE regresses effect on SE squared; both use weights 1/SE squared.':'PET 将效应对 SE 回归；PEESE 将效应对 SE² 回归；两者均使用 1/SE² 权重。',
    'The intercept is the fitted effect extrapolated to SE=0, and intervals use the known sampling variances.':'截距是外推至 SE=0 时的拟合效应；区间使用已知抽样方差计算。',
    'Ratio estimates are analyzed on the log scale, with standard errors already on that scale; FISHER_Z remains on the Fisher z scale.':'比值估计在对数尺度分析，标准误也须已在该尺度；FISHER_Z 保持 Fisher z 尺度。',
    'PET and PEESE are reported separately; no conditional estimate-selection rule is applied.':'PET 和 PEESE 分别报告，不按条件规则自动择取估计值。',
    'Small-study patterns can reflect heterogeneity, chance, or study-design differences; these fits do not establish publication or outcome-reporting bias and do not provide a causal explanation.':'小样本研究效应模式也可能来自异质性、偶然因素或研究设计差异。本拟合不能证明发表偏倚或结局报告偏倚，也不能给出因果解释。',
  };
  let generation = 0;
  const clear = () => { generation++; host.replaceChildren(); run.disabled = false; };
  ['anComparison', 'anOutcome', 'anTimepoint', 'anMeasure'].forEach(id => $('#'+id).addEventListener('input', clear));
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', clear);
  run.addEventListener('click', async () => {
    clear(); const ticket = generation;
    let timer;
    try {
      const params = new URLSearchParams({ comparison:anValue('anComparison'), outcome:anValue('anOutcome'),
        timepoint:anValue('anTimepoint'), measure:anValue('anMeasure') });
      run.disabled = true;
      const started = performance.now();
      const progress = () => { if (ticket === generation) host.textContent =
        T('正在拟合 PET 和 PEESE…已等待 {0} 秒。', ((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress, 500);
      const result = await api(taskApi() + '/analysis/publication-bias/pet-peese?' + params);
      if (ticket !== generation) return;
      host.replaceChildren();
      const scale = result.effect_scale === 'log' ? T('对数尺度')
        : result.effect_scale === 'fisher_z' ? T('Fisher z 尺度') : T('自然尺度');
      host.append(el('p', {}, el('span', { text:T('{0} 项研究', result.n_studies) }), ' · ', result.measure,
        ' · ', el('span', { text:T('分析尺度：') }), ' ', el('span', { text:scale })),
        el('p', { class:'hint', text:result.effect_scale === 'log'
          ? T('系数和区间均为对数比值。截距可指数化为比值。')
          : T('系数和区间均使用所列分析尺度。') }));
      const table = el('table');
      table.append(el('thead', {}, el('tr', {}, ...['拟合','项','估计值','SE','95% CI'].map(text => el('th', { text:T(text) })))));
      const body = el('tbody');
      for (const [name, fit] of Object.entries(result.fits)) {
        for (const [term, coefficient] of Object.entries(fit.coefficients)) {
          const label = term === 'intercept' ? T('截距（SE=0 时的外推效应）')
            : term === 'slope' ? T('{0} 斜率（每 {1}）', name, name === 'PET' ? 'SE' : 'SE²') : term;
          const values = [name, label, Number(coefficient.estimate).toPrecision(5), Number(coefficient.se).toPrecision(5),
            `${Number(coefficient.ci_low).toPrecision(5)}–${Number(coefficient.ci_high).toPrecision(5)}`];
          body.append(el('tr', {}, ...values.map(text => el('td', { text }))));
        }
      }
      table.append(body); host.append(table);
      for (const raw of [result.ci_method, result.small_study_warning, ...(result.assumptions || []), result.limitation]) {
        if (!raw) continue;
        const translated = T(notes[raw] || raw);
        host.append(el('p', { class:'hint', lang:notes[raw] ? null : 'en', text:translated }));
      }
      host.append(el('p', { class:'hint', text:T('PET 与 PEESE 截距是并列的敏感性估计，不会自动选择其一。发表时请引用实际使用的方法和研究来源。') }),
        el('p', { class:'hint', lang:'en', text:(result.method_sources || []).join(' ') }),
        anAuditReportButtons(taskApi() + '/analysis/publication-bias/pet-peese', params,
          'reviewflow_pet_peese_audit', () => ticket === generation));
    } catch (error) { if (ticket === generation) { host.textContent = error.message; toast(error.message, 'warn'); } }
    finally { clearInterval(timer); if (ticket === generation) run.disabled = false; }
  });
})();
