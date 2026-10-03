(function () {
  const panel = $('#anRobustBayesPanel'), priors = $('#anRobustBayesPriors'), host = $('#anRobustBayesResult');
  const run = $('#anRobustBayesRun'), download = $('#anRobustBayesExport');
  let generation = 0, record = null, timer = null;
  function clear() { generation++; clearInterval(timer); run.disabled = false; host.replaceChildren(); record = null; download.disabled = true; panel.querySelectorAll('[data-bayes-report]').forEach(button => { if (button.dataset.originalLabel) button.textContent = button.dataset.originalLabel; button.disabled = true; }); }
  function addPrior() {
    clear(); const group = el('fieldset',{'data-prior-spec':''}), fields = el('div',{class:'row'});
    group.append(el('legend',{text:T('设定')}),fields);
    const specs = [['mu_prior_family','μ 的先验分布',['normal','student_t']],['mu_prior_mean','μ 的位置参数'],['mu_prior_scale','μ 的尺度'],['mu_prior_df','μ 的自由度'],
      ['tau_prior_family','τ 的先验分布',['half_normal','half_student_t']],['tau_prior_scale','τ 的尺度'],['tau_prior_df','τ 的自由度']];
    for (const [key,label,choices] of specs) {
      const node = choices ? el('select',{class:'input','data-prior-key':key,'aria-label':label}) : el('input',{class:'input',type:'number',step:'any','data-prior-key':key,'aria-label':label});
      if (choices) for (const value of ['',...choices]) node.append(el('option',{value,text:value ? T(({normal:'正态',student_t:'Student-t',half_normal:'半正态',half_student_t:'半 Student-t'})[value]) : T('请选择：{0}',label)}));
      fields.append(el('label',{text:label},node));
    }
    for (const parameter of ['mu','tau']) {
      const family = fields.querySelector(`[data-prior-key="${parameter}_prior_family"]`), df = fields.querySelector(`[data-prior-key="${parameter}_prior_df"]`);
      family.addEventListener('change',() => {
        clear(); df.disabled = family.value === 'normal' || family.value === 'half_normal';
        if (df.disabled) df.value = '';
        df.placeholder = df.disabled ? T('不适用') : T('自由度须为正数');
      });
    }
    group.append(el('button',{class:'btn',type:'button',text:T('删除设定'),onclick:() => { if (priors.children.length > 1) { clear(); group.remove(); } }}));
    priors.append(group);
  }
  $('#anRobustBayesAdd').addEventListener('click',addPrior); addPrior();
  panel.addEventListener('input',clear);
  ['anComparison','anOutcome','anTimepoint','anMeasure'].forEach(id => $('#'+id).addEventListener('input',clear));
  document.addEventListener('reviewflow:analysis-core-loaded',clear);
  document.addEventListener('reviewflow:analysis-task-changed',() => { priors.replaceChildren(); addPrior(); });
  run.addEventListener('click',async () => {
    clear(); const ticket = generation;
    try {
      const specs = Array.from(priors.children,group => Object.fromEntries(Array.from(group.querySelectorAll('[data-prior-key]'),node => {
        const key = node.dataset.priorKey;
        if (node.disabled) return [key,null];
        const raw = node.value.trim();
        if (!raw) throw new Error(T('请选择每项先验分布并填写适用参数。本分析不提供默认先验。'));
        if (key.endsWith('family')) return [key,raw];
        const value = Number(raw);
        if (!Number.isFinite(value) || (!key.endsWith('mean') && value <= 0)) throw new Error(T('先验参数须填写有限数值；尺度和自由度须为正数。'));
        return [key,value];
      })));
      const body = {comparison:anValue('anComparison'),outcome:anValue('anOutcome'),timepoint:anValue('anTimepoint'),measure:anValue('anMeasure')};
      if (!['RR','OR','HR','RATE_RATIO','RD','MD','SMD','FISHER_Z'].includes(body.measure)) throw new Error(T('当前支持 RR、OR、HR、RATE_RATIO、RD、MD、SMD 和 FISHER_Z。'));
      const grid = specs.length > 1; body[grid ? 'priors' : 'prior'] = grid ? specs : specs[0];
      run.disabled = true; const started = performance.now();
      const progress = () => { if (ticket === generation) host.textContent = T('正在计算 {0} 组先验设定…已等待 {1} 秒。Student-t 需要更多数值积分，设定越多耗时越长。',specs.length,((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress,500);
      const result = await api(taskApi()+'/analysis/bayesian/'+(grid ? 'prior-grid' : 'robust-meta'),{method:'POST',body});
      if (ticket !== generation) return;
      clearInterval(timer);
      const cells = grid ? result.cells : [result], fmt = value => Number(value).toPrecision(6), interval = values => values.map(fmt).join(' – ');
      const table = el('table'), rows = el('tbody');
      table.append(el('caption',{text:T('先验敏感性比较：{0} 项研究；μ 为 {1} 分析尺度；τ 是标准差',result.study_count,result.analysis_scale)}),
        el('thead',{},el('tr',{},...['设定','μ 均值','μ 中位数','μ 的 95% 可信区间','P(μ > 0)','τ 均值','τ 的 95% 可信区间'].map(text => el('th',{text:T(text)})))),rows);
      cells.forEach((cell,index) => rows.append(el('tr',{},...[String(index+1),fmt(cell.mu.mean),fmt(cell.mu.median),interval(cell.mu.credible_interval_95),fmt(cell.mu.probability_gt_zero),fmt(cell.tau.mean),interval(cell.tau.credible_interval_95)].map(text => el('td',{text})))));
      host.replaceChildren(table);
      cells.forEach((cell,index) => {
        host.append(el('p',{class:'hint'},T('设定 {0} 的参数：',index+1),el('span',{lang:'en',text:JSON.stringify(cell.priors)})));
        if (cell.ratio_summary) host.append(el('p',{text:T('设定 {0}：比值中位数 {1}，95% 可信区间 {2}。',index+1,fmt(cell.ratio_summary.median),interval(cell.ratio_summary.credible_interval_95))}));
      });
      if (result.influence_overview) host.append(el('p',{lang:'en',text:result.influence_overview}));
      if (result.integration) host.append(el('p',{class:'hint'},T('数值积分：方法 '),el('span',{lang:'en',text:result.integration.method}),T('；收敛：{0}；网格细化相对变化：{1}。',result.integration.converged ? T('是') : T('否'),fmt(result.integration.grid_refinement_relative_change))));
      for (const text of result.method_sources) host.append(el('p',{class:'hint',lang:'en',text}));
      host.append(el('p',{class:'hint',lang:'en',text:'Sources: '+result.sources.map(row => {
        const article = result.source_articles?.[row.source_key];
        return `${row.study_id}: ${article?.title || row.source_key}${article?.doi ? ` (DOI ${article.doi})` : ''}${row.source_locator ? `, ${row.source_locator}` : ''}`;
      }).join('; ')}));
      record = result; download.disabled = false; panel.querySelectorAll('[data-bayes-report]').forEach(button => button.disabled = false);
    } catch (error) { if (ticket === generation) host.replaceChildren(el('span',{lang:'en',text:error.message})); }
    finally { if (ticket === generation) { clearInterval(timer); run.disabled = false; } }
  });
  panel.querySelectorAll('[data-bayes-report]').forEach(button => button.addEventListener('click',async () => {
    if (!record) return;
    const ticket = generation, format = button.dataset.bayesReport, label = button.textContent;
    button.dataset.originalLabel = label;
    button.disabled = true;
    const started = Date.now(), progress = setInterval(() => { if (ticket === generation) button.textContent = T('正在导出…已等待 {0} 秒。',((Date.now()-started)/1000).toFixed(1)); },500);
    try {
      const blob = await api(taskApi()+'/analysis/bayesian/'+(record.request.priors ? 'prior-grid' : 'robust-meta')+'?format='+format,{method:'POST',body:record.request,blob:true});
      if (ticket !== generation) return;
      const url = URL.createObjectURL(blob), link = el('a',{href:url,download:'reviewflow_bayesian_audit.'+format});
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
    } catch (error) { if (ticket === generation) toast(error.message,'warn'); }
    finally { clearInterval(progress); if (ticket === generation) { button.textContent = label; button.disabled = !record; } }
  }));
  download.addEventListener('click',() => {
    if (!record) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(record,null,2)],{type:'application/json'}));
    const link = el('a',{href:url,download:'reviewflow_bayesian_prior_audit.json'});
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
  });
})();
