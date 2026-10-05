/* Import onboarding reads existing progress; it never changes review decisions. */
(function(){'use strict';
 const main=document.getElementById('appMain');if(!main)return;
 const card=el('details',{id:'rfImportGuide',open:'','aria-label':T('引导-导入文献')});
 const summary=el('summary',{}),title=el('b',{}),count=el('span',{class:'rf-guide-count'}),chevron=el('span',{class:'rf-guide-chevron','aria-hidden':'true',text:'⌃'});
 summary.append(title,count,chevron);
 const body=el('div',{class:'rf-guide-body'}),list=el('ol',{}),message=el('p',{class:'rf-guide-message',role:'status'});
 body.append(list,message);card.append(summary,body);main.append(card);card.hidden=true;
 let context='',ticket=0,progress=null,imported=false,error=false;
 const session=new Map();
 function key(){return S.user&&S.task?JSON.stringify([S.user.username,S.task.task_id,S.screener||S.user.username]):'';}
 function saved(){if(!context)return {};try{return JSON.parse(localStorage.getItem('rf_import_guide:'+context)||'{}');}catch(_){return session.get(context)||{};}}
 function save(value){session.set(context,value);try{localStorage.setItem('rf_import_guide:'+context,JSON.stringify(value));}catch(_){}}
 function position(){card.style.top=(main.querySelector('.top').getBoundingClientRect().bottom+12)+'px';}
 function row(label,done,action){const item=el('li',{'data-complete':String(done)}),mark=el('span',{class:'rf-guide-mark','aria-hidden':'true',text:done?'✓':''}),button=el('button',{type:'button',text:label,onclick:action});button.setAttribute('aria-label',label+' · '+T(done?'已完成':'待完成'));item.append(mark,button);list.append(item);}
 function focusArea(id){
  if(S.page!=='import')go('import');
  const target=document.getElementById(id);if(!target)return;
  const area=target.getClientRects().length?target:document.querySelector('[data-p="import"]');
  area.setAttribute('tabindex','-1');area.scrollIntoView({block:'start',behavior:'smooth'});area.focus({preventScroll:true});
  area.classList.remove('rf-guide-target');void area.offsetWidth;area.classList.add('rf-guide-target');
  setTimeout(()=>area.classList.remove('rf-guide-target'),1800);
 }
 function visitTrace(){if(!context)return;save({...saved(),traceVisited:true});render();}
 function render(){
  const state=saved(),total=Number(progress?.candidate_groups||0),pending=Number(progress?.pending_groups||0),checked=Math.max(0,total-pending);
  const reviewed=imported&&progress!==null&&!error&&pending===0;
  card.hidden=!S.user||!S.task||S.page!=='import'||(imported&&reviewed&&state.traceVisited);
  if(card.hidden)return;
  title.textContent=T('引导-导入文献');card.setAttribute('aria-label',T('引导-导入文献'));
  count.textContent=T('{0}/3 已完成',Number(imported)+Number(reviewed)+Number(!!state.traceVisited));
  summary.title=T(card.open?'收起为胶囊':'展开引导');chevron.textContent=card.open?'⌃':'⌄';
  list.replaceChildren();
  row(T('导入文献'),imported,()=>focusArea('dropZone'));
  row(progress===null?T('人工核验：等待导入'):T('人工核验：{0}/{1} 组',checked,total),reviewed,()=>focusArea('titleDupWrap'));
  row(T('检查轨迹是否开启'),!!state.traceVisited,()=>{visitTrace();go('trace');});
  message.textContent=error?T('引导进度加载失败，请点击重试。'):!imported?T('导入后自动显示待核验组数。'):total===0&&progress!==null?T('没有需要人工核验的候选组。'):T('完成所有待办后，引导卡片自动关闭。');
  message.classList.toggle('rf-guide-error',error);position();
 }
 async function load(){
  const next=key();if(next!==context){context=next;progress=null;imported=false;error=false;card.open=!saved().collapsed;}
  const active=++ticket;render();if(!context||S.page!=='import')return;
  const base=taskApi(),query=scrQ();
  try{
   const [reviews,history,stages]=await Promise.all([api(base+'/title-duplicate-reviews'+query+(query?'&':'?')+'limit=1&offset=0'),api(base+'/import-history'+query+(query?'&':'?')+'limit=1&offset=0'),api(base+'/progress'+query)]);
   if(active!==ticket||key()!==context)return;
   progress=reviews.progress||{};imported=history.imports?.some(batch=>Number(batch.result?.imported??batch.result?.n_imported??0)>0)||Number(stages.stage1?.total||0)>0||Number(progress.candidate_groups||0)>0;
   error=false;render();
  }catch(_){if(active!==ticket||key()!==context)return;error=true;render();}
 }
 card.addEventListener('toggle',()=>{if(context)save({...saved(),collapsed:!card.open});render();});
 message.addEventListener('click',()=>{if(error)load();});
 document.addEventListener('click',event=>{if(event.target.closest('#appShell [data-nav="trace"]'))visitTrace();},true);
 document.addEventListener('reviewflow:workspace-changed',event=>{if(event.detail==='import')load();else{ticket++;render();}});
 document.addEventListener('reviewflow:task-context-changed',load);
 document.addEventListener('reviewflow:import-completed',load);
 document.addEventListener('reviewflow:title-review-progress',event=>{if(key()!==context||event.detail.taskId!==S.task?.task_id||event.detail.screener!==S.screener)return;progress=event.detail.progress;error=false;render();});
 document.addEventListener('reviewflow:language-changed',render);
 window.addEventListener('resize',position);
 new ResizeObserver(position).observe(main.querySelector('.top'));
 load();
})();
