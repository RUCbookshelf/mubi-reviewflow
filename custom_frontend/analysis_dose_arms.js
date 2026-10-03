(function () {
  const panel = $('#anDoseArmsPanel'), host = $('#anDoseArmsResult'), save = $('#anDoseArmsSave');
  const add = $('#anDoseArmsAdd'), download = $('#anDoseArmsExport');
  let bodyRows, columns = [], generation = 0, activeRequest = 0, saveTimer = null, record = null, pending = false;
  function clear() { generation++; record = null; download.disabled = true; save.disabled = pending; if (!pending) host.replaceChildren(); }
  function renderRecord() {
    if (!record) return;
    const {request:body,result} = record;
    const covarianceKey = result.calculation.covariance_scale === 'log'
      ? '已保存研究 {0} 的 {1} 剂量曲线（原始组别 {2} 组）。协方差使用对数尺度。合并前请选择模型。'
      : '已保存研究 {0} 的 {1} 剂量曲线（原始组别 {2} 组）。协方差使用自然尺度。合并前请选择模型。';
    host.replaceChildren(el('p',{text:T(covarianceKey,body.study_id,body.measure,body.arms.length)}),
      el('pre',{lang:'en',text:JSON.stringify({contrasts:result.calculation.contrasts,covariance:result.calculation.covariance},null,2)}));
    for (const text of result.input_data.method_sources || []) host.append(el('p',{class:'hint',lang:'en',text}));
  }
  window.RFRefreshDoseArmsText = renderRecord;
  function addRow() {
    if (!bodyRows || bodyRows.children.length >= 101) return;
    clear(); const row = el('tr');
    for (const [key,label] of columns) row.append(el('td',{},el('input',{class:'input',type:'number',step:['n','events','total'].includes(key) ? '1' : 'any','data-dose-arm-key':key,'aria-label':label})));
    row.append(el('td',{},el('button',{class:'btn',type:'button',text:T('移除'),onclick:() => {
      if (bodyRows.children.length <= 3) { host.textContent = T('至少保留 3 个剂量组。'); return; }
      clear(); row.remove();
    }}))); bodyRows.append(row);
  }
  $('#anDoseArmsMeasure').addEventListener('change',() => {
    clear(); const measure = anValue('anDoseArmsMeasure');
    $('#anDoseArmsTimeLabel').hidden = measure !== 'RATE_RATIO';
    const holder = $('#anDoseArmsTable'); holder.replaceChildren(); bodyRows = null; add.disabled = !measure;
    if (!measure) return;
      columns = [['dose','剂量'], ...(measure === 'MD' ? [['n','样本量'],['mean','均值'],['sd','标准差']] :
      [['events','事件数'],[measure === 'RATE_RATIO' ? 'person_time' : 'total',measure === 'RATE_RATIO' ? '人时' : '总人数']])];
    const table = el('table'); bodyRows = el('tbody');
    table.append(el('caption',{text:T('各组原始摘要（包括参照组）')}),el('thead',{},el('tr',{},...[...columns.map(item => T(item[1])),'操作'].map(text => el('th',{scope:'col',text:T(text)})))),bodyRows);
    holder.append(table); for (let i=0;i<3;i++) addRow();
  });
  add.addEventListener('click',addRow);
  panel.addEventListener('input',clear);
  const ids = ['anEffectStudy','anOutcome','anTimepoint','anSource','anSourceLocator','anDoseReference','anDoseUnit'];
  ids.forEach(id => $('#'+id).addEventListener('input',clear));
  document.addEventListener('reviewflow:analysis-core-loaded',clear);
  document.addEventListener('reviewflow:analysis-task-changed', () => {
    activeRequest++; clearInterval(saveTimer); pending = false;
    ['anDoseArmsMeasure','anDoseArmsTimeUnit','anDoseReference','anDoseUnit',
      'anDoseContrasts','anDoseCovariance','anDoseModel'].forEach(id => $('#'+id).value = '');
    $('#anDoseArmsIndependent').checked = false;
    $('#anDoseArmsMeasure').dispatchEvent(new Event('change'));
    clear();
  });
  save.addEventListener('click',async () => {
    if (pending) return;
    clear(); const ticket = generation, taskId = S.task?.task_id, request = ++activeRequest;
    let timer;
    try {
      const measure = anValue('anDoseArmsMeasure');
      if (!measure || !$('#anDoseArmsIndependent').checked) throw new Error(T('选择组别数据类型，并确认各组参与者互相独立。'));
      if (!anValue('anDoseReference') || !anValue('anDoseUnit') || !anValue('anSourceLocator')) throw new Error(T('请填写参照剂量、剂量单位和来源表格或页码。'));
      const arms = Array.from(bodyRows.children,row => Object.fromEntries(columns.map(([key,label]) => {
        const raw = row.querySelector(`[data-dose-arm-key="${key}"]`).value.trim(), value = Number(raw);
        if (!raw || !Number.isFinite(value) || (['n','events','total'].includes(key) && !Number.isSafeInteger(value))) throw new Error(T('请为每组输入有效数值。'));
        return [key,value];
      })));
      const body = {study_id:anValue('anEffectStudy'),outcome:anValue('anOutcome'),timepoint:anValue('anTimepoint'),source_key:anValue('anSource'),source_locator:anValue('anSourceLocator'),measure,reference_dose:Number(anValue('anDoseReference')),dose_unit:anValue('anDoseUnit'),independent_arms:'confirmed',arms};
      if (measure === 'RATE_RATIO') body.person_time_unit = anValue('anDoseArmsTimeUnit');
      pending = true; save.disabled = true;
      const started = performance.now();
      const progress = () => { if (request === activeRequest) host.textContent = T(ticket === generation ? '正在保存组别剂量曲线…已等待 {0} 秒。' : '正在保存更改前的剂量输入…已等待 {0} 秒。',((performance.now()-started)/1000).toFixed(1)); };
      progress(); timer = setInterval(progress, 500); saveTimer = timer;
      const result = await api(taskApi()+'/analysis/dose/arm-curves',{method:'POST',body});
      if (request !== activeRequest) { toast(T('研究 {0} 的剂量曲线已保存到先前任务 {1}。',body.study_id,taskId), 'warn'); return; }
      if (ticket !== generation) {
        if (S.task?.task_id === taskId) host.textContent = T('已使用研究 {0} 先前的输入保存剂量曲线。再次保存前请检查曲线。',body.study_id);
        else toast(T('研究 {0} 的剂量曲线已保存到先前任务 {1}。',body.study_id,taskId), 'warn');
        return;
      }
      clearInterval(timer);
      $('#anMeasure').value = measure; $('#anMeasure').dispatchEvent(new Event('change'));
      $('#anDoseContrasts').value = JSON.stringify(result.calculation.contrasts);
      $('#anDoseCovariance').value = JSON.stringify(result.calculation.covariance);
      record = {request:body,result}; download.disabled = false;
      renderRecord();
    } catch (error) { if (request === activeRequest && S.task?.task_id === taskId) { const known=['至少保留 3 个剂量组。','选择组别数据类型，并确认各组参与者互相独立。','请填写参照剂量、剂量单位和来源表格或页码。','请为每组输入有效数值。'].some(key=>T(key)===error.message); host.replaceChildren(el('span',{text:error.message,...(known?{}:{lang:'en'})})); } }
    finally { clearInterval(timer); if (request === activeRequest) { pending = false; save.disabled = false; } }
  });
  download.addEventListener('click',() => {
    if (!record) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(record,null,2)],{type:'application/json'}));
    const link = el('a',{href:url,download:'reviewflow_dose_arm_audit.json'});
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
  });
})();
