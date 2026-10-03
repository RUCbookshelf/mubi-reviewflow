/* Research influence diagnostics: localized presentation of unchanged API results. */
(function () {
  const host = $('#anInfluenceResult'), run = $('#anInfluenceRun');
  let generation = 0, timer, cached = null;
  const clear = () => { generation++; clearInterval(timer); cached = null; host.replaceChildren(); run.disabled = false; };
  ['anComparison', 'anOutcome', 'anTimepoint', 'anMeasure', 'anModel'].forEach(id => $('#'+id).addEventListener('input', clear));
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', clear);
  function render(result,params,ticket) {
    const scale = analysisMeasureCatalog?.measures.find(item => item.code === result.measure)?.scale || 'analysis';
    const tauMethod = {fixed:T('固定模型；τ² 设为 0'),random:'DerSimonian–Laird',random_pm:'Paule–Mandel',random_reml:'REML'}[result.model] || result.model;
    const model = {fixed:T('固定效应 · 共同效应'),random:T('随机效应 · DL'),random_pm:T('随机效应 · PM'),random_reml:T('随机效应 · REML')}[result.model] || result.model;
    host.replaceChildren(el('p',{text:T('{0} 项研究 · {1} · {2} · {3} 分析尺度',result.results.length,result.measure,model,RFAnalysisLocale.term(scale))}));
    const table = el('table',{class:'an-diagnostic-table'});
    const headings = [T('研究'),T('删除后合并效应'),T('删除后 τ²（{0}；{1} 方差尺度）',tauMethod,RFAnalysisLocale.term(scale)),T('标准化残差'),"Cook's D",T('来源')];
    table.append(el('thead',{},el('tr',{},...headings.map(text=>el('th',{text})))));
    const body=el('tbody'),fmt=value=>value==null?'—':Number(value).toPrecision(4);
    for(const row of result.results)body.append(el('tr',{},el('td',{translate:'no',text:row.study_id}),
      ...[row.leave_one_out_pooled_analysis,row.leave_one_out_tau2,row.externally_standardized_residual,row.cooks_distance].map(value=>el('td',{text:fmt(value)})),el('td',{translate:'no',text:row.source_key||'—'})));
    table.append(body);host.append(table,el('p',{class:'hint',text:T('这些是模型诊断值，不是自动排除规则。解释较大数值前，请核对研究设计、所选结果和来源报告。')}));
    for(const source of result.method_sources||[])host.append(el('p',{class:'hint'},RFAnalysisLocale.message(source)));
    host.append(anAuditReportButtons(taskApi() + '/analysis/influence',params,'reviewflow_influence_audit',()=>ticket===generation));
  }
  run.addEventListener('click',async()=>{
    clear();const ticket=generation;
    try {
      anRequireModel();
      const params=new URLSearchParams({comparison:anValue('anComparison'),outcome:anValue('anOutcome'),timepoint:anValue('anTimepoint'),measure:anValue('anMeasure'),model:anValue('anModel')});
      run.disabled=true;const started=performance.now();
      const progress=()=>{if(ticket===generation)host.textContent=T('正在计算研究影响诊断…已等待 {0} 秒。',((performance.now()-started)/1000).toFixed(1));};
      progress();timer=setInterval(progress,500);
      const result=await api(taskApi()+'/analysis/influence?'+params);
      if(ticket!==generation)return;
      cached={result,params,ticket};render(result,params,ticket);
    } catch(error){if(ticket===generation){host.replaceChildren(RFAnalysisLocale.message(error.message));toast(T('分析未完成，请查看错误详情。'),'warn');}}
    finally{if(ticket===generation){clearInterval(timer);run.disabled=false;}}
  });
  document.addEventListener('reviewflow:language-changed',()=>{if(cached)render(cached.result,cached.params,cached.ticket);});
})();
