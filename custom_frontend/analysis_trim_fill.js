(function () {
$('#anTrimEstimator').addEventListener('change', () => { $('#anTrimPooling').value = ''; });
let generation = 0, timer;
const notes = {
  'Each row is one independent study estimate for a common outcome and comparison.':'每行是同一结局和比较中的一项独立研究估计。',
  'A fixed-effect inverse-variance model assumes all studies share one true effect; SEs are on the analysis scale.':'固定效应逆方差模型假定所有研究具有共同真实效应；SE 使用分析尺度。',
  'Missingness is one-sided on the requested funnel-plot side; L0 estimates the number from signed ranks of centered effects.':'假定研究仅在所选漏斗图一侧缺失；L0 根据中心化效应的带符号秩估计缺失数。',
  'Imputed effects mirror the most extreme opposite-side studies around the final trimmed pool and reuse their SEs.':'填补效应以最终修剪后的合并值为中心，镜像另一侧最极端的研究，并沿用其 SE。',
  'The requested funnel-plot side is selected in advance; it is not inferred from the data.':'漏斗图缺失侧由用户预先选择，不从数据中推断。',
  'R0, Q0, and L0 are signed-rank estimators; ties in absolute centered effects are ranked in input order after sorting effects.':'R0、Q0 和 L0 均为带符号秩估计量；中心化效应绝对值相同时，按效应排序后的输入顺序定秩。',
  'FE uses inverse sampling-variance weights; DL and REML re-estimate between-study variance in each trimmed fit and in the augmented fit.':'FE 使用抽样方差倒数加权；DL 和 REML 会在每次修剪拟合及填补后的拟合中重新估计研究间方差。',
  'Imputed effects mirror the most extreme trimmed studies around the final trimmed pooled estimate and reuse their standard errors.':'填补效应以最终修剪后的合并值为中心，镜像最极端的被修剪研究，并沿用其标准误。',
  'Funnel asymmetry does not prove publication bias; heterogeneity, chance, and other mechanisms can also create asymmetry.':'漏斗图不对称不能证明发表偏倚；异质性、偶然因素和其他机制也会造成不对称。',
  'The adjusted estimate is a sensitivity analysis and is not guaranteed to be unbiased.':'填补后的估计值属于敏感性分析，不能保证无偏。',
};
const clear = () => { generation++; clearInterval(timer); $('#anTrimResult').replaceChildren(); $('#anTrimRun').disabled = false; };
['anTrimSide','anTrimEstimator','anTrimPooling','anComparison','anOutcome','anTimepoint','anMeasure'].forEach(id =>
  $('#'+id).addEventListener('input', clear));
document.addEventListener('reviewflow:analysis-core-loaded', clear);
document.addEventListener('reviewflow:analysis-task-changed', clear);
$('#anTrimRun').addEventListener('click', async () => {
  clear();
  const current = generation, holder = $('#anTrimResult');
  try {
    if (!anValue('anTrimSide')) throw new Error(T('请选择假定缺失研究的一侧。'));
    if (!anValue('anTrimEstimator')) throw new Error(T('请选择剪补法的缺失数量估计量。'));
    if (!anValue('anTrimPooling')) throw new Error(T('请选择剪补法的合并模型。'));
    const body = {
      comparison:anValue('anComparison'), outcome:anValue('anOutcome'),
      timepoint:anValue('anTimepoint'), measure:anValue('anMeasure'), side:anValue('anTrimSide'),
      estimator:anValue('anTrimEstimator'), pooling_method:anValue('anTrimPooling')
    };
    $('#anTrimRun').disabled = true;
    const started = performance.now();
    const progress = () => { if (current === generation) holder.textContent =
      T('正在运行剪补法…已等待 {0} 秒；目前没有中间进度。', ((performance.now()-started)/1000).toFixed(1)); };
    progress(); timer = setInterval(progress, 500);
    const result = await api(taskApi() + '/analysis/publication-bias/trim-fill', {method:'POST', body});
    if (current !== generation) return;
    holder.replaceChildren();
    const fmt = value => Number(value).toPrecision(4);
    const side = result.side === 'left' ? T('左侧') : T('右侧');
    const scale = value => value === 'ratio' ? T('自然比值') : value === 'log' ? T('对数尺度') : value === 'fisher_z' ? T('Fisher z 尺度') : T('自然尺度');
    const pool = {FE:T('固定效应'),DL:T('随机效应（DL）'),REML:T('随机效应（REML）')}[result.pooling_method] || result.pooling_method;
    holder.append(el('p', { text:T('剪补法 · {0} · 假定缺失在{1} · 观察研究 {2} 项 · 假设填补 {3} 项', result.estimator, side, result.n_studies, result.n_missing) }),
      el('p', { text:T('观测合并效应：{0}（95% CI {1}–{2}）', fmt(result.observed.pooled), fmt(result.observed.ci_low), fmt(result.observed.ci_high)) }),
      el('p', { text:T('填补后敏感性估计：{0}（95% CI {1}–{2}）', fmt(result.adjusted.pooled), fmt(result.adjusted.ci_low), fmt(result.adjusted.ci_high)) }));
    if (result.estimate_scale && result.se_scale) holder.append(el('p', { class:'hint', text:
      T('合并及填补效应使用{0}；SE 和 τ² 使用{1}。', scale(result.estimate_scale), scale(result.se_scale)) }));
    if (result.pooling_method) holder.append(el('p', { class:'hint', text:
      T('合并模型：{0}；观察 τ²={1}，填补后 τ²={2}（{3}方差）。', pool, fmt(result.observed_tau2), fmt(result.adjusted_tau2), scale(result.analysis_scale)) }));
    if (result.n_missing_p_value != null) holder.append(el('p', { class:'hint', text:
      T('R0 对所选侧“缺失数为零”的检验：p={0}。这不能证明发表偏倚。', fmt(result.n_missing_p_value)) }));
    if (result.imputed_studies.length) {
      const list = el('ul');
      result.imputed_studies.forEach(item => list.append(el('li', {
        text:T('假定填补 {0} ← 镜像来源 {1}：效应 {2}（{3}）；SE {4}（{5}）。', item.study_id, item.source_study_id, fmt(item.estimate), scale(result.estimate_scale), fmt(item.se), scale(result.se_scale))
      })));
      holder.append(list);
    }
    holder.append(...[...(result.assumptions || []), ...(result.warnings || [])].map(raw => {
      const translated = T(notes[raw] || raw);
      return el('p', {class:'hint', lang:translated === raw ? 'en' : null, text:translated});
    }),
      el('p', { class:'hint', text:T('使用该方法撰写论文时，请展开本区参考文献并引用方法来源。') }));
    const download = el('button', { class:'btn', type:'button', text:T('导出剪补法数据（JSON）') });
    download.addEventListener('click', () => {
      const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type:'application/json' }));
      el('a', { href:url, download:'reviewflow_trim_fill.json' }).click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    holder.append(download, el('p', { class:'hint', lang:'en', text:(result.method_sources || []).join(' ') }),
      anAuditReportButtons(taskApi() + '/analysis/publication-bias/trim-fill', null,
        'reviewflow_trim_fill_audit', () => current === generation, body));
  } catch (error) { if (current !== generation) return; holder.textContent = error.message; toast(error.message, 'warn'); }
  finally { if (current === generation) { clearInterval(timer); $('#anTrimRun').disabled = false; } }
});
})();
