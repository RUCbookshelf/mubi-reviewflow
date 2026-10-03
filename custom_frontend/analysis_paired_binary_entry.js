(function () {
  const panel = $('#anPairedBinaryPanel'), host = $('#anPairedBinaryResult');
  const save = $('#anPairedBinarySave'), download = $('#anPairedBinaryExport');
  let generation = 0, activeRequest = 0, record = null, timer = null, saving = false;
  const ids = ['anEffectStudy','anComparison','anOutcome','anTimepoint','anSource','anSourceLocator','anAppendEffect','anEffectDirection'];
  const context = () => JSON.stringify([S.task?.task_id, ...ids.map(id => $('#'+id).type === 'checkbox' ? $('#'+id).checked : anValue(id)),
    ...Array.from(panel.querySelectorAll('input,select'), element => element.value)]);
  const warningText = {
    'The selected add-half correction was applied to both discordant counts because one of them is zero; the corrected result is a sensitivity analysis and not a main result.': '一个不一致配对格为零，已对两个不一致格各加 0.5。校正结果仅供敏感性分析，不应作为主要结果。',
    'One discordant count is zero, so the matched-pair odds ratio rests on very few informative pairs; treat the interval as unreliable and prefer an exact conditional interval when pooling.': '一个不一致配对格为零，配对优势比可用的信息很少；该区间可能不可靠。合并时优先考虑精确条件区间。',
  };
  function clear() { generation++; record = null; download.disabled = true; save.disabled = saving; if (!saving) host.replaceChildren(); }
  panel.addEventListener('input', clear);
  ids.forEach(id => $('#'+id).addEventListener('input', clear));
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', () => {
    activeRequest++; clearInterval(timer); saving = false;
    ['Measure','Direction','Correction','A','B','C','D','Level']
      .forEach(field => $('#anPairedBinary'+field).value = '');
    clear();
  });
  save.addEventListener('click', async () => {
    if (saving) return;
    clear(); const ticket = generation, originalContext = context(), taskId = S.task?.task_id, request = ++activeRequest;
    let requestTimer;
    try {
      const body = {study_id:anValue('anEffectStudy'), comparison:anValue('anComparison'), outcome:anValue('anOutcome'),
        timepoint:anValue('anTimepoint'), source_key:anValue('anSource'), source_locator:anValue('anSourceLocator'),
        append:$('#anAppendEffect').checked, measure:anValue('anPairedBinaryMeasure'),
        direction:anValue('anPairedBinaryDirection'), or_correction:anValue('anPairedBinaryCorrection'),
        effect_direction:anValue('anEffectDirection'),
        level:Number(anValue('anPairedBinaryLevel')) / 100};
      if (!body.measure || !body.direction || !body.or_correction) throw new Error(T('请选择效应指标、条件顺序和校正规则。'));
      if (!body.effect_direction) throw new Error(T('请选择效应方向与比较的对应关系。'));
      if (!(body.level > 0 && body.level < 1)) throw new Error(T('置信水平必须大于 0 且小于 100。'));
      for (const cell of ['a','b','c','d']) {
        const raw = anValue('anPairedBinary'+cell.toUpperCase());
        if (!raw || !Number.isSafeInteger(Number(raw)) || Number(raw) < 0) throw new Error(T('单元格 {0} 必须为非负整数。', cell));
        body[cell] = Number(raw);
      }
      saving = true; save.disabled = true;
      const started = performance.now();
      const progress = () => { if (request === activeRequest) host.textContent = T(ticket === generation ?
        '正在保存配对效应…已等待 {0} 秒。' : '正在保存先前输入…已等待 {0} 秒。',
        ((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress,500); requestTimer = timer;
      const response = await api(taskApi()+'/analysis/paired-binary/effects', {method:'POST', body});
      if (request !== activeRequest) { toast(T('结果 {0} 已保存至先前的任务 {1}。', response.result_id, taskId), 'warn'); return; }
      if (ticket !== generation) {
        if (S.task?.task_id === taskId) { await loadAnalysis(); host.textContent = T('先前输入已保存为结果 {0}。再次保存前请检查效应量列表。', response.result_id); }
        else toast(T('结果 {0} 已保存至先前的任务 {1}。', response.result_id, taskId), 'warn');
        return;
      }
      $('#anMeasure').value = response.measure; $('#anMeasure').dispatchEvent(new Event('change'));
      $('#anEstimate').value = response.estimate; $('#anSe').value = response.se;
      await loadAnalysis();
      if (context() !== originalContext) {
        if (S.task?.task_id === taskId) host.textContent = T('先前输入已保存为结果 {0}。再次保存前请检查效应量列表。', response.result_id);
        else toast(T('结果 {0} 已保存至先前的任务 {1}。', response.result_id, taskId), 'warn');
        return;
      }
      const result = response.calculation, fmt = value => Number(value).toPrecision(6);
      const isOr = response.measure === 'PAIRED_OR';
      host.append(el('p', {text:T(isOr ?
        '已保存 {0}：效应值 {1}；分析尺度估计值 {2}，对数尺度标准误 {3}。结果编号 {4}。' :
        '已保存 {0}：效应值 {1}；分析尺度估计值 {2}，未变换尺度标准误 {3}。结果编号 {4}。',
        response.measure, fmt(isOr ? result.estimate : result.risk_difference), fmt(response.estimate), fmt(response.se), response.result_id)}),
        el('p', {text:T('{0}% 正态 Wald 区间：[{1}, {2}]。', body.level * 100,
          fmt(isOr ? result.or_ci_low : result.rd_ci_low), fmt(isOr ? result.or_ci_high : result.rd_ci_high))}),
        el('p', {text:T(result.or_correction_applied ?
          '已应用 OR 校正；RD 使用原始计数。' : '未应用 OR 校正；RD 使用原始计数。')}));
      for (const warning of result.warnings || []) host.append(el('p', {class:'hint', text:T(warningText[warning] || warning)}));
      for (const source of response.method_sources || []) host.append(el('p', {class:'hint', text:T('方法来源：{0}', source)}));
      host.append(el('p', {class:'hint', text:T('请核对方法原文，并引用所选计算方法、校正规则和研究报告。')}));
      record = {request:body, result:response, citation_reminder:'Verify and cite the original method and source report.'};
      download.disabled = false;
    } catch (error) { if (request === activeRequest && S.task?.task_id === taskId) host.textContent = error.message; }
    finally { clearInterval(requestTimer); if (request === activeRequest) { saving = false; save.disabled = false; if (S.task?.task_id !== taskId) host.replaceChildren(); } }
  });
  download.addEventListener('click', () => {
    if (!record) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2)], {type:'application/json'}));
    const link = el('a', {href:url, download:'reviewflow_paired_binary.json'});
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
