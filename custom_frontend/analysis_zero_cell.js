(function () {
  const host = $('#anZeroCellResult'), save = $('#anZeroCellSave'), previewButton = $('#anZeroCellPreview');
  let preview = null, generation = 0, previewTimer = null;
  const panel = $('#anZeroCellPanel');
  const cellStatus = {
    none:'无零格', single_zero:'一格为零', double_zero:'双组零事件',
    all_event:'双组全事件', mixed_zeros:'多格为零',
  };
  const warningText = {
    'The selected 0.5 correction was added to all four cells because the raw table contains a zero; compare this sensitivity with the uncorrected result.': '原始表中有零格，已对四格均加 0.5；请与不校正结果比较。',
    'The raw table contains zero cells; no continuity correction was applied.': '原始表中有零格，未应用连续性校正。',
    'Both arms have the same outcome status (no events or all events); Cochrane treats this table as uninformative for a relative effect. Any corrected value is a sensitivity calculation.': '两组均无事件或均为全事件；根据 Cochrane 方法，这类表格不能为相对效应提供信息。任何校正后数值仅供敏感性分析。',
    'The selected calculation cannot produce a finite log ratio; estimate and SE are unavailable.': '当前计算无法得到有限的对数比值；估计值和标准误不可用。',
    'The estimate is finite but its sampling variance is zero; inverse-variance weighting is unavailable.': '估计值有限，但抽样方差为零，无法用于逆方差加权。',
  };
  const showCountFields = () => { $('#anBinaryArms').style.display = panel.open || ['RR','OR','RD'].includes(anValue('anMeasure')) ? '' : 'none'; };
  panel.addEventListener('toggle', showCountFields);
  $('#anMeasure').addEventListener('change', showCountFields);
  function clear() { generation++; clearInterval(previewTimer); preview = null; previewButton.disabled = false; save.disabled = true; host.replaceChildren(); }
  ['anEventsT', 'anTotalT', 'anEventsC', 'anTotalC', 'anZeroCellMeasure', 'anZeroCellCorrection',
    'anEffectStudy', 'anComparison', 'anOutcome', 'anTimepoint', 'anSource', 'anSourceLocator', 'anEffectDirection']
    .forEach(id => $('#'+id).addEventListener('input', clear));
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', () => {
    $('#anZeroCellMeasure').value = '';
    $('#anZeroCellCorrection').value = '';
    clear();
  });

  function values() {
    const measure = anValue('anZeroCellMeasure'), correction = anValue('anZeroCellCorrection');
    if (!measure) throw new Error(T('请选择 OR 或 RR 再计算零单元格。'));
    if (!correction) throw new Error(T('请选择零单元格校正规则。'));
    const count = id => {
      const input = $('#'+id);
      if (input.value.trim() === '') throw new Error(T('请填写{0}。', input.getAttribute('aria-label')));
      return Number(input.value);
    };
    return { events_t:count('anEventsT'), total_t:count('anTotalT'),
      events_c:count('anEventsC'), total_c:count('anTotalC'),
      measure,
      correction:correction === 'none' ? 'none' : 0.5 };
  }
  previewButton.addEventListener('click', async () => {
    clear();
    const ticket = generation, taskId = S.task?.task_id;
    const current = () => ticket === generation && S.task?.task_id === taskId;
    try {
      const inputs = values();
      previewButton.disabled = true;
      const started = performance.now();
      const progress = () => { if (current()) host.textContent = T('正在检查零单元格…已等待 {0} 秒。', ((performance.now()-started)/1000).toFixed(1)); };
      progress(); previewTimer = setInterval(progress, 500);
      const result = await api(taskApi() + '/analysis/zero-cell/preview', { method:'POST', body:inputs });
      if (!current()) return;
      preview = inputs;
      const informative = result.inverse_variance_ready && !['double_zero', 'all_event'].includes(result.cell_status);
      save.disabled = !informative;
      host.replaceChildren();
      host.append(el('p', {}, el('span', { text:T(cellStatus[result.cell_status] || result.cell_status) }),
          ' · ', result.measure, ' · ', el('span', { text:T(result.correction_applied ? '已应用 0.5 校正' : '未应用校正') })),
        el('p', { text:result.inverse_variance_ready ?
          T('估计值 {0}；对数尺度标准误 {1}。', Number(result.estimate).toPrecision(5), Number(result.se).toPrecision(5)) :
          T('无法得到可用于逆方差合并的有限效应值和标准误。') }),
        el('pre', { class:'hint', text:JSON.stringify({ original_cells:result.original_cells,
          adjusted_cells:result.adjusted_cells }, null, 2) }));
      for (const warning of result.warnings || []) host.append(el('p', { class:'hint', text:T(warningText[warning] || warning) }));
      if (!informative) host.append(el('p', { class:'hint', text:T('请在二分类组别数据区保存原始计数；该结果不能保存为有信息量的相对效应。') }));
    } catch (error) { if (current()) { clear(); host.textContent = error.message; toast(error.message, 'warn'); } }
    finally { if (current()) { clearInterval(previewTimer); previewButton.disabled = false; } }
  });
  save.addEventListener('click', async () => {
    if (!preview) return;
    const inputs = preview, ticket = generation, taskId = S.task?.task_id, started = performance.now();
    const current = () => ticket === generation && S.task?.task_id === taskId;
    save.disabled = true;
    const progress = () => { if (current()) host.textContent = T('正在保存零单元格效应量…已等待 {0} 秒。', ((performance.now()-started)/1000).toFixed(1)); };
    progress();
    const timer = setInterval(progress, 500);
    try {
      if (!anValue('anEffectDirection')) throw new Error(T('请选择组别与比较的对应方向。'));
      const body = { study_id:anValue('anEffectStudy'), comparison:anValue('anComparison'),
        outcome:anValue('anOutcome'), timepoint:anValue('anTimepoint'), source_key:anValue('anSource'),
        source_locator:anValue('anSourceLocator'), append:$('#anAppendEffect').checked,
        format_name:'zero_cell_binary', values:inputs, effect_direction:anValue('anEffectDirection') };
      const saved = await api(taskApi() + '/analysis/format-effects', { method:'POST', body });
      if (!current()) {
        if (S.task?.task_id === taskId) {
          await loadAnalysis();
          if (S.task?.task_id === taskId) toast(T('先前的零单元格预览已保存为结果 {0}；保存期间输入已改变。', saved.result_id), 'warn');
        } else toast(T('结果 {0} 已保存至先前的任务 {1}。', saved.result_id, taskId), 'info');
        return;
      }
      const effect = saved.effect;
      $('#anMeasure').value = effect.measure;
      $('#anMeasure').dispatchEvent(new Event('change'));
      $('#anEstimate').value = effect.estimate;
      $('#anSe').value = effect.se;
      await loadAnalysis();
      if (S.task?.task_id !== taskId) return;
      clear();
      host.append(el('p', { text:T('已保存结果 {0}；{1} 效应值 {2}，对数尺度标准误 {3}。', saved.result_id, effect.measure, Number(effect.estimate).toPrecision(5), Number(effect.se).toPrecision(5)) }));
    } catch (error) { if (current()) { host.textContent = error.message; toast(error.message, 'warn'); } }
    finally { clearInterval(timer); if (current() && preview) save.disabled = false; }
  });
})();
