/* Report export stays separate from analysis rendering. */
for (const [format, id] of [['docx', 'anReportDocx'], ['pptx', 'anReportPptx'], ['tex', 'anReportTex']]) {
  const button = document.getElementById(id);
  button.addEventListener('click', () => {
    try {
      const ticket = anSynthesisGeneration;
      download(taskApi() + '/analysis/report?' + new URLSearchParams({
        ...Object.fromEntries(anSynthesisQuery()), format,
      }), `reviewflow_synthesis.${format}`, button,
      () => ticket === anSynthesisGeneration && !!anLastForestResult);
    } catch (error) { toast(error.message, 'warn'); }
  });
}

$('#anSynthesisCsv').addEventListener('click', () => {
  const result = anLastForestResult;
  if (!result) return;
  const keys = ['comparison','outcome','timepoint','study_id','result_id','measure','estimate','se',
    'source_key','source_title','source_authors','source_year','source_journal','source_doi','source_locator','entry_method','input_data','synthesis_model','synthesis_method',
    'synthesis_ci_method','effect_scale','n_studies','pooled','pooled_analysis',
    'ci_low','ci_high','ci_analysis_low','ci_analysis_high',
    'prediction_interval_95','prediction_interval_analysis_95',
    'q','q_df','q_p','i2_percent','i2_basis','tau2','tau2_method',
    'heterogeneity_intervals','warnings','method_sources'];
  const context = { comparison:anValue('anComparison'), outcome:anValue('anOutcome'),
    timepoint:anValue('anTimepoint'), synthesis_model:result.model,
    synthesis_method:result.method, synthesis_ci_method:result.ci_method,
    effect_scale:result.effect_scale, n_studies:result.n_studies,
    pooled:result.pooled, pooled_analysis:result.pooled_analysis,
    ci_low:result.ci_low, ci_high:result.ci_high,
    ci_analysis_low:result.ci_analysis_low, ci_analysis_high:result.ci_analysis_high,
    prediction_interval_95:result.prediction_interval_95,
    prediction_interval_analysis_95:result.prediction_interval_analysis_95,
    q:result.q, q_df:result.q_df, q_p:result.q_p,
    i2_percent:result.i2_percent, i2_basis:'fixed inverse-variance Q; max(0, (Q−df)/Q), 0 when Q=0',
    tau2:result.tau2, tau2_method:{fixed:'not estimated',random:'DerSimonian–Laird',
      random_pm:'Paule–Mandel',random_reml:'REML'}[result.model],
    heterogeneity_intervals:result.heterogeneity_intervals,
    warnings:result.warnings,
    method_sources:(result.method_sources || []).join('; ') };
  const rows = result.effects.map(effect => {
    const row = { ...context, ...anSourceFields(effect.source_key), ...effect };
    return keys.map(key => anCsvQuote(key, row[key])).join(',');
  });
  const csv = '\ufeff' + [keys.join(','), ...rows].join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], { type:'text/csv;charset=utf-8' }));
  const link = el('a', { href:url, download:'review_synthesis_audit.csv' });
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
