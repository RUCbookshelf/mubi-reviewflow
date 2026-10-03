/* Original uploads belong to the authenticated task/screener on the server. */
(function(){'use strict';
 const page=document.querySelector('section[data-p="import"]');if(!page)return;
 const panel=el('section',{class:'card import-history'}),head=el('div',{class:'row'}),title=el('h3',{text:T('导入历史')}),refresh=el('button',{type:'button',class:'btn b-out',text:T('刷新历史')}),body=el('div',{'aria-live':'polite'});
 head.append(title,refresh);panel.append(head,el('p',{class:'hint',text:T('查看每次上传的原文件与导入结果。')}),el('p',{class:'hint',text:T('原文件已保存，可下载核对；仅包含启用此功能后的导入。')}),body);page.append(panel);
 let version=0,offset=0;
 const text=(tag,value)=>{const n=document.createElement(tag);n.textContent=String(value??'');return n;};
 async function load(){const ticket=++version;body.replaceChildren();
  if(offset===0){$('#reportBody').replaceChildren();$('#batchFilesBody').replaceChildren();$('#crossDupBody').replaceChildren();$('#batchFilesWrap').style.display='none';$('#crossDupWrap').style.display='none';$('#goScreenBtn').style.display='none';$('#repHint').textContent=T('正在加载…');}
  if(!S.task){$('#repHint').textContent=T('请先选择任务');return;}
  const taskId=S.task.task_id,screener=S.screener,url=taskApi()+'/import-history',query=scrQ(),historyQuery=query+(query?'&':'?')+'limit=20&offset='+offset;body.textContent=T('正在加载…');
  try{const result=await api(url+historyQuery);if(ticket!==version||S.task?.task_id!==taskId||S.screener!==screener)return;body.replaceChildren();
   if(offset===0&&result.imports?.length){renderImportReport(result.imports[0].result||{});$('#goScreenBtn').style.display='';}
   if(!result.imports?.length){body.textContent=T('尚无导入历史');
    if(offset===0){const progress=await api(taskApi()+'/progress'+query);if(ticket!==version||S.task?.task_id!==taskId||S.screener!==screener)return;
     if(progress.stage1?.total>0){$('#reportBody').replaceChildren(el('tr',{},el('td',{text:T('进入筛选（非重复总数）')}),el('td',{},el('b',{text:String(progress.stage1.total)}))));$('#repHint').textContent=T('当前任务')+' · '+T('尚无导入历史');$('#goScreenBtn').style.display='';}else $('#repHint').textContent=T('尚未导入 —— 选择当前任务与筛选员后，点击上方上传区开始。');}
    return;}

   for(const batch of result.imports){const section=document.createElement('section');section.className='import-history-batch';const date=new Date(batch.created_at);section.append(text('h4',Number.isNaN(date.getTime())?batch.created_at:date.toLocaleString(RFLang.get())));
    const r=batch.result||{},parsed=r.total_parsed??r.n_parsed,imported=r.imported??r.n_imported,duplicates=r.n_duplicates??((r.exact_duplicates||0)+(r.fuzzy_duplicates||0));
    section.append(text('p',[parsed==null?null:T('解析')+': '+parsed,imported==null?null:T('导入')+': '+imported,T('重复')+': '+duplicates].filter(Boolean).join(' · ')));
    const list=document.createElement('ul');for(const file of batch.files||[]){const line=document.createElement('li'),name=text('span',file.name+' · '+new Intl.NumberFormat(RFLang.get(),{maximumFractionDigits:1}).format(file.size/1024)+' KB'),download=el('button',{type:'button',class:'btn b-out',text:T('下载原文件')});download.setAttribute('aria-label',T('下载原文件')+' '+file.name);
     download.addEventListener('click',async()=>{download.disabled=true;try{const blob=await api(url+'/'+encodeURIComponent(batch.id)+'/files/'+encodeURIComponent(file.id)+query,{blob:true});const object=URL.createObjectURL(blob),a=document.createElement('a');a.href=object;a.download=file.name;a.click();setTimeout(()=>URL.revokeObjectURL(object),1000);}catch(e){toast(T('导入失败：')+e.message,'warn');}finally{download.disabled=false;}});
     line.append(name,download);const hash=text('small',T('文件校验值')+' (SHA-256): '+file.sha256);hash.className='hint';line.append(hash);list.append(line);}
    section.append(list);body.append(section);
   }
   const footer=el('div',{class:'row'}),first=el('button',{type:'button',class:'btn b-out',text:T('返回首批')}),next=el('button',{type:'button',class:'btn b-out',text:T('下一批')});first.disabled=offset===0;next.disabled=result.next_offset==null;first.addEventListener('click',()=>{offset=0;load();});next.addEventListener('click',()=>{offset=result.next_offset;load();});footer.append(first,next);body.append(footer);
  }catch(e){if(ticket!==version||S.task?.task_id!==taskId||S.screener!==screener)return;body.textContent=T('历史加载失败，请重试。');if(offset===0)$('#repHint').textContent=T('历史加载失败，请重试。');}
 }
 refresh.addEventListener('click',load);
 document.addEventListener('reviewflow:import-completed',()=>{offset=0;load();});
 document.addEventListener('reviewflow:workspace-changed',e=>{if(e.detail==='import')load();});
 document.addEventListener('reviewflow:task-context-changed',()=>{version++;offset=0;body.replaceChildren();if(S.page==='import')load();});
 document.addEventListener('reviewflow:language-changed',()=>{title.textContent=T('导入历史');refresh.textContent=T('刷新历史');if(S.page==='import')load();});
 if(S.page==='import')load();
})();
