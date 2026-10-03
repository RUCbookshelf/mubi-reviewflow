(function () {
  const panel = $('#anNntPanel'), host = $('#anNntResult');
  const run = $('#anNntRun'), exportButton = $('#anNntExport');
  let record = null;
  const formulaText = {
    rd:'NNT = 1/|RD| (Cochrane Handbook v6.5, §15.4.4.1; Altman 1998)',
    or:'EER = OR×CER/(1−CER+OR×CER); NNT = 1/|EER−CER| (Cochrane Handbook v6.5, §15.4.4.3; Altman 1998)',
    rr:'EER = RR×CER; NNT = 1/|EER−CER| (Cochrane Handbook v6.5, §15.4.4.2; Altman 1998)',
  };
  const noteText = {
    'The risk-difference interval spans zero, so the NNT interval is unbounded and contains both infinitely large NNTB and NNTH values; it is reported in the Altman (1998) form \'NNTH a to infinity to NNTB b\'.': '风险差区间跨越零，NNT 区间因此无界，包含获益和伤害两侧；按 Altman（1998）的双分支形式报告。',
    'A risk-difference confidence limit is exactly zero; the corresponding NNT bound is infinite and is reported as an unbounded one-sided interval.': '风险差区间的一端恰为零，相应 NNT 边界为无穷大，按单侧无界区间报告。',
    'The point estimate lies outside the supplied confidence interval; check the inputs.': '点估计值不在输入的置信区间内，请核对原始数据。',
    'The NNT depends on the supplied control event rate (CER); recompute it with the actual control event rate of the target population before use.': 'NNT 取决于输入的对照组事件风险（CER）；使用前请按目标人群实际 CER 重新计算。',
    'The outcome is binary and the same event definition applies to both arms.': '结局为二分类，且两组采用相同的事件定义。',
    'RD is the risk in the treatment arm minus the risk in the control arm, so a positive RD means the treatment increases the event probability (harm, NNTH) and a negative RD means it lowers it (benefit, NNTB).': 'RD 为治疗组风险减对照组风险；正值表示事件风险增加（伤害，NNTH），负值表示风险降低（获益，NNTB）。',
    'Confidence limits are transformed by applying the same formula to each limit (Daly substitution); the level of the supplied interval is taken as given and is not re-derived, and the resulting NNT interval does not reflect uncertainty in the assumed control event rate.': '区间两端分别按同一公式换算（Daly 代入法）；输入的置信水平只作记录，不重新推算；所得 NNT 区间未计入假定 CER 的不确定性。',
    'The control event rate (CER) is an assumption imported from outside the data supplied here; the converted NNT changes whenever a different CER is used.': 'CER 是从输入效应值以外取得的假设；更换 CER 会改变换算后的 NNT。',
    'The odds ratio is not collapsible: with the same OR the converted NNT still depends on the chosen CER, so report the NNT only for a CER that is relevant to the population of interest.': '优势比不可折叠；即使 OR 不变，NNT 仍随 CER 改变。只应对目标人群适用的 CER 报告 NNT。',
  };
  const task = RFAnalysisTask.create({
    host, run,
    reset() { record = null; host.replaceChildren(); exportButton.disabled = true; },
    progress: seconds => T('正在换算 NNT…已等待 {0} 秒。', seconds.toFixed(1)),
    async execute(ticket) {
      const measure = anValue('anNntMeasure');
      if (!measure) throw new Error(T('请选择输入效应指标。'));
      if (!$('#anNntAdverse').checked) throw new Error(T('解释获益或伤害前，请确认记录的是不良事件。'));
      const number = id => {
        const raw = anValue(id), value = Number(raw);
        if (!raw || !Number.isFinite(value)) throw new Error(T('请填写点估计值、区间上下限、原始置信水平，以及所需的 CER。'));
        return value;
      };
      const body = { measure, estimate:number('anNntEstimate'), lower:number('anNntLower'),
        upper:number('anNntUpper'), level:number('anNntLevel')/100, adverse_event_confirmed:true,
        time_horizon:anValue('anNntHorizon'), source_note:anValue('anNntSource') };
      if (!(body.level > 0 && body.level < 1)) throw new Error(T('原始置信水平必须大于 0 且小于 100%。'));
      if (!body.time_horizon) throw new Error(T('请填写效应值和基线风险对应的随访时间。'));
      if (measure !== 'rd') body.cer = number('anNntCer');
      const result = await api(taskApi() + '/analysis/nnt-conversion', { method:'POST', body });
      if (ticket !== task.generation()) return;
      host.replaceChildren();
      const ci = result.nnt_ci, fmt = value => String(Number(Number(value).toPrecision(4)));
      const interval = ci.crosses_zero ? T('NNTH {0} → ∞ → NNTB {1}', fmt(ci.lower), fmt(ci.upper)) :
        ci.unbounded ? T('{0} {1} → ∞', ci.lower_semantics, fmt(ci.lower)) :
        T('{0} {1}–{2}', ci.lower_semantics, fmt(ci.lower), fmt(ci.upper));
      host.append(el('p', { text:T('{0}；向上取整 {1}。随访时间：{2}。', result.nnt_point.display, result.nnt_point.rounded_up, result.time_horizon) }),
        el('p', {}, el('span', { text:T('{0}% 区间：', result.level * 100) }), el('span', { text:interval })));
      if (ci.crosses_zero) host.append(el('p', { role:'note', text:T('该区间同时包含获益和伤害，具有两个无界分支；不能将两端点当作一个有限范围。') }));
      if (result.input_data.cer != null) host.append(el('p', { text:T('假定的对照组事件风险：{0}。', result.input_data.cer) }));
      host.append(el('p', { class:'hint', text:formulaText[result.entry_path] || result.formula }));
      for (const note of [...result.warnings, ...result.assumptions]) host.append(el('p', { class:'hint', text:T(noteText[note] || note) }));
      for (const source of result.method_sources || []) host.append(el('p', { class:'hint', lang:'en', text:source }));
      host.append(el('p', { class:'hint' }, el('span', { text:T('效应值与基线风险来源：') }),
        el('span', { text:result.source_note || T('未记录；发表前请补充来源。') })));
      record = { request:body, result, citation_reminder:'Verify and cite the conversion method, source effect and baseline-risk source.' };
      exportButton.disabled = false;
    },
  });
  const clear = task.clear;
  panel.addEventListener('input', clear);
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', clear);
  $('#anNntMeasure').addEventListener('change', () => {
    clear();
    $('#anNntCerLabel').hidden = !['or','rr'].includes(anValue('anNntMeasure'));
  });
  run.addEventListener('click', task.submit);
  exportButton.addEventListener('click', () => {
    if (!record) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2)], { type:'application/json' }));
    const link = el('a', { href:url, download:'reviewflow_nnt_interpretation.json' });
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
