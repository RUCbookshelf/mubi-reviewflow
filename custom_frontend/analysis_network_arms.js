(function () {
  const rows = $('#anNetworkArmRows'), host = $('#anNetworkArmResult');
  const save = $('#anNetworkArmSave');
  let generation = 0, saveTimer = null;
  const context = () => JSON.stringify([S.task?.task_id,
    ...['anEffectStudy','anSource','anNetworkSourceLocator','anOutcome','anTimepoint','anMeasure','anNetworkArmReference']
      .map(anValue),
    ...Array.from(rows.querySelectorAll('[data-arm-field]'), node => node.value)]);
  rows.addEventListener('input', () => host.replaceChildren());
  const updateRemovals = () => rows.querySelectorAll('[data-arm] button').forEach(button => {
    button.disabled = rows.children.length <= 3;
  });
  function addArm() {
    if (rows.children.length >= 20) return;
    const measure = anValue('anMeasure');
    const fields = measure === 'MD' ? [['n','样本量'], ['mean','均值'], ['sd','样本标准差']] :
      [['events','事件数'], ['total','总人数']];
    const row = el('div', { class:'row' }); row.dataset.arm = '';
    const treatment = el('input', { class:'input', placeholder:T('治疗方案'), 'aria-label':T('组别治疗方案') });
    treatment.dataset.armField = 'treatment'; row.append(treatment);
    for (const [key, label] of fields) {
      const input = el('input', { class:'input', type:'number', step:key === 'mean' || key === 'sd' ? 'any' : '1',
        placeholder:label, 'aria-label':label });
      input.dataset.armField = key; row.append(input);
    }
    row.append(el('button', { class:'btn b-out', type:'button', text:T('移除'), onclick:() => {
      row.remove(); updateRemovals(); host.replaceChildren();
    } }));
    rows.append(row);
    updateRemovals();
  }
  $('#anNetworkArmAdd').addEventListener('click', addArm);
  $('#anMeasure').addEventListener('change', () => { rows.replaceChildren(); host.replaceChildren(); for (let i = 0; i < 3; i++) addArm(); });
  document.addEventListener('reviewflow:analysis-task-changed', () => {
    generation++; clearInterval(saveTimer);
    $('#anNetworkArmReference').value = '';
    rows.replaceChildren(); for (let i = 0; i < 3; i++) addArm();
    host.replaceChildren(); save.disabled = false;
  });
  ['anEffectStudy', 'anSource', 'anOutcome', 'anTimepoint', 'anNetworkArmReference']
    .forEach(id => $('#'+id).addEventListener('input', () => host.replaceChildren()));
  save.addEventListener('click', async () => {
    if (save.disabled) return;
    host.replaceChildren();
    let timer, original, taskId;
    const ticket = ++generation;
    try {
      const measure = anValue('anMeasure');
      if (!['MD', 'RR', 'OR'].includes(measure)) throw new Error(T('请在主效应指标中选择 MD、RR 或 OR。'));
      const arms = [...rows.querySelectorAll('[data-arm]')].map(row => {
        const values = {};
        row.querySelectorAll('[data-arm-field]').forEach(input => {
          if (input.value.trim() === '') throw new Error(T('请填写每个组别的全部数据。'));
          values[input.dataset.armField] = input.dataset.armField === 'treatment' ? input.value.trim() : Number(input.value);
        });
        return values;
      });
      const body = { study_id:anValue('anEffectStudy'), source_key:anValue('anSource'),
        source_locator:anValue('anNetworkSourceLocator'),
        outcome:anValue('anOutcome'), timepoint:anValue('anTimepoint'), measure,
        reference:anValue('anNetworkArmReference'), arms };
      original = context(); taskId = S.task?.task_id;
      save.disabled = true;
      const started = performance.now();
      const progress = () => {
        if (ticket === generation) host.textContent = T(context() === original ? '正在保存网络研究…已等待 {0} 秒。' : '正在保存更改前的网络数据…已等待 {0} 秒。',((performance.now()-started)/1000).toFixed(1));
      };
      progress(); timer = setInterval(progress, 500); saveTimer = timer;
      const saved = await api(taskApi() + '/analysis/network/from-arms', { method:'POST', body });
      if (S.task?.task_id === taskId) document.dispatchEvent(new Event('reviewflow:network-study-saved'));
      if (ticket !== generation) { toast(T('网络研究 {0} 已保存到先前任务 {1}。',saved.study.study_id,taskId), 'warn'); return; }
      if (context() !== original) {
        if (S.task?.task_id === taskId) {
          host.textContent = T('已保存更改前输入生成的网络研究 {0}。再次保存前，请先检查研究列表。',saved.study.study_id);
        } else { host.replaceChildren(); toast(T('网络研究 {0} 已保存到先前任务 {1}。',saved.study.study_id,taskId), 'warn'); }
        return;
      }
      clearInterval(timer);
      host.replaceChildren();
      host.append(el('p', { text:T('已保存 {0}：{1} 个对比，协方差尺度为 {2}；来源 {3}。',saved.study.study_id,saved.study.contrasts.length,saved.study.covariance_scale,saved.source_key) }),
        el('pre', { class:'hint', lang:'en', text:JSON.stringify({ contrasts:saved.study.contrasts, covariance:saved.study.covariance }, null, 2) }));
    } catch (error) { if (ticket === generation && (!original || context() === original)) { const known = ['请在主效应指标中选择 MD、RR 或 OR。','请填写每个组别的全部数据。'].some(key=>T(key)===error.message); host.replaceChildren(el('span',{text:error.message,...(known?{}:{lang:'en'})})); toast(error.message, 'warn'); } }
    finally { clearInterval(timer); if (ticket === generation) save.disabled = false; }
  });
  for (let i = 0; i < 3; i++) addArm();
})();
