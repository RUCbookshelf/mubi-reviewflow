/* Profile interval presentation; endpoint, parameters and calculations are unchanged. */
(function () {
  const host = $('#anTauProfileResult'), run = $('#anTauProfileRun');
  let generation = 0, timer, cached = null;
  const clear = () => { generation++; clearInterval(timer); cached = null; host.replaceChildren(); run.disabled = false; };
  ['anComparison', 'anOutcome', 'anTimepoint', 'anMeasure'].forEach(id => $('#'+id).addEventListener('input', clear));
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', clear);
  function render(result, params, ticket) {
    const fmt = value => value == null ? T('上限不可用') : Number(value).toPrecision(5);
    host.replaceChildren(el('p', {text:T('REML τ²：{0}；95% 轮廓区间：[{1}, {2}]；方差尺度：{3}。',fmt(result.estimate),fmt(result.lower),fmt(result.upper),RFAnalysisLocale.term(result.scale))}));
    host.append(el('p',{class:'hint'},T('{0} 项独立研究。',result.sources.length),' ',T('请引用原始轮廓似然方法及研究报告。')),
      el('p',{class:'hint'},...result.sources.map((row,index)=>el('span',{translate:'no',text:(index?'；':'')+`${row.study_id} (${row.source_key})`}))));
    if (!result.converged) host.append(el('p',{class:'an-warning',role:'alert',text:T('区间上限未收敛，不得将其报告为有限界限。')}));
    for(const source of result.method_sources||[])host.append(el('p',{class:'hint'},RFAnalysisLocale.message(source)));
    host.append(anAuditReportButtons(taskApi() + '/analysis/tau2/profile-interval', params, 'reviewflow_tau2_profile_audit', () => ticket === generation));
  }
  run.addEventListener('click', async () => {
    clear(); const ticket = generation;
    try {
      const params = new URLSearchParams({ comparison:anValue('anComparison'), outcome:anValue('anOutcome'), timepoint:anValue('anTimepoint'), measure:anValue('anMeasure') });
      run.disabled = true; const started = performance.now();
      const progress = () => { if (ticket === generation) host.textContent = T('正在计算 τ² 轮廓区间…已等待 {0} 秒。',((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress, 500);
      const result = await api(taskApi() + '/analysis/tau2/profile-interval?' + params);
      if (ticket !== generation) return;
      cached = {result,params,ticket}; render(result,params,ticket);
    } catch (error) { if (ticket === generation) { host.replaceChildren(RFAnalysisLocale.message(error.message)); toast(T('分析未完成，请查看错误详情。'), 'warn'); } }
    finally { if (ticket === generation) { clearInterval(timer); run.disabled = false; } }
  });
  document.addEventListener('reviewflow:language-changed',()=>{if(cached)render(cached.result,cached.params,cached.ticket);});
})();
