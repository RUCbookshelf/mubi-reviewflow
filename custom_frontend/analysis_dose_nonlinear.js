(function () {
  const host = $('#anDoseNonlinearResult');
  const exportButton = $('#anDoseNonlinearExport');

  const button = $('#anDoseNonlinearRun');
  let generation = 0, timer, lastResult = null, lastBody = null;
  function clear() { generation++; lastResult = lastBody = null; clearInterval(timer); host.replaceChildren(); exportButton.disabled = true; button.disabled = false; }
  window.RFRefreshDoseNonlinearText = () => { if (lastResult) render(lastResult,lastBody,generation); };
  ['anOutcome','anTimepoint','anMeasure','anDoseKnot1','anDoseKnot2','anDoseKnot3','anDosePlotReference']
    .forEach(id => $('#'+id).addEventListener('input', clear));
  $('#anMeasure').addEventListener('change', clear);
  $('#anDoseSave').addEventListener('click', clear);
  document.addEventListener('reviewflow:analysis-core-loaded', clear);
  document.addEventListener('reviewflow:analysis-task-changed', () => {
    ['anDoseKnot1','anDoseKnot2','anDoseKnot3','anDosePlotReference']
      .forEach(id => $('#'+id).value = '');
    clear();
  });

  function render(result, body, ticket) {
    clearInterval(timer); host.replaceChildren();
    const curve = result.curve || [];
    if (curve.length < 2) throw new Error(T('非线性模型未返回可用曲线。'));
    const raw = curve.flatMap(row => [row.estimate, row.ci_low, row.ci_high]);
    const exponentiate = result.analysis_scale === 'log' && raw.every(value => value > -700 && value < 700);
    const show = value => exponentiate ? Math.exp(value) : value;
    const values = curve.map(row => ({ dose:Number(row.dose), mid:show(row.estimate),
      low:show(row.ci_low), high:show(row.ci_high) })).sort((a, b) => a.dose - b.dose);
    if (values.some(row => Object.values(row).some(value => !Number.isFinite(value))))
      throw new Error(T('曲线数值超出可显示范围。'));
    const scaleCode = exponentiate ? 'ratio' : result.analysis_scale;
    const scaleLabel = exponentiate ? T('比值尺度') : result.analysis_scale === 'log' ? T('对数尺度') : T('自然尺度');
    const citations = (result.method_sources || []).map(source => {
      const name = source.match(/^.+?\(\d{4}\)/)?.[0] || source.split(';')[0];
      const doi = source.match(/\bdoi:([^;\s]+)/i)?.[1].replace(/[.,;]+$/, '');
      return name + (doi ? ` · DOI ${doi}` : '');
    });
    const width = 640, height = 320 + citations.length * 18, left = 58, right = 620, top = 24, bottom = 220;
    const doses = values.map(row => row.dose), limits = values.flatMap(row => [row.low, row.high]);
    const minDose = Math.min(...doses), maxDose = Math.max(...doses);
    const minValue = Math.min(...limits), maxValue = Math.max(...limits);
    const x = value => left + (value - minDose) / (maxDose - minDose || 1) * (right - left);
    const y = value => bottom - (value - minValue) / (maxValue - minValue || 1) * (bottom - top);
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`); svg.setAttribute('width', '100%');
    svg.setAttribute('role', 'img'); svg.setAttribute('aria-labelledby', 'anDoseCurveTitle anDoseCurveDesc');
    const add = (tag, attributes, content) => {
      const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
      Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
      if (content !== undefined) element.textContent = content;
      svg.append(element); return element;
    };
    add('title', { id:'anDoseCurveTitle' }, T('非线性剂量反应：{0}',result.outcome));
    add('desc', { id:'anDoseCurveDesc' }, T('三结点限制性立方样条；{0}，{1}尺度。阴影表示均值曲线的点态 95% 置信区间。SVG 附带信息包含完整请求、模型、曲线和研究来源。请查阅并引用原始方法及研究报告。',result.measure,scaleLabel));
    add('metadata', { id:'anDoseCurveAudit', type:'application/json' }, JSON.stringify({
      request:body, result:{...result, studies:result.studies.map(row => ({...row,...anSourceFields(row.source_key)}))}, display_scale:scaleCode,
      display_transform:exponentiate ? 'exp' : 'identity',
    }));
    add('rect', { x:0, y:0, width, height, fill:'#fff' });
    add('path', { d:`M${left},${top}V${bottom}H${right}`, fill:'none', stroke:'#61727d' });
    const band = values.map(row => `${x(row.dose)},${y(row.high)}`).concat(
      values.slice().reverse().map(row => `${x(row.dose)},${y(row.low)}`)).join(' ');
    add('polygon', { points:band, fill:'#dcefeb', opacity:.9 });
    add('polyline', { points:values.map(row => `${x(row.dose)},${y(row.mid)}`).join(' '),
      fill:'none', stroke:'#2f6f6a', 'stroke-width':2.5 });
    for (const [xPos, label] of [[left,minDose],[right,maxDose]])
      add('text', { x:xPos, y:bottom+20, fill:'#425762', 'font-size':12,
        'text-anchor':xPos === left ? 'start' : 'end' }, String(label));
    for (const [yPos, label] of [[bottom,minValue],[top,maxValue]])
      add('text', { x:left-6, y:yPos+4, fill:'#425762', 'font-size':12,
        'text-anchor':'end' }, Number(label).toPrecision(3));
    const axisKey = exponentiate ? '剂量（{0}）· 比值尺度' : result.analysis_scale === 'log' ? '剂量（{0}）· 对数尺度' : '剂量（{0}）· 自然尺度';
    add('text', { x:(left+right)/2, y:255, fill:'#425762', 'font-size':12,
      'text-anchor':'middle' }, T(axisKey,result.dose_unit));
    add('text', { x:58, y:278, fill:'#425762', 'font-size':10 },
      T('拟合方法：{0}；结点：{1}；参照剂量：{2}',result.fit?.method || T('未提供'),body.knots.join(', '),body.prediction_reference_dose ?? T('未指定')));
    if (citations.length) add('text', { x:58, y:296, fill:'#425762', 'font-size':10 }, T('方法来源：'));
    citations.forEach((source, i) => add('text', { x:58, y:313 + i * 18, fill:'#425762', 'font-size':10 }, source));
    add('text', { x:58, y:height-9, fill:'#425762', 'font-size':10 },
      T('请核对并引用原始方法；输入数据和参考信息已保存在 SVG 中。'));
    host.append(el('p', { class:'hint', text:T('{0} · {1} 项研究 · 结点 {2} · {3} 尺度',result.measure,result.studies?.length || 0,result.basis?.knots?.join(', ') || '',scaleLabel) }), svg);
    const details = el('details', {}, el('summary', { text:T('曲线数值与假设') }));
    details.append(el('pre', { class:'hint', lang:'en', text:JSON.stringify({ curve:result.curve, fit:result.fit,
      limitations:result.limitations, studies:result.studies }, null, 2) }));
    host.append(details, ...(result.method_sources || []).map(text => el('p', { class:'hint', lang:'en', text })),
      el('p', { class:'hint', text:T('请查阅方法原文；发表时引用实际使用的计算方法和来源报告。') }),
      anAuditReportButtons(taskApi() + '/analysis/dose/nonlinear', null,
        'reviewflow_dose_nonlinear_audit', () => ticket === generation, body));
    exportButton.disabled = false;
  }

  button.addEventListener('click', async () => {
    clear(); const ticket = generation;
    try {
      const knots = ['anDoseKnot1','anDoseKnot2','anDoseKnot3'].map(anValue);
      if (knots.some(value => value === '')) throw new Error(T('请填写预先指定的 3 个结点。'));
      const reference = anValue('anDosePlotReference');
      const body = { outcome:anValue('anOutcome'), timepoint:anValue('anTimepoint'),
        measure:anValue('anMeasure'), knots:knots.map(Number),
        prediction_reference_dose:reference === '' ? null : Number(reference) };
      if (!body.measure) throw new Error(T('拟合剂量反应前，请选择效应指标。'));
      button.disabled = true;
      const started = performance.now();
      const progress = () => { if (ticket === generation) host.textContent = T('正在拟合非线性剂量反应…已等待 {0} 秒。',((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress,500);
      const result = await api(taskApi() + '/analysis/dose/nonlinear', { method:'POST', body });
      if (ticket !== generation) return;
      lastResult = result; lastBody = body;
      render(result, body, ticket);
    } catch (error) { if (ticket === generation) { clear(); const known=['非线性模型未返回可用曲线。','曲线数值超出可显示范围。','请填写预先指定的 3 个结点。','拟合剂量反应前，请选择效应指标。'].some(key=>T(key)===error.message); host.replaceChildren(el('span',{text:error.message,...(known?{}:{lang:'en'})})); toast(error.message, 'warn'); } }
    finally { if (ticket === generation) { clearInterval(timer); button.disabled = false; } }
  });
  exportButton.addEventListener('click', () => {
    const svg = host.querySelector('svg'); if (!svg) return;
    const blob = new Blob([new XMLSerializer().serializeToString(svg)], { type:'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(blob), link = el('a', { href:url, download:'reviewflow_dose_response.svg' });
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
