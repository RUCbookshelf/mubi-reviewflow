(function () {
  const host = $('#anClusterResult');
  const saveButton = $('#anClusterSave');
  let activeRequest = 0, saveTimer = null, savedRow = null;
  const context = () => JSON.stringify([S.task?.task_id,
    ...['anEffectStudy','anComparison','anOutcome','anTimepoint','anSource','anSourceLocator','anAppendEffect','anEffectDirection']
      .map(id => $('#'+id).type === 'checkbox' ? $('#'+id).checked : anValue(id)),
    ...Array.from($('#anClusterPanel').querySelectorAll('input,select'), node => node.type === 'checkbox' ? node.checked : node.value)]);
  function syncMeasure() {
    const measure = anValue('anClusterMeasure');
    $('#anClusterBinary').hidden = !['OR', 'RR', 'RD'].includes(measure);
    $('#anClusterContinuous').hidden = measure !== 'MD';
    host.replaceChildren();
  }
  $('#anClusterMeasure').addEventListener('change', syncMeasure);
  $('#anClusterSizeModel').addEventListener('change', () => {
    const variable = anValue('anClusterSizeModel') === 'variable';
    $('#anClusterCvFields').hidden = $('#anClusterFewCiField').hidden = !variable;
    if (!variable) $('#anClusterFewCi').checked = false;
    host.replaceChildren();
  });
  syncMeasure();
  $('#anClusterPanel').addEventListener('input', () => { savedRow = null; host.replaceChildren(); });
  document.addEventListener('reviewflow:analysis-core-loaded', () => {
    if (savedRow && JSON.stringify(anEffects.find(row => row.result_id === savedRow.result_id)) !== JSON.stringify(savedRow)) {
      savedRow = null; host.replaceChildren();
    }
  });
  document.addEventListener('reviewflow:analysis-task-changed', () => {
    activeRequest++; clearInterval(saveTimer);
    savedRow = null;
    $('#anClusterPanel').querySelectorAll('input,select').forEach(node => {
      if (node.type === 'checkbox') node.checked = false; else node.value = '';
    });
    syncMeasure(); $('#anClusterSizeModel').dispatchEvent(new Event('change'));
    saveButton.disabled = false;
  });

  function number(id) {
    const input = $('#'+id);
    if (input.value.trim() === '') throw new Error(T('请填写{0}。', input.getAttribute('aria-label')));
    const value = Number(input.value);
    if (!Number.isFinite(value)) throw new Error(T('{0}必须为有限数。', input.getAttribute('aria-label')));
    return value;
  }
  saveButton.addEventListener('click', async () => {
    if (saveButton.disabled) return;
    host.replaceChildren();
    let timer, original, taskId;
    const request = ++activeRequest;
    try {
      const measure = anValue('anClusterMeasure'), sizeModel = anValue('anClusterSizeModel');
      if (!measure) throw new Error(T('请选择整群试验效应指标。'));
      if (!sizeModel) throw new Error(T('请选择整群大小方法。'));
      if (!anValue('anEffectDirection')) throw new Error(T('请选择组别与比较的对应方向。'));
      if (!$('#anClusterUnadjusted').checked)
        throw new Error(T('请确认原文组别摘要尚未按整群设计调整。'));
      const iccSource = anValue('anClusterIccSource').trim();
      if (!iccSource) throw new Error(T('请记录 ICC 来源或假设。'));
      const binary = measure !== 'MD';
      const variable = sizeModel === 'variable';
      const arm = side => {
        const prefix = side === 'treatment' ? 'anClusterT' : 'anClusterC';
        return { clusters:number(prefix+'Clusters'), participants:number(prefix+'Participants'),
          ...(variable ? { cv:number(prefix+'Cv') } : {}),
          ...(binary ? { events:number(prefix+'Events') } :
            { mean:number(prefix+'Mean'), sd:number(prefix+'Sd') }) };
      };
      const body = { study_id:anValue('anEffectStudy'), comparison:anValue('anComparison'),
        outcome:anValue('anOutcome'), timepoint:anValue('anTimepoint'), source_key:anValue('anSource'),
        source_locator:anValue('anSourceLocator'), append:$('#anAppendEffect').checked,
        format_name:variable ? 'cluster_trial_variable_size_design_effect' : 'cluster_trial_design_effect',
        effect_direction:anValue('anEffectDirection'), values:{
          outcome_type:binary ? 'binary' : 'continuous', measure, icc:number('anClusterIcc'),
          ...(variable ? { icc_source:iccSource, few_cluster_ci:$('#anClusterFewCi').checked } : {}),
          analysis_status:'unadjusted', arms:{ treatment:arm('treatment'), control:arm('control') },
          source_provenance:{ icc_source:iccSource, source_key:anValue('anSource'),
          source_locator:anValue('anSourceLocator') }
        } };
      original = context(); taskId = S.task?.task_id;
      saveButton.disabled = true;
      const started = performance.now();
      const progress = () => {
        if (request === activeRequest) host.textContent = T(context() === original ?
          '正在保存整群校正效应…已等待 {0} 秒。' : '正在保存先前输入…已等待 {0} 秒。',
          ((performance.now()-started)/1000).toFixed(1));
      };
      progress(); timer = setInterval(progress, 500); saveTimer = timer;
      const saved = await api(taskApi() + '/analysis/format-effects', { method:'POST', body });
      if (request !== activeRequest) { toast(T('结果 {0} 已保存至先前的任务 {1}。', saved.result_id, taskId), 'warn'); return; }
      if (context() !== original) {
        if (S.task?.task_id === taskId) { await loadAnalysis(); host.textContent = T('先前输入已保存为结果 {0}。再次保存前请检查效应量列表。', saved.result_id); }
        else { host.replaceChildren(); toast(T('结果 {0} 已保存至先前的任务 {1}。', saved.result_id, taskId), 'warn'); }
        return;
      }
      clearInterval(timer); host.replaceChildren();
      savedRow = (saved.effects || []).find(row => row.result_id === saved.result_id) || null;
      const effect = saved.effect, adjustment = effect.cluster_adjustment.arms;
      host.append(el('p', { text:T('{0} 效应值 {1}；标准误 {2}（{3}）；结果编号 {4}。', effect.measure, Number(effect.estimate).toPrecision(5), Number(effect.se).toPrecision(5), effect.se_scale, saved.result_id) }),
        el('p', { class:'hint', text:T('设计效应：治疗组 {0}，对照组 {1}。报告：{2}；ICC：{3}。', Number(adjustment.treatment.design_effect).toPrecision(4), Number(adjustment.control.design_effect).toPrecision(4), body.source_key, iccSource) }),
        el('p', { class:'hint', text:T('这是整群调整的近似值。请核对原始设计，并引用 ICC 来源、计算方法和研究报告。') }),
        ...((saved.effects || []).find(row => row.result_id === saved.result_id)?.input_data?.method_sources || [])
          .map(source => el('p', { class:'hint', text:T('方法来源：{0}。请核对原文并引用所用方法。', source) })));
      if (effect.ci_method) {
        host.append(el('p', { text:T('研究层面 95% t 区间：{0} 至 {1}；自由度 {2}；t 临界值 {3}。', Number(effect.ci_low).toPrecision(5), Number(effect.ci_high).toPrecision(5), effect.df, Number(effect.t_critical).toPrecision(5)) }),
          ...(effect.warnings || []).map(warning => el('p', { class:'hint', text:T(warning.startsWith('The 95% interval uses a t distribution with df = total clusters') ?
            '该 95% t 区间仅描述单项研究。合并仍使用未作少整群自由度调整的正态近似标准误；区间不会改变进入合并的标准误。' : warning) })),
          el('p', { class:'hint', text:T('报告该区间时，请核对并引用 Donner 与 Klar（2000）及 Leyrat 等（2018）的原文方法。') }));
      }
      const download = el('button', { class:'btn', type:'button', text:T('导出整群校正结果 JSON') });
      download.addEventListener('click', () => {
        const url = URL.createObjectURL(new Blob([JSON.stringify({ input:body, effect, result_id:saved.result_id }, null, 2)], { type:'application/json' }));
        const link = el('a', { href:url, download:'reviewflow_cluster_trial.json' });
        link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      });
      host.append(download);
      $('#anMeasure').value = effect.measure;
      $('#anMeasure').dispatchEvent(new Event('change'));
      $('#anEstimate').value = effect.estimate;
      $('#anSe').value = effect.se;
      await loadAnalysis();
      if (context() !== original) {
        if (S.task?.task_id === taskId) host.textContent = T('先前输入已保存为结果 {0}。再次保存前请检查效应量列表。', saved.result_id);
        else { host.replaceChildren(); toast(T('结果 {0} 已保存至先前的任务 {1}。', saved.result_id, taskId), 'warn'); }
      }
    } catch (error) { if (request === activeRequest && (!original || context() === original)) { host.textContent = error.message; toast(error.message, 'warn'); } }
    finally { clearInterval(timer); if (request === activeRequest) saveButton.disabled = false; }
  });
})();
