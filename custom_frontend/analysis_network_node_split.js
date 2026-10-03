(function () {
  const host = $('#anNetworkSplitResult'), run = $('#anNetworkSplitRun');
  let generation = 0, timer = null;
  const clear = () => { generation++; clearInterval(timer); host.replaceChildren(); run.disabled = false; };
  ['anOutcome', 'anTimepoint', 'anMeasure', 'anNetworkSplitTreatment', 'anNetworkSplitComparator', 'anNetworkSplitModel']
    .forEach(id => $('#'+id).addEventListener('input', clear));
  $('#anNetworkStudySave').addEventListener('click', clear);
  document.addEventListener('reviewflow:network-study-saved', clear);
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', clear);
  run.addEventListener('click', async () => {
    clear(); const ticket = generation;
    try {
      if (!anValue('anNetworkSplitModel')) throw new Error(T('请选择节点分裂模型。'));
      const params = new URLSearchParams({ outcome:anValue('anOutcome'),
        timepoint:anValue('anTimepoint'), measure:anValue('anMeasure'),
        treatment:anValue('anNetworkSplitTreatment'), comparator:anValue('anNetworkSplitComparator'),
        model:anValue('anNetworkSplitModel') });
      run.disabled = true;
      const started = performance.now();
      const progress = () => { if (ticket === generation) host.textContent =
        T('正在比较直接与间接证据…已等待 {0} 秒；暂无中间进度。',((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress, 500);
      const result = await api(taskApi() + '/analysis/network/node-split?' + params);
      if (ticket !== generation) return;
      host.replaceChildren();
      const random = params.get('model') === 'random';
      host.append(el('p', { text:T('{0} / {1} · {2} · {3} 直接/间接比较',result.treatment,result.comparator,result.measure,T(random ? '随机效应（REML）' : '固定效应')) }));
      const table = el('table');
      const seHeading = result.direct.se_scale === 'natural' ? 'SE（自然尺度）' : result.direct.se_scale === 'log' ? 'SE（对数尺度）' : 'SE（未注明尺度）';
      table.append(el('thead', {}, el('tr', {}, ...['证据来源', '估计值', seHeading, '95% CI', '研究', ...(random ? ['τ²'] : [])].map(text => el('th', { text:T(text) })))));
      const rows = el('tbody');
      for (const [label, item] of [['直接证据', result.direct], ['间接证据', result.indirect]]) {
        rows.append(el('tr', {}, ...[label, Number(item.estimate).toPrecision(5), Number(item.se).toPrecision(5),
          `[${Number(item.ci_low).toPrecision(5)}, ${Number(item.ci_high).toPrecision(5)}]`,
          item.study_ids.map(id => `${id} (${result.sources[id] || '?'}${result.source_locators?.[id] ? '; ' + result.source_locators[id] : ''})`).join('; '),
          ...(random ? [T("{0} ({1}; {2} variance scale)", Number(item.tau2).toPrecision(5),RFAnalysisLocale.term(item.tau2_method),RFAnalysisLocale.term(item.tau2_scale))] : [])].map(text => el('td', { text }))));
      }
      table.append(rows);
      host.append(table, el('p', { text:T('直接 − 间接（{0}）：差值 {1}；SE {2}；p {3}。',result.disagreement.scale,Number(result.disagreement.estimate).toPrecision(5),Number(result.disagreement.se).toPrecision(5),Number(result.disagreement.p_value).toPrecision(4)) }),
        el('p', { class:'hint', text:random
          ? T('直接与间接证据分别估计 REML τ²，并假定研究区组相互独立。这与 netmeta 使用全网共同 τ² 的口径不同。Wald 区间以 τ² 为条件。本诊断不检验传递性或效应修饰因素平衡。请核对并引用实际使用的方法和来源报告。')
          : T('本地差异诊断采用固定效应，并假定研究相互独立；不检验传递性或效应修饰因素平衡。请引用实际使用的方法和来源报告。') }));
      host.append(anAuditReportButtons(taskApi()+'/analysis/network/node-split',params,
        'reviewflow_network_node_split_audit',() => ticket === generation),
        ...(result.method_sources || []).map(text => el('p',{class:'hint',lang:'en',text})));
    } catch (error) { if (ticket === generation) { host.replaceChildren(el('span',{text:error.message, ...(['请选择节点分裂模型。'].some(key=>T(key)===error.message)?{}:{lang:'en'})})); toast(error.message, 'warn'); } }
    finally { if (ticket === generation) { clearInterval(timer); run.disabled = false; } }
  });
})();
