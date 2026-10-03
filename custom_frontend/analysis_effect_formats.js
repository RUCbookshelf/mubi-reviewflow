(function () {
// 各录入格式的指引文案（exact 键 + 前缀/后缀兜底）：改文案只动这一处，
// 替代此前 16 分支嵌套三元链（改格式需同步 5 处，P2-14）。
const AN_FORMAT_GUIDANCE = {
  'binary_correlation_conversion': 'Independent groups: positive association means a higher event proportion in group t. Choose PHI for observed binary association. For a latent association meta-analysis, Bonett–Price is the recommended approximation; it assumes underlying bivariate-normal continuous variables and adds 0.5 to every cell by definition. Both use natural-scale SE, depend on marginal splits, and must be analyzed separately from each other and continuous-outcome Fisher z. Normal intervals may exceed correlation bounds.',
  'glass_delta_two_arm': 'Glass delta uses the control-arm SD, with no small-sample correction. Keep it in a separate analysis stratum from Hedges g or SMDs with other or unknown standardizers. Select the linked report and record its table/page locator.',
  'ratio_of_means_two_arm': 'Ratio of independent-arm means: use an outcome with a meaningful zero and nonzero same-sign means. This is not a binary risk ratio. Store log ROM and log-scale SE; display ROM by exponentiation. Negative means require particular care interpreting the ratio. No small-sample bias correction is applied.',
  'single_mean_sd_n': 'A one-group mean is not a mean difference. Pool only the same outcome scale and unit.',
  'single_proportion_events_n': 'Pool on the logit scale and report the inverse-logit proportion. Zero or all-event studies need a binomial model and are not accepted by this inverse-variance format.',
  'single_rate_events_time': 'Pool log event rates and report rates per stated person-time unit. Zero-event studies need a Poisson model and are not accepted by this inverse-variance format.',
  'independent_t_n': 'Enter a signed t statistic from an independent-groups pooled-variance test. Positive means treatment > control. This conversion yields Hedges g with the Borenstein/LS2 variance; Welch, paired and adjusted tests require different inputs.',
  'independent_p_n': 'Enter the exact two-sided p-value and direction from a pooled-variance independent-groups t-test. Values reported only as p < threshold cannot determine an effect size. Welch, paired and adjusted tests require different inputs.',
  'generic_wald_p': 'Use only an exact two-sided p-value from a normal Wald test of this same effect against zero (or ratio 1). The SE is recovered on the analysis scale; threshold p-values, t, score and likelihood-ratio tests are unsuitable.',
  'survival_logrank_oe_v': 'Enter treatment-arm observed minus expected events and its corresponding log-rank variance. Positive O−E gives treatment/control HR above 1; check the source sign before saving.',
  'crossover_2x2_md': 'Both sequence differences must be A − B within the same participant. Averaging AB and BA cancels a common additive period effect, even with unequal sequence sizes. Differential carryover, sequence-specific period effects and missing-pair bias remain possible; prefer a published adjusted estimate when available.',
  'independent_f_n': 'Only a one-factor, two-independent-group, equal-variance F test is eligible: numerator df=1 and residual df=n_t+n_c−2. Enter the treatment−control direction separately because F has no sign. Adjusted, Welch and repeated-measures F tests need another method.',
  'independent_d_n': 'Enter a signed Cohen d based on the pooled within-group SD of two independent groups (treatment − control). This applies the exact Hedges correction and Borenstein/LS2 variance; other standardizers need a different method.',
};
const AN_FORMAT_GUIDANCE_PARTIAL = {
  'paired_md_': 'Effect = condition A − B for complete pairs. A single-group pre/post change is not a controlled treatment effect; simple crossover summaries do not adjust period or carryover effects.',
  'parallel_change_': 'Effect = (treatment final − baseline) − (control final − baseline). Each arm needs its own analyzed paired n and change SD or baseline–final correlation. State the source of any assumed correlation and check a plausible range.',
  suffix_direct: 'Enter a raw one-group estimate, then identify whether the reported SE, variance or interval is on the raw or transformed analysis scale. Normal/Wald CI conversion and raw-scale delta conversion are approximations; verify the original method and unit.',
};
function anFormatGuidance(format) {
  if (AN_FORMAT_GUIDANCE[format]) return AN_FORMAT_GUIDANCE[format];
  for (const prefix of ['paired_md_', 'parallel_change_'])
    if (format.startsWith(prefix)) return AN_FORMAT_GUIDANCE_PARTIAL[prefix];
  if (format.endsWith('_direct')) return AN_FORMAT_GUIDANCE_PARTIAL.suffix_direct;
  return '';
}

const AN_FORMAT_FIELDS = {
  binary_correlation_conversion: [['events_t','t 组事件数'], ['total_t','t 组总人数'], ['events_c','c 组事件数'], ['total_c','c 组总人数'], ['transform','相关定义与校正方法']],
  glass_delta_two_arm: [['mean_t','治疗组均值'], ['mean_c','对照组均值'], ['sd_c','对照组 SD（非 SE）'], ['n_t','治疗组样本量'], ['n_c','对照组样本量'], ['direction','确认治疗组减对照组']],
  ratio_of_means_two_arm: [['mean_t','治疗组均值'], ['sd_t','治疗组标准差 SD'], ['n_t','治疗组样本量'], ['mean_c','对照组均值'], ['sd_c','对照组标准差 SD'], ['n_c','对照组样本量'], ['direction','确认治疗组/对照组均值比']],
  correlation_r_n: [['r','相关系数 r'], ['n','样本量']],
  hazard_ratio_ci: [['estimate','风险比 HR'], ['ci_method','原文置信区间方法'], ['ci_low','置信区间下限'], ['ci_high','置信区间上限'], ['confidence_level','置信水平（0–1）']],
  survival_logrank_oe_v: [['oe','治疗组观察事件数减期望事件数（O−E）'], ['variance','Log-rank 方差 V'],
    ['oe_definition','O−E 的定义'], ['direction','HR 的比较方向']],
  rate_ratio_events_time: [['events_t','治疗组事件数'], ['time_t','治疗组人时'], ['events_c','对照组事件数'], ['time_c','对照组人时']],
  generic_ci: [['measure','效应指标'], ['scale','原文效应尺度'], ['estimate','估计值'], ['ci_method','原文置信区间方法'], ['ci_low','置信区间下限'], ['ci_high','置信区间上限'], ['confidence_level','置信水平（0–1）']],
  generic_wald_p: [['measure','效应指标'], ['scale','原文效应尺度'], ['estimate','估计值'], ['p','精确双侧 Wald p 值']],
  independent_t_n: [['t','有符号合并方差 t（治疗组－对照组）'], ['n_t','治疗组样本量'], ['n_c','对照组样本量']],
  independent_p_n: [['p','精确双侧 p 值'], ['direction','效应方向'], ['n_t','治疗组样本量'], ['n_c','对照组样本量']],
  independent_f_n: [['f','两组单因素方差分析 F 值'], ['df_num','分子自由度（=1）'],
    ['df_den','残差自由度（=n_t+n_c−2）'], ['direction','效应方向'],
    ['n_t','治疗组样本量'], ['n_c','对照组样本量']],
  independent_d_n: [['d','原文有符号 Cohen d（治疗组－对照组）'], ['n_t','治疗组样本量'], ['n_c','对照组样本量']],
  paired_md_sd_diff: [['n','完整配对数'], ['mean_a','条件 A 均值'], ['mean_b','条件 B 均值'],
    ['sd_diff','原文配对差值 SD'], ['design','配对设计']],
  paired_md_correlation: [['n','完整配对数'], ['mean_a','条件 A 均值'], ['mean_b','条件 B 均值'],
    ['sd_a','条件 A 的 SD'], ['sd_b','条件 B 的 SD'], ['correlation','配对内相关系数'],
    ['correlation_source','相关系数来源'], ['correlation_source_note','来源报告页码或假设依据'],
    ['design','配对设计']],
  parallel_change_sd: [['n_t','治疗组完整配对数'], ['baseline_mean_t','治疗组基线均值'],
    ['final_mean_t','治疗组末次均值'], ['sd_change_t','治疗组变化值 SD'],
    ['n_c','对照组完整配对数'], ['baseline_mean_c','对照组基线均值'],
    ['final_mean_c','对照组末次均值'], ['sd_change_c','对照组变化值 SD']],
  parallel_change_correlations: [['n_t','治疗组完整配对数'], ['baseline_mean_t','治疗组基线均值'],
    ['final_mean_t','治疗组末次均值'], ['sd_baseline_t','治疗组基线 SD'],
    ['sd_final_t','治疗组末次 SD'], ['correlation_t','治疗组基线与末次相关系数 r'],
    ['correlation_source_t','治疗组相关系数来源'], ['correlation_source_note_t','治疗组相关系数报告页码或假设依据'],
    ['n_c','对照组完整配对数'], ['baseline_mean_c','对照组基线均值'],
    ['final_mean_c','对照组末次均值'], ['sd_baseline_c','对照组基线 SD'],
    ['sd_final_c','对照组末次 SD'], ['correlation_c','对照组基线与末次相关系数 r'],
    ['correlation_source_c','对照组相关系数来源'], ['correlation_source_note_c','对照组相关系数报告页码或假设依据']],
  crossover_2x2_md: [['n_ab','AB 序列完整配对数'], ['mean_diff_ab','AB 序列个体内 A−B 均值'],
    ['sd_diff_ab','AB 序列个体内 A−B 的 SD'], ['n_ba','BA 序列完整配对数'],
    ['mean_diff_ba','BA 序列个体内 A−B 均值'], ['sd_diff_ba','BA 序列个体内 A−B 的 SD'],
    ['design_note','来源、洗脱期与周期假设']],
  single_mean_sd_n: [['n','单组样本量'], ['mean','单组均值'], ['sd','单组 SD']],
  single_proportion_events_n: [['events','事件数'], ['total','单组总人数']],
  single_rate_events_time: [['events','事件数'], ['person_time','注明单位的人时']],
  single_mean_direct: [['estimate','原文单组均值'], ['precision_scale','不确定性尺度'], ['uncertainty_kind','原文不确定性类型']],
  single_proportion_direct: [['estimate','原文单组比例（0–1）'], ['precision_scale','不确定性尺度'], ['uncertainty_kind','原文不确定性类型']],
  single_rate_direct: [['estimate','原文单组发生率（按所注明的人时单位）'], ['precision_scale','不确定性尺度'], ['uncertainty_kind','原文不确定性类型']]
};
const AN_OPTION_LABELS = {
  phi:'φ：观测二分类相关（无校正）',
  r_equiv_approx:'Bonett–Price 近似：潜在连续变量相关',
  HR:'HR · 危险比', RATE_RATIO:'率比', RR:'RR · 相对危险度', OR:'OR · 比值比',
  RD:'RD · 风险差', MD:'MD · 均值差', SMD:'SMD · 标准化均值差',
  FISHER_Z:'Fisher z · 相关系数变换',
  se:'标准误 SE', variance:'方差', ci:'置信区间', raw:'原始尺度', logit:'Logit 尺度',
  log:'自然对数尺度', natural:'自然尺度', fisher_z:'Fisher z 尺度',
  paired_conditions:'配对条件', pre_post:'同组前后', crossover:'交叉试验',
  observed_minus_expected_treatment:'治疗组观察减期望事件数',
  treatment_minus_control:'治疗组减对照组', treatment_vs_control:'治疗组相对于对照组',
  treatment_higher:'治疗组更高', control_higher:'对照组更高',
  reported:'原文报告', derived:'根据数据推导', assumed:'研究者假设'
};
function renderDirectUncertainty(holder) {
  holder.querySelectorAll('[data-direct-uncertainty]').forEach(node => node.remove());
  const kind = holder.querySelector('[data-format-key="uncertainty_kind"]').value;
  if (!kind) return;
  const fields = kind === 'ci' ? [['ci_method','原文置信区间方法'], ['ci_lower','置信区间下限'], ['ci_upper','置信区间上限'], ['confidence_level','置信水平（0–1）']] :
    [[kind, kind === 'se' ? '原文标准误' : '原文方差']];
  fields.forEach(([key, label]) => {
    const input = key === 'ci_method' ? el('select', { class:'input', 'aria-label':label,
      'data-format-key':key }, el('option', {value:'', text:'选择原文区间方法'}),
      el('option', {value:'wald_normal', text:'所选尺度上的正态/Wald 区间'}),
      el('option', {value:'unsupported', text:'精确、score、profile、t 或未知区间：不能反推标准误'})) :
      el('input', { class:'input', type:'number', step:'any', 'aria-label':label,
        'data-format-key':key, placeholder:label });
    if (key === 'confidence_level') { input.min = '0'; input.max = '1'; }
    holder.append(el('label', { 'data-direct-uncertainty':'', text:label }, input));
  });
}
function renderFormatFields() {
  const holder = $('#anFormatFields'); holder.replaceChildren();
  const format = anValue('anFormat');
  if (!format) {
    $('#anFormatGuidance').textContent = T('先选择原文数据格式，再填写数据。');
    ['anFormatCiHint','anFormatReferenceGeneral','anFormatReferenceLogrank','anFormatReferencePaired',
      'anFormatReferenceTests','anFormatReferenceSingle','anFormatReferenceRom','anFormatReferenceGlass','anFormatReferenceBinaryCorrelation'].forEach(id => { $('#'+id).hidden = true; });
    return;
  }
  const paired = format.startsWith('paired_md_') || format.startsWith('parallel_change_') || format.startsWith('crossover_');
  const tests = format === 'independent_t_n' || format === 'independent_p_n' || format === 'independent_f_n' || format === 'independent_d_n';
  $('#anFormatCiHint').hidden = format !== 'generic_ci' && format !== 'hazard_ratio_ci' && !format.endsWith('_direct');
  $('#anFormatReferenceGeneral').hidden = paired || tests || format.startsWith('single_') || format === 'survival_logrank_oe_v';
  $('#anFormatReferenceBinaryCorrelation').hidden = format !== 'binary_correlation_conversion';
  $('#anFormatReferenceGlass').hidden = format !== 'glass_delta_two_arm';
  $('#anFormatReferenceRom').hidden = format !== 'ratio_of_means_two_arm';
  $('#anFormatReferenceLogrank').hidden = format !== 'survival_logrank_oe_v';
  $('#anFormatReferencePaired').hidden = !paired;
  $('#anFormatReferenceTests').hidden = !tests;
  $('#anFormatReferenceSingle').hidden = !format.startsWith('single_');
  $('#anFormatGuidance').textContent = anFormatGuidance(format);
  AN_FORMAT_FIELDS[format].forEach(([key, label]) => {
    let input;
    if (['transform','measure','scale','design','direction','oe_definition','precision_scale','uncertainty_kind','ci_method','correlation_source','correlation_source_t','correlation_source_c'].includes(key)) {
      input = el('select', { class:'input', 'aria-label':label, 'data-format-key':key });
      input.append(el('option', { value:'', text:T('请选择') }));
      (key === 'transform' ? ['phi','r_equiv_approx'] : key === 'uncertainty_kind' ? ['se','variance','ci'] :
        key === 'ci_method' ? ['wald_normal','unsupported'] :
        key === 'precision_scale' ? format === 'single_proportion_direct' ? ['raw','logit'] : format === 'single_rate_direct' ? ['raw','log'] : ['raw'] :
        key === 'measure' ? ['HR','RATE_RATIO','RR','OR','RD','MD','SMD','FISHER_Z'] :
        key === 'scale' ? ['natural','log','fisher_z'] : key === 'design' ?
        ['paired_conditions','pre_post','crossover'] : key === 'oe_definition' ?
        ['observed_minus_expected_treatment'] : key === 'direction' ?
        ['ratio_of_means_two_arm','glass_delta_two_arm'].includes(format) ? ['treatment_minus_control'] : format === 'survival_logrank_oe_v' ? ['treatment_vs_control'] : ['treatment_higher','control_higher'] : ['reported','derived','assumed'])
        .forEach(v => input.append(el('option', { value:v, text:key === 'ci_method' ? (v === 'wald_normal' ? '所选尺度上的正态/Wald 区间' : '精确、score、profile、t 或未知区间：不能反推标准误') : format === 'ratio_of_means_two_arm' && key === 'direction' ? '治疗组均值/对照组均值' : format === 'glass_delta_two_arm' && key === 'direction' ? '治疗组均值－对照组均值' : T(AN_OPTION_LABELS[v] || v) })));
    } else {
      const note = key.startsWith('correlation_source_note') || key === 'design_note';
      input = el('input', { class:'input', type:note ? 'text' : 'number', step:'any', 'aria-label':label,
        'data-format-key':key, placeholder:label });
      if (['n','n_t','n_c','n_ab','n_ba','events','total','events_t','events_c','total_t','total_c'].includes(key)) {
        input.step = '1';
        input.min = key === 'n' && format === 'correlation_r_n' ? '4' : key.startsWith('events') || key.startsWith('total') ?
          (format === 'rate_ratio_events_time' || key.startsWith('total') ? '1' : '0') : '2';
      }
      if (key === 'confidence_level') { input.min = '0'; input.max = '1'; }
      if (key === 'correlation' || key === 'correlation_t' || key === 'correlation_c') {
        input.min = '-1'; input.max = '1';
      }
      if (key === 'p') { input.min = '0'; input.max = '1'; }
      if (key === 'f') input.min = '0';
      if (key === 'df_num') { input.step = '1'; input.min = '1'; }
      if (key === 'df_den') { input.step = '1'; input.min = '2'; }
    }
    holder.append(el('label', { text:label }, input));
  });
  const uncertainty = holder.querySelector('[data-format-key="uncertainty_kind"]');
  if (uncertainty) { uncertainty.addEventListener('change', () => renderDirectUncertainty(holder)); renderDirectUncertainty(holder); }
  const measureInput = holder.querySelector('[data-format-key="measure"]'), scaleInput = holder.querySelector('[data-format-key="scale"]');
  if (measureInput && scaleInput) measureInput.addEventListener('change', () => {
    const allowed = measureInput.value === 'FISHER_Z' ? ['fisher_z'] :
      ['RD','MD','SMD'].includes(measureInput.value) ? ['natural'] : ['natural','log'];
    scaleInput.replaceChildren(el('option', { value:'', text:T('请选择') }),
      ...allowed.map(value => el('option', { value, text:T(AN_OPTION_LABELS[value]) })));
  });
}
$('#anFormat').addEventListener('change', () => { $('#anFormatResult').textContent = ''; $('#anEffectDirection').value = ''; renderFormatFields(); });
$('#anEffectStudy').addEventListener('change', () => { $('#anEffectDirection').value = ''; });
renderFormatFields();
const formatSaveButton = $('#anFormatSave');
let formatSaving = false, formatRequest = 0, formatTimer = null;
document.addEventListener('reviewflow:analysis-task-changed', () => {
  formatRequest++; clearInterval(formatTimer); formatSaving = false;
  formatSaveButton.disabled = false; $('#anFormatResult').textContent = '';
});
const formatContext = () => JSON.stringify([S.task?.task_id,
  ...['anEffectStudy','anComparison','anOutcome','anTimepoint','anSource','anSourceLocator','anAppendEffect','anFormat','anEffectDirection']
    .map(id => $('#'+id).type === 'checkbox' ? $('#'+id).checked : anValue(id)),
  ...Array.from($('#anFormatFields').querySelectorAll('[data-format-key]'), node => node.value)]);
formatSaveButton.addEventListener('click', async () => {
  if (formatSaving || formatSaveButton.disabled) return;
  const request = ++formatRequest;
  let timer, original, taskId;
  try {
    if (!anValue('anFormat')) throw new Error(T('请选择原文数据格式。'));
    const values = {};
    $('#anFormatFields').querySelectorAll('[data-format-key]').forEach(input => {
      if (input.value.trim() === '') { input.focus(); throw new Error(T('请填写当前聚焦的必填字段。')); }
      if (input.dataset.formatKey === 'uncertainty_kind') return;
      values[input.dataset.formatKey] = ['transform','measure','scale','design','direction','oe_definition','precision_scale','ci_method','correlation_source','correlation_source_t','correlation_source_c'].includes(input.dataset.formatKey) ||
        input.dataset.formatKey.startsWith('correlation_source_note') || input.dataset.formatKey === 'design_note' ? input.value : Number(input.value);
    });
    if (Object.entries(values).some(([key, value]) => typeof value === 'number' && !Number.isFinite(value)))
      throw new Error(T('请输入有限数值。'));
    if (['generic_ci','generic_wald_p'].includes(anValue('anFormat'))) {
      const allowed = values.measure === 'FISHER_Z' ? ['fisher_z'] :
        ['RD','MD','SMD'].includes(values.measure) ? ['natural'] : ['natural','log'];
      if (!allowed.includes(values.scale)) throw new Error(T('请选择与效应指标匹配的输入尺度。'));
    }
    if ('confidence_level' in values && !(values.confidence_level > 0 && values.confidence_level < 1))
      throw new Error(T('置信水平必须严格大于 0 且小于 1。'));
    if (anValue('anFormat') === 'generic_wald_p' && !(values.p > 0 && values.p < 1))
      throw new Error(T('精确双侧 p 值必须严格大于 0 且小于 1。'));
    for (const key of ['events','total','events_t','events_c','total_t','total_c']) {
      if (key in values && (!Number.isSafeInteger(values[key]) ||
          values[key] < (key.startsWith('total') || anValue('anFormat') === 'rate_ratio_events_time' ? 1 : 0))) {
        $('#anFormatFields').querySelector(`[data-format-key="${key}"]`)?.focus();
        throw new Error(T('当前聚焦的计数必须是符合范围的整数。'));
      }
    }
    const body = { study_id:anValue('anEffectStudy'), comparison:anValue('anComparison'),
      outcome:anValue('anOutcome'), timepoint:anValue('anTimepoint'), source_key:anValue('anSource'),
      source_locator:anValue('anSourceLocator'), append:$('#anAppendEffect').checked,
      format_name:anValue('anFormat'), values,
      ...(anValue('anEffectDirection') ? {effect_direction:anValue('anEffectDirection')} : {}) };
    original = formatContext(); taskId = S.task?.task_id;
    formatSaving = true; formatSaveButton.disabled = true;
    const started = performance.now();
    const progress = () => {
      if (request === formatRequest) $('#anFormatResult').textContent = T(formatContext() === original ?
        '正在保存效应…已等待 {0} 秒。' : '正在保存修改前的输入…已等待 {0} 秒。',
        ((performance.now()-started)/1000).toFixed(1));
    };
    progress(); timer = formatTimer = setInterval(progress, 500);
    const result = await api(taskApi() + '/analysis/format-effects', { method:'POST', body });
    clearInterval(timer);
    if (request !== formatRequest) {
      if (S.task?.task_id !== taskId) toast(T('结果 {0} 已保存至先前任务 {1}。', result.result_id, taskId), 'warn');
      return;
    }
    if (formatContext() !== original) {
      if (S.task?.task_id === taskId) { await loadAnalysis(); $('#anFormatResult').textContent = T('已保存修改前的输入。再次保存前请检查结果列表。'); }
      else toast(T('结果 {0} 已保存至先前任务 {1}。', result.result_id, taskId), 'warn');
      return;
    }
    const effect = result.effect;
    const audit = [
      effect.standardizer === 'control_arm_sd' ? T('Glass Δ；以对照组 SD 标准化；未做小样本校正。') : '',
      Number.isFinite(effect.ratio) ? T('均值比 {0}（分析尺度：log ROM）。', Number(effect.ratio).toPrecision(5)) : '',
      Number.isFinite(effect.cohen_d) ? T('Cohen d：{0}。', Number(effect.cohen_d).toPrecision(5)) : '',
      Number.isFinite(effect.reconstructed_t) ? T('反推 t：{0}。', Number(effect.reconstructed_t).toPrecision(5)) : '',
      Number.isFinite(effect.period_contrast_p1_minus_p2) ? T('周期对比 P1−P2：{0}。', Number(effect.period_contrast_p1_minus_p2).toPrecision(5)) : ''
    ].filter(Boolean);
    const directionText = {first_vs_second:'方向：前者相对于后者。',second_vs_first:'方向：后者相对于前者。',not_applicable:'方向：不适用。'}[effect.effect_direction];
    const summary = [
      T('已保存 {0}：效应值 {1}；标准误 {2}（尺度 {3}）。', effect.measure,
        Number(effect.estimate).toPrecision(5), Number(effect.se).toPrecision(5), effect.se_scale),
      ...audit, directionText ? T(directionText) : '',
      T('来源报告：{0}；结果 ID：{1}。', body.source_key, result.result_id ?? '—'),
      effect.warnings?.join(' ') || '',
      T('撰写论文或报告时，请引用实际使用的方法原始文献及参考书籍，并按期刊格式核对；仅引用本软件不足以说明方法来源。')
    ].filter(Boolean);
    $('#anFormatResult').replaceChildren(...summary.flatMap((part, i) =>
      i ? [' ', el('span', {text:part})] : [el('span', {text:part})]));
    $('#anMeasure').value = result.effect.measure;
    $('#anMeasure').dispatchEvent(new Event('change'));
    $('#anEstimate').value = result.effect.estimate;
    $('#anSe').value = result.effect.se;
    await loadAnalysis();
    if (formatContext() !== original) {
      if (S.task?.task_id === taskId) $('#anFormatResult').textContent = T('已保存修改前的输入。再次保存前请检查结果列表。');
      else toast(T('结果 {0} 已保存至先前任务 {1}。', result.result_id, taskId), 'warn');
    }
  } catch (e) {
    if (request === formatRequest && (!taskId || S.task?.task_id === taskId)) {
      if (!original || formatContext() === original) { $('#anFormatResult').textContent = e.message; toast(e.message, 'warn'); }
      else $('#anFormatResult').textContent = '';
    }
  }
  finally { clearInterval(timer); if (request === formatRequest) { formatSaving = false; formatSaveButton.disabled = false; if (taskId && S.task?.task_id !== taskId) $('#anFormatResult').textContent = ''; } }
});
})();
