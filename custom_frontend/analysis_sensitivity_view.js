/* One-call sensitivity matrix; API remains the source of all calculations. */
(function () {
  const host = $('#anSensitivityResult');
  const exportButton = $('#anSensitivityExport');
  const runButton = $('#anSensitivityRun');
  const bootstrap = $('#anSensitivityBootstrap');
  let report = null;
  let request = 0;
  let progressTimer = null;
  const known = {
    'fixed-effect inverse variance':'固定效应逆方差模型',
    'DerSimonian-Laird random effects':'DerSimonian–Laird 随机效应',
    'restricted maximum likelihood random effects':'REML 随机效应',
    'Wald normal interval':'Wald 正态区间',
    'modified Hartung-Knapp-Sidik-Jonkman t interval':'修正 Hartung–Knapp–Sidik–Jonkman t 区间',
    'Higgins-type prediction interval':'Higgins 型预测区间',
    'maximum-likelihood random effects':'最大似然随机效应',
    'pooled-effect ML profile likelihood interval':'合并效应 ML 轮廓似然区间',
    'Asymptotic likelihood-ratio limits can be inaccurate with few studies or a boundary tau-squared estimate.':'研究数较少或 τ² 处于边界时，渐近似然比界限可能不准确。',
    negative:'低于无效值', positive:'高于无效值', crosses:'跨越无效值',
    'The modified HKSJ interval is designed for random-effects models; coscreen.review_analysis.synthesize rejects fixed + HKSJ for primary synthesis. Shown here for side-by-side sensitivity comparison only.':'修正 HKSJ 区间用于随机效应模型；主合并分析不接受固定效应 + HKSJ。此处仅供并列敏感性比较。',
    'The fixed-effect model sets tau-squared to 0, so this prediction interval covers only the sampling variation of a new study under homogeneity; random-effect prediction intervals are the reportable ones.':'固定效应模型将 τ² 设为 0，因此该预测区间只反映同质性假设下新研究的抽样误差；报告时应使用随机效应预测区间。',
    'prediction intervals need at least three studies (t quantile with k - 2 df)':'预测区间至少需要三项研究（t 分位数自由度为 k−2）。',
    'needs at least three studies':'至少需要三项研究。',
    'profile REML tau-squared interval is implemented at level 0.95 only':'REML τ² 轮廓区间目前仅支持 95% 水平。',
    'Upper bound unavailable; not a finite interval.':'上限不可用；该区间不是有限区间。',
    'Did not converge.':'未收敛。',
    'Available':'可用',
    'H and I-squared transformed intervals need at least three studies':'H 与 I² 转换区间至少需要三项研究。',
    'Intervals disagree on whether they cross the null. ':'不同区间对是否跨越无效值的结论不一致。',
    'coscreen.tau_q_profile_interval.tau_q_profile_interval':'τ² 非中心卡方近似区间',
    'coscreen.tau_q_profile_interval.q_profile_tau2_interval':'τ² Q-profile 区间',
    'coscreen.heterogeneity_interval.i2_h_interval':'Higgins–Thompson H 转换区间',
    'coscreen.tau_profile_interval.profile_likelihood_tau2_interval':'REML τ² 轮廓似然区间',
    'first-level parametric bootstrap percentile interval':'第一层参数自助法百分位区间',
    'first-level parametric bootstrap basic interval':'第一层参数自助法基本区间',
    'REML profile likelihood interval for tau-squared':'REML τ² 轮廓似然区间',
    'Noncentral chi-square inversion of fixed-IV Q (approximate; DL point estimate)':'固定逆方差 Q 的非中心卡方反演（近似；DL 点估计）',
    'Viechtbauer (2007) Q-profile interval for tau-squared':'Viechtbauer（2007）τ² Q-profile 区间',
    'Higgins & Thompson (2002) transformed interval for tau-squared (H limits mapped through the typical variance (k-1)/C)':'Higgins 与 Thompson（2002）τ² 转换区间（H 界限经典型方差 (k−1)/C 映射）',
    'Higgins & Thompson (2002) transformed interval for tau-squared':'Higgins 与 Thompson（2002）τ² 转换区间',
    'Higgins & Thompson (2002) transformed interval':'Higgins 与 Thompson（2002）转换区间',
    'First-level parametric bootstrap is a conditional sensitivity analysis; it does not regenerate between-study heterogeneity. Check Davison & Hinkley (1997), Bootstrap Methods and their Application, eq. 5.6 for basic intervals; cite the method and report replicate count and seed.':'第一层参数自助法是条件性敏感性分析，不会重新生成研究间异质性。basic 区间请核对 Davison 与 Hinkley（1997）《Bootstrap Methods and their Application》第 5.6 式；报告时引用方法，并报告重复次数和随机种子。',
  };
  const valueText = value => ({ text:T(known[value] || value || '—') });
  const valueNode = value => el('span', valueText(value));
  const number = value => value == null ? T('不可用') : Number(value).toPrecision(4);
  const clear = () => { request++; clearInterval(progressTimer); report = null; host.replaceChildren(); exportButton.disabled = true; document.querySelectorAll('[data-sensitivity-report]').forEach(button => { if (button.dataset.originalLabel) button.textContent = button.dataset.originalLabel; button.disabled = true; }); runButton.disabled = false; };
  ['anComparison', 'anOutcome', 'anTimepoint', 'anMeasure', 'anSensitivityLevel', 'anSensitivityBootN', 'anSensitivitySeed', 'anSensitivityBootType']
    .forEach(id => $('#'+id).addEventListener('input', clear));
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', () => {
    for (const id of ['anSensitivityLevel','anSensitivityBootType','anSensitivityBootN','anSensitivitySeed']) $('#'+id).value = '';
    bootstrap.checked = false;
    $('#anSensitivityBootType').disabled = $('#anSensitivityBootN').disabled = $('#anSensitivitySeed').disabled = true;
    clear();
  });
  bootstrap.addEventListener('change', () => {
    $('#anSensitivityBootType').disabled = $('#anSensitivityBootN').disabled = $('#anSensitivitySeed').disabled = !bootstrap.checked;
    clear();
  });

  function comparisonView(result) {
    const cells = [...result.cells, ...(result.bootstrap || []).map(cell => ({...cell,
      key:cell.model+'_bootstrap_'+cell.interval_type, applicable:true, interval_method:'bootstrap_'+cell.interval_type})),
      ...(result.pooled_effect_profile?.applicable ? [result.pooled_effect_profile] : [])];
    const label = cell => cell.key === 'random_ml_profile'
      ? T('ML 随机效应 / 合并效应轮廓') : `${T(known[cell.model_label] || cell.model_label || cell.model)} / ${T(known[cell.interval_label] || cell.interval_label || cell.interval_method)}`;
    const controls = el('section',{class:'an-interval-comparison'},el('h4',{text:T('比较区间方法')}));
    const target = el('select',{class:'input','aria-label':T('比较区间类型')},
      el('option',{value:'',text:T('请选择区间类型')}),
      el('option',{value:'confidence',text:T('置信区间')}),el('option',{value:'prediction',text:T('预测区间')}));
    const reference = el('select',{class:'input','aria-label':T('比较参考方法'),disabled:true});
    const threshold = el('input',{class:'input',type:'number',min:'1',step:'any','aria-label':T('区间宽度差异阈值'),placeholder:T('宽度倍数 > 1（请明确指定）')});
    const output = el('div',{'aria-live':'polite'});
    controls.append(el('div',{class:'row'},el('label',{text:T('区间类型')},target),el('label',{text:T('参考方法')},reference),el('label',{text:T('高亮宽度倍数')},threshold)),
      el('p',{class:'hint',text:T('更改这些设置不会重新计算分析。宽度倍数将各区间与参考区间比较；标记仅作描述，不是显著性检验。请预先设定敏感性分析，并引用 Cochrane Handbook 第 10.14 节。')}),output);
    const eligible = () => target.value ? cells.filter(cell => cell.applicable &&
      (cell.interval_method === 'prediction') === (target.value === 'prediction')) : [];
    const update = () => {
      output.replaceChildren();
      if (!target.value) { report.comparison_view = null; output.textContent = T('请选择区间类型。'); return; }
      const base = eligible().find(cell => cell.key === reference.value), limit = Number(threshold.value);
      report.comparison_view = {target:target.value,reference:reference.value || null,width_factor_threshold:threshold.value && Number.isFinite(limit) && limit > 1 ? limit : null};
      if (!base) { output.textContent = T('请选择参考方法以计算差异。'); return; }
      const table = el('table'), body = el('tbody');
      table.append(el('thead',{},el('tr',{},...['方法','区间对照（显示尺度）','估计值差异（分析尺度）','宽度 / 参考值','宽度倍数','标记'].map(text => el('th',{text:T(text)})))),body);
      table.className = 'an-reference-table';
      const endpoints = eligible().flatMap(cell => cell.ci_display || []).filter(Number.isFinite);
      const scaleMin = Math.min(...endpoints), scaleMax = Math.max(...endpoints);
      const drawInterval = cell => {
        const limits = cell.ci_display;
        if (!limits || !limits.every(Number.isFinite) || !endpoints.length) return el('span',{text:'—'});
        const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
        svg.setAttribute('viewBox','0 0 170 30'); svg.setAttribute('width','170'); svg.setAttribute('height','30');
        svg.setAttribute('role','img'); svg.setAttribute('aria-label',T('区间：{0} 至 {1}',number(limits[0]),number(limits[1])));
        const x = value => 8 + 154*(value-scaleMin)/(scaleMax-scaleMin || 1);
        const add = (tag,attributes) => {const node=document.createElementNS(svg.namespaceURI,tag);Object.entries(attributes).forEach(([key,value])=>node.setAttribute(key,value));svg.append(node);};
        add('line',{x1:8,x2:162,y1:15,y2:15,stroke:'var(--an-line)'});
        add('line',{x1:x(limits[0]),x2:x(limits[1]),y1:15,y2:15,stroke:'var(--an-accent)','stroke-width':2});
        if (Number.isFinite(cell.estimate_display)) add('circle',{cx:x(cell.estimate_display),cy:15,r:3.5,fill:'var(--an-accent)'});
        return svg;
      };
      let flagged = 0;
      for (const cell of eligible()) {
        const ratio = base.width > 0 ? cell.width/base.width : null;
        const factor = ratio > 0 ? Math.max(ratio,1/ratio) : null;
        const warn = factor != null && threshold.value && Number.isFinite(limit) && limit > 1 && factor > limit;
        if (warn) flagged++;
        const isReference = cell.key === base.key;
        const choose = el('button',{type:'button',class:'an-reference-control',text:T(isReference?'参考行':'设为参考')});
        choose.disabled = isReference;
        choose.addEventListener('click',()=>{reference.value=cell.key;update();});
        const row = el('tr',{class:(warn?'sensitivity-flagged ':'')+(isReference?'is-reference':'')},
          el('td',{},el('span',{text:label(cell)}),choose),el('td',{class:'an-interval-cell'},drawInterval(cell)),
          ...[number(cell.estimate-base.estimate),number(ratio),number(factor),warn?T('超过阈值'):'—'].map(text=>el('td',{text})));
        body.append(row);
      }
      if (flagged) output.append(el('p',{role:'alert',text:T('{0} 个区间超过宽度倍数阈值（{1}）。请核对方法并报告差异。',flagged,limit)}));
      if (threshold.value && !(Number.isFinite(limit) && limit > 1)) output.append(el('p',{text:T('请输入大于 1 的有限宽度倍数以启用提示。')}));
      output.append(table);
    };
    const refresh = () => {
      reference.replaceChildren(el('option',{value:'',text:T('请选择参考方法')}),...eligible().map(cell => el('option',{value:cell.key,text:label(cell)})));
      reference.disabled = !target.value;
      update();
    };
    target.addEventListener('change',refresh); reference.addEventListener('change',update); threshold.addEventListener('input',update);
    // This selects a DISPLAY view, not a model or a reporting recommendation.
    target.value='confidence'; refresh();
    const activeModel=anValue('anModel'), activeInterval=anValue('anCiMethod')==='hksj'?'hksj':'wald';
    const matching=eligible().find(cell=>(cell.model===activeModel || (activeModel==='random' && cell.model==='random_dl')) && cell.interval_method===activeInterval);
    if(matching){reference.value=matching.key;update();}
    return controls;
  }

  function renderSensitivity(result, options) {
      const levelText = `${Number(result.level ?? options.level)*100}%`;
      const displayScale = result.analysis_scale === 'log' ? (result.measure === 'LOG_RATE' ? '自然率' : '自然比值')
        : result.analysis_scale === 'logit' ? '比例'
        : result.analysis_scale === 'fisher_z' ? '相关系数 r' : '自然效应';
      const table = el('table', {class:'an-sensitivity-table'});
      table.append(el('caption', { text:T('所选研究结果的敏感性矩阵') }));
      table.append(el('thead', {}, el('tr', {}, el('th',{text:T('模型')}),el('th',{text:T('区间')}),
        el('th',{},T('估计值（'),T(displayScale),T('尺度）')),
        el('th',{},levelText,' ',T('区间（'),T(displayScale),T('尺度）')),
        el('th',{text:T('宽度（分析尺度）')}),el('th',{text:T('解释')}))));
      const body = el('tbody');
      result.cells.forEach(cell => {
        const values = [cell.model_label || cell.model, cell.interval_label || cell.interval_method,
          number(cell.estimate_display), cell.applicable ? cell.ci_display.map(number).join(T(' 至 ')) : cell.reason,
          cell.applicable ? number(cell.width) : '—', cell.caution || cell.side_of_null || '—'];
        body.append(el('tr', {}, el('td',valueText(values[0])),el('td',valueText(values[1])),
          el('td',{text:values[2]}),el('td',valueText(values[3])),el('td',{text:values[4]}),el('td',valueText(values[5]))));
      });
      const profile = result.pooled_effect_profile;
      if (profile) body.append(el('tr', {}, ...[
        profile.model_label || 'maximum-likelihood random effects',
        profile.interval_label || 'pooled-effect ML profile likelihood interval',
        number(profile.estimate_display),
        profile.applicable ? profile.ci_display.map(number).join(T(' 至 ')) : profile.reason || '不可用',
        profile.applicable ? number(profile.width) : '—',
        profile.caution || profile.side_of_null || '—',
      ].map(value => el('td', valueText(value)))));
      table.append(body);
      const summary = result.summary;
      const analysisScale = result.analysis_scale === 'log' ? '对数尺度' : result.analysis_scale === 'logit' ? 'logit 分析尺度' : result.analysis_scale === 'fisher_z' ? 'Fisher z 尺度' : '自然尺度';
      const info = el('p', { class:'hint' }, T('{0} 项研究 · {1} · ',result.k,result.measure),T(analysisScale),T(' 分析尺度 · '),
        levelText,' ',T('置信区间 · 效应和区间端点按 '),T(displayScale),T(' 展示；宽度和方法差异按 '),T(analysisScale),T(' 计算。 '),
        summary.mixed_conclusion ? T('不同区间对是否跨越无效值的结论不一致。 ') : '',
        T('最大与最小区间宽度比：'),number(summary.width_ratio_widest_to_narrowest),'.');
      const tau = el('table');
      tau.append(el('caption', {text:T('研究间方差（τ²）：不同区间方法')}),
        el('thead', {}, el('tr', {}, ...['方法','点估计','区间','状态与限制'].map(text => el('th',{text:T(text)})))));
      const tauBody = el('tbody');
      for (const item of result.tau2_intervals) {
        const status = !item.applicable ? item.reason : item.upper_unbounded ? '上限不可用；该区间不是有限区间。' :
          item.converged === false ? 'Did not converge.' : 'Available';
        const statusCell = el('td', {}, valueNode(status),
          ...(item.warnings || []).map(warning => el('p', {class:'an-table-note'}, valueNode(warning))),
          item.citation ? el('small', {class:'an-citation',lang:'en',translate:'no',text:item.citation}) : null);
        tauBody.append(el('tr', {}, el('td',valueText(item.method)), el('td',{text:number(item.estimate)}),
          el('td',{text:item.applicable ? item.ci.map(number).join(T(' 至 ')) : T('不可用')}), statusCell));
      }
      tau.append(tauBody);
      const i2 = el('p', { class:'hint' }, T('I² 点估计：{0}%。',number(result.i2.point_percent)), ' ',
        result.i2.applicable ? el('span',{},T('{0} 区间（',levelText),valueNode(result.i2.method),T('）：'),result.i2.interval_percent.map(number).join(T(' 至 ')),'%。')
          : el('span',{},T('区间不可用：'),valueNode(result.i2.reason || 'needs at least three studies')),
        result.i2.h_ci ? el('span',{},' H=',number(result.i2.h),'（',result.i2.h_ci.map(number).join(T(' 至 ')),'）。') : '',
        T(' τ² 点估计：DL {0}；REML {1}。',number(result.tau2_point_estimates.dl),number(result.tau2_point_estimates.reml)));
      const heterogeneityNote = el('p', { class:'hint' },
        T('I² 使用固定效应逆方差 Q={0}（df={1}）并按 max(0, (Q−df)/Q) 计算；',number(result.q),result.q_df),
        T('τ² 是'),T(analysisScale),T('分析尺度上的研究间方差。'),
        el('span',{text:T('请核对 Higgins 与 Thompson（2002）原文，并引用实际使用的估计量和区间方法。')}));
      const source = el('p', { class:'hint' }, T('所选研究及来源：'), ...result.sources.map((item,index) => el('span',{translate:'no',text:(index ? '；' : '') + `${item.study_id} (${item.source_key}${item.source_locator ? ', ' + item.source_locator : ''}; #${item.result_id})`})));
      exportButton.disabled = false; document.querySelectorAll('[data-sensitivity-report]').forEach(button => button.disabled = false);
      const fullMatrix=el('details',{class:'an-support-details an-full-matrix'},el('summary',{text:T('完整模型与区间结果')}),table);
      host.replaceChildren(info, comparisonView(result), fullMatrix,
        el('p', {class:'hint'}, profile?.applicable
          ? [T('合并效应 ML 轮廓结果：ML τ² = {0}。',number(profile.tau2_ml)),el('span',{lang:'en',text:profile.citation || 'Hardy & Thompson (1996)'}),
             valueNode(profile.caution || ''),T('请核对原文方法；使用时引用该方法。')]
          : [T('合并效应 ML 轮廓区间不可用：'),valueNode(profile?.reason || 'no result returned')]),
        i2, heterogeneityNote, tau, source);
      if (result.bootstrap?.length) host.append(el('p', { class:'hint' },T('参数自助法结果：'),...result.bootstrap.flatMap((item,index) => [
        index ? '；' : '',T(known[item.model_label] || item.model_label || item.model),'（',
        T(item.interval_type === 'percentile' ? '百分位法' : '基本法'),'）：',
        item.ci_display.map(number).join(T(' 至 ')),T('（seed {0}, n={1}）',item.seed,item.n_boot)])),
        el('p', { class:'hint', text:T('第一层参数自助法是条件性敏感性分析，不会重新生成研究间异质性。basic 区间请查阅 Davison 与 Hinkley（1997）原文第 5.6 式；请引用实际使用的方法，并报告重复次数和随机种子。') }),
        ...result.bootstrap.flatMap(item => (item.warnings || []).map(warning =>
          el('p', { class:'hint', text:T('{0}：{1}',T(known[item.model] || item.model),T(known[warning] || warning)) }))));
  }
  document.addEventListener('reviewflow:language-changed', () => {
    if (!report) return;
    const selections = report.comparison_view ? {...report.comparison_view} : null;
    const fullOpen = !!host.querySelector('.an-full-matrix')?.open;
    const scrollTop = document.querySelector('.view').scrollTop;
    renderSensitivity(report.result, report.options);
    if (selections) {
      const controls = host.querySelector('.an-interval-comparison');
      const selects = controls.querySelectorAll('select');
      selects[0].value = selections.target || '';
      selects[0].dispatchEvent(new Event('change'));
      selects[1].value = selections.reference || '';
      selects[1].dispatchEvent(new Event('change'));
      const threshold = controls.querySelector('input');
      threshold.value = selections.width_factor_threshold ?? '';
      threshold.dispatchEvent(new Event('input'));
    }
    host.querySelector('.an-full-matrix').open = fullOpen;
    document.querySelector('.view').scrollTop = scrollTop;
  });

  $('#anSensitivityRun').addEventListener('click', async () => {
    clear();
    const current = request;
    const rawLevel = anValue('anSensitivityLevel'), level = Number(rawLevel);
    if (!rawLevel || !Number.isFinite(level) || level <= 0 || level >= 100) {
      host.textContent = T('置信水平须大于 0% 且小于 100%。');
      return;
    }
    const options = { comparison:anValue('anComparison'), outcome:anValue('anOutcome'),
      timepoint:anValue('anTimepoint'), measure:anValue('anMeasure'), level:level/100 };
    if (bootstrap.checked) {
      const n = $('#anSensitivityBootN').value.trim(), seed = $('#anSensitivitySeed').value.trim();
      if (!/^\d+$/.test(n) || !/^\d+$/.test(seed) || Number(n) < 1 || Number(n) > 200000 || !Number.isSafeInteger(Number(seed))) {
        host.textContent = T('请输入 1 至 200000 的重复次数，以及不小于 0 的整数随机种子。');
        return;
      }
      const intervalType = $('#anSensitivityBootType').value;
      if (!intervalType) { host.textContent = T('请选择自助法区间类型。'); return; }
      options.bootstrap_interval_type = intervalType;
      options.bootstrap_n = Number(n); options.seed = Number(seed);
    }
    runButton.disabled = true;
    const started = performance.now();
    const reference = options.bootstrap_n ? T('实际耗时因设备和数据量而异。') : '';
    const showProgress = () => { if (current === request) host.textContent =
      T('正在计算敏感性比较…已等待 {0} 秒。',((performance.now() - started) / 1000).toFixed(1)) + (reference ? ' ' + reference : ''); };
    showProgress();
    progressTimer = setInterval(showProgress, 500);
    try {
      const result = await api(taskApi() + '/analysis/sensitivity-view', { method:'POST', body:options });
      if (current !== request) return;
      report = { generated_at_utc:new Date().toISOString(), options, result };
      renderSensitivity(result, options);
    } catch (error) { if (current === request) { host.textContent = ''; host.append(window.RFAnalysisLocale.message(error.message)); toast(T(error.message), 'warn'); } }
    finally { if (current === request) { clearInterval(progressTimer); runButton.disabled = false; } }
  });
  document.querySelectorAll('[data-sensitivity-report]').forEach(button => button.addEventListener('click',async () => {
    if (!report) return;
    const comparisonSnapshot = JSON.stringify(report.comparison_view);
    const current = request, format = button.dataset.sensitivityReport, label = button.textContent, started = Date.now();
    button.dataset.originalLabel = label;
    button.disabled = true;
    const timer = setInterval(() => { if (current === request) button.textContent = T('正在导出…已等待 {0} 秒。',((Date.now()-started)/1000).toFixed(1)); },500);
    try {
      const blob = await api(taskApi()+'/analysis/sensitivity-view?format='+format,{method:'POST',body:{...report.options,comparison_view:report.comparison_view},blob:true});
      if (current !== request || comparisonSnapshot !== JSON.stringify(report.comparison_view)) return;
      const url = URL.createObjectURL(blob), link = el('a',{href:url,download:'reviewflow_sensitivity.'+format});
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
    } catch (error) { if (current === request) toast(error.message,'warn'); }
    finally { clearInterval(timer); if (current === request) { button.textContent = label; button.disabled = !report; } }
  }));
  exportButton.addEventListener('click', () => {
    if (!report) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type:'application/json' }));
    const link = el('a', { href:url, download:'reviewflow_sensitivity.json' });
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
