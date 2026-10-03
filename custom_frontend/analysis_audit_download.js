/* Audit downloads recalculate from the captured analysis request. */
function anAuditReportButtons(path, params, prefix, isCurrent, body) {
  const row = el('div',{class:'row'});
  const taskId = S.task?.task_id, current = () => isCurrent() && S.task?.task_id === taskId;
  for (const format of ['docx','pptx','csv','tex']) {
    const button = el('button',{class:'btn b-out',type:'button',text:'Export '+(format === 'tex' ? 'LaTeX' : format.toUpperCase())+' audit'});
    button.addEventListener('click',async () => {
      if (!current()) return;
      const label = button.textContent;
      button.disabled = true;
      const started = performance.now();
      const progress = () => { if (current()) button.textContent = T("Preparing report… {0} s elapsed.", ((performance.now()-started)/1000).toFixed(1)); };
      progress();
      const timer = setInterval(progress, 500);
      try {
        const query = new URLSearchParams(params); query.set('format',format);
        const blob = await api(path+'?'+query,body ? {method:'POST',body,blob:true} : {blob:true});
        if (!current()) return;
        const url = URL.createObjectURL(blob), link = el('a',{href:url,download:prefix+'.'+format});
        document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
      } catch (error) { if (current()) toast(error.message,'warn'); }
      finally { clearInterval(timer); button.textContent = label; button.disabled = !current(); }
    });
    row.append(button);
  }
  return row;
}
