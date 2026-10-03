/* Combine eligible independent trial arms before pairwise effect calculation. */
(function () {
  const interventions = document.getElementById('anMultiArmInterventions');
  const comparator = document.getElementById('anMultiArmComparator');
  const save = document.getElementById('anMultiArmSave');
  const status = document.getElementById('anMultiArmStatus');
  const context = () => JSON.stringify([S.task?.task_id,
    ...['anEffectStudy','anComparison','anOutcome','anTimepoint','anMeasure','anSource','anSourceLocator','anAppendEffect','anMultiArmRationale','anEffectDirection']
      .map(id => document.getElementById(id).type === 'checkbox' ? document.getElementById(id).checked : anValue(id)),
    ...Array.from(document.getElementById('anMultiArmPanel').querySelectorAll('[data-key]'), node => node.value)]);
  const fieldSets = {
    binary: [['events', '事件数'], ['total', '总人数']],
    continuous: [['n', '人数 n'], ['mean', '均值'], ['sd', '标准差 SD']],
  };
  let kind = '', saving = false, activeRequest = 0, activeTimer = null;
  let nextArmIndex = { intervention: 2, comparator: 1 };

  function group(index, role) {
    const holder = document.createElement('div');
    holder.className = 'row'; holder.dataset.armRole = role;
    const title = T(role === 'intervention' ? '干预组 {0}' : '对照组 {0}', index);
    const heading = document.createElement('strong'); heading.textContent = title; holder.append(heading);
    heading.id = `anMultiArm-${role}-${index}`;
    const label = document.createElement('input');
    label.className = 'input'; label.placeholder = T('组别名称'); label.setAttribute('aria-label', T('组别名称'));
    label.setAttribute('aria-describedby', heading.id);
    label.dataset.key = 'label'; holder.append(label);
    for (const [key, name] of fieldSets[kind]) {
      const input = document.createElement('input');
      input.className = 'input'; input.type = 'number'; input.step = key === 'events' || key === 'total' || key === 'n' ? '1' : 'any';
      input.min = key === 'total' ? '1' : key === 'n' ? '2' : key === 'events' || key === 'sd' ? '0' : '';
      input.placeholder = T(name); input.setAttribute('aria-label', T(name));
      input.setAttribute('aria-describedby', heading.id);
      input.dataset.key = key; holder.append(input);
    }
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'btn b-out';
    remove.textContent = T('移除组别'); remove.setAttribute('aria-label', T('移除组别'));
    remove.setAttribute('aria-describedby', heading.id);
    remove.addEventListener('click', () => {
      const side = role === 'intervention' ? interventions : comparator;
      if (side.children.length > 1 && interventions.children.length + comparator.children.length > 3) holder.remove();
    });
    holder.append(remove);
    return holder;
  }

  function refresh() {
    const measure = anValue('anMeasure');
    const next = ['RR', 'OR', 'RD'].includes(measure) ? 'binary' : ['MD', 'SMD'].includes(measure) ? 'continuous' : '';
    save.disabled = saving || !next;
    if (!next) { kind = ''; status.textContent = T('请先在上方选择 RR、OR、RD、MD 或 SMD。'); return; }
    if (next === kind) return;
    kind = next; status.textContent = '';
    nextArmIndex = { intervention: 2, comparator: 1 };
    interventions.replaceChildren(group(1, 'intervention'), group(2, 'intervention'));
    comparator.replaceChildren(group(1, 'comparator'));
  }

  document.getElementById('anMeasure').addEventListener('change', refresh);
  document.getElementById('anMultiArmAdd').addEventListener('click', () => {
    refresh();
    if (!kind) return;
    interventions.append(group(++nextArmIndex.intervention, 'intervention'));
  });
  document.getElementById('anMultiArmAddComparator').addEventListener('click', () => {
    refresh();
    if (!kind) return;
    comparator.append(group(++nextArmIndex.comparator, 'comparator'));
  });
  document.getElementById('anMultiArmPanel').addEventListener('toggle', refresh);
  document.addEventListener('reviewflow:analysis-task-changed', () => {
    activeRequest++; clearInterval(activeTimer); saving = false;
    document.getElementById('anMultiArmRationale').value = '';
    kind = ''; interventions.replaceChildren(); comparator.replaceChildren(); refresh();
  });
  refresh();

  function readArm(holder) {
    const arm = {};
    for (const input of holder.querySelectorAll('[data-key]')) {
      const value = input.value.trim();
      if (!value) throw new Error(T('请填写{0}。', input.getAttribute('aria-label')));
      arm[input.dataset.key] = input.dataset.key === 'label' ? value : Number(value);
    }
    return arm;
  }

  save.addEventListener('click', async () => {
    if (saving) return;
    const request = ++activeRequest;
    status.textContent = '';
    let timer, original, taskId;
    try {
      const body = {
        study_id: anValue('anEffectStudy'), comparison: anValue('anComparison'),
        outcome: anValue('anOutcome'), timepoint: anValue('anTimepoint'),
        measure: anValue('anMeasure'), source_key: anValue('anSource'),
        source_locator: anValue('anSourceLocator'), append: document.getElementById('anAppendEffect').checked,
        intervention_arms: [...interventions.children].map(readArm),
        comparator_arms: [...comparator.children].map(readArm),
        combination_rationale: anValue('anMultiArmRationale'),
        effect_direction: anValue('anEffectDirection'),
      };
      if (!body.combination_rationale) throw new Error(T('请填写组别合并依据。'));
      if (!body.effect_direction) throw new Error(T('请选择效应方向与比较组的对应关系。'));
      original = context(); taskId = S.task?.task_id;
      saving = true; save.disabled = true;
      const started = performance.now();
      const progress = () => {
        if (request === activeRequest) status.textContent = context() === original
          ? T('正在保存合并组别…已等待 {0} 秒。', ((performance.now()-started)/1000).toFixed(1))
          : T('正在保存上一组输入…已等待 {0} 秒。', ((performance.now()-started)/1000).toFixed(1));
      };
      progress(); timer = activeTimer = setInterval(progress, 500);
      const response = await api(taskApi() + '/analysis/multi-arm-effects', { method: 'POST', body });
      clearInterval(timer);
      if (request !== activeRequest) {
        if (S.task?.task_id !== taskId) toast(T('结果 {0} 已保存到先前任务 {1}。', response.result_id, taskId), 'warn');
        return;
      }
      const stale = async (refresh = true) => {
        if (S.task?.task_id === taskId) { if (refresh) await loadAnalysis(); status.textContent = T('已按上一组输入保存结果 {0}。再次保存前请核对效应量列表。', response.result_id); }
        else toast(T('结果 {0} 已保存到先前任务 {1}。', response.result_id, taskId), 'warn');
      };
      if (context() !== original) { await stale(); return; }
      await loadAnalysis();
      if (context() !== original) { await stale(false); return; }
      status.textContent = T('已保存研究结果 {0}：合并 {1} 个干预组、{2} 个对照组。合并分析前请核对来源并选择目标结果。', response.result_id, body.intervention_arms.length, body.comparator_arms.length);
    } catch (error) { if (request === activeRequest && (!original || context() === original)) { status.textContent = error.message; toast(error.message, 'warn'); } }
    finally { clearInterval(timer); if (request === activeRequest) { saving = false; refresh(); } }
  });
})();
