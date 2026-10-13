/* Approved M2 mobile interactions. Original nodes, save handlers and APIs retained. */
(function(){
  'use strict';
  const mobile=matchMedia('(max-width:760px)'),reduced=matchMedia('(prefers-reduced-motion:reduce)');
  const q=(s,r=document)=>r?.querySelector(s),qa=(s,r=document)=>r?[...r.querySelectorAll(s)]:[];
  const main=q('#appMain'),view=q('#appMain>.view'),top=q('#appMain>.top');if(!main||!view)return;
  const tr=key=>window.RFMobileLabels?.[key]?.[RFLang.get()]||T(key);
  const wrappers=new Map(),uploads=new Map(),filterLabels=new Map(),pdfToolbars=new Map(),searches=new Map(),searchDrafts=new Map(),hidden=new Map(),statuses=new Map(),animations=new Set(),closingDialogs=new WeakSet();
  const toolbarObserver=new ResizeObserver(entries=>{if(!mobile.matches)return;for(const {target} of entries){const height=target.getBoundingClientRect().height;if(height>0&&target.parentElement)target.parentElement.style.setProperty('--rf-mobile-pdf-toolbar-height',height+'px');}});
  let work=0,lastScroll=0,travel=0,menuAnimation=null,menuClosing=false,tabRect=null,tabAnimation=null;
  const context=()=>[S.user?.user_id,S.task?.task_id,S.screener].join('|');
  let lastContext=context();
  function element(tag,cls,text){const n=document.createElement(tag);n.className=cls;if(text)n.textContent=tr(text);return n;}
  function animate(n,keys,options){if(reduced.matches||!n?.animate)return null;const a=n.animate(keys,options);animations.add(a);a.addEventListener('finish',()=>animations.delete(a),{once:true});a.addEventListener('cancel',()=>animations.delete(a),{once:true});return a;}
  function wrapDetails(){
    for(const d of qa('.page details,.rf-mobile-sheet details',main)){
      if(wrappers.has(d)){const r=wrappers.get(d);if(!r.body.getAnimations().length)r.summary.setAttribute('aria-expanded',String(d.open));continue;}const summary=q(':scope>summary',d);if(!summary)continue;
      const body=element('div','rf-mobile-inline-body');for(const n of [...d.childNodes])if(n!==summary)body.append(n);d.append(body);
      wrappers.set(d,{body,summary,aria:summary.getAttribute('aria-expanded')});summary.setAttribute('aria-expanded',String(d.open));
    }
  }
  function items(record){return qa(record.selector,record.host).filter(n=>!n.closest('.rf-mobile-search-line'));}
  function clearFilter(record){for(const n of items(record))if(hidden.has(n)){n.hidden=hidden.get(n);hidden.delete(n);}record.empty.hidden=true;}
  function filter(record){
    if(record.remote)return;
    const text=record.input.value.trim().toLocaleLowerCase();let shown=0;
    for(const n of items(record)){if(!hidden.has(n))hidden.set(n,n.hidden);const allowed=!hidden.get(n);const match=!text||n.textContent.toLocaleLowerCase().includes(text);n.hidden=!allowed||!match;if(allowed&&match)shown++;}
    record.empty.textContent=tr('No matching records');record.empty.hidden=!text||shown>0;
  }
  function search(host,selector,existing){
    if(!host||searches.has(host)||(host.id==='researchTracePage'&&!existing))return;
    const line=element('div','rf-mobile-search-line'),box=element('div','rf-mobile-search');line.append(box);
    const open=element('button',''),close=element('button','rf-mobile-search-close');open.type=close.type='button';open.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10" cy="10" r="6.5"/><path d="m15 15 5 5"/></svg>';close.textContent='×';
    const input=existing||element('input','input');input.type='search';let anchor=null;
    if(existing){anchor=document.createComment('mobile search anchor');existing.before(anchor);}box.append(open,input,close);
    const empty=element('p','rf-mobile-search-empty');empty.hidden=true;
    let before=q('.timeline,.trace-table',host)?.closest('.trace-panel')||q('.help-layout,.card',host);
    while(before&&before.parentElement!==host)before=before.parentElement;
    host.insertBefore(line,before||host.firstChild);line.after(empty);
    const record={host,line,box,open,close,input,empty,selector,anchor,existing,placeholder:input.getAttribute('placeholder'),aria:input.getAttribute('aria-label'),listener:null,remote:host.id==='researchTracePage'&&Boolean(existing)};
    const draft=searchDrafts.get(host);if(draft){input.value=draft.query;box.classList.toggle('open',draft.open);searchDrafts.delete(host);}
    record.listener=()=>filter(record);input.addEventListener('input',record.listener,true);
    open.addEventListener('click',()=>{if(record.remote&&box.classList.contains('open')){q('.trace-filters button',host)?.click();return;}box.classList.add('open');setTimeout(()=>{if(box.isConnected&&mobile.matches)input.focus({preventScroll:true});},reduced.matches?0:360);});
    close.addEventListener('click',()=>{box.classList.remove('open');open.focus({preventScroll:true});});searches.set(host,record);
  }
  function decorate(){
    work=0;if(!mobile.matches)return;
    observer.disconnect();try{
      document.body.classList.add('rf-mobile-motion');for(const [d] of wrappers)if(!d.isConnected)wrappers.delete(d);for(const [n] of hidden)if(!n.isConnected)hidden.delete(n);wrapDetails();
      // Trace and task panels can replace their contents after a request completes.
      for(const [host,r] of searches)if(!host.isConnected||!r.line.isConnected){if(host.isConnected)searchDrafts.set(host,{query:r.input.value,open:r.box.classList.contains('open')});clearFilter(r);r.input.removeEventListener('input',r.listener,true);r.empty.remove();searches.delete(host);}
      for(const [input] of uploads)if(!input.isConnected)uploads.delete(input);
      for(const [input] of filterLabels)if(!input.isConnected)filterLabels.delete(input);
      for(const toolbar of qa('.pdf-toolbar',main))if(!pdfToolbars.has(toolbar)){
        const wrap=toolbar.parentElement;pdfToolbars.set(toolbar,{wrap,value:wrap.style.getPropertyValue('--rf-mobile-pdf-toolbar-height'),priority:wrap.style.getPropertyPriority('--rf-mobile-pdf-toolbar-height')});toolbarObserver.observe(toolbar);
      }
      for(const input of qa('.trace-filters input[type=date]')){
        if(!filterLabels.has(input)){const label=element('label','rf-mobile-trace-filter'),text=element('span','');input.before(label);label.append(text,input);filterLabels.set(input,{label,text});}
        filterLabels.get(input).text.textContent=input.getAttribute('aria-label')||'';
      }
      for(const input of qa('#cbForms .wf-evidence input[type=file]')){
        if(!uploads.has(input)){const label=element('label','rf-mobile-evidence-upload'),text=element('span','');input.before(label);label.append(text,input);uploads.set(input,{label,text});}
        uploads.get(input).text.textContent=input.getAttribute('aria-label')||T('Upload evidence images');
      }
      for(const [id,key] of [['screening','Screening'],['coding','Coding'],['appearance','Appearance']]){const tab=q('#wf-tab-'+id);if(tab&&tab.textContent!==tr(key))tab.textContent=tr(key);}
      search(q('.page[data-p=help]'),'.rf-mobile-help-topic',q('.rf-mobile-help-search'));
      search(q('#researchTracePage'),'.trace-table tbody tr',q('#researchTracePage .trace-filters input[type=search]'));
      search(q('.page[data-p=import]'),'.wf-import-record,.rf-mobile-report,.import-history .card');
      search(q('#wf-panel-tasks'),'.team-member,.rf-mobile-task');
      for(const r of searches.values()){
        r.open.setAttribute('aria-label',tr('Search'));r.close.setAttribute('aria-label',tr('Close search'));const searchLabel=r.remote?T('文献标题或标识'):tr('Search this page');r.input.placeholder=searchLabel;r.input.setAttribute('aria-label',searchLabel);
        if(r.input.value)filter(r);
      }
      for(const d of qa('.rf-mobile-sheet'))if(!d.dataset.motionBound){d.dataset.motionBound='true';d.addEventListener('toggle',()=>{if(mobile.matches&&d.open)animate(d,[{transform:'translateY(100%)',opacity:.4},{transform:'translateY(0)',opacity:1}],{duration:360,easing:'cubic-bezier(.2,.9,.22,1)'});});d.addEventListener('cancel',e=>{if(mobile.matches){e.preventDefault();closeDialog(d);}});}
      updateTab(false);
    }finally{observer.observe(view,{childList:true,subtree:true});}
  }
  function schedule(){if(!work&&mobile.matches)work=requestAnimationFrame(decorate);}
  function updateTab(moving){
    if(!mobile.matches)return;const tabs=q('.wf-tabs'),target=q('[aria-selected=true]',tabs),indicator=q('.wf-tab-indicator',tabs);if(!target||!indicator||!target.getClientRects().length)return;
    const parent=tabs.getBoundingClientRect(),rect=target.getBoundingClientRect(),next={left:rect.left-parent.left+tabs.scrollLeft,width:rect.width};
    if(tabRect&&Math.abs(next.left-tabRect.left)<.5&&Math.abs(next.width-tabRect.width)<.5)return;
    const start=tabRect||next;tabRect=next;
    // Cancel only indicator presentation animations; activation and handlers are untouched.
    indicator.getAnimations().forEach(a=>a.cancel());indicator.style.top=(tabs.clientHeight-3)+'px';indicator.style.left=next.left+'px';indicator.style.width=next.width+'px';
    if(!moving||reduced.matches)return;
    const ease=x=>{x=Math.max(0,Math.min(1,x));let lo=0,hi=1,u=x;for(let i=0;i<12;i++){u=(lo+hi)/2;const px=3*(1-u)*(1-u)*u*.2+3*(1-u)*u*u*.22+u*u*u;if(px<x)lo=u;else hi=u;}return 3*(1-u)*(1-u)*u*.9+3*(1-u)*u*u+u*u*u;};
    const leftward=next.left<start.left,frames=[];
    for(let ms=0;ms<=460;ms+=10){const lead=ease(ms/360),tail=ease((ms-100)/360);let l=start.left+(next.left-start.left)*(leftward?lead:tail),r=start.left+start.width+(next.left+next.width-start.left-start.width)*(leftward?tail:lead);const over=Math.sin(Math.PI*Math.max(0,Math.min(1,(ms-300)/160)))*3;if(leftward)l-=over;else r+=over;frames.push({left:l+'px',width:Math.max(2,r-l)+'px',offset:ms/460});}
    frames[0]={left:start.left+'px',width:start.width+'px',offset:0};frames[frames.length-1]={left:next.left+'px',width:next.width+'px',offset:1};tabAnimation=animate(indicator,frames,{duration:460});
  }
  const tabsObserver=new MutationObserver(()=>updateTab(true));if(q('.wf-tabs'))tabsObserver.observe(q('.wf-tabs'),{attributes:true,subtree:true,attributeFilter:['aria-selected']});
  function closeDialog(d){if(closingDialogs.has(d))return;closingDialogs.add(d);const finish=()=>{closingDialogs.delete(d);if(d.open)d.close();};const a=animate(d,[{transform:'translateY(0)',opacity:1},{transform:'translateY(100%)',opacity:.4}],{duration:360,easing:'cubic-bezier(.2,.9,.22,1)'});if(a)a.onfinish=finish;else finish();}
  function navigation(open){
    const sheet=q('#rfNavigationSheet');if(!sheet)return;
    menuAnimation?.cancel();menuClosing=!open;
    if(open){RFNavigationSheet.setOpen(true,{immediate:true});menuAnimation=animate(sheet,[{transform:'translateY(100%)',opacity:.4},{transform:'translateY(0)',opacity:1}],{duration:360,easing:'cubic-bezier(.2,.9,.22,1)'});
      qa('#appShell .nav button',sheet).forEach((n,i)=>animate(n,[{opacity:0,transform:'translateY(4px)'},{opacity:1,transform:'translateY(0)'}],{duration:180,delay:300+i*66,fill:'backwards'}));q('#rfSidebarToggle')?.focus({preventScroll:true});
    }else{const finish=()=>{RFNavigationSheet.setOpen(false,{immediate:true});menuClosing=false;q('#rfMobileNavigation')?.focus({preventScroll:true});};menuAnimation=animate(sheet,[{transform:'translateY(0)',opacity:1},{transform:'translateY(100%)',opacity:.4}],{duration:360,easing:'cubic-bezier(.2,.9,.22,1)'});if(menuAnimation)menuAnimation.onfinish=finish;else finish();}
  }
  document.addEventListener('click',event=>{
    if(!mobile.matches)return;
    if(event.target.closest('[data-undo-stage]')){statuses.delete(S.page);q('.page.on .rf-mobile-save-feedback')?.remove();}
    const button=event.target.closest('#rfMobileNavigation,#rfSidebarToggle');
    if(button){event.preventDefault();event.stopImmediatePropagation();navigation(menuClosing||q('#rfSidebarToggle').getAttribute('aria-expanded')!=='true');return;}
    const dialog=event.target.closest('.rf-mobile-sheet[open]');if(dialog){const r=dialog.getBoundingClientRect(),outside=event.target===dialog&&(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom);if(outside||event.target.closest('.rf-mobile-sheet>header>button')){event.preventDefault();event.stopImmediatePropagation();closeDialog(dialog);return;}}
    const summary=event.target.closest('summary'),d=summary?.parentElement,record=wrappers.get(d);if(!record)return;
    event.preventDefault();const {body}=record,opening=!d.open;body.getAnimations().forEach(a=>a.cancel());const height=body.getBoundingClientRect().height;if(opening)d.open=true;
    summary.setAttribute('aria-expanded',String(opening));const a=animate(body,[{height:height+'px',opacity:opening?.4:1},{height:(opening?body.scrollHeight:0)+'px',opacity:opening?1:.4}],{duration:360,easing:'cubic-bezier(.2,.9,.22,1)'});
    if(a)a.onfinish=()=>{if(!opening)d.open=false;};else d.open=opening;
  },true);
  document.addEventListener('pointerdown',event=>{if(mobile.matches&&!menuClosing&&q('#rfSidebarToggle')?.getAttribute('aria-expanded')==='true'&&!q('#rfNavigationSheet').contains(event.target)){event.stopImmediatePropagation();navigation(false);}},true);
  document.addEventListener('keydown',event=>{if(mobile.matches&&event.key==='Escape'&&q('#rfSidebarToggle')?.getAttribute('aria-expanded')==='true'){event.preventDefault();event.stopImmediatePropagation();navigation(false);}},true);
  view.addEventListener('scroll',()=>{
    const y=view.scrollTop,delta=y-lastScroll;lastScroll=y;
    if(!mobile.matches||!['screen','fulltext','bench','import','help','trace'].includes(S.page)||main.contains(document.activeElement)&&document.activeElement.matches('input,textarea,select'))return;
    if(y<20){top.classList.remove('rf-mobile-compact');travel=0;return;}travel=delta>0?Math.max(0,travel)+delta:Math.min(0,travel)+delta;if(travel>60)top.classList.add('rf-mobile-compact');else if(travel< -15)top.classList.remove('rf-mobile-compact');
  },{passive:true});
  function writeStage(path){if(/\/stage2\/decisions(?:\?|$)/.test(path))return 'fulltext';if(/\/decisions(?:\?|$)/.test(path))return 'screen';if(/\/coding\/(values-batch|submission-evidence)(?:\/|\?|$)/.test(path))return 'bench';if(/\/(workflow-settings|reason-library|sources)(?:\/|\?|$)/.test(path))return S.page;return null;}
  function statusHost(stage){const host=q('.page[data-p="'+stage+'"]');return stage==='screen'?q('.scr-decisions',host):stage==='fulltext'?q('.ft-decide',host):stage==='bench'?q('.rf-mobile-coding-footer',host):host;}
  function paintStatus(record){
    if(!mobile.matches||record.scope!==context())return;const host=statusHost(record.stage);if(!host)return;
    let box=q(':scope>.rf-mobile-save-feedback',host);if(!box){box=element('div','rf-mobile-save-feedback');box.setAttribute('role','status');box.setAttribute('aria-live','polite');host.append(box);}box.dataset.status=record.status;box.replaceChildren();
    const label=element('span','rf-mobile-save-label');box.append(label);
    if(record.status==='saving'){label.append(element('i','rf-mobile-saving-ring'),document.createTextNode(tr('Saving…')));return;}
    if(record.status==='saved'){label.innerHTML='<svg class="rf-mobile-save-check" viewBox="0 0 24 24" aria-hidden="true"><path d="m4 12 5 5L20 6"/></svg>';label.append(document.createTextNode(record.message||tr('Saved')));return;}
    label.textContent=tr('Not saved. Your entries are retained.');if(record.error)label.append(document.createTextNode(' '+record.error));
    if(record.stage==='bench'){const retry=element('button','', 'Retry');retry.type='button';retry.addEventListener('click',()=>{if(record.scope===context()&&S.page==='bench'&&WorkflowControls.activeItem('coding')?.key===record.key)q('#cbSave')?.click();});retry.disabled=WorkflowControls.activeItem('coding')?.key!==record.key;box.append(retry);}
  }
  // Observational only: requests, body, authentication, errors and return values stay unchanged.
  window.RFMobileMotion=Object.freeze({beginWrite(path,opts){
    try{if(!mobile.matches)return null;const stage=writeStage(path);if(!stage||stage!==S.page)return null;
      const record={stage,scope:context(),status:'saving',key:opts.body?.zotero_key,error:''};statuses.set(stage,record);paintStatus(record);
      return {finish(ok,result){try{if(statuses.get(stage)!==record)return;record.status=ok?'saved':'error';record.error=ok?'':result?.message||'';record.message=ok&&stage==='bench'?T(result?.complete==null?'Evidence saved':result.complete?'Saved — complete':'Saved as draft — required values or evidence are missing'):tr('Saved');paintStatus(record);}catch(_){/* Presentation must never affect a save. */}}};
    }catch(_){return null;}
  }});
  main.addEventListener('input',event=>{if(!mobile.matches||!event.target.matches('#cbForms .cb-v,#cbForms .wf-note'))return;const record=statuses.get('bench');if(record?.status==='saved'){statuses.delete('bench');q('.rf-mobile-coding-footer>.rf-mobile-save-feedback')?.remove();}});
  function cleanup(){
    observer.disconnect();cancelAnimationFrame(work);work=0;for(const a of animations)a.cancel();animations.clear();menuAnimation=null;menuClosing=false;tabAnimation=null;tabRect=null;
    top.classList.remove('rf-mobile-compact');document.body.classList.remove('rf-mobile-motion');lastScroll=travel=0;
    for(const tab of qa('.wf-tabs [data-wf-label]'))tab.textContent=T(tab.dataset.wfLabel);
    for(const [d,r] of wrappers){if(r.body.isConnected)r.body.replaceWith(...r.body.childNodes);if(r.aria===null)r.summary.removeAttribute('aria-expanded');else r.summary.setAttribute('aria-expanded',r.aria);}wrappers.clear();
    for(const [input,r] of uploads)if(r.label.isConnected)r.label.replaceWith(input);uploads.clear();
    for(const [input,r] of filterLabels)if(r.label.isConnected)r.label.replaceWith(input);filterLabels.clear();
    toolbarObserver.disconnect();for(const {wrap,value,priority} of pdfToolbars.values()){if(value)wrap.style.setProperty('--rf-mobile-pdf-toolbar-height',value,priority);else wrap.style.removeProperty('--rf-mobile-pdf-toolbar-height');}pdfToolbars.clear();
    for(const r of searches.values()){clearFilter(r);r.input.removeEventListener('input',r.listener,true);if(r.anchor?.isConnected)r.anchor.replaceWith(r.input);if(r.existing){if(r.placeholder===null)r.input.removeAttribute('placeholder');else r.input.setAttribute('placeholder',r.placeholder);if(r.aria===null)r.input.removeAttribute('aria-label');else r.input.setAttribute('aria-label',r.aria);}r.line.remove();r.empty.remove();}searches.clear();
    for(const [n,value] of hidden)if(n.isConnected)n.hidden=value;hidden.clear();searchDrafts.clear();qa('.rf-mobile-save-feedback').forEach(n=>n.remove());statuses.clear();
  }
  const observer=new MutationObserver(schedule);
  document.addEventListener('reviewflow:workspace-changed',()=>{top.classList.remove('rf-mobile-compact');lastScroll=travel=0;if(mobile.matches){menuAnimation?.cancel();menuClosing=false;RFNavigationSheet.setOpen(false,{immediate:true});}schedule();});
  document.addEventListener('reviewflow:task-context-changed',()=>{if(lastContext!==context()){lastContext=context();statuses.clear();searchDrafts.clear();qa('.rf-mobile-save-feedback').forEach(n=>n.remove());for(const r of searches.values()){r.input.value='';clearFilter(r);}}schedule();});
  document.addEventListener('reviewflow:language-changed',()=>{tabRect=null;for(const r of statuses.values())paintStatus(r);schedule();});
  document.addEventListener('reviewflow:coding-form-loaded',schedule);
  window.addEventListener('resize',()=>{if(mobile.matches){tabRect=null;schedule();}});
  reduced.addEventListener('change',()=>{for(const a of animations)a.finish();});
  mobile.addEventListener('change',()=>{if(mobile.matches)schedule();else cleanup();});
  if(mobile.matches)decorate();
})();
