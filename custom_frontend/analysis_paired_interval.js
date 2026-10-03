(function () {
  const panel = $('#anPairedIntervalPanel'), host = $('#anPairedIntervalResult');
  const run = $('#anPairedIntervalRun'), exportButton = $('#anPairedIntervalExport');
  let record = null, generation = 0;
  const methodLabels = {
    wald_bonett_price:'Bonett–Price 调整 Wald（书籍推荐）',
    newcombe_square_and_add:'Newcombe 平方相加法（小／中样本，通常不超过 200 对）',
    wald:'未调整 Wald（对照）',
  };
  const warningText = {
    'This is a research-level presentation interval for one paired table: the module exports no standard error and none may be back-derived from the interval limits for pooling. Meta-analytic combination must keep using the existing RD+SE pipeline.': '此区间仅描述单项研究的配对表；不提供标准误，也不能从区间端点反推标准误用于合并。Meta 分析仍应使用已有的 RD 与标准误。',
    'The selected interval is an asymptotic approximation, not an exact-coverage method: its realized coverage can depart from the nominal level, and the limits are generally not centred on the raw point estimate.': '所选区间是渐近近似，实际覆盖率可能偏离标称水平，区间通常也不以原始观察值为中心。',
    'A confidence limit left the achievable risk-difference range [-1, 1] and was truncated to the boundary.': '置信区间端点超出风险差的可取范围 [−1, 1]，已截断到边界。',
    "At least one marginal count is zero, so the Newcombe correlation coefficient estimate psi is set to 0 by the method's own definition.": '至少一个边际计数为零；按 Newcombe 方法本身的定义，相关系数 ψ 设为 0。',
    "The interval is zero-width at the point estimate because at least one marginal count is 0/n or n/n and the corresponding Wilson score interval collapses; this is the method's own behaviour at fully determined margins.": '至少一个边际比例为 0/n 或 n/n，相应 Wilson 区间退化，故该方法得出零宽区间。',
    'One discordant count is zero, so the paired Wald variance rests on very little discordant information; the recommended Bonett-Price and Newcombe intervals handle such tables more reliably.': '一个不一致配对格为零，Wald 方差依据的信息很少；Bonett–Price 和 Newcombe 区间更适合此类表格。',
  };
  function clear() {
    generation++; record = null; host.replaceChildren();
    exportButton.disabled = true; run.disabled = false;
  }
  panel.addEventListener('input', clear);
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', clear);
  run.addEventListener('click', async () => {
    clear();
    const request = generation;
    let timer;
    try {
      const method = anValue('anPairedIntervalMethod');
      if (!method) throw new Error(T('请选择配对区间方法。'));
      const body = { method, source_note:anValue('anPairedIntervalSource') };
      for (const cell of ['a','b','c','d']) {
        const raw = anValue('anPairedInterval' + cell.toUpperCase()), value = Number(raw);
        if (!raw || !Number.isSafeInteger(value) || value < 0) throw new Error(T('单元格 {0} 必须为非负整数。', cell));
        body[cell] = value;
      }
      const level = anValue('anPairedIntervalLevel');
      body.level = Number(level) / 100;
      if (!level || !(body.level > 0 && body.level < 1)) throw new Error(T('置信水平必须大于 0 且小于 100。'));
      run.disabled = true;
      const started = performance.now();
      const progress = () => { if (request === generation) host.textContent = T('正在计算配对区间…已等待 {0} 秒。', ((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress, 500);
      const result = await api(taskApi() + '/analysis/study-interval/paired-risk-difference', { method:'POST', body });
      if (request !== generation) return;
      host.replaceChildren();
      const fmt = value => Number(value).toPrecision(7);
      host.append(el('p', {}, el('span', { text:T(methodLabels[result.method] || result.method_label) }),
          ' · ', el('span', { text:T('{0}% 置信区间', result.level * 100) })),
        el('p', { text:T('{0} 对配对；{1} 对不一致。观察风险差（T − C）：{2}；区间 [{3}, {4}]。',
          result.n_pairs, result.discordant_total, fmt(result.rd), fmt(result.ci[0]), fmt(result.ci[1])) }));
      if (result.bonett_price) host.append(el('p', { text:T('Bonett–Price 调整后区间中心：{0}；观察风险差仍为 {1}。',
        fmt(result.bonett_price.delta_tilde), fmt(result.rd)) }));
      for (const warning of result.warnings || []) host.append(el('p', { class:'hint', text:T(warningText[warning] || warning) }));
      for (const source of result.method_sources || []) host.append(el('p', { class:'hint', lang:'en', text:source }));
      host.append(el('p', { class:'hint' }, el('span', { text:T('研究来源及条件定义：') }),
        el('span', { text:result.source_note || T('未记录；发表前请补充。') })));
      record = { request:body, result, citation_reminder:'Check the original method and book. Cite the actual method and the study report.' };
      exportButton.disabled = false;
    } catch (error) {
      if (request === generation) host.textContent = error.message.startsWith('b = c = 0 gives no discordant pairs') ?
        T('b = c = 0：没有不一致配对，无法计算配对区间。') : error.message;
    } finally {
      clearInterval(timer);
      if (request === generation) run.disabled = false;
    }
  });
  exportButton.addEventListener('click', () => {
    if (!record) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2)], { type:'application/json' }));
    const link = el('a', { href:url, download:'reviewflow_paired_interval.json' });
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
