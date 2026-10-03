(function () {
  const blocks = $('#anDependentBlocks');
  const output = $('#anDependentResult');
  let prepared = null;
  let lastResult = null;
  let request = 0, progressTimer = null;
  const runButton = $('#anDependentRun');

  function invalidateResult() {
    request++;
    clearInterval(progressTimer);
    runButton.disabled = false;
    lastResult = null;
    $('#anDependentExport').disabled = true;
    output.replaceChildren();
  }

  function renderResult(result, requestBody, current) {
    const table = el('table');
    table.append(el('thead', {}, el('tr', {}, ...['结局', '研究数', '合并估计', 'SE（分析尺度）', '95% CI', '边际 Q（抽样方差）'].map(label => el('th', { text:label })))));
    const body = el('tbody');
    (result.estimates || []).forEach(item => body.append(el('tr', {},
      ...[item.outcome, item.n_studies, item.estimate, item.se,
        `${item.ci_lower} – ${item.ci_upper}`, `${item.q?.statistic} (df ${item.q?.df})`]
        .map(value => el('td', { text:String(value) })))));
    table.append(body);
    let heterogeneity = null;
    if (result.between_study_covariance) {
      const matrix = result.between_study_covariance.matrix;
      const names = result.between_study_covariance.outcomes;
      const variances = names.map((name, i) => `${name} ${matrix[i][i]}`).join('；');
      const correlations = [];
      names.forEach((name, i) => names.slice(i + 1).forEach((other, offset) => {
        const j = i + offset + 1;
        const denominator = Math.sqrt(matrix[i][i] * matrix[j][j]);
        correlations.push(`${name} / ${other} ${denominator > 0 ? matrix[i][j] / denominator : T('不可识别')}`);
      }));
      heterogeneity = el('p', { class:'hint', text:T('研究间方差：{0}。研究间相关：{1}。{2}',variances,correlations.join('; '),result.fit?.boundary ? T('边界解，谨慎解读相关系数。') : '') });
    }
    const audit = el('details', {}, el('summary', { text:'查看协方差与输入快照' }),
      el('pre', { class:'hint', text:JSON.stringify({ between_study_covariance:result.between_study_covariance,
        fit:result.fit, coefficient_covariance:result.coefficient_covariance,
        input_snapshot:result.input_snapshot, limitations:result.limitations }, null, 2) }));
    const summary = T('{0} · {1} scale',RFAnalysisLocale.term(result.model),RFAnalysisLocale.term(result.analysis_scale)) +
      (result.q ? ` · ${T(result.q.scope === 'sampling_error_heterogeneity' ? '异质性 Q（抽样协方差）' : '等效应残差 Q')}=${result.q.statistic} (df ${result.q.df}, p=${result.q.p_value})` : '');
    output.replaceChildren(el('p', { class:'hint', text:summary }),
      table, ...(heterogeneity ? [heterogeneity] : []),
      ...(result.warnings || result.fit?.warnings || []).map(warning => el('div', { class:'hint' },RFAnalysisLocale.message(warning))),
      audit, el('div', { class:'hint' },RFAnalysisLocale.message(result.citation_reminder || '')),
      el('p', { class:'hint',translate:'no', text:(result.method_sources || []).join(' ') }),
      anAuditReportButtons(taskApi() + '/analysis/dependent-synthesis', null,
        'reviewflow_dependent_synthesis_audit', () => current === request, requestBody));
  }

  function selectedRows(box) {
    const ids = new Set(Array.from(box.querySelectorAll('input[data-result-id]:checked'), node => Number(node.dataset.resultId)));
    return prepared.rows.filter(row => row.study_id === box.dataset.studyId && ids.has(row.result_id));
  }

  function renderCovariance(box) {
    const rows = selectedRows(box);
    const area = box.querySelector('[data-covariance]');
    area.replaceChildren();
    if (rows.length < 2) return;
    const table = el('table');
    table.append(el('thead', {}, el('tr', {}, el('th', { text:'结局' }),
      ...rows.map(row => el('th', { text:row.outcome })))));
    const body = el('tbody');
    rows.forEach((row, i) => {
      const cells = [el('th', { text:row.outcome })];
      rows.forEach((other, j) => {
        if (i === j) cells.push(el('td', { text:String(row.se ** 2) }));
        else if (i < j) cells.push(el('td', { text:'↘' }));
        else {
          const key = [Math.min(row.result_id, other.result_id), Math.max(row.result_id, other.result_id)].join(':');
          const input = el('input', { class:'input', type:'number', step:'any', 'aria-label':T("{0} / {1} covariance", row.outcome,other.outcome),
            'data-pair':key, placeholder:'协方差' });
          input.value = prepared.covariances.get(key) ?? '';
          input.addEventListener('input', () => { prepared.covariances.set(key, input.value); invalidateResult(); });
          cells.push(el('td', {}, input));
        }
      });
      body.append(el('tr', {}, ...cells));
    });
    table.append(body);
    area.append(table, el('label', { class:'lbl', text:'协方差来源或假设（必填）' }));
    const note = el('input', { class:'input', 'data-note':'', placeholder:'例如：原文表 3 的相关系数，或预设假设及理由',
      'aria-label':T("{0} covariance source", box.dataset.studyId) });
    note.value = prepared.notes.get(box.dataset.studyId) || '';
    note.addEventListener('input', () => { prepared.notes.set(box.dataset.studyId, note.value); invalidateResult(); });
    area.append(note);
  }

  $('#anDependentPrepare').addEventListener('click', () => {
    try {
      if (!S.task) throw new Error('请先选择任务');
      const comparison = anValue('anComparison').trim();
      const timepoint = anValue('anTimepoint').trim();
      const measure = anValue('anMeasure');
      if (!comparison || !timepoint) throw new Error('请先填写比较和时间点');
      const rows = anEffects.filter(row => Number(row.selected) === 1 && row.comparison === comparison &&
        row.timepoint === timepoint && row.measure === measure);
      if (new Set(rows.map(row => row.outcome)).size < 2) throw new Error('此层至少需要两个已选用的结局');
      prepared = { taskId:S.task.task_id, comparison, timepoint, measure, rows,
        covariances:new Map(), notes:new Map() };
      blocks.replaceChildren(); invalidateResult();
      const studies = [...new Set(rows.map(row => row.study_id))].sort();
      studies.forEach(studyId => {
        const study = anStudies.find(item => item.id === studyId);
        const box = el('div', { class:'card', 'data-study-id':studyId },
          el('b', { text:(study?.label || studyId) + ' [' + studyId + ']' }));
        rows.filter(row => row.study_id === studyId).forEach(row => {
          const check = el('input', { type:'checkbox', 'data-result-id':row.result_id, 'aria-label':T("Include result {0}", row.result_id) });
          check.addEventListener('change', () => { renderCovariance(box); invalidateResult(); });
          box.append(el('label', { class:'row' }, check,
            el('span', { text:`${row.outcome} · ID ${row.result_id} · SE ${row.se} · ${row.source_key}${row.source_locator ? ' · '+row.source_locator : ''}` })));
        });
        box.append(el('div', { 'data-covariance':'' }));
        blocks.append(box);
        renderCovariance(box);
      });
    } catch (error) { toast(error.message, 'warn'); }
  });

  runButton.addEventListener('click', async () => {
    invalidateResult();
    const current = request;
    try {
      if (!prepared || prepared.taskId !== S.task?.task_id) throw new Error('请先载入当前层的结果');
      if (prepared.comparison !== anValue('anComparison').trim() ||
          prepared.timepoint !== anValue('anTimepoint').trim() || prepared.measure !== anValue('anMeasure'))
        throw new Error('比较、时间点或效应指标已变化，请重新载入结果');
      const requestBlocks = Array.from(blocks.querySelectorAll('[data-study-id]')).map(box => {
        const rows = selectedRows(box);
        if (!rows.length) return null;
        const covariance = rows.map((row, i) => rows.map((other, j) => {
          if (i === j) return row.se ** 2;
          const key = [Math.min(row.result_id, other.result_id), Math.max(row.result_id, other.result_id)].join(':');
          const value = prepared.covariances.get(key);
          if (value === undefined || value.trim() === '') throw new Error(T('请填写 {0} 的结局间协方差',box.dataset.studyId));
          const number = Number(value);
          if (!Number.isFinite(number)) throw new Error('协方差必须是有限数值');
          return number;
        }));
        const note = rows.length > 1 ? (prepared.notes.get(box.dataset.studyId) || '').trim() : 'SE from saved study result';
        if (!note) throw new Error(T('请填写 {0} 的协方差来源或假设',box.dataset.studyId));
        return { study_id:box.dataset.studyId, result_ids:rows.map(row => row.result_id), covariance,
          covariance_source_note:note };
      }).filter(Boolean);
      if (requestBlocks.length < 2) throw new Error('至少需要两项独立研究');
      const counts = new Map();
      requestBlocks.forEach(block => block.result_ids.forEach(id => {
        const outcome = prepared.rows.find(row => row.result_id === id).outcome;
        counts.set(outcome, (counts.get(outcome) || 0) + 1);
      }));
      if ([...counts.values()].some(count => count < 2)) throw new Error('每个结局至少需要两项独立研究');
      const model = anValue('anDependentModel');
      if (!model) throw new Error('请选择相关结局联合合并模型');
      if (model === 'random_reml_un' && (counts.size < 2 || counts.size > 8)) throw new Error('Random-effects REML requires two to eight outcomes');
      runButton.disabled = true;
      const started = performance.now();
      const reference = model === 'random_reml_un' && counts.size === 5
        ? T('运行时间随数据量和系统负载变化。') : '';
      const showProgress = () => { if (current === request) output.textContent =
        T('正在计算 {0}：已等待 {1} 秒。此分析运行期间不显示中途进度。{2}',model === 'random_reml_un' ? 'REML' : 'GLS',((performance.now() - started) / 1000).toFixed(1),reference); };
      showProgress();
      progressTimer = setInterval(showProgress, 500);
      const body = {blocks:requestBlocks, model};
      const result = await api(taskApi() + '/analysis/dependent-synthesis', {method:'POST', body});
      if (current !== request) return;
      lastResult = result;
      $('#anDependentExport').disabled = false;
      renderResult(result, body, current);
    } catch (error) { if (current === request) { output.replaceChildren(RFAnalysisLocale.message(error.message)); toast(error.message, 'warn'); } }
    finally { if (current === request) { clearInterval(progressTimer); runButton.disabled = false; } }
  });

  $('#anDependentExport').addEventListener('click', () => {
    if (!lastResult) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(lastResult, null, 2)], { type:'application/json' }));
    const link = el('a', { href:url, download:'reviewflow_dependent_synthesis.json' });
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  ['anComparison', 'anTimepoint', 'anMeasure', 'anDependentModel'].forEach(id =>
    $('#'+id).addEventListener('input', invalidateResult));

  document.addEventListener('reviewflow:analysis-core-loaded', () => {
    if (prepared) {
      prepared = null;
      blocks.replaceChildren(); invalidateResult();
    }
  });
  document.addEventListener('reviewflow:analysis-task-changed', () => {
    prepared = null;
    blocks.replaceChildren();
    $('#anDependentModel').value = '';
    invalidateResult();
  });
})();
