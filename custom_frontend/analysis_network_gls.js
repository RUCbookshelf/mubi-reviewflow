(function () {
const rows = $('#anNetworkContrastRows');
const matrixHost = $('#anNetworkCovariance');
const resultHost = $('#anNetworkGlsResult');
const listHost = $('#anNetworkStudyList');
const run = $('#anNetworkGlsRun');
const saveStudy = $('#anNetworkStudySave');
let resultGeneration = 0, listGeneration = 0, activeRequest = 0, timer = null, listTimer = null, pending = false;
function clearResult() { resultGeneration++; clearInterval(timer); resultHost.replaceChildren(); run.disabled = pending; saveStudy.disabled = pending; }

function currentRows() {
  return [...rows.querySelectorAll('[data-network-contrast]')];
}
function draftMatrix() {
  const size = currentRows().length;
  const matrix = Array.from({ length:size }, () => Array(size).fill(''));
  matrixHost.querySelectorAll('input[data-i][data-j]').forEach(input => {
    const i = Number(input.dataset.i), j = Number(input.dataset.j);
    matrix[i][j] = matrix[j][i] = input.value;
  });
  return matrix;
}
function renderMatrix(values = []) {
  const size = currentRows().length;
  matrixHost.replaceChildren();
  if (!size) return;
  const table = el('table');
  const headings = Array.from({ length:size }, (_, i) => el('th', { text:T('对比 {0}',i+1) }));
  table.append(el('thead', {}, el('tr', {}, el('th', { text:T('协方差') }),
    ...headings)));
  const body = el('tbody');
  for (let i = 0; i < size; i++) {
    const cells = [el('th', { text:T('对比 {0}',i+1) })];
    for (let j = 0; j < size; j++) {
      if (j > i) {
        cells.push(el('td', { text:values[i]?.[j] || '—' }));
      } else {
        const input = el('input', { class:'input', type:'number', step:'any', 'aria-label':T('协方差 {0}，{1}',i+1,j+1) });
        input.dataset.i = String(i); input.dataset.j = String(j);
        input.style.width = '7rem'; input.value = values[i]?.[j] || '';
        input.addEventListener('input', () => {
          if (i !== j) table.rows[j+1].cells[i+1].textContent = input.value || '—';
        });
        cells.push(el('td', {}, input));
      }
    }
    body.append(el('tr', {}, ...cells));
  }
  table.append(body);
  const scroller = el('div', {}, table); scroller.style.overflowX = 'auto';
  matrixHost.append(scroller);
}
function addContrast(values = {}, matrix = draftMatrix()) {
  const row = el('div', { class:'row' }); row.dataset.networkContrast = '';
  for (const [field, label] of [['treatment','治疗方案'], ['comparator','比较方案'], ['estimate','估计值']]) {
    const input = el('input', { class:'input', 'aria-label':label, placeholder:label,
      type:field === 'estimate' ? 'number' : 'text' });
    if (field === 'estimate') input.step = 'any';
    input.dataset.field = field; input.value = values[field] ?? '';
    row.append(input);
  }
  row.append(el('button', { class:'btn b-out', type:'button', text:T('移除'), onclick:() => {
    const index = currentRows().indexOf(row), retained = draftMatrix();
    retained.splice(index, 1); retained.forEach(values => values.splice(index, 1));
    row.remove(); renderMatrix(retained);
  } }));
  rows.append(row); renderMatrix(matrix);
}
function selectedScale() {
  const measure = anValue('anMeasure');
  return ['RR','OR'].includes(measure) ? 'log' : ['RD','MD','SMD'].includes(measure) ? 'natural' : '';
}
function query() {
  return new URLSearchParams({ outcome:anValue('anOutcome'), timepoint:anValue('anTimepoint'),
    measure:anValue('anMeasure') });
}
async function loadStudies() {
  clearInterval(listTimer);
  if (!S.task) { listGeneration++; listHost.replaceChildren(); return; }
  const ticket = ++listGeneration, taskId = S.task.task_id, search = query().toString();
  const current = () => ticket === listGeneration && S.task?.task_id === taskId && query().toString() === search && $('#anNetworkGlsPanel').open;
  const started = performance.now();
  const progress = () => { if (current()) listHost.textContent = T('正在加载已保存研究…已等待 {0} 秒。',((performance.now()-started)/1000).toFixed(1)); };
  progress(); const waiting = setInterval(progress, 500); listTimer = waiting;
  try {
    const data = await api(taskApi() + '/analysis/network/studies?' + search);
    if (current()) listHost.textContent = data.studies.length ? T('已保存研究：') + data.studies.map(study =>
      `${study.study_id}（${T('对比 {0}',study.contrasts.length)}；${T('来源')} ${study.source_key}${study.source_locator ? '，'+study.source_locator : ''}）`).join('，') :
      T('当前结局、时间点和效应指标下尚无已保存的相关研究。');
  } catch (error) { if (current()) listHost.replaceChildren(el('span',{lang:'en',text:error.message})); }
  finally { clearInterval(waiting); }
}
$('#anNetworkAddContrast').addEventListener('click', () => addContrast());
['anEffectStudy','anSource'].forEach(id => $('#'+id).addEventListener('change', () => {
  $('#anNetworkSourceLocator').value = '';
}));
['anOutcome','anTimepoint','anMeasure','anNetworkReference','anNetworkGlsModel']
  .forEach(id => $('#'+id).addEventListener('input',clearResult));
['anOutcome','anTimepoint','anMeasure'].forEach(id => $('#'+id).addEventListener('input', () => {
  listGeneration++; clearInterval(listTimer); listHost.replaceChildren();
}));
document.addEventListener('reviewflow:analysis-core-loaded',clearResult);
document.addEventListener('reviewflow:network-study-saved', () => { clearResult(); if ($('#anNetworkGlsPanel').open) loadStudies(); });
document.addEventListener('reviewflow:analysis-task-changed', () => {
  activeRequest++; pending = false; clearResult();
  listGeneration++; clearInterval(listTimer); listHost.replaceChildren();
  rows.replaceChildren(); addContrast({}, []);
  ['anNetworkSourceLocator', 'anNetworkReference', 'anNetworkGlsModel'].forEach(id => $('#'+id).value = '');
});
$('#anNetworkGlsPanel').addEventListener('toggle', () => {
  if ($('#anNetworkGlsPanel').open) {
    $('#anNetworkCovarianceScale').value = selectedScale();
    loadStudies();
  } else { listGeneration++; clearInterval(listTimer); listHost.replaceChildren(); }
});
$('#anMeasure').addEventListener('change', () => {
  $('#anNetworkCovarianceScale').value = selectedScale();
  renderMatrix();
  if ($('#anNetworkGlsPanel').open) loadStudies();
});
for (const id of ['anOutcome', 'anTimepoint']) $('#'+id).addEventListener('change', () => {
  if ($('#anNetworkGlsPanel').open) loadStudies();
});
saveStudy.addEventListener('click', async () => {
  if (pending || saveStudy.disabled) return;
  clearResult();
  const ticket = resultGeneration, request = ++activeRequest;
  let saveTimer;
  try {
    if (!selectedScale()) throw new Error(T('网络相关对比仅支持 RR、OR、RD、MD 或 SMD。'));
    const contrasts = currentRows().map(row => {
      const fields = Object.fromEntries([...row.querySelectorAll('[data-field]')].map(input =>
        [input.dataset.field, input.value.trim()]));
      if (!fields.treatment || !fields.comparator || fields.estimate === '') throw new Error(T('请填写每个对比的治疗方案、比较方案和估计值。'));
      return { treatment:fields.treatment, comparator:fields.comparator, estimate:Number(fields.estimate) };
    });
    if (!contrasts.length) throw new Error(T('请至少添加一个对比。'));
    const draft = draftMatrix();
    if (draft.some(row => row.some(value => value === ''))) throw new Error(T('请填写完整的协方差矩阵。'));
    const body = { study_id:anValue('anEffectStudy'), source_key:anValue('anSource'),
      source_locator:anValue('anNetworkSourceLocator'),
      outcome:anValue('anOutcome'), timepoint:anValue('anTimepoint'), measure:anValue('anMeasure'),
      covariance_scale:anValue('anNetworkCovarianceScale'), contrasts,
      covariance:draft.map(row => row.map(Number)) };
    pending = true; saveStudy.disabled = run.disabled = true;
    const started = performance.now();
    const progress = () => { if (ticket === resultGeneration) resultHost.textContent = T('正在保存相关研究…已等待 {0} 秒。',((performance.now()-started)/1000).toFixed(1)); };
    progress(); saveTimer = setInterval(progress, 500);
    const saved = await api(taskApi() + '/analysis/network/studies', { method:'POST', body });
    if (ticket !== resultGeneration) return;
    clearInterval(saveTimer); resultHost.replaceChildren();
    resultHost.append(el('p', { text:T('已保存研究 {0}，包含 {1} 个对比。',saved.study_id,saved.n_contrasts) }));
    await loadStudies();
  } catch (error) { if (ticket === resultGeneration) { resultHost.replaceChildren(el('span',{lang:'en',text:error.message})); toast(error.message, 'warn'); } }
  finally { clearInterval(saveTimer); if (request === activeRequest) { pending = false; saveStudy.disabled = run.disabled = false; } }
});
run.addEventListener('click', async () => {
  if (pending || run.disabled) return;
  clearResult(); const ticket = resultGeneration, request = ++activeRequest;
  try {
    if (!anValue('anNetworkGlsModel')) throw new Error(T('请选择网络模型。'));
    const params = query(); params.set('reference', anValue('anNetworkReference'));
    params.set('model', anValue('anNetworkGlsModel'));
    pending = true; run.disabled = saveStudy.disabled = true;
    const started = performance.now();
    const progress = () => { if (ticket === resultGeneration) resultHost.textContent =
      T('正在拟合网络…已等待 {0} 秒；暂无中间进度。',((performance.now()-started)/1000).toFixed(1)); };
    progress(); timer = setInterval(progress, 500);
    const result = await api(taskApi() + '/analysis/network/gls?' + params);
    if (ticket !== resultGeneration) return;
    resultHost.replaceChildren();
    resultHost.append(el('p', { text:result.random_effects
      ? T('{0} 项研究 · {1} 个对比 · 随机效应 REML 网络模型（各对比共用 τ²）',result.n_studies,result.n_contrasts)
      : T('{0} 项研究 · {1} 个对比 · 固定效应网络模型',result.n_studies,result.n_contrasts) }));
    const table = el('table');
    const scaleKey = result.estimates[0]?.se_scale === 'log' ? 'SE（对数尺度）' : result.estimates[0]?.se_scale === 'natural' ? 'SE（自然尺度）' : 'SE（未注明尺度）';
    const headings = ['治疗方案','比较方案','估计值',scaleKey,'95% CI'].map(label => el('th', { text:T(label) }));
    table.append(el('thead', {}, el('tr', {}, ...headings)));
    table.append(el('tbody', {}, ...result.estimates.map(item => el('tr', {},
      ...[item.treatment, item.comparator, Number(item.estimate).toPrecision(4), Number(item.se).toPrecision(4),
        `${Number(item.ci_low).toPrecision(4)}–${Number(item.ci_high).toPrecision(4)}`].map(value => el('td', { text:value }))))));
    resultHost.append(table);
    if (result.random_effects) resultHost.append(el('p', { class:'hint', lang:'en', text:
      T("Between-study variance τ²={0} ({1}; {2} variance scale); {3}.", Number(result.random_effects.tau2).toPrecision(4),RFAnalysisLocale.term(result.random_effects.method||result.method),RFAnalysisLocale.term(result.random_effects.scale),RFAnalysisLocale.term(result.random_effects.covariance_rule)) }));
    resultHost.append(el('p', { class:'hint', text:T('总残差 Q={0}（df {1}）；这不是正式的不一致性检验。',Number(result.residual_q).toPrecision(4),result.residual_df) }));
    if (result.warning || result.warnings?.length) resultHost.append(el('p', { class:'hint', lang:'en', text:[result.warning, ...(result.warnings || [])].filter(Boolean).join(' ') }));
    resultHost.append(anAuditReportButtons(taskApi()+'/analysis/network/gls',params,
      'reviewflow_network_synthesis_audit',() => ticket === resultGeneration));
    (result.method_sources || []).forEach(text => resultHost.append(el('p',{class:'hint',lang:'en',text})));
    resultHost.append(el('p', { class:'hint', text:T('使用该方法撰写论文时，请展开本区参考文献并引用方法来源。') }));
  } catch (error) { if (ticket === resultGeneration) { const known = ['网络相关对比仅支持 RR、OR、RD、MD 或 SMD。','请填写每个对比的治疗方案、比较方案和估计值。','请至少添加一个对比。','请填写完整的协方差矩阵。','请选择网络模型。'].some(key=>T(key)===error.message); resultHost.replaceChildren(el('span',{text:error.message,...(known?{}:{lang:'en'})})); toast(error.message, 'warn'); } }
  finally { if (request === activeRequest) { clearInterval(timer); pending = false; run.disabled = saveStudy.disabled = false; } }
});
addContrast();
})();
