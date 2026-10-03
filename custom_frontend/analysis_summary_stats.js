(function () {
  const panel = $('#anSummaryStatsPanel'), host = $('#anSummaryStatsResult');
  const save = $('#anSummaryStatsSave'), download = $('#anSummaryStatsExport');
  let generation = 0, activeRequest = 0, record = null, timer = null, saving = false;
  function clear() { generation++; record = null; download.disabled = true; save.disabled = saving; if (!saving) host.replaceChildren(); }
  for (const arm of ['treatment','control']) {
    const group = el('fieldset', {'data-summary-arm':arm}), format = el('select', {class:'input', 'data-summary-key':'format'});
    for (const [value, text] of [['','选择原文汇总格式'],['reported','原文均值和标准差'],['range','中位数、最小值和最大值'],['iqr','中位数、第一和第三四分位数']]) format.append(el('option', {value,text:T(text)}));
    const fields = el('div', {class:'row'});
    group.append(el('legend', {text:T(arm === 'treatment' ? '治疗组' : '对照组')}), el('label', {text:T('原文汇总格式')}, format), fields);
    $('#anSummaryStatsArms').append(group);
    format.addEventListener('change', () => {
      clear(); fields.replaceChildren();
      if (!format.value) return;
      if (format.value !== 'reported') {
        const method = el('select', {class:'input', 'data-summary-key':'method'});
        method.append(el('option', {value:'', text:T('选择估算方法')}));
        for (const [value,text] of [['wan_2014','Wan (2014)'],['luo_2018','Luo (2018) 均值 + Wan 标准差'],...(format.value === 'range' ? [['hozo_2005','Hozo (2005)']] : [])]) method.append(el('option',{value,text:T(text)}));
        fields.append(el('label',{text:T('估算方法')},method),
          el('p',{class:'hint',text:T('这些公式依赖分布假设。数据可能偏态时，请比较可用方法，并对估算研究做纳入与排除分析。请核对并引用方法原文。')}));
      }
      const specs = [['n','样本量'], ...(format.value === 'reported' ? [['mean','均值'],['sd','标准差 SD']] :
        [['median','中位数'], ...(format.value === 'range' ? [['min_val','最小值'],['max_val','最大值']] : [['q1','第一四分位数 Q1'],['q3','第三四分位数 Q3']])])];
      for (const [key,text] of specs) fields.append(el('label',{text:T(text)},el('input',{class:'input',type:'number',step:key === 'n' ? '1' : 'any','data-summary-key':key})));
    });
  }
  const ids = ['anEffectStudy','anComparison','anOutcome','anTimepoint','anSource','anSourceLocator','anAppendEffect','anEffectDirection'];
  const context = () => JSON.stringify([S.task?.task_id,...ids.map(id => $('#'+id).type === 'checkbox' ? $('#'+id).checked : anValue(id)),...Array.from(panel.querySelectorAll('input,select'),node => node.value)]);
  panel.addEventListener('input',clear);
  ids.forEach(id => $('#'+id).addEventListener('input',clear));
  document.addEventListener('reviewflow:analysis-core-loaded',clear);
  document.addEventListener('reviewflow:analysis-task-changed', () => {
    activeRequest++; clearInterval(timer); saving = false;
    ['anSummaryStatsMeasure','anSummaryStatsDirection'].forEach(id => $('#'+id).value = '');
    panel.querySelectorAll('[data-summary-key="format"]').forEach(node => {
      node.value = ''; node.dispatchEvent(new Event('change'));
    });
    clear();
  });
  save.addEventListener('click',async () => {
    if (saving) return;
    clear(); const ticket = generation, original = context(), taskId = S.task?.task_id, request = ++activeRequest;
    let requestTimer;
    try {
      const body = {study_id:anValue('anEffectStudy'),comparison:anValue('anComparison'),outcome:anValue('anOutcome'),timepoint:anValue('anTimepoint'),source_key:anValue('anSource'),source_locator:anValue('anSourceLocator'),append:$('#anAppendEffect').checked,measure:anValue('anSummaryStatsMeasure'),direction:anValue('anSummaryStatsDirection'),effect_direction:anValue('anEffectDirection')};
      if (!body.measure || !body.direction || !body.source_locator) throw new Error(T('请选择效应指标和方向，并填写上方原文页码或表号及单位。'));
      if (!body.effect_direction) throw new Error(T('请选择效应方向与比较组的对应关系。'));
      for (const arm of ['treatment','control']) {
        body[arm] = {};
        panel.querySelectorAll(`[data-summary-arm="${arm}"] [data-summary-key]`).forEach(input => {
          const key = input.dataset.summaryKey, value = input.value.trim();
          if (!value) throw new Error(T('请填写两组汇总，并分别选择估算方法。'));
          if (!['format','method'].includes(key) && !Number.isFinite(Number(value))) throw new Error(T('数值输入必须为有限数。'));
          body[arm][key] = ['format','method'].includes(key) ? value : Number(value);
        });
      }
      saving = true; save.disabled = true;
      const started = performance.now();
      const progress = () => { if (request === activeRequest) host.textContent = T(ticket === generation
        ? '正在保存估算结果…已等待 {0} 秒。' : '正在保存先前输入…已等待 {0} 秒。', ((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress,500); requestTimer = timer;
      const response = await api(taskApi()+'/analysis/estimated-summary/effects',{method:'POST',body});
      if (request !== activeRequest) { toast(T('结果 {0} 已保存到先前任务 {1}。', response.result_id, taskId), 'warn'); return; }
      if (ticket !== generation) {
        if (S.task?.task_id === taskId) { await loadAnalysis(); host.textContent = T('已保存先前输入。请核对效应量列表，确认后再保存。'); }
        else toast(T('结果 {0} 已保存到先前任务 {1}。', response.result_id, taskId), 'warn');
        return;
      }
      $('#anMeasure').value = response.effect.measure; $('#anMeasure').dispatchEvent(new Event('change'));
      $('#anEstimate').value = response.effect.estimate; $('#anSe').value = response.effect.se;
      await loadAnalysis();
      if (context() !== original) {
        if (S.task?.task_id === taskId) host.textContent = T('已保存先前输入。请核对效应量列表，确认后再保存。');
        else toast(T('结果 {0} 已保存到先前任务 {1}。', response.result_id, taskId), 'warn');
        return;
      }
      host.append(el('p',{text:T('已保存 {0}：估计值 {1}；SE {2}。结果包含公式估算的汇总值。',
        response.effect.measure, Number(response.effect.estimate).toPrecision(6), Number(response.effect.se).toPrecision(6))}));
      for (const [arm, result] of Object.entries(response.input_data.arm_estimation)) host.append(el('p',{},
        el('span',{text:T(arm === 'treatment' ? '治疗组' : '对照组')}), ': ',
        el('span',{text:T(result.estimated ? '估算均值' : '原文均值')}), ` ${Number(result.mean_estimate).toPrecision(6)}, SD ${Number(result.sd_estimate).toPrecision(6)}; ${result.method}.`));
      for (const text of response.warnings) host.append(el('p',{class:'hint',text:T(
        text === 'Formula-estimated means/SDs are not observed data. The usual effect SE omits reconstruction uncertainty; compare analyses with and without these studies.'
          ? '公式估算的均值和标准差不是观测值；常规效应量标准误未计入重建不确定性。请比较纳入与排除这些研究的分析结果。' : text)}));
      for (const text of response.input_data.method_sources) host.append(el('p',{class:'hint',lang:'en',text}));
      host.append(el('p',{class:'hint',text:T('发表时请核对方法原文，并引用各组使用的估算方法及研究报告。')}));
      record = {request:body,result:response}; download.disabled = false;
    } catch (error) { if (request === activeRequest && S.task?.task_id === taskId) host.textContent = error.message; }
    finally { clearInterval(requestTimer); if (request === activeRequest) { saving = false; save.disabled = false; if (S.task?.task_id !== taskId) host.replaceChildren(); } }
  });
  download.addEventListener('click',() => {
    if (!record) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(record,null,2)],{type:'application/json'}));
    const link = el('a',{href:url,download:'reviewflow_estimated_summary.json'});
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
  });
})();
