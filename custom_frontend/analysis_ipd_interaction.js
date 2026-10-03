(function () {
  const host = $('#anIpdInteractionResult'), run = $('#anIpdInteractionRun');
  let generation = 0, timer = null, renderLast = null;
  const clear = () => { generation++; clearInterval(timer); renderLast = null; host.replaceChildren(); run.disabled = false; };
  window.RFRefreshIpdInteractionText = () => { if (renderLast) renderLast(); };
  ['anIpdInteractionRows', 'anIpdInteractionSources', 'anIpdInteractionCenter']
    .forEach(id => $('#'+id).addEventListener('input', clear));
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', clear);

  run.addEventListener('click', async () => {
    clear(); const ticket = generation;
    try {
      const centerInput = anValue('anIpdInteractionCenter');
      if (centerInput === '') throw new Error(T('请填写协变量参照值。'));
      const requestBody = {rows:JSON.parse(anValue('anIpdInteractionRows')),
        study_sources:JSON.parse(anValue('anIpdInteractionSources')),
        covariate_center:Number(centerInput)};
      run.disabled = true;
      const started = performance.now();
      const progress = () => { if (ticket === generation) host.textContent =
        T('正在拟合研究内交互模型…已等待 {0} 秒。暂不提供中间进度。', ((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress,500);
      const result = await api(taskApi() + '/analysis/ipd/interaction/logistic', {
        method:'POST', body:requestBody
      });
      if (ticket !== generation) return;
      const draw = () => {
      host.replaceChildren();
      const fmt = value => value == null ? T('超出有限数值显示范围') : Number(value).toPrecision(5);
      const effects = [
        [T('参照值处的治疗效应'), result.treatment_or_at_reference],
        [T('每单位个体协变量的研究内交互'), result.within_study_interaction_or_per_unit],
        [T('每单位研究均值差异的研究间交互'), result.between_study_interaction_or_per_unit]
      ];
      host.append(el('p', { text:T('研究数：{0}；参与者数：{1}；事件数：{2}', result.studies, result.n, result.events) }));
      const table = el('table');
      const headings = ['项','比值比 OR','95% CI'].map(text => el('th', { text:T(text) }));
      table.append(el('thead', {}, el('tr', {}, ...headings)));
      const body = el('tbody');
      for (const [name, item] of effects) {
        const values = [name, fmt(item.or), `${fmt(item.ci95[0])}–${fmt(item.ci95[1])}`];
        body.append(el('tr', {}, ...values.map(text => el('td', { text }))));
      }
      table.append(body); host.append(table);
      host.append(el('p', { class:'hint', text:T('治疗效应的协变量参照说明：{0}', result.treatment_or_at_reference.reference) }),
        el('p', { class:'hint', text:T('研究内交互利用同一研究中参与者之间的差异；研究间交互反映研究均值之间的生态关联。参与者记录仅用于本次计算，不会保存。') }));
      const sources = el('details', {}, el('summary', { text:T('研究与报告来源对应表') }));
      sources.append(el('pre', { class:'hint', text:JSON.stringify(result.study_sources, null, 2) }));
      host.append(sources,
        anAuditReportButtons(taskApi() + '/analysis/ipd/interaction/logistic', new URLSearchParams(),
          'reviewflow_ipd_interaction_audit', () => ticket === generation, requestBody),
        ...(result.method_sources || []).map(text => el('p', { class:'hint', lang:'en', text })),
        el('p', { class:'hint', text:T('请查阅原书和方法论文，并引用实际使用的方法。报告只含汇总结果与来源对应信息，不含参与者记录。') }));
      };
      renderLast = draw; draw();
    } catch (error) { if (ticket === generation) { host.replaceChildren(el('p',{lang:'en',text:error.message})); toast(error.message, 'warn'); } }
    finally { if (ticket === generation) { clearInterval(timer); run.disabled = false; } }
  });
})();
