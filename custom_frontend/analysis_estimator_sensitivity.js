/* Compare random-effects estimators on the same selected results. */
(function () {
const estimatorButton = document.getElementById('anCompareEstimators');
const estimatorHost = document.getElementById('anEstimatorComparison');
let estimatorGeneration = 0, estimatorTimer = null, cachedResults = null;
function clearEstimatorComparison() {
  estimatorGeneration++; clearInterval(estimatorTimer); cachedResults = null;
  estimatorHost.replaceChildren(); estimatorButton.disabled = false;
}
['anComparison', 'anOutcome', 'anTimepoint', 'anMeasure', 'anCiMethod'].forEach(id =>
  document.getElementById(id).addEventListener('input', clearEstimatorComparison));
document.addEventListener('reviewflow:analysis-core-loaded', clearEstimatorComparison);
document.addEventListener('reviewflow:analysis-task-changed', clearEstimatorComparison);
function renderEstimatorResults(results,models) {
    const table = document.createElement('table');
    const head = table.createTHead().insertRow();
    for (const label of ['Estimator', 'Pooled effect (95% CI)', 'τ²']) {
      const cell = document.createElement('th'); cell.textContent = T(label); head.append(cell);
    }
    const body = table.createTBody();
    const number = value => Number(value).toPrecision(4);
    results.forEach((result, index) => {
      const row = body.insertRow();
      for (const value of [models[index][1],
        `${number(result.pooled)} (${number(result.ci_low)}${T(' 至 ')}${number(result.ci_high)})`,
        number(result.tau2)]) {
        const cell = row.insertCell(); cell.textContent = value;
      }
    });
    const caption = table.createCaption();
    caption.textContent = T('{0} 项独立研究 · {1} · {2}',results[0].n_studies,results[0].measure,RFAnalysisLocale.term(results[0].ci_method));
    const note = document.createElement('p'); note.className = 'hint';
    note.textContent = T('结果相近并不能验证模型假设。请核对原始报告，并引用实际使用的估计方法。');
    const scope = document.createElement('p'); scope.className = 'hint';
    scope.textContent = T('τ² 表示 {0} 分析尺度上的研究间方差。三次拟合均使用固定逆方差 Q={1}（自由度 {2}），但 τ² 估计方法不同。',
      RFAnalysisLocale.term(results[0].effect_scale),number(results[0].q),results[0].q_df);
    const sources = el('p',{class:'hint'},T('所选研究及来源：'),...results[0].effects.map((effect,index)=>
      el('span',{translate:'no',text:(index?'；':'')+`${effect.study_id} (${effect.source_key}${effect.source_locator?', '+effect.source_locator:''}; #${effect.result_id})`})));
    estimatorHost.replaceChildren(table, scope, sources, note);
}
document.addEventListener('reviewflow:language-changed',()=>{if(cachedResults)renderEstimatorResults(cachedResults.results,cachedResults.models);});
estimatorButton.addEventListener('click', async () => {
  clearEstimatorComparison();
  const ticket = estimatorGeneration, host = estimatorHost;
  try {
    anRequireCi();
    const base = Object.fromEntries(anAnalysisQuery());
    const models = [['random', 'DL'], ['random_pm', 'Paule–Mandel'], ['random_reml', 'REML']];
    estimatorButton.disabled = true;
    const started = performance.now();
    const progress = () => { if (ticket === estimatorGeneration) host.textContent =
      T('正在比较三种研究间方差估计方法…已等待 {0} 秒。', ((performance.now()-started)/1000).toFixed(1)); };
    progress(); estimatorTimer = setInterval(progress,500);
    const results = await Promise.all(models.map(async ([model]) => api(
      taskApi() + '/analysis/synthesis?' + new URLSearchParams({ ...base, model }))));
    if (ticket !== estimatorGeneration) return;
    const selected = results.map(result => result.effects.map(effect => effect.result_id).join(','));
    if (new Set(selected).size !== 1) throw new Error('Selected results changed during comparison; run it again.');

    cachedResults = {results,models};
    renderEstimatorResults(results,models);
  } catch (error) {
    if (ticket === estimatorGeneration) { host.replaceChildren(RFAnalysisLocale.message(error.message)); toast(T(error.message), 'warn'); }
  } finally {
    if (ticket === estimatorGeneration) { clearInterval(estimatorTimer); estimatorButton.disabled = false; }
  }
});
})();
