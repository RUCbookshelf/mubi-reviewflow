(function () {
let anMetaCatalog = [], anMetaMods = [], anMetaInteractions = [], metaTaskId = null;
let generation = 0, record = null, timer = null;
const noteText = {
  'One effect per independent study for one outcome and time point; within-study covariance is not modeled.':'同一结局与时间点下，每项独立研究只取一个效应值；模型未处理研究内协方差。',
  'Study-level associations are ecological and do not establish participant-level effect modification or causality.':'研究层面的关联属于生态关联，不能证明参与者层面的效应修饰或因果关系。',
  'Requires at least 10 studies, a full-rank design, and positive residual degrees of freedom.':'至少需要 10 项研究、满秩设计矩阵和正的残差自由度。',
  'Coefficient estimates can be unstable with sparse covariate levels or near-collinearity.':'协变量类别样本稀少或变量接近共线时，系数估计可能不稳定。',
  'Explained heterogeneity R²* = max(0, 1 - tau2_model/tau2_null) when tau2_null > 0; it is None when tau2_null is zero.':'空模型 τ² 大于零时，解释的异质性 R²*=max(0, 1−τ²模型/τ²空模型)；空模型 τ² 为零时该指标不可计算。',
  'For ratio measures, se must be the standard error on the log-ratio scale; it is not transformed from the raw ratio scale.':'比值类效应的标准误必须在对数比值尺度输入；程序不会从原始比值尺度自动换算。',
  'Modified KNHA (ad hoc) uses max(1, residual Q/df) and a t reference distribution.':'修正 Knapp–Hartung 使用 max(1，残差 Q/自由度) 调整尺度，并采用 t 分布。',
  'Normal Wald intervals may understate uncertainty in small meta-regressions.':'研究数较少时，正态 Wald 区间可能低估不确定性。',
  'DL tau2 was floored at zero; robust intervals then protect against variance misspecification but not against real between-study heterogeneity.':'DL τ² 截断为零；稳健区间可应对方差设定错误，但不能消除真实的研究间异质性。',
  'Profile REML tau2 was floored at zero; robust intervals then protect against variance misspecification but not against real between-study heterogeneity.':'Profile REML τ² 截断为零；稳健区间可应对方差设定错误，但不能消除真实的研究间异质性。',
  'At least one study had leverage within 1e-12 of one; its CR2 adjustment was set to zero, matching the clubSandwich convention.':'至少一项研究的杠杆值与 1 相差不超过 1e−12；其 CR2 调整按 clubSandwich 约定设为零。',
  'Robust inference changes standard errors, tests, and intervals only; the point estimates and tau2 are identical to the model-based fit.':'稳健推断只改变标准误、检验和区间；点估计值与 τ² 与模型拟合结果相同。',
  'This robust approach is not Knapp-Hartung: KH (and its modified variant in coscreen.meta_regression_model) rescales the model-based covariance by a residual heterogeneity factor, while the sandwich estimators here are consistent under variance misspecification with each study as its own cluster. CR2 with Satterthwaite df, rather than modified KH, is the recommended small-sample default in the cluster-robust literature (Pustejovsky & Tipton 2018; Imbens & Kolesar 2016).':'这里的稳健推断不同于 Knapp–Hartung：KH 按残差异质性调整模型协方差；本工具的三明治估计将每项研究作为独立簇，在方差设定错误时仍可保持一致性。簇稳健文献更推荐小样本使用 CR2 与 Satterthwaite 自由度（Pustejovsky 与 Tipton 2018；Imbens 与 Kolesar 2016）。',
};
function translateNote(raw) {
  const match = /^Only (\d+) independent studies were analyzed\. Robust variance estimation is designed for few-study meta-regressions, and CR2 with Satterthwaite degrees of freedom holds its nominal level best of the CR family \(Tipton & Pustejovsky 2015; Pustejovsky & Tipton 2018\), but power is low and results are sensitive to influential studies\.$/.exec(raw);
  return match ? T('仅分析了 {0} 项独立研究。CR2 与 Satterthwaite 自由度在 CR 方法中较能保持名义检验水平，但统计效能低，结果容易受高影响研究左右（Tipton 与 Pustejovsky 2015；Pustejovsky 与 Tipton 2018）。', match[1]) : T(noteText[raw] || raw);
}
function clearResult() { generation++; clearInterval(timer); record = null; $('#anMetaResult').replaceChildren(); $('#anMetaRun').disabled = false; $('#anMetaExport').disabled = true; document.querySelectorAll('[data-meta-report]').forEach(button => { if (button.dataset.exporting === 'true') button.textContent = T(button.dataset.originalLabel); delete button.dataset.exporting; button.disabled = true; }); }
['anComparison','anOutcome','anTimepoint','anMeasure','anMetaCiMethod','anMetaRobustModel','anMetaRobustLevel'].forEach(id => $('#'+id).addEventListener('input',clearResult));
document.addEventListener('reviewflow:analysis-core-loaded',clearResult);
document.querySelectorAll('[data-meta-report]').forEach(button => button.addEventListener('click',async () => {
  if (!record) return;
  const ticket = generation, format = button.dataset.metaReport;
  button.disabled = true;
  const label = RFLang.source(button.textContent) || button.textContent, started = performance.now();
  button.dataset.originalLabel = label;
  button.dataset.exporting = 'true';
  const progress = () => { if (ticket === generation) button.textContent = T('正在导出…已等待 {0} 秒。', ((performance.now()-started)/1000).toFixed(1)); };
  progress(); const exportTimer = setInterval(progress, 500);
  try {
    const blob = await api(taskApi()+'/analysis/meta-regression/multiple?format='+format,{method:'POST',body:record.request,blob:true});
    if (ticket !== generation) return;
    const url = URL.createObjectURL(blob), link = el('a',{href:url,download:'reviewflow_meta_regression.'+format});
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
  } catch (error) { if (ticket === generation) toast(error.message,'warn'); }
  finally { clearInterval(exportTimer); if (ticket === generation) { button.textContent = T(label); button.disabled = !record; } delete button.dataset.exporting; }
}));
$('#anMetaCiMethod').addEventListener('change',() => { $('#anMetaRobustOptions').hidden = !anValue('anMetaCiMethod').startsWith('cr'); });
$('#anMetaExport').addEventListener('click',() => {
  if (!record) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(record,null,2)],{type:'application/json'}));
  const link = el('a',{href:url,download:'reviewflow_meta_regression.json'});
  document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
});
function resetMetaTask() {
  anMetaMods = []; anMetaInteractions = []; anMetaCatalog = [];
  ['anMetaDimension','anMetaKind','anMetaReference','anMetaCiMethod','anMetaRobustModel','anMetaRobustLevel']
    .forEach(id => { $('#'+id).value = ''; });
  $('#anMetaKind').dispatchEvent(new Event('change'));
  $('#anMetaCiMethod').dispatchEvent(new Event('change'));
  anOptions('anMetaDimension', [['', T('请选择协变量')]]);
  metaTaskId = S.task?.task_id;
  renderMetaTerms();
}
document.addEventListener('reviewflow:analysis-task-changed', resetMetaTask);
document.addEventListener('reviewflow:analysis-loaded', event => {
  if (metaTaskId !== S.task?.task_id) resetMetaTask();
  anMetaCatalog = event.detail.dimensions;
  anOptions('anMetaDimension', [['', T('请选择协变量')], ...anMetaCatalog.map(d => [String(d.id), d.name])], anValue('anMetaDimension'));
  renderMetaTerms();
});
function renderMetaTerms() {
  clearResult();
  const holder = $('#anMetaTerms'); holder.replaceChildren();
  anMetaMods.forEach(mod => {
    const label = anMetaCatalog.find(d => d.id === mod.dimension_id)?.name || String(mod.dimension_id);
    holder.append(el('div', { class:'row' },
      el('span', { text:label }), el('span', { text:' · ' }),
      el('span', { text:T(mod.kind === 'continuous' ? '连续变量' : '分类变量') }),
      ...(mod.reference ? [el('span', { text:' · '+mod.reference })] : []),
      el('button', { class:'btn b-out', type:'button', text:T('移除'), onclick:() => {
        anMetaMods = anMetaMods.filter(item => item !== mod);
        anMetaInteractions = anMetaInteractions.filter(pair => !pair.includes(mod.dimension_id));
        renderMetaTerms();
      } })));
  });
  const options = anMetaMods.map(mod => [String(mod.dimension_id),
    anMetaCatalog.find(d => d.id === mod.dimension_id)?.name || String(mod.dimension_id)]);
  anOptions('anMetaInteractionA', [['',T('请选择第一个协变量')], ...options], anValue('anMetaInteractionA'));
  anOptions('anMetaInteractionB', [['',T('请选择第二个协变量')], ...options], anValue('anMetaInteractionB'));
  const interactions = $('#anMetaInteractions'); interactions.replaceChildren();
  anMetaInteractions.forEach(pair => interactions.append(el('div', { class:'row' },
    el('span', { text:pair.map(id => anMetaCatalog.find(d => d.id === id)?.name || String(id)).join(' × ') }),
    el('button', { class:'btn b-out', type:'button', text:T('移除'), onclick:() => {
      anMetaInteractions = anMetaInteractions.filter(item => item !== pair); renderMetaTerms();
    } }))));
}
$('#anMetaKind').addEventListener('change', () => {
  $('#anMetaReference').style.display = anValue('anMetaKind') === 'categorical' ? '' : 'none';
});
$('#anMetaReference').style.display = 'none';
$('#anMetaAdd').addEventListener('click', () => {
  const dimension_id = Number(anValue('anMetaDimension'));
  const kind = anValue('anMetaKind'), reference = anValue('anMetaReference');
  if (!dimension_id || anMetaMods.some(mod => mod.dimension_id === dimension_id)) return toast(T('请选择尚未添加的协变量。'), 'warn');
  if (!kind) return toast(T('请选择协变量类型。'), 'warn');
  if (kind === 'categorical' && !reference) return toast(T('分类变量需要填写参照类别。'), 'warn');
  anMetaMods.push({ dimension_id, kind, reference:kind === 'categorical' ? reference : null });
  renderMetaTerms();
});
$('#anMetaAddInteraction').addEventListener('click', () => {
  const a = Number(anValue('anMetaInteractionA')), b = Number(anValue('anMetaInteractionB'));
  if (!a || !b || a === b || anMetaInteractions.some(pair => pair.includes(a) && pair.includes(b)))
    return toast(T('请选择两个不同的协变量。'), 'warn');
  anMetaInteractions.push([a,b]); renderMetaTerms();
});
$('#anMetaRun').addEventListener('click', async () => {
  clearResult(); const ticket = generation, holder = $('#anMetaResult'), run = $('#anMetaRun');
  try {
    if (!anMetaMods.length) throw new Error(T('请至少添加一个协变量。'));
    const method = anValue('anMetaCiMethod'), robust = method.startsWith('cr');
    if (!method) throw new Error(T('请选择 Meta 回归区间方法。'));
    const request = {comparison:anValue('anComparison'),outcome:anValue('anOutcome'),timepoint:anValue('anTimepoint'),measure:anValue('anMeasure'),moderators:anMetaMods,interactions:anMetaInteractions,ci_method:robust ? 'normal' : method};
    if (robust) {
      request.vcov_type = method; request.model = anValue('anMetaRobustModel'); request.level = Number(anValue('anMetaRobustLevel'))/100;
      if (!request.model || !(request.level > 0 && request.level < 1)) throw new Error(T('请选择稳健回归模型及有效的置信水平。'));
    }
    run.disabled = true;
    const started = performance.now();
    const progress = () => { if (ticket === generation) holder.textContent =
      T('正在计算 Meta 回归…已等待 {0} 秒；暂无法显示中间进度。', ((performance.now()-started)/1000).toFixed(1)); };
    progress(); timer = setInterval(progress,500);
    const result = await api(taskApi()+'/analysis/meta-regression/multiple',{method:'POST',body:request});
    if (ticket !== generation) return;
    const fmt = value => value == null ? T('不可计算') : Number(value).toPrecision(6);
    const scaleCode = robust ? result.analysis_scale : result.effect_scale;
    const scale = scaleCode === 'log' ? T('对数尺度') : scaleCode === 'fisher_z' || scaleCode === 'Fisher z' ? T('Fisher z 尺度') : T('自然尺度');
    const tauMethod = robust ? result.model === 'fixed' ? T('固定效应') : result.model === 'dl' ? 'DL' : 'profile REML' : 'profile REML';
    const intervalMethod = robust ? result.vcov_type.toUpperCase()+' + Satterthwaite' : T(method === 'knha' ? '修正 Knapp–Hartung' : '正态近似');
    holder.replaceChildren(el('p',{class:'hint',text:T('{0} 项研究 · {1} · τ²（{2}）={3} · {4}', result.n_studies, scale, tauMethod, fmt(result.tau2), intervalMethod)}));
    const table = el('table'), rows = el('tbody');
    const headers = robust ? [T('回归项'),T('估计值'),T('稳健标准误'),T('模型标准误'),T('Satterthwaite 自由度'),T('{0}% 区间', result.level*100),'p'] : [T('回归项'),T('估计值'),T('标准误'),T('95% 置信区间'),'p'];
    table.append(el('thead',{},el('tr',{},...headers.map(text => el('th',{text})))),rows);
    result.coefficients.forEach(coef => {
      const name = Object.entries(result.moderator_labels).sort((a,b) => b[0].length-a[0].length).reduce((label,[key,value]) => label.replaceAll(key,value),coef.name);
      const interval = `${fmt(coef.ci_low)}–${fmt(coef.ci_high)}`;
      const values = robust ? [name,fmt(coef.estimate),fmt(coef.robust_se),fmt(coef.model_based_se),fmt(coef.df),interval,fmt(coef.p_value)] : [name,fmt(coef.estimate),fmt(coef.se),interval,fmt(coef.p)];
      rows.append(el('tr',{},...values.map(text => el('td',{text}))));
    });
    holder.append(table);
    if (result.moderator_test) holder.append(el('p',{class:'hint',text:T('协变量整体检验：{0}={1}，p={2}。', result.moderator_test.statistic_name, fmt(result.moderator_test.statistic), fmt(result.moderator_test.p_value))}));
    if (result.tau2_null != null) holder.append(el('p',{class:'hint',text:T('空模型 τ²={0}；解释的异质性 R²*={1}。', fmt(result.tau2_null), result.explained_heterogeneity_r2 == null ? T('不可计算') : (100*result.explained_heterogeneity_r2).toFixed(1)+'%')}));
    if (result.residual_q != null) holder.append(el('p',{class:'hint'},
      el('span',{text:T('残差加权 RSS（REML 权重；并非 metafor 的 QE）={0}（自由度 {1}）',fmt(result.residual_q),result.residual_df)}),
      ...(result.knha_scale == null ? [] : [el('span',{text:T('；修正 KH 尺度因子={0}',fmt(result.knha_scale))})])));
    for (const raw of [...(result.warnings || []),...(Array.isArray(result.limitations) ? result.limitations : result.limitations ? [result.limitations] : [])]) {
      const translated = translateNote(raw);
      holder.append(el('p',{class:'hint',lang:translated === raw ? 'en' : null,text:translated}));
    }
    for (const source of result.method_sources || []) holder.append(el('p',{class:'hint',lang:'en',text:source}));
    if (result.sources) holder.append(el('p',{class:'hint'},el('span',{text:T('来源记录：')}),
      el('span',{text:result.sources.map(row => `${row.study_id} (${row.source_key}${row.source_locator ? ', '+row.source_locator : ''}; result ${row.result_id})`).join('; ')})));
    holder.append(el('p',{class:'hint',text:T('使用该方法撰写论文时，请展开本区参考文献并引用方法来源。')}));
    record = {request,result}; $('#anMetaExport').disabled = false; document.querySelectorAll('[data-meta-report]').forEach(button => button.disabled = false);
  } catch (error) { if (ticket === generation) { holder.textContent = error.message; toast(error.message,'warn'); } }
  finally { if (ticket === generation) { clearInterval(timer); run.disabled = false; } }
});
})();
