/* Workflow policy, source-scoped queues and draft-preserving refresh. */
(function () {
  'use strict';
  const W = window.WorkflowControls = {};
  const stages = ['screen', 'fulltext', 'coding'];
  const stagePage = {screen:'screen', fulltext:'fulltext', coding:'bench'};
  const selected = {screen:'', fulltext:'', coding:''};
  const queues = {}, items = {}, readOnlyKeys = {}, reasons = {screen:[], fulltext:[]};
  const tickets = {screen:0, fulltext:0, coding:0};
  const dirtyFields = new Set();
  const editLibrary = field => { dirtyFields.add(field); libraryDirty=true; };
  const cleanLibrary = field => { if(field)dirtyFields.delete(field); libraryDirty=dirtyFields.size>0; };
  let settings = null, draft = null, settingsDirty = false, libraryDirty = false, alDirty = false;
  let context = '', refreshTicket = 0, activeTab = 'overview', animation = null, dialogPromise = null;
  let order = localStorage.getItem('rf_workflow_order') || 'import';
  const scope = () => [S.task?.task_id, S.user?.user_id, S.screener].join('|');
  const n = (tag, text, attrs={}) => el(tag, {...attrs, ...(text == null ? {} : {text:T(text)})});
  const button = (text, fn) => n('button', text, {type:'button', class:'btn b-out sm', onclick:fn});
  const literal = (tag, text) => el(tag, {text:String(text ?? ''), translate:'no'});
  const run = fn => Promise.resolve().then(fn).catch(error => W.error(error));
  W.scope = scope;
  W.settings = () => settings;
  W.version = () => settings?.version;
  W.order = () => order;
  W.activeItem = stage => items[stage];
  W.maybe = stage => settings?.stages?.[stage]?.show_maybe !== false;
  W.settingsDirty = () => settingsDirty || libraryDirty || alDirty;
  const decisionBaselines={};
  const reasonSignature=stage=>JSON.stringify([$(stage==='screen'?'#excReason':'#ftReason')?.value||'',$(stage==='screen'?'#excCustomReason':'#ftCustomReason')?.value||'']);
  W.markDecisionClean=stage=>{decisionBaselines[stage]=reasonSignature(stage);};
  W.decisionDirty=()=>['screen','fulltext'].some(stage=>S.page===stage&&decisionBaselines[stage]!=null&&decisionBaselines[stage]!==reasonSignature(stage));
  W.anyDirty=()=>W.settingsDirty()||W.decisionDirty()||window.WorkflowEvidence?.dirty()||window.WorkflowExtraction?.dirty();
  W.error = error => {
    toast(error.status === 409 ? T('Workflow changed. Your draft is retained. Refresh the policy, review the changes and retry.') + ' ' + error.message : error.message, 'warn');
    if (error.status === 409) run(() => W.refresh('conflict'));
  };
  const home = document.querySelector('section.page[data-p="home"]');
  const panels = {};
  function homeTabs() {
    const original = [...home.children];
    const tabs = n('div', null, {class:'wf-tabs', role:'tablist', 'aria-label':T('Home settings')});
    const indicator = n('i', null, {class:'wf-tab-indicator', 'aria-hidden':'true'});
    const labels = [['overview','Overview'],['tasks','Tasks & team'],['screening','Screening'],['ml','Machine Learning'],['coding','Coding'],['appearance','Appearance']];
    for (const [key,label] of labels) {
      const tab = button(label, () => activate(key));
      tab.id = 'wf-tab-' + key; tab.dataset.wfLabel = label;
      tab.setAttribute('role','tab'); tab.setAttribute('aria-controls','wf-panel-' + key);
      tab.addEventListener('keydown', event => {
        const i = labels.findIndex(row => row[0] === key);
        const next = event.key === 'ArrowRight' ? (i+1)%6 : event.key === 'ArrowLeft' ? (i+5)%6 : event.key === 'Home' ? 0 : event.key === 'End' ? 5 : null;
        if (next == null) return;
        event.preventDefault(); activate(labels[next][0]); tabs.querySelectorAll('[role=tab]')[next].focus();
      });
      tabs.append(tab);
      const panel = n('div', null, {id:'wf-panel-' + key, class:'wf-panel', role:'tabpanel', 'aria-labelledby':tab.id});
      panels[key] = panel;
    }
    tabs.append(indicator); home.append(tabs, ...Object.values(panels));
    let destination = 'tasks';
    for (const node of original) {
      if (node.tagName === 'H2') { home.insertBefore(node,tabs); continue; }
      if (node.classList.contains('steps')) destination = 'overview';
      if (node.tagName === 'H3' && node.nextElementSibling?.classList.contains('steps')) destination = 'overview';
      if (node.id === 'localStorageTitle') destination = 'appearance';
      if (node.tagName === 'H3' && node.nextElementSibling?.querySelector('#alStrategy')) destination = 'ml';
      panels[destination].append(node);
    }
    const current = n('div', null, {id:'wf-current-task', class:'card'});
    panels.overview.prepend(current);
    panels.overview.append(n('div', null, {id:'wf-pending-imports', class:'card', hidden:''}));
    const lang = n('select', null, {class:'input', 'aria-label':T('Language')});
    for (const [value,label] of [['zh-CN','简体中文'],['en','English'],['fr','Français'],['ru','Русский'],['es','Español'],['ja','日本語'],['pt','Português'],['de','Deutsch'],['sr','Српски'],['ko','한국어']]) lang.append(el('option',{value,text:label,translate:'no'}));
    lang.value=RFLang.get(); lang.addEventListener('change',()=>RFLang.set(lang.value));
    panels.appearance.prepend(n('label','Language'),lang);
    const defaultOrder = n('select',null,{class:'input','aria-label':T('Default screening order')});
    for (const [value,label] of [['import','Import'],['ai','Machine Learning'],['random','Random']]) defaultOrder.append(n('option',label,{value}));
    defaultOrder.value=order; defaultOrder.addEventListener('change',()=>{order=defaultOrder.value;localStorage.setItem('rf_workflow_order',order);});
    panels.ml.append(n('label','Default screening order'),defaultOrder);
    const notePref=n('select',null,{class:'input','aria-label':T('Notes display')});
    for(const [value,label] of [['content','Expand saved evidence'],['all','Expand all notes']]) notePref.append(n('option',label,{value}));
    notePref.value=localStorage.getItem('rf_evidence_display')||'content';
    notePref.addEventListener('change',()=>localStorage.setItem('rf_evidence_display',notePref.value));
    panels.coding.append(n('label','Notes display'),notePref);
    const policy = n('section',null,{id:'wf-policy',class:'card'});
    panels.tasks.append(policy);
    panels.screening.append(n('div',null,{id:'wf-personal-maybe',class:'card'}),n('section',null,{id:'wf-reasons',class:'card'}));
    panels.coding.append(n('section',null,{id:'wf-requirements',class:'card'}));
    panels.tasks.append(n('section',null,{id:'wf-sources',class:'card'}));
    const savebar=n('div',null,{class:'wf-settings-save'});
    savebar.append(button('Save workflow settings',()=>run(W.saveSettings)),button('Reload policy',()=>run(async()=>{if(await W.guard()) {settingsDirty=false; await W.refresh('reload');}})),n('span',null,{id:'wf-settings-notice',role:'status'}));
    home.append(savebar);
    const recovery=n('div',null,{id:'wf-policy-recovery',class:'card',hidden:'',role:'status'});
    recovery.append(n('p','Workflow changed. Your draft is retained. Refresh the policy, review the changes and retry.'),button('Review current policy',()=>{const details=n('dialog',null,{class:'wf-source-dialog','aria-label':T('Current workflow policy')});details.append(n('h3','Current workflow policy'),literal('pre',JSON.stringify(settings,null,2)),button('Apply current policy version to retained draft',()=>run(async()=>{if(draft)draft.version=settings.version;await window.WorkflowEvidence?.revalidate();if(['screen','fulltext'].includes(S.page)){const stage=S.page,key=stage==='screen'?S.articles[S.scrIdx]?.zotero_key:curFt()?.zotero_key;if(key){await W.item(stage,key);W.decorateStage(stage);}}details.close();details.remove();recovery.hidden=true;})),button('Stay',()=>{details.close();details.remove();}));document.body.append(details);details.showModal();}));
    document.querySelector('#appMain').prepend(recovery);
    function measure(immediate=false) {
      animation?.cancel(); animation=null;
      const target=tabs.querySelector('[aria-selected="true"]');
      if(!target?.isConnected || !tabs.isConnected || !target.getClientRects().length) return;
      const rect=target.getBoundingClientRect(), parent=tabs.getBoundingClientRect();
      const left=rect.left-parent.left+tabs.scrollLeft, top=rect.bottom-parent.top-3, width=rect.width;
      const old=indicator.getBoundingClientRect(), start=old.left-parent.left+tabs.scrollLeft, oldWidth=old.width;
      indicator.style.top=top+'px'; indicator.style.left=left+'px';indicator.style.width=width+'px';
      if(immediate || matchMedia('(prefers-reduced-motion: reduce)').matches || !oldWidth || Math.abs(left-start)<1&&Math.abs(width-oldWidth)<1) return;
      const right=left>start;
      // Both edges follow the same 360 ms cubic-bezier; the trailing edge starts 100 ms later.
      const ease=x=>{x=Math.max(0,Math.min(1,x));let lo=0,hi=1,t=x;for(let i=0;i<12;i++){t=(lo+hi)/2;const px=3*(1-t)*(1-t)*t*.22+3*(1-t)*t*t*.25+t*t*t;if(px<x)lo=t;else hi=t;}return 3*(1-t)*(1-t)*t*.8+3*(1-t)*t*t+t*t*t;};
      const frames=[];
      for(let ms=0;ms<=460;ms+=10){
        const leading=ease(ms/360),trailing=ms<=100?0:ease((ms-100)/360);
        let l=start+(left-start)*(right?trailing:leading),r=start+oldWidth+(left+width-start-oldWidth)*(right?leading:trailing);
        const stretch=Math.sin(Math.PI*Math.min(1,ms/360))*Math.max(oldWidth,width)*1.2;
        if(right)r+=stretch;else l-=stretch;
        const overshoot=ms>360?Math.sin(Math.PI*(ms-360)/100)*3:0;l+=right?overshoot:-overshoot;r+=right?overshoot:-overshoot;
        frames.push({left:l+'px',width:Math.max(1,r-l)+'px',offset:ms/460});
      }
      frames[0]={left:start+'px',width:oldWidth+'px',offset:0};frames[frames.length-1]={left:left+'px',width:width+'px',offset:1};
      animation=indicator.animate(frames,{duration:460,fill:'none'});
    }
    function activate(key) {
      activeTab=key;
      for(const tab of tabs.querySelectorAll('[role=tab]')) {const on=tab.id==='wf-tab-'+key;tab.setAttribute('aria-selected',String(on));tab.tabIndex=on?0:-1;}
      for(const [id,panel] of Object.entries(panels)) panel.hidden=id!==key;
      measure();
    }
    activate(activeTab);
    const resize=new ResizeObserver(()=>measure(true));resize.observe(tabs);for(const tab of tabs.querySelectorAll('[role=tab]'))resize.observe(tab);
    document.fonts?.ready.then(()=>measure(true));
    document.addEventListener('reviewflow:language-changed',()=>{for(const tab of tabs.querySelectorAll('[data-wf-label]'))tab.textContent=T(tab.dataset.wfLabel);measure(true);});
    W.homeTab=activate;
  }
  function policyEditor() {
    if(!settings || W.settingsDirty()) return;
    draft=structuredClone(settings);
    const host=$('#wf-policy');host.replaceChildren(n('h3','Stage source policies'));
    const personal=$('#wf-personal-maybe');personal.replaceChildren(n('h3','Maybe decisions'));
    for(const stage of stages) {
      const row=n('fieldset',null,{class:'wf-policy-stage'});row.append(n('legend',stage==='screen'?'Screening':stage==='fulltext'?'Full text':'Coding'));
      const select=n('select',null,{class:'input','aria-label':T('Stage source policies')});
      for(const value of ['all','selected','paused'])select.append(n('option',{all:'All',selected:'Selected',paused:'Paused'}[value],{value}));
      select.value=draft.stages[stage].mode;select.disabled=!settings.can_manage;
      const sourceList=n('div',null,{class:'wf-source-checks'});
      for(const source of settings.sources||[]) {
        const check=n('input',null,{type:'checkbox',value:source.id});check.checked=draft.stages[stage].source_ids.includes(source.id);check.disabled=!settings.can_manage;
        check.addEventListener('change',()=>{draft.stages[stage].source_ids=[...sourceList.querySelectorAll('input:checked')].map(c=>c.value);settingsDirty=true;});
        sourceList.append(el('label',{},check,literal('span',source.name)));
      }
      sourceList.hidden=select.value!=='selected';select.addEventListener('change',()=>{draft.stages[stage].mode=select.value;sourceList.hidden=select.value!=='selected';settingsDirty=true;});
      row.append(select,sourceList);host.append(row);
      if(stage!=='coding') {
        const check=n('input',null,{type:'checkbox'});check.checked=draft.stages[stage].show_maybe;check.disabled=!settings.can_manage;
        check.addEventListener('change',()=>{draft.stages[stage].show_maybe=check.checked;settingsDirty=true;});
        (settings.task_kind==='personal'?personal:row).append(el('label',{},check,n('span',stage==='screen'?'Allow Maybe in screening':'Allow Maybe in full text')));
      }
    }
    if(settings.task_kind!=='personal')personal.append(n('p','The task owner controls Maybe decisions. Effective policy is shown in Tasks & team.'));
    const fields=$('#wf-requirements');fields.replaceChildren(n('h3','Required evidence fields'));
    const known=new Set();
    for(const field of settings.evidence_fields||[]) {
      known.add(field.field_id);
      const check=n('input',null,{type:'checkbox',value:field.field_id});check.checked=draft.evidence_required_field_ids.includes(field.field_id);check.disabled=!settings.can_manage;
      check.addEventListener('change',()=>{draft.evidence_required_field_ids=[...fields.querySelectorAll('input:checked')].map(c=>c.value);settingsDirty=true;});
      fields.append(el('label',{class:'wf-field-option'},check,literal('span',field.name)));
    }
    for(const id of draft.evidence_required_field_ids.filter(id=>!known.has(id))) {
      const check=n('input',null,{type:'checkbox',value:id});check.checked=true;check.disabled=!settings.can_manage;
      check.addEventListener('change',()=>{draft.evidence_required_field_ids=[...fields.querySelectorAll('input:checked')].map(c=>c.value);settingsDirty=true;});
      fields.append(el('label',{},check,n('span','Removed field — deselect to resolve'),literal('code',id)));
    }
    if(settings.mapping_issues?.length) fields.append(n('p','Evidence field mapping needs owner review. Existing values are preserved.',{class:'hint'}));
    sourceEditor();
  }
  W.saveSettings=async()=>{
    if(!draft || !settings.can_manage)return false;
    for(const stage of stages) if(draft.stages[stage].mode==='selected'&&!draft.stages[stage].source_ids.length)throw Error(T('Select at least one source, or choose Paused.'));
    const current=scope();
    const payload={expected_version:draft.version,stages:structuredClone(draft.stages),evidence_required_field_ids:[...draft.evidence_required_field_ids]};
    const result=await api(taskApi()+'/workflow-settings',{method:'PUT',body:payload});
    if(current!==scope())return false;
    settingsDirty=JSON.stringify(payload.stages)!==JSON.stringify(draft.stages)||JSON.stringify(payload.evidence_required_field_ids)!==JSON.stringify(draft.evidence_required_field_ids);
    if(result.version!=null)draft.version=result.version;
    await W.refresh('saved');return !settingsDirty;
  };
  function sourceEditor() {
    const host=$('#wf-sources');host.replaceChildren(n('h3','Sources'));
    for(const source of settings.sources||[]) {
      const row=n('div',null,{class:'row'}),name=el('input',{class:'input',value:source.name,translate:'no',maxlength:'2000'});name.disabled=!settings.can_manage||source.reserved;
      name.addEventListener('input',()=>editLibrary(name));
      row.append(name);
      if(!name.disabled) {
        name.wfSave=async()=>{const sent=name.value;await api(taskApi()+'/sources/'+encodeURIComponent(source.id),{method:'PUT',body:{name:sent,active:source.active,expected_version:settings.version}});if(name.value===sent)cleanLibrary(name);await W.refresh('source');return !dirtyFields.has(name);};
        row.append(button('Save source',()=>run(name.wfSave)),button(source.active?'Pause source':'Activate source',()=>run(async()=>{await api(taskApi()+'/sources/'+encodeURIComponent(source.id),{method:'PUT',body:{name:source.name,active:!source.active,expected_version:settings.version}});await W.refresh('source');})));
      }
      host.append(row);
    }
    if(settings.can_manage) {
      const name=el('input',{class:'input',placeholder:T('New source name'),translate:'no'});name.addEventListener('input',()=>editLibrary(name));
      name.wfSave=async()=>{const sent=name.value;await api(taskApi()+'/sources',{method:'POST',body:{name:sent,active:true,expected_version:settings.version}});if(name.value===sent)cleanLibrary(name);await W.refresh('source');return !dirtyFields.has(name);};
      host.append(name,button('Create source',()=>run(name.wfSave)));
    }
  }
  async function loadReasons() {
    const current=scope();
    const results=await Promise.all(['screen','fulltext'].map(stage=>api(taskApi()+'/reason-library?stage='+stage)));
    if(current!==scope())return;
    for(let i=0;i<2;i++)reasons[['screen','fulltext'][i]]=results[i].entries||[];
    if(!libraryDirty) {
      const host=$('#wf-reasons');host.replaceChildren(n('h3','Reusable exclusion reasons'),n('p',results[0].task_kind==='personal'?'Personal reusable reasons':'Team reusable reasons',{class:'hint'}));
      ['screen','fulltext'].forEach((stage,i)=>{
        host.append(n('h4',stage==='screen'?'Screening':'Full text'));
        for(const entry of reasons[stage]) {
          const row=n('div',null,{class:'row'}),text=el('input',{class:'input',value:entry.text,maxlength:'2000',translate:'no'});text.disabled=!results[i].can_manage;text.addEventListener('input',()=>editLibrary(text));row.append(text);
          text.wfSave=()=>mutateReason(stage,'PUT',entry.id,text.value,text);
          if(results[i].can_manage)row.append(button('Save reason',()=>mutateReason(stage,'PUT',entry.id,text.value,text)),button('Delete reason',()=>mutateReason(stage,'DELETE',entry.id,undefined,text)));
          host.append(row);
        }
        if(results[i].can_manage){const text=el('input',{class:'input',maxlength:'2000',placeholder:T('New reusable reason'),translate:'no'});text.addEventListener('input',()=>editLibrary(text));text.wfSave=()=>mutateReason(stage,'POST',null,text.value,text);host.append(text,button('Add reason',()=>mutateReason(stage,'POST',null,text.value,text)));}
      });
    }
    W.decorateReasons('screen'); W.decorateReasons('fulltext');
  }
  function mutateReason(stage,method,id,text,field=null) {return run(async()=>{if(method!=='DELETE'&&(!text?.trim()||text.trim().length>2000))throw Error(T('A reason must contain 1–2000 characters.'));await api(taskApi()+'/reason-library'+(id?'/'+encodeURIComponent(id):'')+'?stage='+stage,{method,body:method==='DELETE'?undefined:{text:text.trim(),stage}});if(!field||method==='DELETE'||field.value.trim()===text.trim()){cleanLibrary(field);if(field&&method==='POST')field.value='';}await loadReasons();return !field||!dirtyFields.has(field);});}
  W.decorateReasons=stage=>{
    const select=$(stage==='screen'?'#excReason':'#ftReason'),custom=$(stage==='screen'?'#excCustomReason':'#ftCustomReason');if(!select)return;
    const value=select.value;
    select.querySelectorAll('[data-library]').forEach(o=>o.remove());
    for(const entry of reasons[stage])if(![...select.options].some(o=>o.value===entry.text)){const option=el('option',{value:entry.text,text:entry.text,translate:'no','data-library':'true'});select.insertBefore(option,select.querySelector('[value="__custom__"]'));}
    if([...select.options].some(o=>o.value===value))select.value=value;
    else if(value){select.value='__custom__';custom.value=value;custom.hidden=false;}
    const id='wf-save-reason-'+stage;
    if(!document.getElementById(id)&&settings?.can_manage){const b=button('Save as reusable reason',()=>mutateReason(stage,'POST',null,select.value==='__custom__'?custom.value:select.value));b.id=id;select.parentElement.append(b);}
  };
  function tools(stage) {
    const page=document.querySelector('section[data-p="'+stagePage[stage]+'"]');
    let host=page.querySelector('.wf-queue-tools');if(host)return host;
    host=n('div',null,{class:'wf-queue-tools'});
    const select=n('select',null,{class:'input','aria-label':T('Source scope')});select.dataset.stage=stage;
    select.addEventListener('change',()=>run(async()=>{const value=select.value;select.value=selected[stage];if(!await W.guard())return;selected[stage]=value;await W.reload(stage);}));
    host.append(n('label','Source scope'),select,n('span',null,{class:'wf-scope-progress',role:'status'}),button('Retry / refresh',()=>run(async()=>{if(await W.guard())await W.reload(stage);})),n('span',null,{class:'wf-readonly',role:'status'}));
    if(stage==='fulltext')host.append(n('span','Initial Include or Maybe decisions enter full-text screening.',{class:'hint'}));
    page.insertBefore(host,page.querySelector('.al-mode')||page.children[1]||null);
    return host;
  }
  W.queue=async stage=>{
    await W.refresh('entry',false);
    const current=scope(),ticket=++tickets[stage],host=tools(stage);host.querySelector('.wf-scope-progress').textContent=T('Loading scope…');
    const query=new URLSearchParams({order:stage==='screen'&&order==='random'?'random':'import'});if(selected[stage])query.set('source_id',selected[stage]);
    try {
      const result=await api(taskApi()+'/work-queue/'+stage+'?'+query);
      if(current!==scope()||ticket!==tickets[stage])throw Error(T('The task changed. Reload the queue.'));
      queues[stage]=result;delete items[stage];delete readOnlyKeys[stage];
      const select=host.querySelector('select');select.replaceChildren(n('option','All permitted sources',{value:''}));
      for(const id of result.allowed_sources||[]){const source=settings.sources.find(source=>source.id===id);if(source)select.append(el('option',{value:source.id,text:source.name,translate:'no'}));}
      if(selected[stage]&&![...select.options].some(o=>o.value===selected[stage])){selected[stage]='';return W.queue(stage);}
      select.value=selected[stage];
      const p=result.scope_progress;host.querySelector('.wf-scope-progress').textContent=T('Scope: {0} / {1} complete',p.completed,p.total);
      host.querySelector('.wf-readonly').textContent=result.paused?T('Paused — saved work is read-only.'):'';
      return result;
    } catch(error){if(current===scope())host.querySelector('.wf-scope-progress').textContent=T('Queue unavailable. Use Retry / refresh.')+' '+error.message;throw error;}
  };
  W.item=async(stage,key)=>{
    const current=scope(),ticket=++tickets[stage];
    delete items[stage];
    const item=await api(taskApi()+'/work-items/'+stage+'/'+encodeURIComponent(key));
    if(current!==scope()||ticket!==tickets[stage])return null;
    if(readOnlyKeys[stage]===key){item.writable=false;item.reason=T('This saved article is outside the selected queue. Return to the queue to continue.');}
    items[stage]={...item,key};return item;
  };
  W.updateProgress=async stage=>{
    if(!S.task)return;
    const current=scope(),source=selected[stage],query=new URLSearchParams({order:stage==='screen'&&order==='random'?'random':'import'});if(source)query.set('source_id',source);
    const result=await api(taskApi()+'/work-queue/'+stage+'?'+query);
    if(current!==scope()||source!==selected[stage])return;
    const p=result.scope_progress;tools(stage).querySelector('.wf-scope-progress').textContent=T('Scope: {0} / {1} complete',p.completed,p.total);
  };
  W.decorateStage=stage=>{
    const item=items[stage],key=stage==='screen'?S.articles[S.scrIdx]?.zotero_key:stage==='fulltext'?curFt()?.zotero_key:S.cbQueue[S.cbIdx]?.zotero_key;
    const writable=item?.key===key&&item.writable&&settings?.stages?.[stage]?.mode!=='paused'&&item.policy_version===settings?.version;
    const ids=stage==='screen'?['btnInc','btnMay','btnExc']:stage==='fulltext'?['ftInc','ftMay','ftExc']:['cbSave'];
    for(const id of ids){const b=$('#'+id);if(b)b.disabled=!writable;}
    const maybe=$(stage==='screen'?'#btnMay':'#ftMay');if(stage!=='coding'&&maybe){maybe.hidden=!W.maybe(stage);maybe.disabled=!writable||!W.maybe(stage);}
    const notice=tools(stage).querySelector('.wf-readonly');notice.textContent=item?.key===key&&!item.writable?T('Read-only: current policy or source scope does not allow saving.')+' '+(item.reason||''):'';
    if(stage!=='coding')W.decorateReasons(stage);
  };
  W.canSave=(stage,decision)=>{
    const item=items[stage],key=stage==='screen'?S.articles[S.scrIdx]?.zotero_key:stage==='fulltext'?curFt()?.zotero_key:S.cbQueue[S.cbIdx]?.zotero_key;
    if(!item||item.key!==key||!item.writable||settings?.stages?.[stage]?.mode==='paused'||item.policy_version!==settings?.version||decision==='maybe'&&!W.maybe(stage)){toast(T('This item is read-only under the current workflow policy.'),'warn');return false;}return true;
  };
  W.reload=stage=>stage==='screen'?loadScreen():stage==='fulltext'?loadFulltext():loadBench();
  W.reopen=async(stage,key,access='personal',field=null,{guarded=false}={})=>{
    if(!stages.includes(stage)||!S.task)return false;
    const requested=scope();
    if(!guarded&&!await W.guard())return false;
    if(requested!==scope())return false;
    const current=scope();
    if(stage==='coding')WorkflowEvidence.setContext(access);
    await go(stagePage[stage],{guarded:true,skipLoad:true});
    await W.reload(stage);
    if(current!==scope())return false;
    const queue=stage==='screen'?S.articles:stage==='fulltext'?S.ftQueue:S.cbQueue;
    let index=queue.findIndex(article=>article.zotero_key===key);
    if(index<0){
      const item=await api(taskApi()+(access==='coding_submission_evidence'?'/coding/submission-evidence/':'/work-items/'+stage+'/')+encodeURIComponent(key));
      if(current!==scope())return false;
      // A separate one-item read-only view does not broaden the selected queue.
      const article={...item.article,zotero_key:key};index=0;readOnlyKeys[stage]=key;
      if(stage==='screen')S.articles=[article];else if(stage==='fulltext')S.ftQueue=[article];else {S.cbQueue=[article];WorkflowEvidence.readOnly(key);}
    }
    if(stage==='screen'){S.screenEditingKey=key;S.scrIdx=index;await renderScreen();}
    else if(stage==='fulltext'){S.ftIdx=index;await renderFtDoc();await openFtPdf();}
    else {S.cbIdx=index;await renderBench();}
    if(current!==scope())return false;
    if(stage==='coding'&&field){const card=[...document.querySelectorAll('#cbForms [data-field-id]')].find(node=>node.dataset.fieldId===String(field.field_id||field.id||''))||document.getElementById('wf-field-'+field.dimension_id);if(card){card.querySelector('details')?.setAttribute('open','');card.querySelector('textarea,input,select')?.focus();}else toast(T('The historical field is no longer mapped. Current saved work is open.'),'info');}
    return true;
  };
  W.setOrder=async mode=>{
    if(!await W.guard())return;
    order=mode;S.al.mode=mode==='ai'?'ai':'import';
    $('#alModeRandom').classList.toggle('on',mode==='random');
    if(S.task)await loadScreen();
    $('#alModeImport').classList.toggle('on',mode==='import');
  };
  W.refresh=async(reason='poll',reload=true)=>{
    if(!S.task||!S.user)return;
    const current=scope(),ticket=++refreshTicket;
    const result=await api(taskApi()+'/workflow-settings');
    if(current!==scope()||(settings&&result.version<settings.version))return;
    const changed=settings&&JSON.stringify(settings)!==JSON.stringify(result);
    settings=result;
    for(const [stage,id] of [['screen','btnMay'],['fulltext','ftMay']]){const maybe=$('#'+id);if(maybe){maybe.hidden=!W.maybe(stage);if(!W.maybe(stage))maybe.disabled=true;}}
    for(const [stage,ids] of [['screen',['btnInc','btnMay','btnExc']],['fulltext',['ftInc','ftMay','ftExc']]])if(result.stages[stage].mode==='paused')for(const id of ids){const control=$('#'+id);if(control)control.disabled=true;}
    const dirty=W.anyDirty();
    $('#wf-settings-notice').textContent=changed&&dirty?T('Workflow changed. Your draft is retained. Refresh the policy, review the changes and retry.'):'';
    if(changed&&dirty||reason==='conflict')$('#wf-policy-recovery').hidden=false;
    if(!W.settingsDirty())policyEditor();
    await loadReasons(); // Libraries can change without a policy version increment.
    if(current!==scope())return;
    $('#wf-current-task').replaceChildren(n('b','Current task'),literal('p',S.task.name||S.task.task_id),button('Continue screening',()=>go('screen')),button('Continue coding',()=>go('bench')));
    window.WorkflowEvidence?.discovery(result.submission_evidence);
    window.WorkspaceUndo?.sync();
    if(S.page==='home'&&window.WorkflowImports)await WorkflowImports.notice();
    if(current!==scope())return;
    if(changed&&reload&&!dirty&&stages.includes({bench:'coding'}[S.page]||S.page))await W.reload({bench:'coding'}[S.page]||S.page);
    if(reason==='conflict'&&draft&&!settingsDirty)draft.version=result.version;
  };
  W.guard=async()=>{
    if(apiPendingWrites>0||window.WorkspaceUndo?.isBusy()||window.WorkflowEvidence?.uploading()||window.WorkflowExtraction?.saving()){toast(T('Wait for the current save or upload to finish.'),'warn');return false;}
    if(!W.anyDirty())return true;
    if(dialogPromise)return dialogPromise;
    dialogPromise=new Promise(resolve=>{
      const dialog=n('dialog',null,{class:'wf-dirty-dialog','aria-label':T('Unsaved changes')});
      dialog.append(n('h3','Unsaved changes'),n('p','Save your changes, discard them, or stay here.'));
      const finish=value=>{dialog.close();dialog.remove();dialogPromise=null;resolve(value);};
      dialog.append(button('Save',()=>run(async()=>{
        if(W.decisionDirty()){toast(T('Choose a screening decision to save this reason before leaving.'),'warn');return;}
        if(settingsDirty&&!await W.saveSettings())return;
        for(const field of [...dirtyFields])if(!field.wfSave||!await field.wfSave())return;
        if(alDirty&&!await saveALSettings())return;
        if(window.WorkflowEvidence?.dirty()&&!await WorkflowEvidence.save())return;
        if(window.WorkflowExtraction?.dirty()&&!await WorkflowExtraction.save())return;
        finish(true);
      })),button('Discard',()=>{settingsDirty=false;libraryDirty=false;dirtyFields.clear();alDirty=false;window.WorkflowEvidence?.discard();window.WorkflowExtraction?.discard();for(const stage of ['screen','fulltext'])W.markDecisionClean(stage);policyEditor();finish(true);}),button('Stay',()=>finish(false)));
      dialog.addEventListener('cancel',event=>{event.preventDefault();finish(false);});document.body.append(dialog);dialog.showModal();
    });
    return dialogPromise;
  };
  homeTabs();stages.forEach(tools);
  const overallLabels=()=>{for(const id of ['capScreen','capFull','capCode'])$('#'+id)?.setAttribute('data-wf-overall-label',T('Overall'));};overallLabels();
  document.addEventListener('reviewflow:language-changed',overallLabels);
  const random=button('Random',()=>run(()=>W.setOrder('random')));random.id='alModeRandom';$('#alModeAI').after(random,button('Shuffle remaining again',()=>run(async()=>{if(!await W.guard())return;const q=selected.screen?'?source_id='+encodeURIComponent(selected.screen):'';await api(taskApi()+'/work-queue/screen/shuffle'+q,{method:'POST'});order='random';S.al.mode='import';await loadScreen();})));
  $('#alModeAI').querySelector('span').textContent=T('Machine Learning');
  $('#alModeImport').querySelector('span').textContent=T('Import');
  for(const id of ['alStrategy','alSeed','alMinLabeled','alStopThreshold'])$('#'+id).addEventListener('input',()=>alDirty=true);
  document.addEventListener('reviewflow:al-settings-saved',()=>alDirty=false);
  document.addEventListener('reviewflow:task-context-changed',()=>{
    if(context===scope())return;context=scope();refreshTicket++;for(const stage of stages){tickets[stage]++;selected[stage]='';delete items[stage];delete queues[stage];delete decisionBaselines[stage];}settings=null;draft=null;settingsDirty=false;libraryDirty=false;dirtyFields.clear();alDirty=false;$('#wf-policy-recovery').hidden=true;run(()=>W.refresh('context',false));
  });
  document.addEventListener('reviewflow:workspace-changed',()=>run(()=>W.refresh('entry',false)));
  document.addEventListener('reviewflow:workflow-changed',event=>{if(String(event.detail?.task_id)===String(S.task?.task_id))run(()=>W.refresh(event.detail.reason||'event'));});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)run(()=>W.refresh('focus'));});
  window.addEventListener('focus',()=>run(()=>W.refresh('focus')));
  setInterval(()=>{if(!document.hidden&&S.user&&S.task)run(()=>W.refresh());},30000);
  window.addEventListener('beforeunload',event=>{if(W.anyDirty()||window.WorkflowEvidence?.uploading()||window.WorkflowExtraction?.saving()){event.preventDefault();event.returnValue='';}});
  window.addEventListener('load',()=>run(async()=>{if(!S.task)return;await W.refresh('bootstrap',false);const stage={bench:'coding'}[S.page]||S.page;if(stages.includes(stage)&&!W.anyDirty())await W.reload(stage);}));
})();
