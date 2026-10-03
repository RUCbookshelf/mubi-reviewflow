(function () {
  const host = $('#anChangeResult'), button = $('#anChangeSave');
  let generation = 0, activeRequest = 0, saving = false, timer = null;
  const clear = () => { generation++; button.disabled = saving; if (!saving) host.replaceChildren(); };
  const ids = ['anChangeMethod','anChangeN','anChangePre','anChangePost','anChangePreSd','anChangeSd','anChangeCorrelation','anChangeCorrelationSource',
    'anEffectStudy','anComparison','anOutcome','anTimepoint','anSource','anSourceLocator','anAppendEffect','anEffectDirection'];
  ids.forEach(id => $('#'+id).addEventListener('input', clear));
  const context = () => JSON.stringify([S.task?.task_id, ...ids.map(id => $('#'+id).type === 'checkbox' ? $('#'+id).checked : anValue(id))]);
  $('#anChangeMethod').addEventListener('change', () => {
    const method = anValue('anChangeMethod');
    $('#anChangeSdLabel').hidden = !['pretest_change','change_sd'].includes(method);
    $('#anChangeCorrelationLabel').hidden = method !== 'pretest_correlation';
    $('#anChangeSourceLabel').hidden = method !== 'pretest_correlation';
  });
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', () => {
    activeRequest++; clearInterval(timer); saving = false;
    ids.slice(0, 8).forEach(id => $('#'+id).value = '');
    $('#anChangeMethod').dispatchEvent(new Event('change'));
    clear(); button.disabled = false;
  });
  button.addEventListener('click', async () => {
    if (saving) return;
    clear(); const ticket = generation, originalContext = context(), taskId = S.task?.task_id, request = ++activeRequest;
    let requestTimer;
    try {
      const method = anValue('anChangeMethod');
      if (!method) throw new Error(T('请选择标准化分母和方差方法。'));
      if (!anValue('anEffectDirection')) throw new Error(T('请确认效应方向与比较组顺序。'));
      const number = id => { const value = anValue(id); if (value === '' || !Number.isFinite(Number(value))) throw new Error(T('请填写所有显示的数值字段。')); return Number(value); };
      const values = {n:number('anChangeN'), mean_pre:number('anChangePre'), mean_post:number('anChangePost'), sd_pre:number('anChangePreSd')};
      if (!Number.isSafeInteger(values.n) || values.n < 3) throw new Error(T('完整配对数 n 须为不小于 3 的整数。'));
      if (method === 'pretest_correlation') {
        values.correlation = number('anChangeCorrelation'); values.correlation_source = anValue('anChangeCorrelationSource');
        if (values.correlation < -1 || values.correlation > 1) throw new Error(T('前后测相关系数须在 -1 到 1 之间。'));
        if (!values.correlation_source) throw new Error(T('请记录相关系数的来源或设定理由。'));
      } else values.sd_change = number('anChangeSd');
      const body = {study_id:anValue('anEffectStudy'), comparison:anValue('anComparison'), outcome:anValue('anOutcome'),
        timepoint:anValue('anTimepoint'), source_key:anValue('anSource'), source_locator:anValue('anSourceLocator'),
        append:$('#anAppendEffect').checked, format_name:'single_group_standardized_change',
        standardizer:method === 'change_sd' ? 'change_sd' : 'pretest_sd', values,
        effect_direction:anValue('anEffectDirection')};
      saving = true; button.disabled = true;
      const started = performance.now();
      const progress = () => { if (request === activeRequest) host.textContent = T('正在保存{0}…已等待 {1} 秒', ticket === generation ? T('标准化变化效应') : T('先前输入'), ((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress, 500); requestTimer = timer;
      const response = await api(taskApi() + '/analysis/format-effects', {method:'POST', body});
      if (request !== activeRequest) { toast(T('结果 {0} 已保存到先前任务 {1}。', response.result_id, taskId), 'warn'); return; }
      if (ticket !== generation) {
        if (S.task?.task_id === taskId) { await loadAnalysis(); host.textContent = T('先前输入已保存。再次保存前请核对结果列表。'); }
        else toast(T('结果 {0} 已保存到先前任务 {1}。', response.result_id, taskId), 'warn');
        return;
      }
      const effect = response.effect;
      $('#anMeasure').value = effect.measure; $('#anMeasure').dispatchEvent(new Event('change'));
      $('#anEstimate').value = effect.estimate; $('#anSe').value = effect.se;
      await loadAnalysis();
      if (context() !== originalContext) {
        if (S.task?.task_id === taskId) host.textContent = T('先前输入已保存。再次保存前请核对结果列表。');
        else toast(T('结果 {0} 已保存到先前任务 {1}。', response.result_id, taskId), 'warn');
        return;
      }
      host.textContent = T('{0}：{1}；SE {2}。后测 − 前测；分母 {3}；Hedges J={4}；方差方法 {5}。已保存结果 {6}；来源 {7}。请核对原文并引用所用方法。', effect.measure, Number(effect.estimate).toPrecision(6), Number(effect.se).toPrecision(6), effect.standardizer, Number(effect.hedges_correction).toPrecision(6), effect.variance_method, response.result_id, body.source_key);
    } catch (error) { if (request === activeRequest && S.task?.task_id === taskId) host.textContent = error.message; }
    finally { clearInterval(requestTimer); if (request === activeRequest) { saving = false; button.disabled = false; if (S.task?.task_id !== taskId) host.replaceChildren(); } }
  });
})();
