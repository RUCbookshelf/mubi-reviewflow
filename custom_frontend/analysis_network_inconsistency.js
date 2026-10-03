(function () {
  const host = $('#anNetworkInconsistencyResult'), button = $('#anNetworkInconsistencyRun');
  let generation = 0, timer;
  const clear = () => { generation++; clearInterval(timer); button.disabled = false; host.replaceChildren(); };
  $('#anNetworkInconsistencyModel').addEventListener('change', () => {
    $('#anNetworkInconsistencyTau').disabled = anValue('anNetworkInconsistencyModel') !== 'preset';
    clear();
  });
  ['anOutcome', 'anTimepoint', 'anMeasure', 'anNetworkReference', 'anNetworkInconsistencyTau'].forEach(id => $('#'+id).addEventListener('input', clear));
  $('#anNetworkStudySave').addEventListener('click', clear);
  document.addEventListener('reviewflow:network-study-saved', clear);
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', () => {
    $('#anNetworkInconsistencyModel').value = '';
    $('#anNetworkInconsistencyTau').value = '';
    $('#anNetworkInconsistencyTau').disabled = true;
    clear();
  });

  button.addEventListener('click', async () => {
    clear();
    const current = generation;
    try {
      const model = anValue('anNetworkInconsistencyModel');
      if (!model) throw new Error(T('请选择不一致性模型。'));
      const params = new URLSearchParams({ outcome:anValue('anOutcome'),
        timepoint:anValue('anTimepoint'), measure:anValue('anMeasure'), model });
      if (model !== 'fixed') {
        const reference = anValue('anNetworkReference').trim();
        if (!reference) throw new Error(T('请输入参考治疗方案。'));
        params.set('reference', reference);
      }
      if (model === 'preset') {
        const value = anValue('anNetworkInconsistencyTau');
        if (!value.trim() || !Number.isFinite(Number(value)) || Number(value) < 0) throw new Error(T('请输入有限且非负的 τ²。'));
        params.set('tau2', value);
      }
      button.disabled = true;
      const started = performance.now();
      const progress = () => { if (current === generation) host.textContent = T('正在计算不一致性分解…已等待 {0} 秒。',((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress,500);
      const result = await api(taskApi() + '/analysis/network/inconsistency?' + params);
      if (current !== generation) return;
      host.replaceChildren();
      host.append(el('p', { text:T('{0} 项研究 · {1} 个对比 · 设计间不一致性 Q',result.n_studies,result.n_contrasts) }));
      const stats = el('table');
      const headings = ['分解项', 'Q', 'df', 'p'].map(text => el('th', { text:T(text) }));
      stats.append(el('thead', {}, el('tr', {}, ...headings)));
      const body = el('tbody');
      for (const [label, q, df, p] of [
        ['总计', result.q_total, result.df_total, null],
        ['设计内', result.q_within_designs, result.df_within_designs, null],
        ['设计间', result.q_between_designs, result.df_between_designs, result.p_value]
      ]) {
        const values = [label, Number(q).toPrecision(4), String(df), p == null ? '—' : Number(p).toPrecision(4)];
        body.append(el('tr', {}, ...values.map(text => el('td', { text:T(String(text)) }))));
      }
      stats.append(body);
      host.append(stats);
      if (result.reason) host.append(el('p', { class:'hint', lang:'en', text:result.reason }));
      host.append(el('p', { class:'hint', text:T('若检验显著，表示所选模型下不同设计间可能存在不一致。结果不显著不能证明网络一致或满足传递性。') }));
      if (result.heterogeneity) {
        const h = result.heterogeneity;
        const tauSummary = h.scale === 'log'
          ? T('分解所用 τ²={0}；全网一致性 REML τ²={1}。两者均为对数尺度方差。全网异质性可能吸收不一致性，两种口径不能互换。',h.tau2,h.consistency_reml.tau2 ?? '不可用')
          : T('分解所用 τ²={0}；全网一致性 REML τ²={1}。两者均为自然尺度方差。全网异质性可能吸收不一致性，两种口径不能互换。',h.tau2,h.consistency_reml.tau2 ?? '不可用');
        host.append(el('p', { class:'hint', text:tauSummary }));
        if (result.at_reml_tau2) host.append(el('pre', { class:'hint', lang:'en', text:T('按全网一致性 REML τ² 进行敏感性分析：') + JSON.stringify(result.at_reml_tau2, null, 2) }));
      }
      (result.warnings || []).forEach(text => host.append(el('p', { class:'hint', lang:'en', text })));
      const download = el('button', { class:'btn', type:'button', text:T('导出不一致性数据（JSON）') });
      download.addEventListener('click', () => {
        const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type:'application/json' }));
        el('a', { href:url, download:'reviewflow_network_inconsistency.json' }).click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      });
      host.append(download, el('p', { class:'hint', lang:'en', text:'Higgins et al. (2012), Research Synthesis Methods 3:98–110, doi:10.1002/jrsm.1044; Jackson et al. (2012), Statistics in Medicine 31:3805–3820, doi:10.1002/sim.5453.' }));
      host.append(anAuditReportButtons(taskApi()+'/analysis/network/inconsistency',params,
        'reviewflow_network_inconsistency_audit',() => current === generation));
      const designs = el('details', {}, el('summary', { text:T('设计与来源研究') }));
      designs.append(el('pre', { class:'hint', text:JSON.stringify({ designs:result.designs, sources:result.sources, source_locators:result.source_locators }, null, 2) }));
      host.append(designs, el('p', { class:'hint', text:T('使用该方法撰写论文时，请展开本区参考文献并引用方法来源。') }));
    } catch (error) { if (current !== generation) return; const known = ['请选择不一致性模型。','请输入参考治疗方案。','请输入有限且非负的 τ²。'].some(key=>T(key)===error.message); host.replaceChildren(el('span',{text:error.message,...(known?{}:{lang:'en'})})); toast(error.message, 'warn'); }
    finally { if (current === generation) { clearInterval(timer); button.disabled = false; } }
  });
})();
