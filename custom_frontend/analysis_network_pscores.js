(function () {
  const host = $('#anNetworkPscoreResult'), run = $('#anNetworkPscoreRun');
  let generation = 0, timer;
  const clear = () => { generation++; clearInterval(timer); host.replaceChildren(); run.disabled = false; };
  ['anOutcome', 'anTimepoint', 'anMeasure', 'anNetworkReference', 'anNetworkBenefit', 'anNetworkGlsModel']
    .forEach(id => $('#'+id).addEventListener('input', clear));
  $('#anNetworkStudySave').addEventListener('click', clear);
  document.addEventListener('reviewflow:network-study-saved', clear);
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', clear);

  run.addEventListener('click', async () => {
    clear(); const ticket = generation;
    try {
      if (anValue('anNetworkGlsModel') !== 'random_reml_common_tau') throw new Error(T('请选择上方的随机效应 REML 网络模型。'));
      const benefit = anValue('anNetworkBenefit');
      if (!benefit) throw new Error(T('请选择有利方向。'));
      const params = new URLSearchParams({ outcome:anValue('anOutcome'),
        timepoint:anValue('anTimepoint'), measure:anValue('anMeasure'),
        reference:anValue('anNetworkReference'), benefit });
      run.disabled = true;
      const started = performance.now();
      const progress = () => { if (ticket === generation) host.textContent =
        T('正在计算 P-score…已等待 {0} 秒。暂无中间进度。',((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress, 500);
      const result = await api(taskApi() + '/analysis/network/pscores?' + params);
      if (ticket !== generation) return;
      host.replaceChildren();
      host.append(el('p', {},T('分析方法：'),el('span',{lang:'en',text:result.method}),' · ',
        T('效应指标：'),el('span',{lang:'en',text:result.measure}),' · ',T('分析尺度：'),
        el('span',{lang:'en',text:result.scale}),' · ',T('有利方向：'),T(benefit === 'higher' ? '效应越高越有利' : '效应越低越有利')));
      const table = el('table');
      const headings = ['排名', '治疗方案', 'P-score'].map(text => el('th', { text:T(text) }));
      table.append(el('thead', {}, el('tr', {}, ...headings)));
      const body = el('tbody');
      for (const row of result.ranking) {
        const values = [String(row.rank), row.treatment, Number(row.p_score).toPrecision(4)];
        body.append(el('tr', {}, ...values.map(text => el('td', { text }))));
      }
      table.append(body);
      const warningMap = {'P-scores summarize pairwise normal-model certainty; they are not probabilities of being best and do not establish transitivity, clinical superiority, or clinical importance.':'P-score 是基于正态模型的两两比较确定性平均值，不是成为最佳的概率；它不能验证传递性，也不能证明临床优越性或临床重要性。'};
      host.append(table,...result.warnings.map(warning => el('p', { class:'hint', ...(warningMap[warning] ? {text:T(warningMap[warning])} : {text:warning,lang:'en'}) })));
      const sources = el('details', {}, el('summary', { text:T('研究来源') }));
      sources.append(el('pre', { class:'hint', lang:'en', text:JSON.stringify(result.sources, null, 2) }));
      host.append(sources, anAuditReportButtons(taskApi()+'/analysis/network/pscores',params,
        'reviewflow_network_pscores_audit',() => ticket === generation),
        ...(result.method_sources || []).map(text => el('p',{class:'hint',lang:'en',text})),
        el('p', { class:'hint', text:T('使用该方法撰写论文时，请展开本区参考文献并引用方法来源。') }));
    } catch (error) { if (ticket === generation) { host.replaceChildren(el('span',{lang:'en',text:error.message})); toast(error.message, 'warn'); } }
    finally { if (ticket === generation) { clearInterval(timer); run.disabled = false; } }
  });
})();
