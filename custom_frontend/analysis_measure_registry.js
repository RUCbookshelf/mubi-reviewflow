/* The backend registry owns which effect measures are ready for synthesis. */
let analysisMeasureCatalog = null;
const analysisMeasureCautions = {
  paired_discordant_cells: '配对效应只用不一致配对计算，不能与独立两组效应合并。',
  smcr_correlation_or_change_sd: 'SMCR 使用前测标准差。计算方差还需前后测相关系数（请注明来源）或实测变化值标准差；请记录所用数据。',
  rom_not_risk_ratio: '均值比用于连续结局，不能与二分类风险比 RR 合并或直接比较。',
  marginal_dependent_correlation: '二分类相关系数受两组边际比例影响，不能与连续结局的 Fisher z 合并。',
  no_se_presentation_only: '此区间没有可用的标准误，只供查看；不能据此反推标准误或纳入逆方差合并。',
  nnt_depends_on_cer: 'NNT 取决于目标人群的对照组事件风险（CER）；使用前请按实际 CER 重算。',
};
const analysisMeasureFamilies = {
  risk_ratio: '风险比', odds_ratio: '优势比', risk_difference: '风险差',
  mean_difference: '均值差', std_mean_difference: '标准化均值差',
  hazard_ratio: '风险比 HR', rate_ratio: '发生率比',
  correlation_pearson: 'Pearson 相关', mean: '单组均值',
  logit_proportion: '单组比例（logit）', log_rate: '发生率（对数）',
  odds_ratio_paired: '配对优势比', risk_difference_paired: '配对风险差',
  std_mean_change_pretest: '标准化变化（前测 SD）',
  std_mean_change_score: '标准化变化（变化值 SD）',
  ratio_of_means: '均值比', correlation_binary: '二分类相关（φ）',
  correlation_tetrachoric_approx: '近似四分相关',
};
const analysisScaleTitles = {
  log: '分析尺度：对数', natural: '分析尺度：未变换尺度',
  fisher_z: '分析尺度：Fisher z', logit: '分析尺度：logit',
};
function showAnalysisMeasureCaution() {
  const code = $('#anMeasure').value;
  const item = analysisMeasureCatalog?.measures.find(entry => entry.code === code);
  const caution = item?.caution_key && analysisMeasureCatalog.cautions[item.caution_key];
  const note = $('#anMeasureCaution');
  note.textContent = caution ? T(analysisMeasureCautions[item.caution_key] || caution) : '';
  note.hidden = !caution;
}
$('#anMeasure').addEventListener('change', showAnalysisMeasureCaution);
async function loadAnalysisMeasures() {
  const select = $('#anMeasure');
  if (analysisMeasureCatalog) return;
  try {
    const result = await api('/api/analysis/measures');
    const ready = result.measures.filter(item => item.synthesis_ready);
    if (!ready.length) throw new Error('No synthesis-ready effect measures are available.');
    const previous = select.value;
    const groups = new Map();
    for (const item of ready) {
      if (!groups.has(item.family)) groups.set(item.family, el('optgroup', { label:T(analysisMeasureFamilies[item.family] || item.code) }));
      groups.get(item.family).append(el('option', { value:item.code, text:item.code,
        title:T(analysisScaleTitles[item.scale] || '分析尺度：{0}', item.scale) }));
    }
    select.replaceChildren(el('option', { value:'', text:T('请选择效应指标') }), ...groups.values());
    if (ready.some(item => item.code === previous)) select.value = previous;
    select.disabled = false;
    analysisMeasureCatalog = result;
    select.dispatchEvent(new Event('change'));
  } catch (error) {
    select.replaceChildren(el('option', { value:'', text:T('效应指标暂不可用；重新打开分析页后重试') }));
    select.disabled = true;
    analysisMeasureCatalog = null;
    select.dispatchEvent(new Event('change'));
    toast(T('效应指标暂不可用；重新打开分析页后重试'), 'warn');
  }
}
