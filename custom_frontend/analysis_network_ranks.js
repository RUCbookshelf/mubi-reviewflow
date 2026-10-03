(function () {
  const host = $('#anNetworkRanksResult'), run = $('#anNetworkRanksRun'), output = $('#anNetworkRanksExport');
  const reports = document.querySelectorAll('[data-network-ranks-report]');
  let generation = 0, record = null, timer = null;
  function clear() { generation++; clearInterval(timer); record = null; run.disabled = false; output.disabled = true; reports.forEach(button => { if (button.dataset.originalLabel) button.textContent = button.dataset.originalLabel; button.disabled = true; }); host.replaceChildren(); }
  ['anOutcome','anTimepoint','anMeasure','anNetworkReference','anNetworkBenefit','anNetworkGlsModel','anNetworkRanksN','anNetworkRanksSeed']
    .forEach(id => $('#'+id).addEventListener('input', clear));
  $('#anNetworkStudySave').addEventListener('click', clear);
  document.addEventListener('reviewflow:network-study-saved', clear);
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', clear);
  run.addEventListener('click', async () => {
    clear(); const ticket = generation;
    try {
      const n = Number(anValue('anNetworkRanksN')), seedText = anValue('anNetworkRanksSeed'), seed = Number(seedText);
      if (anValue('anNetworkGlsModel') !== 'random_reml_common_tau') throw new Error(T('请选择上方的随机效应 REML 网络模型。'));
      if (!anValue('anNetworkBenefit')) throw new Error(T('请选择有利方向。'));
      if (!Number.isInteger(n) || n < 2 || n > 100000 || seedText === '' || !Number.isInteger(seed) || seed < 0 || seed > 4294967295)
        throw new Error(T('模拟次数须为 2–100000 的整数，随机种子须为 0–4,294,967,295 的整数。'));
      const body = {outcome:anValue('anOutcome'), timepoint:anValue('anTimepoint'), measure:anValue('anMeasure'),
        reference:anValue('anNetworkReference'), model:anValue('anNetworkGlsModel'), benefit:anValue('anNetworkBenefit'), n_sim:n, seed};
      run.disabled = true;
      const started = performance.now();
      const progress = () => { if (ticket === generation) host.textContent = T('正在拟合网络并模拟 {0} 次排名…已等待 {1} 秒。',n,((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress, 500);
      const result = await api(taskApi() + '/analysis/network/ranks', {method:'POST', body});
      if (ticket !== generation) return;
      clearInterval(timer);
      const fmt = value => Number(value).toPrecision(5), table = el('table'), rows = el('tbody');
      table.append(el('thead', {}, el('tr', {}, ...['治疗方案','SUCRA','MC SE（SUCRA 模拟误差）','P(rank 1)','MC SE（P(rank 1) 模拟误差）','平均排名'].map(text => el('th',{text:T(text)})))), rows);
      for (const row of result.ranking) rows.append(el('tr', {}, ...[row.treatment, fmt(row.sucra), fmt(result.mc_error.sucra_se[row.treatment]),
        fmt(row.p_best), fmt(result.mc_error.p_best_se[row.treatment]), fmt(row.mean_rank)].map(text => el('td',{text}))));
      host.replaceChildren(el('p', {},T('{0} 次模拟 · 随机种子 {1} · ',result.n_sim,result.seed),
        T(result.benefit === 'higher' ? '效应越高越有利' : '效应越低越有利'),' · ',
        el('span',{lang:'en',text:`${result.measure} (${result.scale})`}),' · ',T('参考方案：'),result.reference),table);
      const probabilities = el('table'), ranks = el('tbody');
      probabilities.append(el('caption', {text:T('排名概率（rank 1 表示所选方向下的最高排名）')}),
        el('thead', {}, el('tr', {}, el('th',{text:T('治疗方案')}), ...result.treatments.map((_,i) => el('th',{text:T('排名 {0}',i+1)})))), ranks);
      for (const row of result.ranking) ranks.append(el('tr', {}, el('th',{scope:'row',text:row.treatment}),
        ...row.rankogram.map(count => el('td',{text:`${(100*count/result.n_sim).toFixed(2)}%`}))));
      const warningMap = {
        'SUCRA and p-best are relative model-based ranking summaries; the top-ranked treatment is not necessarily the best choice and the ranking does not establish clinical superiority or importance.':'SUCRA 和 P(rank 1) 是相对的模型排名摘要；排名最高的治疗方案不一定是最佳选择，排名也不能证明临床优越性或重要性。',
        'netmeta::rankogram / netrank(method="SUCRA") resample independent normal effects from the diagonal of the fitted covariance and pair them with netmeta\'s own tau-squared convention; this module resamples the full reference-coded covariance of network_random, so the two implementations coincide only up to Monte Carlo error when the fitted correlations are negligible.':'netmeta::rankogram / netrank(method="SUCRA") 从拟合协方差矩阵的对角线抽取独立正态效应，并使用 netmeta 自身的 τ² 口径；本模块从 network_random 完整的参考组编码协方差中抽样。只有拟合相关性可忽略时，两者才会在蒙特卡洛误差范围内一致。',
        'Ranking conditions on the fitted common tau-squared and the normal approximation, does not propagate heterogeneity estimation uncertainty, and inherits the consistency model\'s transitivity assumptions.':'排名以拟合得到的共同 τ² 和正态近似为条件，不传播异质性估计的不确定性，并沿用一致性模型的传递性假设。'
      };
      const warnings = result.warnings.map(text => {
        const mc = text.match(/^Monte Carlo ranking with n_sim=(\d+) leaves estimated standard errors up to ([\d.e+-]+) for SUCRA and ([\d.e+-]+) for p-best; rank summaries remain subject to simulation error that shrinks as 1\/sqrt\(n_sim\)\.$/);
        return mc ? el('p',{class:'hint',text:T('蒙特卡洛排名使用 {0} 次模拟；SUCRA 的标准误最高为 {1}，P(rank 1) 的标准误最高为 {2}。排名仍有模拟误差，随模拟次数增加按 1/√n 缩小。',...mc.slice(1))})
          : warningMap[text] ? el('p',{class:'hint',text:T(warningMap[text])}) : el('p',{class:'hint',lang:'en',text});
      });
      const scroll = el('div', {style:'overflow-x:auto'}); scroll.append(probabilities); host.append(scroll,
        ...warnings,
        ...(result.method_sources || []).map(text => el('p',{class:'hint',lang:'en',text})),
        el('p',{class:'hint'},T('来源研究：'),Object.entries(result.sources).map(([study,source]) => {
          const article = result.source_articles?.[source];
          return `${study}: ${article?.title || source}${article?.doi ? ` (DOI ${article.doi})` : ''}`;
        }).join('; ')));
      record = result; output.disabled = false; reports.forEach(button => button.disabled = false);
    } catch (error) { if (ticket === generation) host.replaceChildren(el('span',{lang:'en',text:error.message})); }
    finally { if (ticket === generation) { clearInterval(timer); run.disabled = false; } }
  });
  output.addEventListener('click', () => {
    if (!record) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(record,null,2)], {type:'application/json'}));
    const link = el('a',{href:url,download:'reviewflow_network_ranks.json'});
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
  });
  reports.forEach(button => button.addEventListener('click', async () => {
    if (!record) return;
    const ticket = generation, label = button.textContent, format = button.dataset.networkRanksReport;
    button.disabled = true;
    const started = performance.now();
    button.dataset.originalLabel = label;
    const progress = () => { if (ticket === generation) button.textContent = T('正在重新生成审计报告…已等待 {0} 秒。',((performance.now()-started)/1000).toFixed(1)); };
    progress(); const exportTimer = setInterval(progress, 500);
    try {
      const blob = await api(taskApi() + '/analysis/network/ranks?format=' + format,
        {method:'POST', body:record.request, blob:true});
      if (ticket !== generation) return;
      const url = URL.createObjectURL(blob), link = el('a',{href:url,download:'reviewflow_network_ranks_audit.'+format});
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
    } catch (error) { if (ticket === generation) toast(error.message,'warn'); }
    finally { clearInterval(exportTimer); button.textContent = label; button.disabled = !record || ticket !== generation; }
  }));
})();
