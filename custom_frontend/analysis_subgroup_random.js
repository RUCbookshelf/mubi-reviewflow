(function () {
  const host = $('#anSubgroupResult'), download = $('#anSubgroupExport'), run = $('#anSubgroupRun');
  let result = null, generation = 0, timer;
  const clear = () => { generation++; clearInterval(timer); result = null; host.replaceChildren(); download.disabled = true; run.disabled = false; };
  ['anComparison', 'anOutcome', 'anTimepoint', 'anMeasure', 'anAdvancedDimension', 'anSubgroupTau']
    .forEach(id => $('#'+id).addEventListener('input', clear));
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', clear);
  const number = value => value == null ? '—' : Number(value).toPrecision(5);
  const warningText = raw => {
    const fixed = {
      'The between-subgroups test uses one DerSimonian-Laird tau-squared shared by all subgroups; subgroups should be prespecified in the review protocol (Cochrane Handbook section 10.11) and each study must contribute to one subgroup only.':'亚组差异检验在各亚组内共用一个 DL τ²。亚组应在综述方案中预先定义，且每项研究只能归入一个亚组（Cochrane Handbook §10.11）。',
      'Each subgroup uses its own DerSimonian-Laird tau-squared; with few studies per subgroup these estimates are imprecise and a pooled tau-squared is usually preferable below roughly 10-20 studies per subgroup (Borenstein 2021, chapter 21).':'每个亚组分别估计 DL τ²；每组研究较少时估计不精确。每组少于约 10–20 项研究时通常宜共用 τ²（Borenstein 2021，第 21 章）。',
      'Pooled tau-squared was truncated at zero; the shared-tau-squared random-effects analysis coincides with the fixed-effect analysis.':'共用 τ² 截断为零；此时共用 τ² 的随机效应结果与固定效应结果一致。',
      'Fewer than ten studies: subgroup analyses have low power and tau-squared estimates are imprecise (Cochrane Handbook section 10.11.5.1).':'研究数少于 10：亚组分析效能低，τ² 估计不精确（Cochrane Handbook §10.11.5.1）。',
    };
    if (fixed[raw]) return T(fixed[raw]);
    let match = /^tau-squared was truncated at zero for subgroup\(s\) (.+): their within-subgroup dispersion does not exceed sampling error\.$/.exec(raw);
    if (match) return T('亚组 {0} 的 τ² 截断为零：组内研究间离散程度未超过抽样误差。', match[1]);
    match = /^Subgroup '(.+)' has only (\d+) studies; within-subgroup heterogeneity estimates are imprecise\.$/.exec(raw);
    if (match) return T('亚组“{0}”仅有 {1} 项研究；组内异质性估计不精确。', match[1], match[2]);
    match = /^(\d+) pairwise subgroup comparisons are multiple tests; only the omnibus test \(overall\.q_between_p\) controls the type-I error across subgroups, and Bonferroni-adjusted p-values are provided for the pairwise z-tests\.$/.exec(raw);
    if (match) return T('进行了 {0} 次成对亚组比较。请优先查看总体亚组差异检验；成对 z 检验同时提供 Bonferroni 校正 p 值。', match[1]);
    return raw;
  };
  run.addEventListener('click', async () => {
    clear();
    const current = generation;
    try {
      const policy = anValue('anSubgroupTau'), dimension = anValue('anAdvancedDimension');
      if (!policy || !dimension) throw new Error(T('请选择研究特征和亚组内 τ² 处理方式。'));
      const body = {comparison:anValue('anComparison'), outcome:anValue('anOutcome'), timepoint:anValue('anTimepoint'),
        measure:anValue('anMeasure'), dimension_id:Number(dimension), tau2_policy:policy};
      run.disabled = true;
      const started = performance.now();
      const progress = () => { if (current === generation) host.textContent =
        T('正在比较亚组…已等待 {0} 秒；暂无法显示中间进度。', ((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress, 500);
      const data = await api(taskApi()+'/analysis/subgroup-random', {method:'POST', body});
      if (current !== generation) return;
      result = data;
      const table = el('table');
      const scale = data.analysis_scale === 'log' ? T('对数尺度') : T('自然尺度');
      table.append(el('thead', {}, el('tr', {}, ...[T('亚组'), T('研究数'), T('估计值'), T('标准误'), T('95% 置信区间'), T('τ²（DL；{0}方差）', scale)].map(text => el('th', { text })))));
      const rows = el('tbody');
      Object.entries(data.subgroups).forEach(([name, group]) => rows.append(el('tr', {},
        ...[name, String(group.k), number(group.pooled), number(group.se), group.ci.map(number).join('–'), number(group.tau2)]
          .map(text => el('td', { text })))));
      table.append(rows);
      const r2 = data.r2_variance_explained == null ? T('不可计算') : (100 * data.r2_variance_explained).toFixed(1) + '%';
      const method = data.tau2_policy === 'pooled' ? T('随机效应亚组分析：按 DL 法估计并共用亚组内 τ²；按模型权重计算 Q 分解和 z 检验。') :
        T('随机效应亚组分析：各亚组按 DL 法分别估计 τ²；按模型权重计算 Q 分解和 z 检验。');
      host.replaceChildren(el('p', { class:'hint', text:method }), el('p', { text:T('{0} 项研究 · {1} · {2}（估计值、区间及成对差异均保持此尺度）。', data.n_studies, data.measure, scale) }), table,
        el('p', { text:T('亚组间 Q={0}，自由度={1}，p={2}；R²（τ² 降幅）={3}。', number(data.overall.q_between), data.overall.q_between_df, number(data.overall.q_between_p), r2) }),
        ...(data.warnings || []).map(raw => { const translated = warningText(raw); return el('p', { class:'hint', lang:translated === raw ? 'en' : null, text:translated }); }),
        el('details', {}, el('summary', { text:T('成对差异、校正后的 p 值与来源记录') }),
          el('pre', { class:'hint', text:JSON.stringify({ pairwise:data.pairwise, input_data:data.input_data }, null, 2) })),
        el('p', { class:'hint', lang:'en', text:data.method_sources.join(' ') }),
        anAuditReportButtons(taskApi()+'/analysis/subgroup-random', null,
          'reviewflow_random_subgroups_audit', () => current === generation, body));
      download.disabled = false;
    } catch (error) { if (current === generation) host.textContent = error.message; }
    finally { if (current === generation) { clearInterval(timer); run.disabled = false; } }
  });
  download.addEventListener('click', () => {
    if (!result) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type:'application/json' }));
    el('a', { href:url, download:'reviewflow_random_subgroups.json' }).click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
