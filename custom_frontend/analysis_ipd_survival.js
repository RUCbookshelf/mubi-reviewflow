(function () {
  const host = $('#anIpdSurvivalResult');
  let generation = 0, record = null, activeTimer = null, renderLast = null;
  const clear = () => { generation++; clearInterval(activeTimer); record = null; renderLast = null; host.replaceChildren(); $('#anIpdSurvivalRun').disabled = false; $('#anIpdSurvivalExport').disabled = true; document.querySelectorAll('[data-cox-report]').forEach(button => button.disabled = true); };
  window.RFRefreshIpdSurvivalText = () => { if (renderLast) renderLast(); };
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', clear);
  ['anIpdSurvivalCenters','anIpdSurvivalScales','anIpdSurvivalTau'].forEach(id => $('#'+id).addEventListener('input',clear));
  $('#anIpdSurvivalExport').addEventListener('click', () => {
    if (!record) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(record,null,2)],{type:'application/json'}));
    const link = el('a',{href:url,download:'reviewflow_cox_summary.json'});
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
  });
  document.querySelectorAll('[data-cox-report]').forEach(button => button.addEventListener('click',async () => {
    if (!record) return;
    const ticket = generation, format = button.dataset.coxReport;
    const endpoint = record.endpoint;
    button.disabled = true;
    const label = button.textContent, started = performance.now();
    const progress = () => { if (ticket === generation) button.textContent = T('正在导出 Cox 审计报告…已等待 {0} 秒。', ((performance.now()-started)/1000).toFixed(1)); };
    progress(); const exportTimer = setInterval(progress, 500);
    try {
      const blob = await api(taskApi()+'/analysis/ipd/survival/'+endpoint+'?format='+format,{method:'POST',blob:true,
        body:{...record.parameters,participants:JSON.parse(anValue('anIpdSurvivalRows')),study_sources:JSON.parse(anValue('anIpdSurvivalSources'))}});
      if (ticket !== generation) return;
      const url = URL.createObjectURL(blob), link = el('a',{href:url,download:'reviewflow_cox_audit.'+format});
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
    } catch (error) { if (ticket === generation) toast(error.message,'warn'); }
    finally { clearInterval(exportTimer); button.textContent = label; if (ticket === generation) button.disabled = !record; }
  }));
  ['anIpdSurvivalRows', 'anIpdSurvivalSources'].forEach(id => $('#'+id).addEventListener('input', clear));
  $('#anIpdSurvivalTies').addEventListener('change', clear);
  $('#anIpdSurvivalMode').addEventListener('change', () => {
    $('#anIpdSurvivalCovariateFields').hidden = anValue('anIpdSurvivalMode') !== 'adjusted';
    $('#anIpdSurvivalMultiFields').hidden = anValue('anIpdSurvivalMode') !== 'multi';
    $('#anIpdSurvivalTwoStageFields').hidden = anValue('anIpdSurvivalMode') !== 'two_stage';
    clear();
  });
  ['anIpdSurvivalCenter', 'anIpdSurvivalScale'].forEach(id => $('#'+id).addEventListener('input', clear));

  $('#anIpdSurvivalRun').addEventListener('click', async () => {
    clear(); const ticket = generation, run = $('#anIpdSurvivalRun'); let timer;
    try {
      if (!anValue('anIpdSurvivalMode')) throw new Error(T('请选择 Cox 协变量模型。'));
      if (!anValue('anIpdSurvivalTies')) throw new Error(T('请选择 Cox 并列事件处理方法。'));
      const participants = JSON.parse(anValue('anIpdSurvivalRows'));
      const study_sources = JSON.parse(anValue('anIpdSurvivalSources'));
      const mode = anValue('anIpdSurvivalMode'), adjusted = mode === 'adjusted', twoStage = mode === 'two_stage';
      const number = id => {
        const text = anValue(id).trim(), value = Number(text);
        if (!text || !Number.isFinite(value)) throw new Error(T('请为“{0}”填写有限数值。', $('#'+id).getAttribute('aria-label')));
        return value;
      };
      const endpoint = {unadjusted:'stratified-cox',adjusted:'adjusted-stratified-cox',multi:'multi-adjusted-stratified-cox',two_stage:'two-stage-random-slopes'}[mode];
      const parameters = {ties:anValue('anIpdSurvivalTies')};
      if (adjusted) Object.assign(parameters,{covariate_center:number('anIpdSurvivalCenter'),covariate_scale:number('anIpdSurvivalScale')});
      if (mode === 'multi') Object.assign(parameters,{covariate_centers:JSON.parse(anValue('anIpdSurvivalCenters')),covariate_scales:JSON.parse(anValue('anIpdSurvivalScales'))});
      if (twoStage) {
        parameters.tau2_method = anValue('anIpdSurvivalTau');
        if (!parameters.tau2_method) throw new Error(T('请选择研究间方差估计方法。'));
      }
      run.disabled = true;
      const started = Date.now();
      const progress = () => { if (ticket === generation) host.textContent = T('正在拟合 Cox 模型…已等待 {0} 秒。', ((Date.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress,500); activeTimer = timer;
      const result = await api(taskApi() + '/analysis/ipd/survival/' + endpoint, {method:'POST',body:{participants,study_sources,...parameters}});
      clearInterval(timer);
      if (ticket !== generation) return;
      const draw = () => {
      host.replaceChildren();
      const fmt = value => value == null ? T('超出有限数值显示范围') : Number(value).toPrecision(5);
      host.append(el('p', { text:T('研究数：{0}；参与者数：{1}；事件数：{2}；并列事件：{3}', result.studies, result.n, result.events, result.ties) }),
        el('p', { text:T('风险比 HR：{0}（95% CI {1}–{2}）；log HR：{3}；SE：{4}。', fmt(twoStage ? result.pooled_hazard_ratio : result.hazard_ratio), fmt(result.hr_ci_low), fmt(result.hr_ci_high), fmt(twoStage ? result.pooled_log_hr : result.log_hr), fmt(result.se)) }),
        ...(adjusted ? [el('p', { text:T('协变量 HR：每增加 {0} 个原始单位为 {1}（95% CI {2}–{3}），中心值为 {4}。', fmt(result.covariate_scale), fmt(result.covariate_hazard_ratio), fmt(result.covariate_hr_ci_low), fmt(result.covariate_hr_ci_high), fmt(result.covariate_center)) })] : []),
        el('p', { class:'hint', text:twoStage ? T('两阶段随机效应模型合并未调整的研究内 Cox 处理效应。参与者记录仅用于本次计算，不会保存。') : T('模型假设各研究有共同的比例风险比，并允许各研究基线风险不同。参与者记录仅用于本次计算，不会保存。') }));
      const table = (headers,rows) => el('table',{},el('thead',{},el('tr',{},...headers.map(text => el('th',{text})))),el('tbody',{},...rows.map(row => el('tr',{},...row.map(text => el('td',{text:String(text)}))))));
      if (mode === 'multi') host.append(table(['协变量','中心值','尺度','log HR','SE','HR（95% CI）'].map(text => T(text)),Object.entries(result.covariate_effects).map(([name,row]) => [name,fmt(result.covariate_centers[name]),fmt(result.covariate_scales[name]),fmt(row.log_hr),fmt(row.se),`${fmt(row.hazard_ratio)}（${fmt(row.hr_ci_low)}–${fmt(row.hr_ci_high)}）`])));
      if (twoStage) {
        host.append(el('p',{text:T('{0}：τ²={1}（log HR 方差尺度）；固定效应权重 Q={2}（df={3}），p={4}；固定 Q 的 I²={5}%。',result.tau2_method,fmt(result.tau2),fmt(result.q),result.q_df,fmt(result.q_p),fmt(result.i2_percent))}),
          el('p',{text:T('HR 的 95% 预测区间：{0}', result.prediction_interval_hazard_ratio_95 ? result.prediction_interval_hazard_ratio_95.map(fmt).join('–') : T('不可用：至少需要 3 项研究'))}),
          table(['研究','来源','样本量','事件数','log HR','SE','权重比例'].map(text => T(text)),result.study_estimates.map(row => [row.study_id,row.source,row.n,row.events,fmt(row.log_hr),fmt(row.se),fmt(row.weight_share)])));
      }
      for (const text of [...(result.assumptions || []),...(result.warnings || []),...(result.method_sources || [])]) host.append(el('p',{class:'hint',lang:'en',text}));
      const sources = el('details', {}, el('summary', { text:T('研究与报告来源对应表') }));
      sources.append(el('pre', { class:'hint', text:JSON.stringify(result.study_sources, null, 2) }));
      host.append(sources, el('p', { class:'hint', text:T('请查阅方法原文；发表时引用实际使用的方法和研究报告。') }));
      };
      renderLast = draw; draw();
      record = {...result,parameters,endpoint}; $('#anIpdSurvivalExport').disabled = false;
      document.querySelectorAll('[data-cox-report]').forEach(button => button.disabled = false);
    } catch (error) { if (ticket === generation) { host.replaceChildren(el('p',{lang:'en',text:error.message})); toast(error.message, 'warn'); } }
    finally { clearInterval(timer); if (ticket === generation) run.disabled = false; }
  });
})();
