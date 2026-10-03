/* Guided entry consumes the backend's data-shape catalogue and ranked routes. */
(function () {
  const panel = $('#anEntryGuidePanel'), choices = $('#anEntryShapes'), resultHost = $('#anEntryResults'), run = $('#anEntryGuideRun');
  let catalog = [], loadedTask = '', request = 0, resultTimer = null, renderLast = null;
  const selected = new Set();
  const displayLabels = { median_summary:'中位数与极差/IQR（估算均值和 SD）',
    effect_p:'效应估计与 p 值（通用）' };
  const label = code => T(displayLabels[code] || catalog.find(item => item.code === code)?.label ||
    code.replaceAll('_', ' '));
  const precisionLabels = {
    1:'原文直接报告的臂级或表格汇总', 2:'原文直接报告的组间效应与区间或标准误',
    3:'由检验统计量或 p 值反推', 4:'需按研究设计调整的汇总数据',
    5:'由间接数据推导的估计值'
  };
  const cautionLabels = {
    'Formula-estimated means and SDs are not observed data; reconstruction uncertainty is omitted from ordinary effect SEs.':
      '按公式估算的均值和 SD 不是观测数据；普通效应量标准误未计入重建不确定性。',
    'Choose Hozo, Wan or Luo explicitly; Luo mean uses Wan SD. Compare analyses with and without estimated studies and cite each method used.':
      '请明确选择 Hozo、Wan 或 Luo；Luo 均值搭配 Wan SD。请比较纳入与排除估算研究的结果，并引用所用方法。'
  };
  const clearResult = () => { request++; clearInterval(resultTimer); renderLast = null; run.disabled = false; resultHost.replaceChildren(); };
  window.RFRefreshEntryGuidanceText = () => { if (renderLast) renderLast(); };

  function renderChoices() {
    const search = anValue('anEntrySearch').toLowerCase();
    choices.replaceChildren(...catalog.filter(item =>
      label(item.code).toLowerCase().includes(search) || item.code.includes(search)).map(item => {
      const input = el('input', { type:'checkbox', value:item.code });
      input.checked = selected.has(item.code);
      input.addEventListener('change', () => {
        if (input.checked) selected.add(item.code); else selected.delete(item.code);
        clearResult();
      });
      return el('label', {}, input, el('span', { text:label(item.code) }));
    }));
  }

  async function loadCatalog() {
    if (!S.task || !panel.open) return;
    const taskId = S.task.task_id;
    if (loadedTask === taskId && catalog.length) return;
    selected.clear(); clearResult();
    const current = request;
    choices.textContent = T('正在加载数据类型…');
    try {
      // The read-only endpoint requires one shape even when only its catalogue is needed.
      const response = await api(taskApi() + '/analysis/entry-guidance', {
        method:'POST', body:{ available:['effect_ci'] } });
      if (current !== request || S.task?.task_id !== taskId || !panel.open) return;
      catalog = response.shape_catalog; loadedTask = taskId;
      renderChoices();
    } catch (error) { if (current === request && S.task?.task_id === taskId) choices.replaceChildren(el('span',{lang:'en',text:error.message})); }
  }

  function entryTarget(candidate) {
    if (candidate.shape === 'median_summary') return 'panel:anSummaryStatsPanel';
    if (candidate.shape === 'median_survival') return 'panel:anSurvivalMedianPanel';
    if (candidate.shape === 'paired_2x2') return 'panel:anPairedBinaryPanel';
    if (candidate.module === 'coscreen.binary_correlation_conversion' || candidate.entry_function.includes('calculate_binary_correlation_conversion')) return 'format:binary_correlation_conversion';
    if (candidate.entry_function === 'calculate_ratio_of_means') return 'format:ratio_of_means_two_arm';
    if (candidate.entry_function === 'calculate_glass_delta_effect') return 'format:glass_delta_two_arm';
    if (candidate.entry_module === 'coscreen.two_group_rd_interval') return 'viewer:risk_difference';
    if (candidate.entry_function.startsWith('rate_ratio_interval')) return 'viewer:rate_ratio';
    const match = candidate.entry_function.match(/\(([^()]+)\)/);
    const formats = match ? match[1].split('/').map(value => value.trim().replace(/^format\s+/, '').split(',')[0].trim())
      .filter(value => [...$('#anFormat').options].some(option => option.value === value)) : [];
    if (formats.length === 1) return 'format:' + formats[0];
    if (formats.length > 1) return 'formats:' + formats.join(',');
    if (candidate.entry_function.includes('save_arm_effect'))
      return 'measure:' + (candidate.shape === 'arm_mean_sd_n' ? 'MD / SMD' : 'RR / OR / RD');
    if (candidate.entry_function.startsWith('save_effect (manual')) return 'field:anEstimate';
    if (candidate.shape === 'dta_2x2') return 'field:anDtaTest';
    if (candidate.shape === 'dose_arms') return 'field:anDoseReference';
    if (candidate.shape === 'cluster_design_effect') return candidate.entry_function.includes('variable_size')
      ? 'cluster:variable' : 'cluster:equal';
    if (candidate.shape === 'zero_cell_binary') return 'panel:anZeroCellPanel';
    return null;
  }

  function openTarget(target) {
    const [kind, id] = target.split(':');
    document.dispatchEvent(new CustomEvent('reviewflow:analysis-reveal', { detail:
      kind === 'viewer' ? 'anStudyIntervalPanel' : kind === 'format' || kind === 'formats' ? 'anFormatPanel' :
      kind === 'cluster' ? 'anClusterPanel' : kind === 'field' || kind === 'panel' ? id : 'anJumpEffects' }));
    if (kind === 'format' || kind === 'formats') {
      const select = $('#anFormat'); select.closest('details').open = true;
      select.value = kind === 'format' ? id : '';
      select.dispatchEvent(new Event('change'));
      const hint = $('#anEntryFormatHint');
      hint.hidden = kind !== 'formats';
      if (kind === 'formats') hint.replaceChildren(el('span', {text:T('请根据原文选择格式：')}),
        ...id.split(',').flatMap((value, i) => [i ? '; ' : ' ', el('span', {
          text:[...select.options].find(option => option.value === value)?.textContent || value
        })]));
      select.scrollIntoView({ block:'center' }); select.focus();
    } else if (kind === 'cluster') {
      const panel = $('#anClusterPanel'), model = $('#anClusterSizeModel');
      panel.open = true; model.value = id; model.dispatchEvent(new Event('change'));
      panel.scrollIntoView({ block:'center' }); $('#anClusterMeasure').focus();
    } else if (kind === 'viewer') {
      const panel = $('#anStudyIntervalPanel'), quantity = $('#anIntervalKind');
      panel.open = true; quantity.value = id; quantity.dispatchEvent(new Event('change'));
      panel.scrollIntoView({ block:'center' }); $('#anIntervalMethod').focus();
    } else if (kind === 'measure') {
      const node = $('#anMeasure'), hint = $('#anEntryMeasureHint');
      hint.textContent = T('请为这种数据选择 {0}；随后会显示对应的组别字段。', id);
      hint.hidden = false;
      node.scrollIntoView({ block:'center' }); node.focus();
    } else {
      const node = $('#'+id);
      if (kind === 'panel') node.open = true;
      if (id === 'anDoseReference') $('#anDoseArmsPanel').open = false;
      if (id === 'anZeroCellPanel') {
        $('#anBinaryArms').style.display = '';
        $('#anEventsT').scrollIntoView({ block:'center' }); $('#anEventsT').focus();
        return;
      }
      node.scrollIntoView({ block:'center' });
      if (kind === 'field') node.focus();
      if (kind === 'panel') node.querySelector('input,select,textarea')?.focus();
    }
  }

  panel.addEventListener('toggle', () => { if (panel.open) loadCatalog(); else clearResult(); });
  document.addEventListener('reviewflow:analysis-task-changed', () => {
    loadedTask = ''; catalog = []; selected.clear(); clearResult();
    $('#anEntrySearch').value = '';
    choices.textContent = panel.open ? T('正在加载数据类型…') : '';
  });
  document.addEventListener('reviewflow:analysis-core-loaded', loadCatalog);
  $('#anEntryStart').addEventListener('click', () => {
    panel.open = true;
    panel.scrollIntoView({ block:'start' });
    $('#anEntrySearch').focus();
  });
  $('#anEntrySearch').addEventListener('input', renderChoices);
  $('#anFormat').addEventListener('change', () => { $('#anEntryFormatHint').hidden = true; });
  $('#anMeasure').addEventListener('change', () => { $('#anEntryMeasureHint').hidden = true; });
  run.addEventListener('click', async () => {
    const available = [...selected];
    resultHost.replaceChildren();
    if (!available.length) { resultHost.textContent = T('请至少选择一种原文数据类型。'); return; }
    const current = ++request, taskId = S.task?.task_id;
    run.disabled = true;
    const started = performance.now();
    const progress = () => { if (current === request) resultHost.textContent = T('正在查找录入路径…已等待 {0} 秒。', ((performance.now()-started)/1000).toFixed(1)); };
    progress(); resultTimer = setInterval(progress, 500);
    try {
      const response = await api(taskApi() + '/analysis/entry-guidance', { method:'POST', body:{ available } });
      if (current !== request || S.task?.task_id !== taskId) return;
      const draw = () => {
      resultHost.replaceChildren(el('p', { class:'hint', text:response.candidates.length
        ? T('找到 {0} 条录入路径。排序靠前的路径通常使用更直接的原文数据。', response.candidates.length)
        : T('仅凭所选数据无法计算受支持的效应量；请查看下方建议。') }));
      response.combinations.forEach(item => resultHost.append(el('p', { class:'hint', text:item.note })));
      response.candidates.forEach(candidate => {
        const required = Array.isArray(candidate.required_fields)
          ? candidate.required_fields.join(', ') : T('请打开对应表单核对字段');
        const card = el('article', {},
          el('b', {}, `${candidate.precision_rank}. `, el('span', {text:label(candidate.shape)}),
            ` → ${candidate.produces_measure}`),
          el('p', { class:'hint' }, el('span', {text:T('所需字段：')}), ' ',
            el('span', {text:required}), '。 ',
            el('span', {text:T(precisionLabels[candidate.precision_rank] || candidate.precision_label)})),
          ...candidate.cautions.map(caution => el('p', { class:'hint', text:T(cautionLabels[caution] || caution) })));
        if (candidate.shape === 'median_survival') card.append(el('p', { class:'hint', text:
          T('由中位生存时间换算 HR 需要假设生存时间服从指数分布（即瞬时风险率恒定）。Tierney 等（2007）未提供这一中位数公式；Spruance 等（2004）讨论中位数比，但未给出其标准误。这里的标准误采用 Poisson 近似。请核对并引用相应来源。') }));
        const target = entryTarget(candidate);
        if (target) card.append(el('button', { type:'button', class:'btn b-out',
          text:target === 'cluster:equal' ? T('打开等大小整群表单') :
            target === 'cluster:variable' ? T('打开变大小整群表单') :
            target.startsWith('formats:') ? T('选择匹配的格式') :
            target.startsWith('viewer:') ? T('打开只读查看区') : T('打开录入表单'),
          onclick:() => openTarget(target) }));
        resultHost.append(card);
      });
      };
      renderLast = draw; draw();
    } catch (error) { if (current === request) resultHost.replaceChildren(el('span',{lang:'en',text:error.message})); }
    finally { if (current === request) { clearInterval(resultTimer); run.disabled = false; } }
  });
})();
