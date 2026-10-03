(function () {
const holder = $('#anEffectVariants');

function fillChoices(id, key) {
  const values = [...new Set(anEffects.map(effect => effect[key]))].sort();
  const list = $('#'+id); list.replaceChildren();
  values.forEach(value => list.append(el('option', { value })));
}
function storedScale(measure) {
  if (['RR','OR','HR','RATE_RATIO'].includes(measure)) return T('自然尺度');
  if (['LOG_ROM','LOG_RATE','PAIRED_OR'].includes(measure)) return T('对数尺度');
  if (measure === 'FISHER_Z') return T('Fisher z 尺度');
  if (measure === 'LOGIT_PROP') return T('分析尺度：logit');
  return T('自然尺度');
}
function renderVariants() {
  fillChoices('anComparisonOptions', 'comparison');
  fillChoices('anOutcomeOptions', 'outcome');
  fillChoices('anTimepointOptions', 'timepoint');
  const context = [anValue('anEffectStudy'), anValue('anComparison'),
    anValue('anOutcome'), anValue('anTimepoint')];
  holder.replaceChildren();
  if (context.some(value => !value)) return;
  const variants = anEffects.filter(effect => [effect.study_id, effect.comparison,
    effect.outcome, effect.timepoint].every((value, index) => value === context[index]));
  if (!variants.length) return;
  const table = el('table');
  table.append(el('caption', { text:T('当前组合：{0} 个结果', variants.length) }));
  table.append(el('thead', {}, el('tr', {}, ...['采用','ID','效应指标','估计值','分析尺度上的标准误 SE','来源报告','来源定位','录入方式'].map(label => el('th', { text:T(label), scope:'col' })))));
  const body = el('tbody');
  variants.forEach(effect => {
    const action = effect.selected ? el('strong', { text:T('已采用') }) :
      el('button', { class:'btn b-out', type:'button', text:T('采用此结果'),
        'aria-label':T('采用结果 {0}', effect.result_id), onclick:() => anButtonWithProgress(action, async () => {
            await api(taskApi() + `/analysis/effects/${effect.result_id}/select`, { method:'POST' });
            await loadAnalysis();
        }) });
    const row = el('tr', { class:effect.selected ? 'an-variant-selected' : '' }, el('td', {}, action),
      ...[effect.result_id, effect.measure, Number(effect.estimate).toPrecision(5),
        Number(effect.se).toPrecision(5), effect.source_key, effect.source_locator || '—',
        effect.input_data?.estimated_summary ? T('由各组估计摘要计算；见估计审计') : effect.input_data?.survival_summary_conversion ? T('中位生存时间近似换算') :
        effect.input_data?.standardizer === 'control_arm_sd' && effect.input_data?.bias_correction === 'none' ? T('Glass Δ · 对照组 SD') :
        effect.input_data?.standardizer === 'pooled_within_group_sd' && effect.input_data?.bias_correction === 'hedges' ? T('Hedges g · 合并组内 SD') :
        effect.input_data?.ci_method === 'wald_normal' ? T('{0} · 原文正态/Wald 区间', effect.entry_method) : effect.entry_method].map((value, index) => el('td', { text:String(value) + (index === 6 && effect.input_data?.effect_direction ? ` · ${T({first_vs_second:'比较组前者相对于后者',second_vs_first:'比较组后者相对于前者',not_applicable:'方向不适用'}[effect.input_data.effect_direction] || effect.input_data.effect_direction)}` : '') })));
    row.children[3].replaceChildren(String(Number(effect.estimate).toPrecision(5)), ' (', el('span', {text:storedScale(effect.measure)}), ')');
    body.append(row);
  });
  table.append(body); holder.append(table);
  const legacy = variants.filter(effect =>
    !['FISHER_Z','PHI','R_EQUIV_APPROX','MEAN','LOGIT_PROP','LOG_RATE'].includes(effect.measure) &&
    !effect.input_data?.effect_direction);
  if (!legacy.length) return;
  holder.append(el('p', { class:'hint', text:T('这些旧结果尚未确认效应方向。请核对原文报告并记录页码或表号。确认不会改变估计值和 SE；反向结果须先转换方向才能合并。') }));
  legacy.forEach(effect => {
    const direction = el('select', { class:'input', 'aria-label':T('结果 {0} 的效应方向', effect.result_id) },
      el('option', { value:'', text:T('选择组间比较的方向') }),
      el('option', { value:'first_vs_second', text:T('比较组前者相对于后者') }),
      el('option', { value:'second_vs_first', text:T('比较组后者相对于前者') }));
    const locator = el('input', { class:'input', type:'text',
      'aria-label':T('结果 {0} 的原文页码或表号', effect.result_id),
      placeholder:T('原文页码或表号') });
    locator.value = effect.source_locator || '';
    const confirm = el('button', { class:'btn b-out', type:'button', text:T('确认方向'),
      'aria-label':T('确认结果 {0} 的方向', effect.result_id) });
    confirm.addEventListener('click', () => anButtonWithProgress(confirm, async () => {
      if (!direction.value || !locator.value.trim()) throw new Error(T('请选择方向并填写原文页码或表号。'));
      const taskId = S.task?.task_id, submitted = [direction.value, locator.value.trim()];
      await api(taskApi() + `/analysis/effects/${effect.result_id}/direction`, { method:'POST',
        body:{effect_direction:submitted[0], source_locator:submitted[1]} });
      if (S.task?.task_id !== taskId) { toast(T('已在先前任务 {0} 确认结果方向。', taskId), 'warn'); return; }
      const changed = direction.value !== submitted[0] || locator.value.trim() !== submitted[1];
      await loadAnalysis();
      if (changed && S.task?.task_id === taskId) toast(T('已按先前输入确认方向，请核对结果列表。'), 'warn');
    }, '正在确认…{0} 秒'));
    holder.append(el('div', { class:'row', 'data-result-id':effect.result_id }, el('strong', { text:T('结果 {0} · {1}', effect.result_id, effect.source_key) }),
      direction, locator, confirm));
  });
}
document.addEventListener('reviewflow:analysis-core-loaded', renderVariants);
document.addEventListener('reviewflow:analysis-task-changed', () => {
  holder.replaceChildren();
  for (const id of ['anComparisonOptions','anOutcomeOptions','anTimepointOptions']) $('#'+id).replaceChildren();
});
for (const id of ['anEffectStudy', 'anComparison', 'anOutcome', 'anTimepoint'])
  $('#'+id).addEventListener('input', renderVariants);
renderVariants();
})();
