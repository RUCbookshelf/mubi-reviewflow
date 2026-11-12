/* Workflow policy, source-scoped queues and draft-preserving refresh. */
(function () {
  'use strict';
  const W = window.WorkflowControls = {};
  // Every global shortcut uses the same modal boundary, including dialogs opened
  // by independently loaded workspaces.
  window.RFModalActive = () => [...document.querySelectorAll('dialog[open], [role="dialog"][aria-modal="true"]')]
    .some(dialog => !dialog.hidden && dialog.getClientRects().length > 0);
  const registeredDrafts = new Map();
  W.registerDraft = (name, handlers) => {
    registeredDrafts.set(name, handlers);
    return () => { if(registeredDrafts.get(name) === handlers) registeredDrafts.delete(name); };
  };
  const extraDirty = () => [...registeredDrafts.values()].some(draft => draft.dirty?.());
  const stages = ['screen', 'fulltext', 'coding'];
  const stagePage = {screen:'screen', fulltext:'fulltext', coding:'bench'};
  const selected = {screen:'', fulltext:'', coding:''};
  const queues = {}, items = {}, readOnlyKeys = {}, reasons = {screen:[], fulltext:[]};
  const tickets = {screen:0, fulltext:0, coding:0};
  const dirtyFields = new Set();
  const trackLibrary = (field,value=()=>field.value.trim()) => {field.wfDraftValue=value;field.wfBaseline=value();};
  const editLibrary = field => {if(field.wfDraftValue()===field.wfBaseline)dirtyFields.delete(field);else dirtyFields.add(field);libraryDirty=dirtyFields.size>0;};
  const cleanLibrary = field => {if(field){field.wfBaseline=field.wfDraftValue();dirtyFields.delete(field);}libraryDirty=dirtyFields.size>0;};
  let settings = null, draft = null, settingsDirty = false, libraryDirty = false, alDirty = false;
  let draftBaseline=null,alBaseline=null,pendingPolicyReview=false;
  const alIds=['alStrategy','alSeed','alStopThreshold','alReorderInterval'];
  const alValues=()=>Object.fromEntries(alIds.map(id=>[id,$('#'+id).value]));
  const alSignature=values=>JSON.stringify(alIds.map(id=>{const value=values[id];return id!=='alStrategy'&&String(value).trim()!==''&&Number.isFinite(Number(value))?Number(value):value;}));
  const syncALDirty=()=>{alDirty=alBaseline!==null&&alSignature(alValues())!==alSignature(alBaseline);return alDirty;};
  const draftSignature=value=>JSON.stringify({stages:stages.map(stage=>{const s=value?.stages?.[stage]||{};return[s.mode,[...(s.source_ids||[])].sort()];}),evidence:[...(value?.evidence_required_field_ids||[])].sort()});
  const syncSettingsDirty=()=>{settingsDirty=!!draft&&draftBaseline!==null&&draftSignature(draft)!==draftBaseline;return settingsDirty;};
  // Catalogue counters and the mutation version are not editable policy changes.
  const policySignature=value=>JSON.stringify({draft:draftSignature(value),can_manage:value?.can_manage,task_kind:value?.task_kind,sources:(value?.sources||[]).map(s=>[s.id,s.active]).sort((a,b)=>String(a[0]).localeCompare(String(b[0]))),fields:value?.evidence_fields,issues:value?.mapping_issues,submission:value?.submission_evidence?.available});
  const stageSignature=(value,stage)=>JSON.stringify({stage:value?.stages?.[stage],sources:(value?.sources||[]).filter(source=>value?.stages?.[stage]?.mode!=='selected'||value.stages[stage].source_ids.includes(source.id)).map(source=>[source.id,source.active,source.linked_count]).sort((a,b)=>String(a[0]).localeCompare(String(b[0]))),coding:stage==='coding'?[value?.evidence_required_field_ids,value?.evidence_fields,value?.mapping_issues,value?.submission_evidence?.available]:null});
  const pendingStages=new Set();
  let editorRendered=null,reasonsRendered=null;
  let pollFailures=0,nextPollAt=0,pollNoticeDismissed=false;
  let savingSettings=false;
  let editRevision = 0, autoReload = null, deferredRefresh = false, reasonsTicket = 0, pendingPolicyRefresh = false;
  let policyFlight = null;
  let context = '', refreshTicket = 0, activeTab = 'overview', animation = null, dialogPromise = null;
  let order = localStorage.getItem('rf_workflow_order') || 'import';
  const scope = () => [S.task?.task_id, S.user?.user_id, S.screener].join('|');
  const n = (tag, text, attrs={}) => el(tag, {...attrs, ...(text == null ? {} : {text:T(text)})});
  const button = (text, fn) => n('button', text, {type:'button', class:'btn b-out sm', onclick:fn});
  const literal = (tag, text) => el(tag, {text:String(text ?? ''), translate:'no'});
  const run = fn => Promise.resolve().then(fn).catch(error => W.error(error));
  document.addEventListener('reviewflow:language-changed',()=>{for(const input of document.querySelectorAll('[data-wf-aria]'))input.setAttribute('aria-label',T(input.dataset.wfAria));for(const input of document.querySelectorAll('[data-wf-placeholder]')){input.placeholder=T(input.dataset.wfPlaceholder);input.setAttribute('aria-label',input.placeholder);}});
  W.scope = scope;
  W.settings = () => settings;
  W.version = () => settings?.version;
  W.acceptImportVersion = (version, requestedScope) => {
    if(requestedScope!==scope()||!settings||!Number.isInteger(version)||version<settings.version)return;
    settings.version=version;
    if(draft&&!settingsDirty)draft.version=version;
  };
  W.order = () => order;
  W.selectedSource = stage => selected[stage];
  W.activeItem = stage => items[stage];
  W.settingsDirty = () => syncSettingsDirty() || libraryDirty || syncALDirty();
  const decisionBaselines={};
  const reasonSignature=stage=>{
    const article=stage==='screen'?S.articles[S.scrIdx]:curFt();
    const select=$(stage==='screen'?'#excReason':'#ftReason');
    const custom=$(stage==='screen'?'#excCustomReason':'#ftCustomReason');
    // A completed/loading queue has no editable article. Missing controls are
    // not an empty reason draft, even if the preceding save had a reason.
    if(!article||!select)return null;
    return JSON.stringify([scope(),article.zotero_key,select.value||'',custom?.value||'']);
  };
  W.markDecisionClean=stage=>{decisionBaselines[stage]=reasonSignature(stage);};
  W.decisionDirty=()=>['screen','fulltext'].some(stage=>{
    if(S.page!==stage||decisionBaselines[stage]==null)return false;
    const signature=reasonSignature(stage);
    return signature!=null&&decisionBaselines[stage]!==signature;
  });
  /* ---- F27 半步（r10-u1）：「理由草稿」与「可保存草稿」分开看 ----
     批准单 F27：仅在下拉选了理由（尚未决策）即判 dirty，此后任何导航都弹模态，
     而模态的主按钮 Save 对理由草稿必然失败（Save 只会提示「先做决策」）。
     半步口径（完整「模态改内联」归结构包）：
       ① 模态正文列出未保存草稿（文献题名＋所选理由）并给「清空草稿」按钮；
       ② Save 对不可保存的理由草稿置灰并以 title 说明；       */
  const draftDecisionOf=stage=>($(stage==='screen'?'#excReason':'#ftReason')?.value||'');
  const draftCustomOf=stage=>($(stage==='screen'?'#excCustomReason':'#ftCustomReason')?.value||'').trim();
  const draftTitleOf=stage=>{
    const article=stage==='screen'?S.articles[S.scrIdx]:curFt();
    return String(article?.title||article?.zotero_key||'').trim();
  };
  W.reasonDraft=()=>{
    for(const stage of ['screen','fulltext']){
      if(S.page!==stage||decisionBaselines[stage]==null)continue;
      if(!W.decisionDirty())continue;
      const picked=draftDecisionOf(stage),custom=draftCustomOf(stage);
      const reason=picked==='__custom__'?custom:picked;
      return {stage,title:draftTitleOf(stage),reason:reason||'',custom:picked==='__custom__'};
    }
    return null;
  };
  /* 只有「理由草稿」而没有其它可保存内容时，Save 无物可存。 */
  W.reasonDraftOnly=()=>{
    const draftInfo=W.reasonDraft();
    if(!draftInfo)return null;
    const other=W.settingsDirty()||window.WorkflowEvidence?.dirty()||window.WorkflowExtraction?.dirty()||extraDirty();
    return other?null:draftInfo;
  };
  W.clearReasonDraft=stage=>{
    const target=stage||W.reasonDraft()?.stage;
    if(!target)return false;
    const select=$(target==='screen'?'#excReason':'#ftReason'),custom=$(target==='screen'?'#excCustomReason':'#ftCustomReason');
    if(select){select.value='';select.dispatchEvent(new Event('change',{bubbles:true}));}
    if(custom){custom.value='';custom.hidden=true;}
    window.RFScreenReason?.clearMessage?.();
    W.markDecisionClean(target);
    return true;
  };
  W.anyDirty=()=>W.settingsDirty()||W.decisionDirty()||window.WorkflowEvidence?.dirty()||window.WorkflowExtraction?.dirty()||extraDirty();
  const pendingWork=()=>apiPendingWrites>0||window.WorkspaceUndo?.isBusy()||window.WorkflowEvidence?.uploading()||window.WorkflowExtraction?.saving()||[...registeredDrafts.values()].some(draft=>draft.saving?.());
  const policyDraftDirty=()=>syncSettingsDirty()||W.decisionDirty()||window.WorkflowEvidence?.dirty()||window.WorkflowExtraction?.dirty()||extraDirty();
  const retainDraft=()=>{if(!pendingPolicyReview||!policyDraftDirty())return;$('#wf-settings-notice').textContent=T('Workflow changed. Your draft is retained. Refresh the policy, review the changes and retry.');$('#wf-policy-recovery').hidden=false;};
  for(const event of ['input','change'])document.addEventListener(event,e=>{if(!['taskSel','screenerSel'].includes(e.target.id))editRevision++;},true);
  W.refreshing=()=>!!autoReload;
  W.renderAllowed=()=>!autoReload||(autoReload.scope===scope()&&autoReload.page===S.page&&autoReload.ticket===refreshTicket&&autoReload.revision===editRevision);
  async function automaticReload(stage,token){
    if(token.scope!==scope()||token.page!==S.page||token.ticket!==refreshTicket)return;
    if(token.revision!==editRevision||W.anyDirty()||pendingWork()){retainDraft();return;}
    const page=document.querySelector('section[data-p="'+stagePage[stage]+'"]'),inert=page.inert,busy=page.getAttribute('aria-busy');
    // Only the clean replacement transaction is frozen. Settings/reason requests leave drafts editable.
    const contextControls=['taskSel','screenerSel'].map(id=>[document.getElementById(id),document.getElementById(id).disabled]);
    for(const [control] of contextControls)control.disabled=true;
    autoReload=token;page.inert=true;page.setAttribute('aria-busy','true');
    try{if(W.renderAllowed()){await W.reload(stage);if(W.renderAllowed()){pendingStages.delete(stage);pendingPolicyRefresh=pendingStages.size>0;pendingPolicyReview=false;}}}
    finally{autoReload=null;for(const [control,disabled] of contextControls)control.disabled=disabled;page.inert=inert;if(busy==null)page.removeAttribute('aria-busy');else page.setAttribute('aria-busy',busy);if(deferredRefresh){deferredRefresh=false;run(()=>W.refresh('deferred'));}}
  }

  const reasonLabels={task_read_only:'This item is read-only under the current workflow policy.',stage_paused:'This stage is paused.',article_not_eligible:'This article is not eligible for this stage.',article_outside_scope:'This article is outside the permitted sources.',requirement_mapping_gap:'Evidence field mapping needs owner review. Existing values are preserved.',coding_submission_access_denied:'You do not have permission for this action.',saved_state_changed:'The current saved state has changed. Reload before retrying.',unlinked_subject:'This history record cannot be linked to the current task.',no_linked_latest_save:'There is no linked latest save to undo.',workflow_version_conflict:'Workflow changed. Your draft is retained. Refresh the policy, review the changes and retry.',import_catalogue_changed:'The current saved state has changed. Reload before retrying.',missing_evidence_file:'The saved evidence image is no longer in the data directory, so this save cannot be undone.'};
  W.reason = value => T(reasonLabels[value] || value || '');
  /* F65（r10-u1，前端半步）：403 文案此前是「已译的中文句 + 空格 + 后端英文原串」，
     中英混排。这里把两段都过 T()（词典按中英双键索引，后端英文原串命中即译全）；
     仍未注册的技术串只在**不是 403** 时追加——403 的统一句式已说明「你无权执行
     此操作」，再拼英文原串只会制造混排。结构化原因与指引（缺哪个角色、找谁授权）
     需前后端契约协同，属结构包，本单不做。 */
  W.errorText = error => {
    /* R2：带 message_key 的契约错误直接渲染对应引导文案（十语言），
       不再拼 403 通用句 + 后端原文。 */
    const keyed = error.messageKey ? window.RFServerMessage?.(error.messageKey, error.params) : null;
    if (keyed) return keyed;
    if (error.code === 'maybe_disabled') return RFServerMessage('workflow.maybe_disabled');
    if (error.code && reasonLabels[error.code]) return W.reason(error.code);
    if (!error.status) return T(error.message);
    const base = T(error.status === 403
      ? 'You do not have permission for this action.'
      : 'The request could not be completed. Your draft is retained. Review the details and retry.');
    const detail = T(error.message || '');
    if (!detail || detail === base) return base;
    if (error.status === 403 && detail === (error.message || '')) return base;
    return base + ' ' + detail;
  };
  W.sourceLabel = source => source.reserved || source.id==='00000000-0000-0000-0000-000000000000' ? T('Source not recorded') : source.name;
  W.error = error => {
    if(error.status===401)return; // The auth owner already gives one session-expired notice.
    toast(W.errorText(error), 'warn');
    if (error.status === 409 && (error.workflowSettings || ['workflow_version_conflict','stage_paused','article_outside_scope'].includes(error.code))) run(() => W.refresh('conflict'));
  };
  const home = document.querySelector('section.page[data-p="home"]');
  const panels = {};
  function homeTabs() {
    const original = [...home.children];
    const tabs = n('div', null, {class:'wf-tabs', role:'tablist', 'aria-label':T('Home settings')});
    const indicator = n('i', null, {class:'wf-tab-indicator', 'aria-hidden':'true'});
    /* F15（r10-u1）：设置标签加类型限定词，消除与侧栏页面的逐字/近似同名——
       「数据提取与编码」与侧栏编码工作区页面逐字相同却指向任务级设置（编码证据
       要求），改为「编码证据要求」；「Screening settings」与侧栏「文献初筛」/阶段
       「筛选」近似同名，改为「筛选策略设置」。两个新串已随本批进十语言词典。 */
    const labels = [['overview','Overview'],['tasks','Tasks & team'],['screening','Screening policy settings'],['ml','Machine Learning'],['coding','Coding evidence requirements'],['appearance','Appearance & storage']];
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
    /* F14（r10-u1）：零任务状态下概览顶部的一步引导卡（与 F13 空态同批）。
       只在「任务列表已载入且为空」时出现，加载中不闪；样式克制（单行 + 一个
       主按钮），不与下方 12 张功能卡抢视觉优先级。 */
    panels.overview.append(n('div', null, {id:'wf-pending-imports', class:'card', hidden:''}));
    const lang = n('select', null, {class:'input', 'aria-label':T('Language')});
    for (const [value,label] of [['zh-CN','简体中文'],['en','English'],['fr','Français'],['ru','Русский'],['es','Español'],['ja','日本語'],['pt','Português'],['de','Deutsch'],['sr','Српски'],['ko','한국어']]) lang.append(el('option',{value,text:label,translate:'no'}));
    lang.value=RFLang.get(); lang.addEventListener('change',()=>RFLang.set(lang.value));
    panels.appearance.prepend(n('label','Language'),lang);
    const defaultOrder = n('select',null,{class:'input','aria-label':T('Default title-and-abstract screening order')});
    for (const [value,label] of [['import','Import'],['ai','Machine Learning'],['random','Random']]) defaultOrder.append(n('option',label,{value}));
    defaultOrder.value=order; defaultOrder.addEventListener('change',()=>{order=defaultOrder.value;localStorage.setItem('rf_workflow_order',order);});
    panels.ml.append(n('label','Default title-and-abstract screening order'),defaultOrder);
    const notePref=n('select',null,{class:'input','aria-label':T('Notes & evidence display')});
    for(const [value,label] of [['content','Expand populated or required notes'],['all','Expand all notes']]) notePref.append(n('option',label,{value}));
    notePref.value=localStorage.getItem('rf_evidence_display')||'content';
    notePref.addEventListener('change',()=>localStorage.setItem('rf_evidence_display',notePref.value));
    panels.coding.append(n('label','Notes & evidence display'),notePref);
    const policy = n('section',null,{id:'wf-policy',class:'card'});
    const teamBox=n('section',null,{id:'wf-team-management',class:'card wf-team-management'});
    teamBox.append(n('h3','Team Management',{id:'wf-team-title'}));
    const existingTeam=document.getElementById('teamPanel');
    if(existingTeam){existingTeam.classList.remove('card');teamBox.append(existingTeam);}
    policy.classList.remove('card');teamBox.append(policy);
    panels.tasks.append(teamBox);
    panels.screening.append(n('section',null,{id:'wf-reasons',class:'card'}));
    panels.coding.append(n('section',null,{id:'wf-requirements',class:'card'}));
    teamBox.append(n('section',null,{id:'wf-sources'}));
    const savebar=n('div',null,{class:'wf-settings-save'});
    savebar.append(button('Save current task workflow settings',()=>run(W.saveSettings)),button('Reload current task workflow settings',()=>run(async()=>{if(await W.guard()) {settingsDirty=false; await W.refresh('reload');}})),n('span',null,{id:'wf-settings-notice',role:'status'}));
    const saveHint=n('p',"此按钮保存当前任务的阶段来源与证据要求。排除理由、来源名称和机器学习设置分别保存。显示偏好自动保存在本浏览器。",{class:'hint'});
    home.append(saveHint,savebar);
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
    const tabScroll=new Map();
    function activate(key) {
      const scroller=document.querySelector('#appMain .view');
      if(scroller&&key!==activeTab)tabScroll.set(activeTab,scroller.scrollTop);
      activeTab=key;
      for(const tab of tabs.querySelectorAll('[role=tab]')) {const on=tab.id==='wf-tab-'+key;tab.setAttribute('aria-selected',String(on));tab.tabIndex=on?0:-1;}
      for(const [id,panel] of Object.entries(panels)) panel.hidden=id!==key;
      savebar.hidden=saveHint.hidden=!['tasks','coding'].includes(key);
      savebar.style.display=savebar.hidden?'none':'';
      measure();
      if(scroller)scroller.scrollTop=tabScroll.get(key)||0;
    }
    activate(activeTab);
    const resize=new ResizeObserver(()=>measure(true));resize.observe(tabs);for(const tab of tabs.querySelectorAll('[role=tab]'))resize.observe(tab);
    document.fonts?.ready.then(()=>measure(true));
    document.addEventListener('reviewflow:language-changed',()=>{for(const tab of tabs.querySelectorAll('[data-wf-label]'))tab.textContent=T(tab.dataset.wfLabel);measure(true);W.renderCurrentTask?.();});
    W.homeTab=activate;
    /* F13 / F14（r10-u1）：零任务与加载中必须是两种可见状态。此前占位「正在
       载入范围…」由 task-context-changed 写入，而唯一的填充点在 refreshPolicy
       的 :447 守卫之后——零任务新用户永远等不到填充，也看不到空态。这里把
       「当前任务卡」与「第一步引导卡」的渲染收成一个幂等函数，并在任务列表
       变化（loadTasks/pickTask 触发的上下文事件）时立即重算。 */
    W.renderCurrentTask=renderCurrentTask;
    document.addEventListener('reviewflow:task-list-changed',()=>renderCurrentTask());
  }
  /* 判定依据（批准单 F13 风险项「需确认零任务与加载中的判定字段可靠」）：
     S.tasks 只在 /api/tasks 成功返回后赋值（loadTasks），因此
     · S.tasks.length===0 → 确属零任务（空态）；
     · S.task 存在 → 照常显示当前任务；
     · 其余（有任务列表但 S.task 未定）→ 仍显示载入占位。
     不使用「settings===null」判空，因为设置刷新期间它本来就可能为 null。 */
  function renderCurrentTask(){
    const host=document.getElementById('wf-current-task'),first=document.getElementById('wf-first-step');
    if(!host)return;
    const noTasks=Array.isArray(S.tasks)&&S.tasks.length===0;
    const signature=JSON.stringify([scope(),S.task?.name,noTasks,RFLang.get()]);
    if(host.dataset.wfRendered===signature&&host.childNodes.length)return;
    host.dataset.wfRendered=signature;
    if(first)first.hidden=!noTasks;
    if(S.task){
      host.replaceChildren(n('b','Current task'),literal('p',S.task.name||S.task.task_id),button('Continue title-and-abstract screening',()=>go('screen')),button('Full-text screening',()=>go('fulltext')),button('Continue data extraction & coding',()=>go('bench')));
      return;
    }
    if(noTasks){
      host.replaceChildren(n('b','No task yet'),
        n('p','Create a task to import records and start screening.',{class:'hint'}),
        button('Create a task',()=>{window.WorkflowControls?.homeTab?.('tasks');document.getElementById('newTaskName')?.focus();}));
      return;
    }
    host.replaceChildren(n('p','Loading scope…'));
  }
  function policyEditor() {
    if(!settings || W.settingsDirty()) return;
    const signature=JSON.stringify([scope(),policySignature(settings),settings.sources,RFLang.get()]);
    if(editorRendered===signature&&draft){draft.version=settings.version;return;}
    editorRendered=signature;
    draft=structuredClone(settings);
    draftBaseline=draftSignature(draft);
    $('#wf-team-title').textContent=T(settings.task_kind==='team'?'Team Management':'Task workflow');
    $('#teamTitle').textContent=T('Members & progress');
    const host=$('#wf-policy');host.replaceChildren(n('h4','Sources available by stage'),n('p','Imported sources are discovered automatically. Selected sources limits work; Paused retains read-only access.',{class:'hint'}));
    for(const stage of stages) {
      const row=n('fieldset',null,{class:'wf-policy-stage'});row.append(n('legend',stage==='screen'?'Title-and-abstract screening':stage==='fulltext'?'Full-text screening':'Coding'));
      const select=n('select',null,{class:'input','aria-label':T('Permitted sources by stage')});
      for(const value of ['all','selected','paused'])select.append(n('option',{all:'All sources',selected:'Selected sources',paused:'Paused (read-only)'}[value],{value}));
      select.value=draft.stages[stage].mode;select.disabled=!settings.can_manage;
      const sourceList=n('div',null,{class:'wf-source-checks'});
      for(const source of settings.sources||[]) {
        const check=n('input',null,{type:'checkbox',value:source.id});check.checked=draft.stages[stage].source_ids.includes(source.id);check.disabled=!settings.can_manage;
        check.addEventListener('change',()=>{draft.stages[stage].source_ids=[...sourceList.querySelectorAll('input:checked')].map(c=>c.value);settingsDirty=true;});
        sourceList.append(el('label',{},check,source.reserved?n('span','Source not recorded'):literal('span',source.name)));
      }
      sourceList.hidden=select.value!=='selected';select.addEventListener('change',()=>{draft.stages[stage].mode=select.value;sourceList.hidden=select.value!=='selected';settingsDirty=true;});
      row.append(select,sourceList);host.append(row);
    }
    const fields=$('#wf-requirements');fields.replaceChildren(n('h3','Coding dimensions requiring evidence'),n('p',"Select dimensions that require evidence, not required coding values. Nonblank notes or one image satisfy the evidence requirement. Missing evidence can be saved as a draft but does not count as complete.",{class:'hint'}));
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
    if(savingSettings||!draft || !settings.can_manage)return false;
    for(const stage of stages) if(draft.stages[stage].mode==='selected'&&!draft.stages[stage].source_ids.length)throw Error(T('Select at least one source, or choose Paused.'));
    const current=scope();
    const payload={expected_version:draft.version,stages:structuredClone(draft.stages),evidence_required_field_ids:[...draft.evidence_required_field_ids]};
    savingSettings=true;
    const buttons=[...document.querySelectorAll('.wf-settings-save button')],states=buttons.map(button=>button.disabled);
    buttons.forEach(button=>button.disabled=true);$('#wf-settings-notice').textContent=T('Saving…');
    try{
    const result=await api(taskApi()+'/workflow-settings',{method:'PUT',body:payload});
    if(current!==scope())return false;
    draftBaseline=draftSignature(payload);
    settingsDirty=JSON.stringify(payload.stages)!==JSON.stringify(draft.stages)||JSON.stringify(payload.evidence_required_field_ids)!==JSON.stringify(draft.evidence_required_field_ids);
    if(result.version!=null)draft.version=result.version;
    await W.refresh('saved');$('#wf-settings-notice').textContent=T('Saved.');return !settingsDirty;
    }catch(error){if(current===scope())$('#wf-settings-notice').textContent=W.errorText(error);throw error;}
    finally{savingSettings=false;buttons.forEach((button,index)=>button.disabled=states[index]);}
  };
  function sourceEditor() {
    const host=$('#wf-sources');host.replaceChildren(n('h4','Imported sources'));
    if(!settings.sources?.length)host.append(n('p','Sources appear automatically after an import.',{class:'hint'}));
    for(const source of settings.sources||[]) {
      const row=n('div',null,{class:'row'}),name=el('input',{class:'input','aria-label':T('Source'),'data-wf-aria':'Source',value:W.sourceLabel(source),translate:source.reserved?'yes':'no',maxlength:'200'});name.readOnly=true;name.disabled=source.reserved;
      trackLibrary(name);
      name.addEventListener('input',()=>editLibrary(name));
      if(source.reserved)row.append(n('span','Source not recorded',{class:'input'}));else row.append(el('span',{class:'input',text:name.value,translate:'no'}));
      row.append(n('span',({metadata:'Database identified from RIS',confirmed:'Confirmed source',inferred:'Suggested database — review identification',file:'Source named from imported filename',legacy:'Legacy source'})[source.identification],{class:'hint'}),el('span',{text:T('Retained import records: {0} · Active articles: {1}',source.record_count||0,source.linked_count||0)}),literal('small',(source.files||[]).join(', ')));
      if(settings.can_manage&&!source.reserved) {
        const type=n('select',null,{class:'input','aria-label':T('Source type')});
        type.append(n('option','Needs confirmation',{value:''}));
        for(const value of ['database','register','website','organisation','citation_searching','other'])type.append(n('option',{database:'Database',register:'Registry',website:'Website',organisation:'Organisation',citation_searching:'Citation searching',other:'Other'}[value],{value}));
        type.value=source.source_type||'';type.addEventListener('change',()=>editLibrary(name));
        trackLibrary(name,()=>JSON.stringify([name.value,type.value]));name.wfControls=[type];
        name.wfSave=async()=>{const sent=name.value,selected=type.value;await api(taskApi()+'/sources/'+encodeURIComponent(source.id),{method:'PUT',body:{name:sent,active:source.active,expected_version:settings.version,...(selected?{source_type:selected}:{})}});if(name.value===sent&&type.value===selected)cleanLibrary(name);await W.refresh('source');return !dirtyFields.has(name);};
        row.append(type,button('Confirm source identification',()=>run(name.wfSave)),button(source.active?'Pause source':'Activate source',()=>run(async()=>{await api(taskApi()+'/sources/'+encodeURIComponent(source.id),{method:'PUT',body:{name:source.name,active:!source.active,expected_version:settings.version}});await W.refresh('source');})));
        if(source.suggestion)row.append(literal('small',source.suggestion));
      }
      host.append(row);
    }
  }
  async function loadReasons(refresh=null,prefetched=null) {
    const current=scope(),ticket=++reasonsTicket;
    const results=prefetched||await Promise.all(['screen','fulltext'].map(stage=>api(taskApi()+'/reason-library?stage='+stage)));
    if(current!==scope()||ticket!==reasonsTicket||(refresh!=null&&refresh!==refreshTicket))return;
    for(let i=0;i<2;i++)reasons[['screen','fulltext'][i]]=results[i].entries||[];
    const signature=JSON.stringify([scope(),results,RFLang.get()]);
    if(!libraryDirty&&reasonsRendered!==signature) {
      reasonsRendered=signature;
      const host=$('#wf-reasons');host.replaceChildren(n('h3','Reusable exclusion reasons'),n('p',results[0].task_kind==='personal'?'Personal exclusion reasons':'Team exclusion reasons',{class:'hint'}));
      ['screen','fulltext'].forEach((stage,i)=>{
        host.append(n('h4',stage==='screen'?'Title-and-abstract screening':'Full-text screening'));
        for(const entry of reasons[stage]) {
          const row=n('div',null,{class:'row'}),text=el('input',{class:'input',value:entry.text,'aria-label':T('Reusable exclusion reasons'),'data-wf-aria':'Reusable exclusion reasons',maxlength:'2000',translate:'no'});text.disabled=!results[i].can_manage;text.addEventListener('input',()=>editLibrary(text));row.append(text);
          trackLibrary(text);text.wfRestore=()=>{text.value=entry.text;};
          text.wfSave=()=>mutateReason(stage,'PUT',entry.id,text.value,text);
          if(results[i].can_manage)row.append(button('Save reason',()=>mutateReason(stage,'PUT',entry.id,text.value,text)),button('Delete reason',()=>mutateReason(stage,'DELETE',entry.id,undefined,text)));
          host.append(row);
        }
        if(results[i].can_manage){const text=el('input',{class:'input',maxlength:'2000',placeholder:T('New reusable reason'),'aria-label':T('New reusable reason'),'data-wf-placeholder':'New reusable reason',translate:'no'});trackLibrary(text);text.wfRestore=()=>{text.value='';};text.addEventListener('input',()=>editLibrary(text));text.wfSave=()=>mutateReason(stage,'POST',null,text.value,text);host.append(text,button('Add reason',()=>mutateReason(stage,'POST',null,text.value,text)));}
      });
    }
    W.decorateReasons('screen'); W.decorateReasons('fulltext');
  }
  function mutateReason(stage,method,id,text,field=null) {return run(async()=>{if(method!=='DELETE'&&(!text?.trim()||text.trim().length>2000))throw Error(T('A reason must contain 1–2000 characters.'));await api(taskApi()+'/reason-library'+(id?'/'+encodeURIComponent(id):'')+'?stage='+stage,{method,body:method==='DELETE'?undefined:{text:text.trim(),stage}});if(!field||method==='DELETE'||field.value.trim()===text.trim()){if(field&&method==='POST')field.value='';cleanLibrary(field);}await loadReasons();return !field||!dirtyFields.has(field);});}
  /* ---- F24（r10-u1）：把「存为常用理由」移出决策行 ----
     批准单 F24：按钮与自定义输入框挤进决策行、误点即静默 POST 写库。改为收进
     理由下拉末尾的特殊选项「＋ 将此理由存为常用」——下拉项文案即说明后果，
     选中后立刻还原到先前的理由（不改变本次决策取值），保存成功后给出带「撤销」
     的 toast（撤销即 DELETE 同一条目）。写入仍走同一 reason-library 端点。 */
  const SAVE_REASON_VALUE='__save_reason__';
  function saveReasonToLibrary(stage,text){
    const reason=String(text||'').trim();
    if(!reason){toast(T('请先选择或输入要保存的理由'),'warn');return;}
    return run(async()=>{
      const scoped={task:scope(),stage,reason};
      const created=await api(taskApi()+'/reason-library?stage='+stage,{method:'POST',body:{text:reason,stage}});
      await loadReasons();
      const undoId=created?.id;
      if(!undoId){toast(T('已存为常用理由，可在「筛选设置 · 可复用排除理由」中管理'),'inc');return;}
      undoToast(T('已存为常用理由：{0}',reason),()=>run(async()=>{
        if(scoped.task!==scope())throw Error(T('The task changed. Reload the queue.'));
        await api(taskApi()+'/reason-library/'+encodeURIComponent(undoId)+'?stage='+stage,{method:'DELETE'});
        await loadReasons();
        toast(T('已撤销保存该常用理由'),'info');
      }));
    });
  }
  /* toast 的可撤销版本：与主应用的 #toasts 容器同款样式，附加一个「撤销」按钮。 */
  function undoToast(message,undo){
    const host=document.getElementById('toasts');
    if(!host){toast(message,'inc');return;}
    const box=el('div',{class:'toast t-inc wf-undo-toast',role:'status'},el('span',{text:message}));
    const action=el('button',{type:'button',class:'rf-link',text:T('撤销'),onclick:()=>{
      action.disabled=true;box.remove();
      try{Promise.resolve(undo()).catch(error=>W.error(error));}catch(error){W.error(error);}
    }});
    box.append(action);host.append(box);
    setTimeout(()=>box.classList.add('hide'),6000);
    setTimeout(()=>box.remove(),6360);
  }
  W.decorateReasons=stage=>{
    const select=$(stage==='screen'?'#excReason':'#ftReason'),custom=$(stage==='screen'?'#excCustomReason':'#ftCustomReason');if(!select)return;
    const value=select.value;
    const signature=JSON.stringify([reasons[stage],!!settings?.can_manage,RFLang.get()]);
    if(select.dataset.wfLibraryRendered===signature&&select.querySelector('[value="__custom__"]'))return;
    select.dataset.wfLibraryRendered=signature;
    select.querySelectorAll('[data-library]').forEach(o=>o.remove());
    for(const entry of reasons[stage])if(![...select.options].some(o=>o.value===entry.text)){const option=el('option',{value:entry.text,text:entry.text,translate:'no','data-library':'true'});select.insertBefore(option,select.querySelector('[value="__custom__"]'));}
    if([...select.options].some(o=>o.value===value))select.value=value;
    else if(value&&value!==SAVE_REASON_VALUE){select.value='__custom__';custom.value=value;custom.hidden=false;}
    // Saving a reusable reason is an explicit action, not a selectable reason.
    select.setAttribute('aria-label',T('排除理由（必选）'));
    select.querySelector('[data-wf-save-reason]')?.remove();
    let saveAction=select.parentElement.querySelector('[data-wf-save-reason-action]');
    if(settings?.can_manage){
      if(!saveAction){
        saveAction=button('＋ 将此理由存为常用',()=>saveReasonToLibrary(stage,select.value==='__custom__'?(custom?.value||'').trim():select.value));
        saveAction.dataset.wfSaveReasonAction='1';select.after(saveAction);
      }
    }else saveAction?.remove();
    if(custom&&!custom.parentElement.classList.contains('wf-custom-reason-row')){
      /* 自定义理由仍包在 .wf-custom-reason-row 内：移动端 CSS 以该行整行排布，
         审批单 F24 只移除行内的「存为常用」按钮，不改输入框的布局契约。 */
      const row=n('div',null,{class:'wf-custom-reason-row'});select.parentElement.append(row);row.append(custom);
    }
  };
  function tools(stage) {
    const page=document.querySelector('section[data-p="'+stagePage[stage]+'"]');
    let host=page.querySelector('.wf-queue-tools');if(host)return host;
    host=n('div',null,{class:'wf-queue-tools'});
    const select=n('select',null,{class:'input','aria-label':T('Source scope')});select.dataset.stage=stage;
    select.addEventListener('change',()=>run(async()=>{const value=select.value;select.value=selected[stage];if(!await W.guard())return;selected[stage]=value;await W.reload(stage);}));
    host.append(n('label','Source scope'),select,n('span',null,{class:'wf-scope-progress',role:'status'}),button('Retry / refresh',()=>run(async()=>{if(await W.guard())await W.reload(stage);})),n('span',null,{class:'wf-readonly',role:'status'}));
    if(stage==='fulltext')host.append(n('span','初筛纳入的文献进入复筛。',{class:'hint'}));
    page.insertBefore(host,page.querySelector('.al-mode')||page.children[1]||null);
    return host;
  }
  W.clearRecovery=()=>{if(!policyDraftDirty()){$('#wf-policy-recovery').hidden=true;$('#wf-settings-notice').textContent='';}};
  W.queue=async(stage,{url=null,evidenceOnly=false,attempt=0}={})=>{
    const requested=scope();
    if(!autoReload)await W.refresh('entry',false);
    if(requested!==scope()||!settings)throw Error(T('The task changed. Reload the queue.'));
    const current=scope(),policy=settings,ticket=++tickets[stage],host=tools(stage);
    host.querySelector('.wf-scope-progress').textContent=T('Loading scope…');
    const query=new URLSearchParams({order:stage==='screen'&&order==='random'?'random':'import'});if(selected[stage])query.set('source_id',selected[stage]);
    try {
      const result=await api((url||taskApi()+'/work-queue/'+stage)+'?'+query);
      if(current!==scope()||ticket!==tickets[stage]||!W.renderAllowed())throw Error(T('The task changed. Reload the queue.'));
      if(policy.version!==settings.version||result.policy_version!==settings.version){
        if(attempt>=2)throw Error(T('The task changed. Reload the queue.'));
        if(autoReload){deferredRefresh=true;throw Error(T('The task changed. Reload the queue.'));}
        return W.queue(stage,{url,evidenceOnly,attempt:attempt+1});
      }
      queues[stage]=result;delete items[stage];delete readOnlyKeys[stage];
      const select=host.querySelector('select');select.disabled=false;select.replaceChildren(n('option','All permitted sources',{value:''}));
      for(const id of result.allowed_sources||[]){const source=policy.sources.find(source=>source.id===id);if(source)select.append(el('option',{value:source.id,text:W.sourceLabel(source),translate:source.reserved?'yes':'no'}));}
      if(selected[stage]&&![...select.options].some(o=>o.value===selected[stage])){selected[stage]='';return W.queue(stage,{url,evidenceOnly,attempt});}
      select.value=selected[stage];
      const p=result.scope_progress;host.querySelector('.wf-scope-progress').textContent=T(evidenceOnly?'Evidence scope: {0} / {1} requirements complete':'Scope: {0} / {1} complete',p.completed,p.total);
      host.querySelector('.wf-readonly').textContent=result.paused?T('Paused — saved work is read-only.'):'';
      pendingStages.delete(stage);pendingPolicyRefresh=pendingStages.size>0;
      W.clearRecovery();
      return result;
    } catch(error){if(current===scope()&&ticket===tickets[stage])host.querySelector('.wf-scope-progress').textContent=T('Queue unavailable. Use Retry / refresh.')+' '+W.errorText(error);throw error;}
  };
  W.item=async(stage,key)=>{
    const current=scope(),ticket=++tickets[stage];
    delete items[stage];
    const item=await api(taskApi()+'/work-items/'+stage+'/'+encodeURIComponent(key));
    if(current!==scope()||ticket!==tickets[stage]||!W.renderAllowed())return null;
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
    const writable=item && item.key===key&&item.writable&&settings?.stages?.[stage]?.mode!=='paused'&&item.policy_version===settings?.version;
    const ids=stage==='screen'?['btnInc','btnExc']:stage==='fulltext'?['ftInc','ftExc']:['cbSave'];
    for(const id of ids){const b=$('#'+id);if(b)b.disabled=!writable;}
    const notice=tools(stage).querySelector('.wf-readonly');notice.replaceChildren();if(item && item.key===key&&!item.writable)notice.append(n('span','Read-only: current policy or source scope does not allow saving.'),document.createTextNode(' '),n('span',W.reason(item.reason)));
    if(stage!=='coding')W.decorateReasons(stage);
  };
  W.canSave=(stage,decision)=>{
    if(autoReload)return false;
    if(decision==='maybe'&&stage!=='coding'){toast(RFServerMessage('workflow.maybe_disabled'),'warn');return false;}
    const item=items[stage],key=stage==='screen'?S.articles[S.scrIdx]?.zotero_key:stage==='fulltext'?curFt()?.zotero_key:S.cbQueue[S.cbIdx]?.zotero_key;
    if(!item||item.key!==key||!item.writable||settings?.stages?.[stage]?.mode==='paused'||item.policy_version!==settings?.version){toast(T('This item is read-only under the current workflow policy.'),'warn');return false;}
    return true;
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
    if(stage==='coding'&&field){const candidate=field.dimension_id!=null?document.getElementById('wf-field-'+field.dimension_id):null;const card=candidate&&candidate.dataset.fieldDefinition===JSON.stringify(field.definition)?candidate:null;if(card){card.querySelector('details')?.setAttribute('open','');card.querySelector('textarea,input,select')?.focus();}else toast(T('The historical field is no longer mapped. Current saved work is open.'),'info');}
    return true;
  };
  W.setOrder=async mode=>{
    if(!await W.guard())return;
    order=mode;S.al.mode=mode==='ai'?'ai':'import';
    $('#alModeRandom').classList.toggle('on',mode==='random');
    if(S.task)await loadScreen();
    $('#alModeImport').classList.toggle('on',mode==='import');
  };
  let binaryResetSummary = null;
  let binaryResetRequest = null;
  function renderBinaryReset() {
    const host = $('#wf-binary-reset');
    if (!host) return;
    const totals = binaryResetSummary?.totals;
    const stage2 = totals ? totals.stage2_reset + totals.stage2_cascade_reset : 0;
    host.hidden = !totals || (totals.stage1_reset === 0 && stage2 === 0);
    host.replaceChildren();
    if (host.hidden) return;
    const affected = binaryResetSummary.members.filter(member =>
      member.stage1_reset || member.stage2_reset || member.stage2_cascade_reset);
    const available = affected.filter(member => member.backup?.exists);
    const backups = available.map(member => el('li', {
      'data-rf-verbatim':'', text:member.backup.name, title:member.backup.sha256
    }));
    host.append(el('p', {role:'status', text:RFServerMessage('workflow.binary_reset_summary', {
      stage1:totals.stage1_reset, stage2
    })}), n('p', T('可用的升级前备份：{0} / {1}。建议先完成筛选训练，再重新筛选。', available.length, affected.length)));
    if (backups.length) host.append(el('ul', {}, backups));
    const training = button('打开筛选训练', () => window.PilotScreening?.open());
    training.id = 'wf-binary-reset-training';
    host.append(training);
  }
  async function loadBinaryReset() {
    if (binaryResetSummary || binaryResetRequest) return;
    const request = api(taskApi()+'/binary-reset-summary');
    binaryResetRequest = request;
    try {
      const result = await request;
      if (binaryResetRequest !== request) return;
      binaryResetSummary = result;
      renderBinaryReset();
    } catch (_) {
      // The optional summary does not block opening a task.
    } finally {
      if (binaryResetRequest === request) binaryResetRequest = null;
    }
  }
  document.addEventListener('reviewflow:language-changed', renderBinaryReset);
  async function refreshPolicy(reason='poll',reload=true){
    if(!S.task||!S.user)return;
    if(autoReload){if(reason!=='entry')deferredRefresh=true;return;}
    const current=scope(),ticket=++refreshTicket,revision=editRevision,page=S.page;
    const [result,reasonResults]=await Promise.all([api(taskApi()+'/workflow-settings'),Promise.all(['screen','fulltext'].map(stage=>api(taskApi()+'/reason-library?stage='+stage)))]);
    if(current!==scope()||ticket!==refreshTicket||(settings&&result.version<settings.version))return;
    const previous=settings;
    const policyChanged=!!previous&&policySignature(previous)!==policySignature(result);
    for(const stage of stages){
      if(previous&&stageSignature(previous,stage)!==stageSignature(result,stage))pendingStages.add(stage);
      // An unrelated policy version must not invalidate the article being read.
      // Keep the server concurrency token current without replacing its DOM.
      if(!pendingStages.has(stage)){
        if(items[stage])items[stage].policy_version=result.version;
        if(queues[stage])queues[stage].policy_version=result.version;
      }
    }
    pendingPolicyReview=pendingPolicyReview||policyChanged;
    pendingPolicyRefresh=pendingStages.size>0;
    const changed=pendingPolicyRefresh;
    if(draft&&!policyChanged)draft.version=result.version;
    settings=result;
    window.RFArbitrationReader?.sync();
    void loadBinaryReset();
    for(const [stage,ids] of [['screen',['btnInc','btnExc']],['fulltext',['ftInc','ftExc']]])if(result.stages[stage].mode==='paused')for(const id of ids){const control=$('#'+id);if(control)control.disabled=true;}
    W.anyDirty(); // Refresh value-based dirty flags before rendering the policy.
    $('#wf-settings-notice').textContent=pendingPolicyReview&&policyDraftDirty()?T('Workflow changed. Your draft is retained. Refresh the policy, review the changes and retry.'):'';
    if(pendingPolicyReview&&policyDraftDirty())$('#wf-policy-recovery').hidden=false;else W.clearRecovery();
    if(!W.settingsDirty())policyEditor();
    await loadReasons(ticket,reasonResults); // Libraries can change without a policy version increment.
    if(current!==scope()||ticket!==refreshTicket)return;
    renderCurrentTask();
    window.WorkflowEvidence?.discovery(result.submission_evidence);
    window.WorkspaceUndo?.sync();
    if(S.page==='home'&&window.WorkflowImports)await WorkflowImports.notice();
    if(current!==scope()||ticket!==refreshTicket)return;
    if(changed&&(W.anyDirty()||pendingWork()||revision!==editRevision))retainDraft();
    const currentStage={bench:'coding'}[page]||page;
    if(changed&&pendingStages.has(currentStage)&&reload&&page===S.page&&stages.includes(currentStage))await automaticReload(currentStage,{scope:current,page,ticket,revision});
    if(reason==='conflict'&&draft&&!settingsDirty)draft.version=result.version;
    if(reason==='reload'&&!W.anyDirty())W.clearRecovery();
  };
  function pollNotice(failed){
    let host=document.getElementById('wf-connection-notice');
    if(!failed){host?.remove();pollNoticeDismissed=false;return;}
    if(host||pollNoticeDismissed)return;
    host=n('div',null,{id:'wf-connection-notice',class:'card',role:'status',style:'margin:8px 22px;padding:10px 14px;flex:none'});
    host.append(n('p','Connection interrupted. Your work is retained. Retrying automatically.'),
      button('Retry / refresh',()=>run(()=>W.refresh('retry'))),
      button('Dismiss',()=>{pollNoticeDismissed=true;host.remove();}));
    document.querySelector('#appMain .top').after(host);
  }
  W.refresh=async(reason='poll',reload=true)=>{
    if(!S.task||!S.user)return null;
    if(['poll','focus'].includes(reason)&&Date.now()<nextPollAt)return settings;
    const requested=scope();
    if(autoReload){if(reason!=='entry')deferredRefresh=true;return settings;}
    if(policyFlight?.scope===requested){
        try{await policyFlight.promise;}catch(error){if(['poll','focus'].includes(reason))return settings;throw error;}
      if(requested!==scope())throw Error(T('The task changed. Reload the queue.'));
      // Mutation/event refreshes arriving during a read must observe a fresh read.
      if(!['entry','bootstrap','focus','poll','context'].includes(reason))return W.refresh(reason,reload);
      if(!settings)throw Error(T('The task changed. Reload the queue.'));
      return settings;
    }
    const flight={scope:requested,promise:null};policyFlight=flight;
    flight.promise=refreshPolicy(reason,reload);
    try{
      await flight.promise;
      if(requested!==scope()||!settings)throw Error(T('The task changed. Reload the queue.'));
      pollFailures=0;nextPollAt=0;pollNotice(false);
      return settings;
    }catch(error){
      if(requested===scope()&&['poll','focus','retry'].includes(reason)){
        if(error.status===401)return settings;
        pollFailures++;nextPollAt=Date.now()+Math.min(300000,30000*2**(pollFailures-1));
        pollNotice(true);
        return settings;
      }
      throw error;
    }finally{if(policyFlight===flight)policyFlight=null;}
  };
  W.guard=async()=>{
    if(autoReload){toast(T('Wait for the current refresh to finish.'),'info');return false;}
    if(pendingWork()){toast(T('Wait for the current save or upload to finish.'),'warn');return false;}
    if(!W.anyDirty())return true;
    if(dialogPromise)return dialogPromise;
    const invokingElement=document.activeElement;
    dialogPromise=new Promise(resolve=>{
      const dialog=n('dialog',null,{class:'wf-dirty-dialog','aria-label':T('Unsaved changes')});
      dialog.append(n('h3','Unsaved changes'),n('p','Save your changes, discard them, or stay here.'));
      // Hidden home settings must be editable where the user is asked to save.
      const editors=[];
      const validationMessage=n('p',null,{class:'wf-draft-error',role:'alert',id:'wf-guard-validation',hidden:true});
      function bindEditor(field,input,validateReason=false){
        input.id='wf-guard-field-'+editors.length;input.removeAttribute('name');input.value=field.value;input.checked=field.checked;
        input.setAttribute('aria-describedby','wf-guard-validation');
        const sync=()=>{field.value=input.value;if(input.type==='checkbox')field.checked=input.checked;field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));input.setCustomValidity('');validationMessage.hidden=true;};
        input.addEventListener('input',sync);input.addEventListener('change',sync);editors.push({input,validateReason});
      }
      function addEditor(field,host,validateReason=false){
        const input=field.cloneNode(true),label=el('label',{class:'wf-draft-field'});
        label.append(literal('span',field.id?document.querySelector('label[for="'+field.id+'"]')?.textContent||field.getAttribute('aria-label'):field.getAttribute('aria-label')||field.placeholder),input);host.append(label);
        bindEditor(field,input,validateReason);
      }
      if(settingsDirty){
        const fields=n('fieldset',null,{class:'wf-draft-fields wf-draft-policy'});fields.append(n('legend','Task workflow'));
        for(const selector of ['#wf-policy','#wf-requirements']){
          const original=$(selector),copy=original.cloneNode(true);copy.removeAttribute('id');copy.hidden=false;
          const originals=original.querySelectorAll('input,select'),copies=copy.querySelectorAll('input,select');copies.forEach((input,index)=>bindEditor(originals[index],input));
          for(const stage of copy.querySelectorAll('.wf-policy-stage')){const mode=stage.querySelector('select'),sources=stage.querySelector('.wf-source-checks');mode.addEventListener('change',()=>sources.hidden=mode.value!=='selected');}
          fields.append(copy);
        }
        dialog.append(fields);
      }
      if(alDirty){const fields=n('fieldset',null,{class:'wf-draft-fields'});fields.append(n('legend','Machine Learning'));for(const id of alIds)addEditor($('#'+id),fields);dialog.append(fields);}
      if(libraryDirty)for(const sourceFields of [true,false]){
        const pending=[...dirtyFields].filter(field=>!!field.wfControls===sourceFields);if(!pending.length)continue;
        const fields=n('fieldset',null,{class:'wf-draft-fields'});fields.append(n('legend',sourceFields?'Imported sources':'Reusable exclusion reasons'));
        for(const field of pending){if(sourceFields){for(const control of field.wfControls){addEditor(control,fields);fields.lastElementChild.firstElementChild.textContent=T('Source type')+' · '+field.value;}}else addEditor(field,fields,true);}dialog.append(fields);
      }
      /* F27 半步：把不可保存的「理由草稿」摆在模态正文里（题名＋所选理由），
         并给一键「清空草稿」——此前草稿既不可见也无法就地放弃，用户只能靠
         Discard 连其它改动一起丢掉。 */
      const reasonDraft=W.reasonDraft(),reasonDraftOnly=W.reasonDraftOnly();
      if(reasonDraft){
        const fields=n('fieldset',null,{class:'wf-draft-fields wf-draft-reason'});
        fields.append(n('legend','Unsaved screening reason'));
        /* 注意：n() 只有 (tag,text,attrs) 三参，子节点必须用 el() 直接构造——
           用 n('p',null,{...},child) 会把子节点丢掉，草稿就等于没显示。 */
        fields.append(el('p',{class:'hf-reason-draft'},literal('strong',reasonDraft.title||''),el('br'),
          el('span',{text:reasonDraft.reason?T('所选理由：{0}',reasonDraft.reason):T('尚未选择理由')})));
        fields.append(n('p','理由随纳入或排除决定一同保存。关闭此对话框后即可选择决定。',{class:'hint'}));
        const clearDraft=button('Clear draft',()=>{
          W.clearReasonDraft(reasonDraft.stage);
          finish(!W.anyDirty());
        });
        clearDraft.dataset.wfClearReasonDraft='1';
        fields.append(clearDraft);
        dialog.append(fields);
      }
      dialog.append(validationMessage);
      const finish=value=>{dialog.close();dialog.remove();dialogPromise=null;if(!value&&invokingElement?.isConnected)invokingElement.focus({preventScroll:true});resolve(value);};
      const saveButton=button('Save',()=>run(async()=>{
        if(!W.anyDirty()){finish(true);return;}
        for(const {input,validateReason} of editors){
          if(validateReason&&(!input.value.trim()||input.value.trim().length>2000))input.setCustomValidity(T('A reason must contain 1–2000 characters.'));
          if(!input.checkValidity()){
            const label=input.closest('label')?.querySelector('span')?.textContent||input.getAttribute('aria-label')||'';
            validationMessage.textContent=label+'：'+(input.type==='number'?T('Enter an integer from {0} to {1}.',input.min,input.max):input.validationMessage);validationMessage.hidden=false;input.focus();return;
          }
        }
        const controls=[...dialog.querySelectorAll('button')];controls.forEach(control=>control.disabled=true);
        try{
        if(W.decisionDirty()){toast(T('Choose a screening decision to save this reason before leaving.'),'warn');return;}
        if(settingsDirty&&!await W.saveSettings())return;
        for(const field of [...dirtyFields])if(!field.wfSave||!await field.wfSave())return;
        if(alDirty&&!await saveALSettings())return;
        if(window.WorkflowEvidence?.dirty()&&!await WorkflowEvidence.save())return;
        if(window.WorkflowExtraction?.dirty()&&!await WorkflowExtraction.save())return;
        for(const draft of registeredDrafts.values())if(draft.dirty?.()&&(!draft.save||!await draft.save()))return;
        finish(true);
        }finally{controls.forEach(control=>control.disabled=false);}
      }));
      /* F27 半步：无物可存的理由草稿 → Save 置灰，title 说明该怎么做
         （先按下 1/2/3 做决策，理由才会随决策保存；或「清空草稿」）。 */
      if(reasonDraftOnly){
        saveButton.disabled=true;
        saveButton.title=T('请先保存纳入或排除决定，或清空理由草稿。');
        saveButton.setAttribute('aria-disabled','true');
      }
      dialog.append(saveButton,button('Discard',()=>{settingsDirty=false;for(const field of dirtyFields)field.wfRestore?.();libraryDirty=false;dirtyFields.clear();if(alBaseline)for(const id of alIds)$('#'+id).value=alBaseline[id];alDirty=false;window.WorkflowEvidence?.discard();window.WorkflowExtraction?.discard();for(const draft of registeredDrafts.values())if(draft.dirty?.())draft.discard?.();for(const stage of ['screen','fulltext'])W.markDecisionClean(stage);draftBaseline=null;editorRendered=null;policyEditor();W.clearRecovery();finish(true);}),button('Stay',()=>finish(false)));
      dialog.addEventListener('cancel',event=>{event.preventDefault();if(pendingWork())return;finish(false);});document.body.append(dialog);dialog.showModal();dialog.lastElementChild?.focus();
    });
    return dialogPromise;
  };
  homeTabs();stages.forEach(tools);
  const overallLabels=()=>{for(const id of ['capScreen','capFull','capCode'])$('#'+id)?.setAttribute('data-wf-overall-label',T('Overall'));};overallLabels();
  document.addEventListener('reviewflow:language-changed',overallLabels);
  const random=button('Random',()=>run(()=>W.setOrder('random')));random.id='alModeRandom';
  const shuffleIcon=document.createElementNS('http://www.w3.org/2000/svg','svg');shuffleIcon.setAttribute('viewBox','0 0 24 24');shuffleIcon.setAttribute('class','al-import-icon');shuffleIcon.setAttribute('aria-hidden','true');shuffleIcon.setAttribute('focusable','false');
  const shufflePath=document.createElementNS(shuffleIcon.namespaceURI,'path');shufflePath.setAttribute('d','M3 6h3c5 0 7 12 12 12h3m-4-4 4 4-4 4M3 18h3c2 0 3-2 4-4m4-4c1-2 2-4 4-4h3m-4-4 4 4-4 4');shuffleIcon.append(shufflePath);random.replaceChildren(shuffleIcon,n('span','Random'));
  $('#alModeAI').after(random,button('Shuffle remaining again',()=>run(async()=>{if(!await W.guard())return;const q=selected.screen?'?source_id='+encodeURIComponent(selected.screen):'';await api(taskApi()+'/work-queue/screen/shuffle'+q,{method:'POST'});order='random';S.al.mode='import';await loadScreen();})));
  $('#alModeAI').querySelector('span').textContent=T('Machine Learning');
  $('#alModeImport').querySelector('span').textContent=T('Import');
  alBaseline=alValues();
  for(const id of alIds)for(const event of ['input','change'])$('#'+id).addEventListener(event,syncALDirty);
  for(const event of ['reviewflow:al-settings-loaded','reviewflow:al-settings-saved'])document.addEventListener(event,e=>{alBaseline=e.detail?.values||alValues();syncALDirty();});
  document.addEventListener('reviewflow:task-context-changed',()=>{
    if(context===scope())return;
    pendingStages.clear();editorRendered=null;reasonsRendered=null;pollFailures=0;nextPollAt=0;pollNotice(false);
    binaryResetSummary = null;
    binaryResetRequest = null;
    renderBinaryReset();
    context=scope();refreshTicket++;for(const stage of stages){tickets[stage]++;selected[stage]='';delete items[stage];delete queues[stage];delete decisionBaselines[stage];}settings=null;draft=null;draftBaseline=null;alBaseline=alValues();policyFlight=null;pendingPolicyRefresh=false;pendingPolicyReview=false;settingsDirty=false;libraryDirty=false;dirtyFields.clear();alDirty=false;$('#wf-policy-recovery').hidden=true;
    for(const id of ['scrBody','scrHist','scrHistCounts','scrPos','ftDocTitle','ftDocLabel','ftDocChip','cbTitle','cbForms','cbLabel','cbStatus','cbSaveProg','capScreen','capFull','capCode','annList'])document.getElementById(id)?.replaceChildren();
    for(const id of ['wf-current-task','wf-policy','wf-reasons','wf-requirements','wf-sources'])document.getElementById(id)?.replaceChildren(n('p','Loading scope…'));
    for(const id of ['barScreen','barFull','barCode']){const bar=document.getElementById(id);if(bar)bar.style.width='0%';}
    for(const id of ['btnInc','btnExc','ftInc','ftExc','cbSave']){const control=document.getElementById(id);if(control)control.disabled=true;}
    for(const stage of stages){const host=tools(stage);host.querySelector('select').replaceChildren();host.querySelector('select').disabled=true;host.querySelector('.wf-scope-progress').textContent=T('Loading scope…');host.querySelector('.wf-readonly').textContent='';}
    /* F13：切到零任务上下文时不留在「Loading scope…」——占位只对有任务但尚未
       载入完成的情形有意义。 */
    W.renderCurrentTask?.();
    if(S.task&&S.user)run(()=>W.refresh('context',false));
  });
  document.addEventListener('reviewflow:workspace-changed',()=>run(()=>W.refresh('entry',false)));
  document.addEventListener('reviewflow:workflow-changed',event=>{if(String(event.detail?.task_id)===String(S.task?.task_id))run(()=>W.refresh(event.detail.reason||'event'));});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)run(()=>W.refresh('focus'));});
  window.addEventListener('focus',()=>run(()=>W.refresh('focus')));
  setInterval(()=>{if(!document.hidden&&S.user&&S.task)run(()=>W.refresh());},30000);
  window.addEventListener('beforeunload',event=>{if(W.anyDirty()||window.WorkflowEvidence?.uploading()||window.WorkflowExtraction?.saving()){event.preventDefault();event.returnValue='';}});
  window.addEventListener('load',()=>run(async()=>{if(!S.task)return;await W.refresh('bootstrap',false);const stage={bench:'coding'}[S.page]||S.page;if(stages.includes(stage))await automaticReload(stage,{scope:scope(),page:S.page,ticket:refreshTicket,revision:editRevision});}));
})();
