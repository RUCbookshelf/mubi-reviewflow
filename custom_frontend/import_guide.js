/* Import onboarding reads existing progress; it never changes review decisions. */
(function(){'use strict';
 const main=document.getElementById('appMain');if(!main)return;
 const card=el('details',{id:'rfImportGuide',open:'','aria-label':T('引导-导入文献')});
 const summary=el('summary',{}),title=el('b',{}),count=el('span',{class:'rf-guide-count'}),chevron=el('span',{class:'rf-guide-chevron','aria-hidden':'true',text:'⌃'});
 const companion=document.querySelector('#alModeAI .rf-pearl-companion')?.cloneNode(true);
 if(companion){companion.classList.add('rf-pearl-guide');summary.append(companion);}
 summary.append(title,count,chevron);
 const retry=el('button',{type:'button',text:T('Retry / refresh'),onclick:load});
 const body=el('div',{class:'rf-guide-body'}),list=el('ol',{}),message=el('p',{class:'rf-guide-message',role:'status'});
 body.append(list,message,retry);card.append(summary,body);(main.querySelector('[data-p="import"]')||main).prepend(card);card.hidden=true;
 let context='',ticket=0,progress=null,imported=false,error=false,staged=false,reviewSummary=null;
 const session=new Map();
 function key(){return S.user&&S.task?JSON.stringify([S.user.username,S.task.task_id,S.screener||S.user.username]):'';}
 function saved(){if(!context)return {};try{return JSON.parse(localStorage.getItem('rf_import_guide:'+context)||'{}');}catch(_){return session.get(context)||{};}}
 function save(value){session.set(context,value);try{localStorage.setItem('rf_import_guide:'+context,JSON.stringify(value));}catch(_){}}
 function position(){const top=main.querySelector('.top');if(top)card.style.top=(top.getBoundingClientRect().bottom+12)+'px';}
 function row(label,done,action,detail){const item=el('li',{'data-complete':String(done)}),mark=el('span',{class:'rf-guide-mark','aria-hidden':'true',text:done?'✓':''}),button=el('button',{type:'button',text:label,onclick:action});if(detail)button.append(el('small',{class:'rf-guide-detail',text:detail}));button.setAttribute('aria-label',label+(detail?' · '+detail:'')+' · '+T(done?'已完成':'待完成'));item.append(mark,button);list.append(item);}
 async function focusArea(id){
  const expected=key();
  if(S.page!=='import'&&!(await go('import')))return;
  if(key()!==expected)return;
  if(id==='dropZone')window.ImportExportWorkspace?.showImport('report');
  if(['wf-import-attention','wf-import-activation','titleDupWrap'].includes(id))window.ImportExportWorkspace?.showImport('review');
  const target=document.getElementById(id)||(['wf-import-attention','wf-import-activation'].includes(id)?document.getElementById('wf-import-review'):null);if(!target)return;
  const area=target.getClientRects().length?target:document.querySelector('[data-p="import"]');
  area.setAttribute('tabindex','-1');area.scrollIntoView({block:'start',behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});area.focus({preventScroll:true});
  area.classList.remove('rf-guide-target');void area.offsetWidth;area.classList.add('rf-guide-target');
  setTimeout(()=>area.classList.remove('rf-guide-target'),1800);
 }
 async function focusReview(activation=false){
  const expected=key();
  if(S.page!=='import'&&!(await go('import')))return;
  if(key()!==expected)return;
  if(reviewSummary&&(reviewSummary.pending_records||reviewSummary.metadata_review_count||reviewSummary.pending_candidate_groups)&&window.WorkflowImports){
   let offset=0;
   while(true){
    const batches=await api(taskApi()+'/import-batches?limit=50&offset='+offset);
    if(key()!==expected||S.page!=='import')return;
    const batch=batches.batches?.find(b=>b.pending_records||b.metadata_review_count||b.pending_candidate_groups);
    if(batch){await WorkflowImports.open(batch.batch_id||batch.id);if(key()!==expected||S.page!=='import')return;break;}
    if(batches.next_offset==null)break;offset=batches.next_offset;
   }
   return focusArea(activation?'wf-import-activation':'wf-import-attention');
  }
  if(window.WorkflowImports){await WorkflowImports.open(null);return focusArea('wf-import-attention');}
  return focusArea('titleDupWrap');
 }
 function visitTrace(){if(!context)return;save({...saved(),traceVisited:true});render();}
 function render(){
  retry.hidden=!error;retry.textContent=T('Retry / refresh');
  const state=saved();
  const workflow=ImportReviewModel.guideState({imported,progress,summary:reviewSummary,traceVisited:state.traceVisited,error});
  const reviewed=workflow.done[1];
  card.hidden=!S.user||!S.task||S.page!=='import'||workflow.complete;
  document.body.classList.toggle('rf-import-guide-open',!card.hidden&&card.open);
  if(card.hidden)return;
  const taskLabels=[T('导入文献'),T('Review: {0} duplicate groups · {1} metadata changes',workflow.groups,workflow.metadata),T('Activate reviewed imports: {0} waiting',workflow.waiting),T('检查轨迹是否开启')];
  title.textContent=card.open?T('Import checklist'):taskLabels[workflow.done.findIndex(done=>!done)];card.setAttribute('aria-label',T('Import checklist'));
  count.textContent=T('{0}/4 completed',workflow.completed);
  summary.title=T(card.open?'收起为胶囊':'展开引导');chevron.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
  list.replaceChildren();
  row(T('导入文献'),imported,()=>focusArea('dropZone'));
  row(workflow.known?T('Review: {0} duplicate groups · {1} metadata changes',workflow.groups,workflow.metadata):T('Checking review progress…'),reviewed,()=>focusReview().catch(()=>{error=true;render();}),Number(progress?.candidate_groups||0)>0?T('Title checks: {0}/{1} groups resolved',Math.max(0,Number(progress.candidate_groups)-Number(progress.pending_groups||0)),progress.candidate_groups):null);
  row(workflow.known?T('Activate reviewed imports: {0} waiting',workflow.waiting):T('Checking activation progress…'),workflow.done[2],()=>focusReview(true).catch(()=>{error=true;render();}));
  row(T('检查轨迹是否开启'),!!state.traceVisited,async()=>{if(await go('trace'))visitTrace();});
  message.textContent=error?T('引导进度加载失败，请点击重试。'):T('Click to collapse into a capsule. Completed checklist items disappear automatically.');
  message.classList.toggle('rf-guide-error',error);position();
 }
 async function load(){
  const next=key();if(next!==context){context=next;progress=null;imported=false;error=false;staged=false;reviewSummary=null;card.open=!saved().collapsed;}
  const active=++ticket;render();if(!context||S.page!=='import')return;
  const base=taskApi(),query=scrQ();
  try{
   const [reviews,history,stages,summaryData]=await Promise.all([api(base+'/title-duplicate-reviews'+query+(query?'&':'?')+'limit=1&offset=0'),api(base+'/import-history'+query+(query?'&':'?')+'limit=1&offset=0'),api(base+'/progress'+query),api(base+'/import-review-summary')]);
   if(active!==ticket||key()!==context)return;
   staged=Number(summaryData.pending_records||0)>0;
   reviewSummary=summaryData;progress=reviews.progress||{};imported=Number(summaryData.source_records||0)>0||staged||history.imports?.some(batch=>Number(batch.result?.imported??batch.result?.n_imported??0)>0)||Number(stages.stage1?.total||0)>0||Number(progress.candidate_groups||0)>0;
   error=false;render();
  }catch(_){if(active!==ticket||key()!==context)return;error=true;render();}
 }
 card.addEventListener('toggle',()=>{if(context)save({...saved(),collapsed:!card.open});render();});
 document.addEventListener('click',event=>{if(event.target.closest('#appShell [data-nav="trace"]'))visitTrace();},true);
 document.addEventListener('reviewflow:workspace-changed',event=>{if(event.detail==='import')load();else{ticket++;render();}});
 document.addEventListener('reviewflow:task-context-changed',load);
 document.addEventListener('reviewflow:import-completed',load);
 document.addEventListener('reviewflow:title-review-progress',event=>{if(key()!==context||event.detail.taskId!==S.task?.task_id||event.detail.screener!==S.screener)return;progress=event.detail.progress;load();});
 document.addEventListener('reviewflow:language-changed',render);
 document.addEventListener('reviewflow:workflow-changed',event=>{if(String(event.detail?.task_id)===String(S.task?.task_id))load();});
 setInterval(()=>{if(!document.hidden&&S.user&&S.task&&S.page==='import')load();},30000);
 window.addEventListener('resize',position);
 if(window.ResizeObserver&&main.querySelector('.top'))new ResizeObserver(position).observe(main.querySelector('.top'));
 load();
})();
