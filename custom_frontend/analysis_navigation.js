/* ReviewFlow analysis workspace.
 * Existing controls are MOVED, never recreated, so their API handlers and input state
 * remain intact. Draft model controls are intentionally separate from committed inputs.
 */
(function () {
  'use strict';
  const page = document.querySelector('.page[data-p="analysis"]');
  if (!page) return;
  const $id = id => document.getElementById(id);
  const nav = page.querySelector('.an-jump');
  const workspace = page.querySelector('.an-workspace');
  const layout = page.querySelector('.an-layout');
  const effects = $id('anJumpEffects');
  const originalChildren = [...effects.children];
  const split = originalChildren.indexOf($id('anConfigSummary'));
  const entry = originalChildren.slice(2, split);
  const synthesis = originalChildren.slice(split);
  const entryIntro = originalChildren[1];
  const entryStart = $id('anEntryStart').parentElement;
  const entryMode = $id('anEntryMode');
  const guide = $id('anEntryGuidePanel');
  const alternatives = entry.filter(node => node.matches('details') && node !== guide);
  const basicEntry = entry.slice(entry.indexOf(guide) + 1, entry.indexOf(alternatives[0]));
  const intervalCards = new Set(['anIntervalsPanel','anSurvivalIntervalsPanel','anNntIntervalsPanel']);
  const registry = window.RFNavigation;
  const h = (tag,attrs,...children) => el(tag,attrs,...children);
  const button = (id,text,cls='btn b-out') => h('button',{id,type:'button',class:cls,text:T(text)});
  const plain = text => h('span',{translate:'no',text:text || '—'});
  const state = {view:'',tab:'results',editing:false,editor:'model',draft:null,snapshot:null,status:'empty',lastByGroup:{},scroll:new Map(),tool:null,catalog:true,catalogGroup:null,catalogReturn:null};
  const committed = ['anModel','anCiMethod'].map($id);
  const settings = $id('anModelSettings');
  const modelLabels = {fixed:'固定效应 · 共同效应',random:'随机效应 · DL',random_pm:'随机效应 · PM',random_reml:'随机效应 · REML'};
  const ciLabels = {normal:'正态 / Wald · 95%',hksj:'修正 HKSJ · 95%（随机效应）'};
  const readScope = () => Object.fromEntries(['anComparison','anOutcome','anTimepoint','anMeasure','anModel','anCiMethod'].map(id=>[id,$id(id).value]));
  const draftDirty = () => !!state.draft && committed.some(input=>state.draft[input.id] !== input.value);

  // The workspace title replaces the duplicated full-width navigation in analysis mode.
  const pageHeading = page.querySelector(':scope > h2');
  const pageHeader = h('header',{class:'an-page-header'});
  pageHeading.before(pageHeader); pageHeader.append(pageHeading);
  const headingTools = h('div',{class:'an-page-tools'});
  const directoryToggle = button('anDirectoryToggle','打开分析目录','btn an-quiet-button');
  directoryToggle.setAttribute('aria-controls','anDirectory'); directoryToggle.setAttribute('aria-expanded','false');
  nav.id = 'anDirectory'; headingTools.append(directoryToggle); pageHeader.append(headingTools);
  const top = document.querySelector('#appMain .top');
  // Global workspace_shell.js owns the shared, interactive breadcrumb.
  const pageNotes = [...page.querySelectorAll(':scope > p.hint:not(#anLoadStatus)')];
  pageNotes.forEach(node=>node.classList.add('an-intro-note'));
  const navTitle=h('strong',{text:T('分析目录')});
  const search=h('input',{id:'anMethodSearch',class:'input an-nav-search',type:'search','aria-label':T('查找分析方法'),placeholder:T('查找分析方法')});
  const allButton=button('anShowAllMethods','全部方法','an-all-methods');
  allButton.setAttribute('aria-controls','anMethodCatalog');
  const categoryList=h('div',{class:'an-jump-categories'});
  const noMatch=h('p',{class:'hint',text:T('未找到匹配的方法'),hidden:true});
  nav.replaceChildren(navTitle,search,allButton,categoryList,noMatch);
  const groupButtons=[],groupPanels=[],links=[];
  for(const group of registry.groups){
    const groupButton=button('anGroupButton-'+group.id,group.label,'an-category-button');
    groupButton.dataset.anGroup=group.id;groupButton.setAttribute('aria-controls','anGroup-'+group.id);
    const panel=h('div',{class:'an-jump-group',id:'anGroup-'+group.id,'data-an-group-panel':group.id,hidden:true});
    const section=h('div',{class:'an-category-node','data-category':group.id},groupButton,panel);
    for(const tool of registry.methods(group.id)){
      const link=h('a',{href:registry.href(registry.routeFor(tool)),'data-an-tool':tool.id,'data-an-view':tool.view,text:T(tool.label)});
      panel.append(link);links.push(link);
    }
    categoryList.append(section);groupButtons.push(groupButton);groupPanels.push(panel);
  }
  // Keep the existing Bayesian forms mounted; only their navigation ownership changes.
  for (const id of ['anBayesianPanel','anRobustBayesPanel']) {
    const panel=$id(id);panel.classList.add('card');workspace.append(panel);
  }
  const cards=[...workspace.querySelectorAll(':scope > .card')];
  // Late-loaded modules share the same navigation visibility as existing methods.
  new MutationObserver(records=>{
    for(const record of records)for(const card of record.addedNodes){
      if(card.nodeType!==1||!card.matches('.card')||cards.includes(card))continue;
      cards.push(card);
      cardHeadings.set(card,card.querySelector(':scope>b,:scope>summary,:scope>h3'));
      card.classList.toggle('an-view-hidden',state.catalog||state.tool?.view!==card.id);
    }
  }).observe(workspace,{childList:true});
  const cardHeadings=new Map(cards.map(card=>[card,card.querySelector(':scope>b,:scope>summary')]));
  const catalog=h('section',{id:'anMethodCatalog',class:'an-method-catalog',tabindex:'-1','aria-labelledby':'anCatalogTitle',hidden:true});
  const catalogTitle=h('h3',{id:'anCatalogTitle',text:T('全部分析方法')});
  const catalogDescription=h('p',{class:'an-catalog-description'});
  const catalogCount=h('span',{id:'anCatalogCount',role:'status','aria-live':'polite'});
  const catalogSearch=h('input',{id:'anCatalogSearch',class:'input',type:'search',placeholder:T('查找分析方法'),'aria-label':T('查找分析方法')});
  const clearSearch=button('anClearMethodSearch','清空搜索','btn an-quiet-button');clearSearch.hidden=true;
  const returnButton=button('anReturnToMethod','返回当前方法','btn an-quiet-button');returnButton.hidden=true;
  catalog.append(h('header',{class:'an-catalog-heading'},h('div',{},catalogTitle,catalogDescription),returnButton),
    h('div',{class:'an-catalog-search'},catalogSearch,clearSearch,catalogCount));
  const catalogSections=[];
  for(const group of registry.groups){
    const heading=h('a',{href:registry.href({page:'analysis',group:group.id}),'data-catalog-group':group.id,text:T(group.label)});
    const list=h('ul',{class:'an-catalog-methods'});
    const section=h('section',{class:'an-catalog-group','data-category':group.id},h('header',{},h('h4',{},heading),h('p',{text:T(group.description)})),list);
    for(const tool of registry.methods(group.id)){
      const link=h('a',{href:registry.href(registry.routeFor(tool)),'data-an-tool':tool.id},h('span',{text:T(tool.label)}));
      const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.setAttribute('viewBox','0 0 20 20');icon.setAttribute('aria-hidden','true');
      const path=document.createElementNS(icon.namespaceURI,'path');path.setAttribute('d','M4 10h11m-4-4 4 4-4 4');path.setAttribute('fill','none');path.setAttribute('stroke','currentColor');path.setAttribute('stroke-width','1.5');path.setAttribute('stroke-linecap','round');path.setAttribute('stroke-linejoin','round');icon.append(path);link.append(icon);
      list.append(h('li',{'data-catalog-tool':tool.id},link));
    }
    catalogSections.push(section);catalog.append(section);
  }
  const catalogEmpty=h('div',{class:'an-catalog-empty',hidden:true},h('h4',{text:T('未找到匹配的方法')}),h('p',{text:T('请选择一种方法继续；已有输入和结果会保留。')}));catalog.append(catalogEmpty);
  workspace.prepend(catalog);

  // Shared scope: the study selector is an ENTRY control, not a synthesis filter.
  const scope=$id('anScope'), scopeRow=scope.querySelector('.row');
  $id('anEffectStudy').before(h('span',{id:'anStudySelectorAnchor',hidden:true}));
  const studyField=h('label',{class:'an-study-entry',text:T('研究（仅用于录入）')},$id('anEffectStudy'));
  entryStart.after(studyField);
  const scopeLabels={anComparison:'比较组',anOutcome:'结局',anTimepoint:'时间点',anMeasure:'效应指标'};
  for(const [id,label] of Object.entries(scopeLabels)) {
    const input=$id(id); const field=h('label',{class:'an-scope-field',for:id},h('span',{text:T(label)}),input);scopeRow.append(field);
  }
  scope.append($id('anMeasureCaution'));
  scope.append(h('p',{class:'an-scope-note',text:T('当前范围按比较组、结局、时间点和效应指标筛选，不按单个研究筛选。')}));
  // Provenance is shared by every entry format. It must not disappear with the
  // basic numerical input rows: alternative save handlers use these same IDs.
  const entryModeRow=entryMode.closest('.an-entry-mode-row');
  const entryProvenance=h('section',{class:'an-entry-provenance'},
    h('label',{class:'an-provenance-source',for:'anSource'},h('span',{text:T('来源报告')}),$id('anSource')),
    $id('anSourceLocator').parentElement,$id('anEffectDirection').parentElement);
  entryModeRow.after(entryProvenance);
  const effectVariants=$id('anEffectVariants');
  // Keep arm labels visible after values have replaced the input placeholders.
  for(const rowId of ['anBinaryArms','anContinuousArms']){
    for(const input of [...$id(rowId).querySelectorAll(':scope>input')]){
      const label=h('label',{class:'an-number-field',for:input.id},h('span',{text:T(input.getAttribute('aria-label')||input.placeholder)}));
      input.before(label);label.append(input);
    }
  }
  const provenanceUses={
    anSingleGroupPanel:{location:true},anBinaryPoolPanel:{location:true},
    anJumpDose:{location:true},anJumpIpd:{direction:true},anJumpNetwork:{}
  };


  // One working surface; related task views with persistent, mounted content.
  const shell=h('div',{class:'an-synthesis-shell'}), main=h('div',{class:'an-synthesis-main'});
  const head=h('header',{class:'an-analysis-header'});
  const title=h('h3',{text:T('合并分析')});
  const status=h('span',{id:'anRunStatus',class:'an-run-state','role':'status',text:T('未运行')});
  const actions=h('div',{class:'an-header-actions'},$id('anSynthesize'));
  const exportTrigger=button('anExportMenuButton','导出','btn an-export-button');
  const exportPopover=h('div',{id:'anExportMenu',class:'an-export-menu',hidden:true});
  exportTrigger.setAttribute('aria-expanded','false'); exportTrigger.setAttribute('aria-controls',exportPopover.id);
  const exportWrap=h('div',{class:'an-export-wrap'},exportTrigger,exportPopover);actions.append(exportWrap);
  head.append(h('div',{class:'an-analysis-heading'},title,status),actions);
  const tabs=h('div',{class:'an-view-tabs',role:'tablist','aria-label':T('合并分析')});
  const panels={};
  const tabSpec=[['results','结果'],['heterogeneity','异质性统计量与区间'],['compare','模型与区间比较'],['diagnostics','研究诊断'],['methods','方法与引用']];
  for(const [key,label] of tabSpec) {
    const tab=button('anTab-'+key,label,'an-view-tab');tab.setAttribute('role','tab');tab.dataset.tab=key;
    tab.setAttribute('aria-controls','anView-'+key);tab.setAttribute('aria-selected',String(key==='results'));tab.tabIndex=key==='results'?0:-1;tabs.append(tab);
    panels[key]=h('section',{id:'anView-'+key,class:'an-task-view',role:'tabpanel','aria-labelledby':tab.id}); panels[key].hidden=key!=='results';
  }
  const config=$id('anConfigSummary'); config.querySelector(':scope > b')?.remove();
  const stale=$id('anStaleResult');
  stale.querySelector('span').textContent=T('设置已更新，以下仍为上次运行结果。');
  const previous=$id('anPreviousSynthesis'); previous.className='an-previous-result';
  const errorHost=h('div',{id:'anRunError',class:'an-error-message',role:'alert',hidden:true});
  const empty=h('div',{id:'anEmptyResult',class:'an-empty-result'},h('div',{class:'an-empty-rule','aria-hidden':'true'}),
    h('h4',{text:T('等待运行')}),h('p',{text:T('先确认分析范围和模型，再运行合并分析。')}),
    h('p',{class:'hint',text:T('结果将显示合并效应、区间和森林图。此处不会生成示例数据。')}));
  const figureHeader=h('div',{class:'an-figure-header'},h('h4',{text:T('森林图')}));
  const appearanceButton=button('anEditAppearance','显示设置','btn an-quiet-button');figureHeader.append(appearanceButton);
  const figure=h('section',{id:'anResultFigure',class:'an-result-figure',hidden:true},figureHeader,$id('anForest'));
  const warnings=h('div',{id:'anResultWarnings',class:'an-result-warnings'});
  const heterogeneityNote=$id('anHeterogeneityNote');
  const raw=h('details',{class:'an-support-details an-raw-record'},h('summary',{text:T('查看原始计算记录')}),
    h('p',{class:'hint',text:T('原始字段保留用于核查，不作为界面文案翻译。')}),$id('anSynthesisResult'));
  panels.results.append(previous,empty,$id('anSynthesisOverview'),figure,warnings);
  const heterogeneityEmpty=h('p',{id:'anHeterogeneityEmpty',class:'hint',text:T('先运行合并分析，再查看异质性统计量。')});
  panels.heterogeneity.append(h('p',{class:'an-view-description',text:T('此处复用最近一次合并分析，不单独生成新的检验结果。')}),heterogeneityEmpty,heterogeneityNote);
  panels.methods.append(raw);
  const usedMethods=h('section',{class:'an-method-section'},h('div',{class:'an-section-heading'},h('h4',{text:T('本次实际采用的方法')}),button('anCopyMethods','复制方法说明','btn an-quiet-button')),
    h('div',{id:'anUsedMethods'},h('p',{class:'hint',text:T('尚未运行；以下为候选方法说明。')})));
  const sourceList=h('section',{class:'an-method-section'},h('h4',{text:T('所选研究与来源')}),h('div',{id:'anUsedSources'}));
  panels.methods.prepend(usedMethods,sourceList);
  const referenceSection=h('section',{class:'an-method-section'},h('h4',{text:T('参考方法说明')}));panels.methods.append(referenceSection);

  // Explicitly map each existing tool to a task view. No statistical handler is replaced.
  const sensitivity=$id('anSensitivityPanel');
  const sensitivitySection=h('section',{id:'anSensitivityWorkspace',class:'an-comparison-section'});
  sensitivitySection.append(h('h4',{text:T('效应与区间方法')}));
  [...sensitivity.children].filter(node=>node.tagName!=='SUMMARY').forEach(node=>sensitivitySection.append(node));
  // Preserve external anchor links without retaining an invisible disclosure.
  const sensitivityAnchor=h('span',{id:'anSensitivityPanel'});sensitivity.replaceWith(sensitivityAnchor);sensitivitySection.prepend(sensitivityAnchor);
  panels.compare.append(sensitivitySection);
  const compareEstimators=h('section',{class:'an-comparison-section'},h('div',{class:'an-section-heading'},h('h4',{text:T('研究间方差估计比较')}),$id('anCompareEstimators')),$id('anEstimatorComparison'));
  const compareTau=h('section',{class:'an-comparison-section'},h('div',{class:'an-section-heading'},h('h4',{text:T('τ² 区间')}),$id('anTauProfileRun')),$id('anTauProfileResult'));
  panels.compare.append(compareEstimators);
  panels.heterogeneity.append(compareTau);
  panels.compare.prepend(h('p',{class:'an-view-description',text:T('敏感性分析应依据预先规定的比较，不根据显著性挑选结果。')}));
  const diagnosticHeader=h('div',{class:'an-section-heading'},h('h4',{text:T('研究诊断')}),h('div',{class:'row'},$id('anLeaveOneOut'),$id('anInfluenceRun')));
  const looRaw=$id('anLeaveOneOutResult');looRaw.setAttribute('translate','no');
  panels.diagnostics.append(diagnosticHeader,h('p',{class:'an-view-description',text:T('逐研究删除并重新拟合，检查单项研究对结果的影响。')}),
    h('p',{class:'hint',text:T('诊断值较大不是自动排除研究的理由。')}),looRaw,$id('anInfluenceResult'));
  for (const [id,label] of [['anForestExport','导出当前森林图'],['anReportDocx','重新计算并导出 Word'],['anReportPptx','重新计算并导出 PowerPoint'],['anReportTex','重新计算并导出 LaTeX'],['anSynthesisCsv','导出合并审计 CSV'],['anExport','导出效应量 CSV']]) {
    if(id==='anForestExport') exportPopover.append(h('h4',{text:T('图表与报告')}));
    if(id==='anSynthesisCsv') exportPopover.append(h('h4',{text:T('数据与审计记录')}));
    const control=$id(id);control.textContent=T(label);exportPopover.append(control);
  }
  exportPopover.append(h('p',{class:'hint',text:T('报告会按已应用的设置重新计算。')}));

  // The small settings sidebar is an editor, not a second permanent parameter page.
  const committedHolder=h('div',{class:'an-committed-controls',hidden:true});committed.forEach(input=>committedHolder.append(input));
  const editorTitle=h('h4',{id:'anEditorTitle',text:T('模型配置')});
  const editorClose=button('anCloseModelSettings','取消','btn an-quiet-button');
  const draftArea=h('div',{class:'an-draft-fields'});
  const draftInputs={};
  for(const input of committed) {
    const clone=input.cloneNode(true);clone.id=input.id==='anModel'?'anDraftModel':'anDraftCiMethod';clone.removeAttribute('hidden');
    draftInputs[input.id]=clone;
    draftArea.append(h('label',{for:clone.id},h('span',{text:T(input.id==='anModel'?'合并模型':'置信区间方法')}),clone));
    clone.addEventListener('change',()=>{if(!state.draft)return;state.draft[input.id]=clone.value;updateDraftUI();});
  }
  const draftInfo=h('p',{class:'an-editor-note',text:T('修改仅在点击“应用设置”后生效。')});
  const draftError=h('p',{id:'anDraftError',class:'an-editor-error',role:'alert',hidden:true});
  const apply=button('anApplyModel','应用设置','btn b-inc');
  const cancel=button('anCancelModel','取消修改','btn b-out');
  const draftFooter=h('footer',{class:'an-editor-footer'},h('span',{id:'anDraftState',class:'hint'}),h('div',{class:'row'},cancel,apply));
  const appearance=$id('anForestAppearancePanel');
  appearance.classList.add('an-appearance-editor');appearance.open=true;
  appearance.querySelector('summary').hidden=true;appearance.hidden=true;
  settings.replaceChildren(h('header',{},editorTitle,editorClose),draftInfo,draftArea,draftError,appearance,draftFooter);
  settings.setAttribute('aria-labelledby',editorTitle.id);settings.tabIndex=-1;
  main.append(config,stale,errorHost,...Object.values(panels));shell.append(main,settings,committedHolder);
  effects.append(head,tabs,shell);

  // Retain all explanatory notes. Candidates are clearly separated from actual methods.
  for(const node of synthesis) {
    if(node.parentElement!==effects) continue;
    if(node===settings || node===config || node===stale) continue;
    if(node.id==='anModelActions') {node.remove();continue;}
    if(node.tagName==='DETAILS' && !node.id) {
      node.querySelector('summary')?.remove(); [...node.childNodes].forEach(child=>referenceSection.append(child));node.remove();
    } else if(node.classList.contains('hint')) {
      if(node.textContent.includes('Study influence') || node.textContent.includes('研究影响诊断逐研究')) panels.diagnostics.append(node);
      else referenceSection.append(node);
    } else if(node.classList.contains('row')) {
      [...node.querySelectorAll('.hint')].forEach(child=>referenceSection.append(child));node.remove();
    }
  }
  originalChildren[0].classList.add('an-entry-title');
  $id('anSensitivityRun').classList.add('b-inc');
  $id('anSensitivityRun').classList.remove('b-out');
  // Group the pre-existing recomputing exports behind one contextual disclosure.
  const auditButtons=[...sensitivitySection.querySelectorAll('[data-sensitivity-report]')];
  const audit=h('details',{class:'an-inline-export'},h('summary',{text:T('导出')}));
  for(const control of [$id('anSensitivityExport'),...auditButtons]) audit.append(control);
  audit.append(h('p',{class:'hint',text:T('审计报告会按当前已选数据和设置重新计算。')}));
  sensitivitySection.querySelector('.row').append(audit);
  sensitivitySection.querySelectorAll('.row > .hint').forEach(n=>n.remove());

  function updateConfig() {
    for(const [id,out] of [['anMeasure','anConfigMeasure'],['anModel','anConfigModel'],['anCiMethod','anConfigCi']]) {
      const input=$id(id);$id(out).textContent=input.value?input.selectedOptions[0]?.textContent || input.value:'—';
    }
    window.RFWorkspaceShell?.refresh();
  }
  window.RFRefreshAnalysisConfig=updateConfig;
  function updateDraftUI() {
    const dirty=draftDirty();$id('anDraftState').textContent=T(dirty?'未应用的修改':'已应用');
    apply.disabled=!dirty;draftError.hidden=true;
  }
  function refreshLayout() {
    const available=layout.getBoundingClientRect().width;
    layout.classList.toggle('an-focus-editing',state.editing && available<1250);
    layout.classList.toggle('an-compact-editing',state.editing && available<1040);
    directoryToggle.hidden=!state.editing && available>900;
    if(state.editing && available<1040) editorClose.textContent=T('返回结果');
    else editorClose.textContent=T(state.editor==='appearance'?'完成':'取消');
  }
  const resizeObserver=new ResizeObserver(refreshLayout);resizeObserver.observe(layout);
  function openEditor(kind='model') {
    state.editing=true;state.editor=kind;settings.hidden=false;shell.classList.add('editing');
    draftArea.hidden=draftFooter.hidden=kind!=='model';appearance.hidden=kind!=='appearance';draftError.hidden=true;
    editorTitle.textContent=T(kind==='model'?'模型配置':'图形样式');
    draftInfo.textContent=T(kind==='model'?'修改仅在点击“应用设置”后生效。':'显示设置立即应用，不改变统计模型。');
    if(kind==='model') {
      if(!state.draft)state.draft=Object.fromEntries(committed.map(input=>[input.id,input.value]));
      committed.forEach(input=>{draftInputs[input.id].value=state.draft[input.id];});updateDraftUI();
    }
    layout.classList.remove('an-directory-open');directoryToggle.setAttribute('aria-expanded','false');refreshLayout();
    (kind==='model'?draftInputs.anModel:$id('anForestCustomTitle')).focus({preventScroll:true});
  }
  function closeEditor(discard=false,restoreFocus=true) {
    if(discard)state.draft=null;
    state.editing=false;settings.hidden=true;shell.classList.remove('editing');refreshLayout();
    if(restoreFocus)$id(state.editor==='appearance'?'anEditAppearance':'anEditModel').focus({preventScroll:true});
  }
  $id('anEditModel').addEventListener('click',()=>openEditor('model'));
  appearanceButton.addEventListener('click',()=>openEditor('appearance'));
  editorClose.addEventListener('click',()=>closeEditor(state.editor==='model'));
  cancel.addEventListener('click',()=>closeEditor(true));
  apply.addEventListener('click',()=>{
    if(!state.draft)return;
    const {anModel:model,anCiMethod:ci}=state.draft;
    const problem=!model?'请先选择合并模型。':!ci?'请先选择置信区间方法。':model==='fixed'&&ci==='hksj'?'修正 HKSJ 仅用于随机效应模型。':null;
    if(problem){draftError.textContent=T(problem);draftError.hidden=false;return;}
    const changed=committed.filter(input=>input.value!==state.draft[input.id]);
    committed.forEach(input=>{input.value=state.draft[input.id];});state.draft=null;
    for(const input of changed){input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));}
    updateConfig();closeEditor();
    if(!state.snapshot){state.status='empty';status.textContent=T('设置已应用；运行分析以更新结果。');}
  });
  $id('anSynthesize').addEventListener('click',event=>{
    if(draftDirty()) {event.preventDefault();event.stopImmediatePropagation();openEditor('model');draftError.textContent=T('请先应用或取消模型修改。');draftError.hidden=false;return;}
    if(state.tool?.id!=='pooled-effect')navigateRoute(registry.routeFor(registry.tool('pooled-effect')));
    else selectTab('results');
  },true);
  $id('anRerun').addEventListener('click',()=>$id('anSynthesize').click());

  const taskGuard=h('dialog',{class:'an-task-guard','aria-labelledby':'anTaskGuardTitle'},
    h('h3',{id:'anTaskGuardTitle',text:T('尚有未应用的模型修改')}),
    h('p',{text:T('切换任务将放弃这些修改。已经完成的运行不会因此被重算。')}));
  const keep=button('anKeepDraft','保留修改并继续编辑','btn b-inc');
  const discard=button('anDiscardDraft','放弃修改并切换','btn b-out');
  taskGuard.append(h('div',{class:'row'},keep,discard));document.body.append(taskGuard);
  let pendingTask=null;
  $id('taskSel').addEventListener('change',event=>{
    if(!draftDirty()||event.target.value===S.task?.task_id)return;
    event.preventDefault();event.stopImmediatePropagation();pendingTask=event.target.value;
    event.target.value=S.task?.task_id||'';taskGuard.showModal();keep.focus();
  },true);
  const keepDraft=()=>{pendingTask=null;taskGuard.close();navigateRoute(registry.routeFor(registry.tool('pooled-effect')),{restoreScroll:true});openEditor('model');};
  keep.addEventListener('click',keepDraft);
  taskGuard.addEventListener('cancel',event=>{event.preventDefault();keepDraft();});
  discard.addEventListener('click',()=>{const next=pendingTask;pendingTask=null;state.draft=null;taskGuard.close();$id('taskSel').value=next;$id('taskSel').dispatchEvent(new Event('change',{bubbles:true}));});

  window.addEventListener('beforeunload',event=>{if(draftDirty()){event.preventDefault();event.returnValue='';}});

  function selectTab(key,focus=false) {
    if(!panels[key])return;
    state.tab=key;
    for(const tab of tabs.children){const active=tab.dataset.tab===key;tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;}
    for(const [name,panel] of Object.entries(panels))panel.hidden=name!==key;
    if(focus)$id('anTab-'+key).focus();
    if(state.editing && state.editor==='appearance' && key!=='results')closeEditor(false);
  }
  tabs.addEventListener('click',event=>{const tab=event.target.closest('[data-tab]');if(tab){const tool=registry.fromView('synthesis',tab.dataset.tab);if(tool)navigateRoute(registry.routeFor(tool));}});
  tabs.addEventListener('keydown',event=>{
    const index=tabSpec.findIndex(([name])=>name===state.tab);let next=null;
    if(event.key==='ArrowRight')next=(index+1)%tabSpec.length;
    if(event.key==='ArrowLeft')next=(index-1+tabSpec.length)%tabSpec.length;
    if(event.key==='Home')next=0;if(event.key==='End')next=tabSpec.length-1;
    if(next!==null){event.preventDefault();const tool=registry.fromView('synthesis',tabSpec[next][0]);if(tool){navigateRoute(registry.routeFor(tool));$id('anTab-'+tool.tab).focus();}}
  });
  function syncEntryMode() {
    const all=state.view==='all',selected=entryMode.value;
    for(const node of basicEntry){
      const shared=entryProvenance.contains(node)||node===effectVariants;
      const needsCounts=node.id==='anBinaryArms'&&['anZeroCellPanel','anClusterPanel'].includes(selected);
      const needsMeans=node.id==='anContinuousArms'&&selected==='anClusterPanel';
      node.classList.toggle('an-entry-alt-hidden',!shared&&!needsCounts&&!needsMeans&&!all&&state.view==='entry'&&selected!=='basic');
    }
    for(const node of alternatives){
      const active=state.view==='entry'&&node.id===selected;
      node.classList.toggle('an-entry-alt-hidden',!all&&state.view==='entry'&&!active);
      node.open=active;
    }
  }
  const viewport=document.querySelector('#appMain .view');
  const advancedMethod=$id('anAdvancedMethod');
  const advancedOptionStates=new Map([...advancedMethod.options].map(option=>[option,{hidden:option.hidden,disabled:option.disabled}]));
  let lastAdvancedMode='',advancedResults=new Map(),lastAdvancedDimension=new Map(),switchingAdvanced=false;
  let lastAdvancedSubmethod=new Map();
  let fragmentNodes=[],motion=null;
  function animateSurface(node){
    motion?.cancel();if(!node||matchMedia('(prefers-reduced-motion: reduce)').matches||S.page!=='analysis')return;
    motion=node.animate([{opacity:.72,transform:'translateY(4px)'},{opacity:1,transform:'translateY(0)'}],{duration:150,easing:'cubic-bezier(.2,.7,.2,1)'});
  }
  function savePosition(){if(state.tool&&S.page==='analysis')state.scroll.set(state.tool.id,viewport.scrollTop);}
  function currentRoute(){return state.catalog?{page:'analysis',...(state.catalogGroup?{group:state.catalogGroup}:{})}:registry.routeFor(state.tool);}
  function announceRoute(){document.dispatchEvent(new CustomEvent('reviewflow:analysis-view-changed',{detail:currentRoute()}));}
  function navigateRoute(route,options={}){
    if(window.RFWorkspaceShell){window.RFWorkspaceShell.navigate(route,options);return;}
    applyRoute(route,options);if(options.history!==false&&location.hash!==registry.href(route))history.pushState(null,'',registry.href(route));
  }
  function resetFragments(){
    fragmentNodes.forEach(node=>node.classList.remove('an-fragment-hidden','an-fragment-primary'));fragmentNodes=[];
    advancedMethod.hidden=false;
    advancedOptionStates.forEach((props,option)=>{option.hidden=props.hidden;option.disabled=props.disabled;});
  }
  function hideFragment(node){if(node){node.classList.add('an-fragment-hidden');fragmentNodes.push(node);}}
  function setAdvancedModes(tool){
    const method=advancedMethod.value;
    if(lastAdvancedMode&&lastAdvancedMode!==tool.id){
      const output=$id('anAdvancedResult');
      if(!$id('anAdvancedRun').disabled)advancedResults.set(lastAdvancedMode,output.textContent);
      lastAdvancedDimension.set(lastAdvancedMode,$id('anAdvancedDimension').value);
      lastAdvancedSubmethod.set(lastAdvancedMode,method);
    }
    const allowed=tool.modes;
    for(const option of advancedMethod.options){option.hidden=!!option.value&&!allowed.includes(option.value);option.disabled=option.hidden;}
    const remembered=lastAdvancedSubmethod.get(tool.id);
    const desired=allowed.includes(method)?method:allowed.includes(remembered)?remembered:allowed.length===1?allowed[0]:'';
    if(method!==desired){
      switchingAdvanced=true;
      try{advancedMethod.value=desired;advancedMethod.dispatchEvent(new Event('change',{bubbles:true}));}
      finally{switchingAdvanced=false;}
    }
    advancedMethod.hidden=allowed.length===1;
    if(lastAdvancedMode!==tool.id){
      $id('anAdvancedResult').textContent=advancedResults.get(tool.id)||'';
      if(lastAdvancedDimension.has(tool.id))$id('anAdvancedDimension').value=lastAdvancedDimension.get(tool.id);
    }
    lastAdvancedMode=tool.id;
    if(!allowed.includes('subgroup'))hideFragment($id('anSubgroupRandomPanel'));
    const intro=$id('anExploratoryPanel').querySelector(':scope>p.hint');
    if(intro){intro.textContent=T(registry.group(tool.group).description);}
  }
  // Invalidation from actual data/model/input changes must also clear the navigation cache.
  document.addEventListener('reviewflow:analysis-computed-invalidated',()=>{if(!switchingAdvanced)advancedResults.clear();});
  // Shared advanced controls retain distinct result text across navigation. Switching methods
  // does not start a request. Existing generation/context guards still reject late responses.
  new MutationObserver(()=>{
    if(state.tool?.modes&&!$id('anAdvancedRun').disabled)advancedResults.set(state.tool.id,$id('anAdvancedResult').textContent);
  }).observe($id('anAdvancedResult'),{childList:true,characterData:true,subtree:true});
  function renderTool(tool,options={}){
    savePosition();resetFragments();
    state.catalog=false;state.catalogGroup=null;state.tool=tool;state.view=tool.view;
    state.lastByGroup[tool.group]=tool.id;catalog.hidden=true;
    for(const card of cards){const selected=card.id===tool.view||(card===effects&&(tool.view==='entry'||tool.view==='synthesis'))||(tool.view==='anIntervalsPanel'&&intervalCards.has(card.id));
      card.classList.toggle('an-view-hidden',card.id==='anScope'?['anJumpStudies','anJumpFeatures','rfGradePanel','rfManuscriptPanel','rfLivingReviewPanel','rfEvidenceMapPanel'].includes(tool.view):!selected);
      if(selected&&card.tagName==='DETAILS')card.open=true;
    }
    const inEntry=tool.view==='entry';
    // Nodes re-parented out of the entry card (e.g. the measure caution now living in the
    // scope card) follow their new owner's visibility, not the entry view's.
    for(const node of [entryStart,entryIntro,studyField,entryProvenance,originalChildren[0],...entry]){
      if(!effects.contains(node)&&![entryStart,studyField,entryProvenance].includes(node))continue;
      node.classList.toggle('an-view-hidden',!inEntry);
    }
    const entryDependents=Object.keys(provenanceUses);
    studyField.classList.toggle('an-view-hidden',!inEntry&&(!entryDependents.includes(tool.view)||!!tool.fragment));
    for(const node of [shell,head,tabs])node.classList.toggle('an-view-hidden',tool.view!=='synthesis');
    if(tool.view!=='synthesis'&&state.editing)closeEditor(false,false);
    tabs.hidden=false;
    if(tool.tab)selectTab(tool.tab);else selectTab(state.tab);
    if(tool.tab==='heterogeneity'&&state.snapshot)renderHeterogeneity(state.snapshot.result);
    if(tool.entryMode&&$id(tool.entryMode)){entryMode.value=tool.entryMode;}
    syncEntryMode();
    if(tool.entryMode){
      [entryStart,entryIntro,entryModeRow,guide].forEach(hideFragment);
      if(tool.id==='zero-cells')hideFragment($id('anMeasure').closest('.an-scope-field'));
      const primary=$id(tool.entryMode);primary.classList.add('an-fragment-primary');fragmentNodes.push(primary);
    }
    const card=cards.find(node=>node.id===tool.view);
    const heading=cardHeadings.get(card);
    if(heading)heading.textContent=T(tool.label);
    const provenanceUse=!tool.fragment&&provenanceUses[tool.view];
    if(provenanceUse&&card){
      if(heading)heading.after(entryProvenance);else card.prepend(entryProvenance);
      entryProvenance.classList.remove('an-view-hidden');
      for(const node of entryProvenance.querySelectorAll('.an-view-hidden'))node.classList.remove('an-view-hidden');
      if(!provenanceUse.location)hideFragment($id('anSourceLocator').parentElement);
      if(!provenanceUse.direction)hideFragment($id('anEffectDirection').parentElement);
      hideFragment($id('anAppendEffect').closest('label'));
    }else entryModeRow.after(entryProvenance);

    if(tool.view==='synthesis'){title.textContent=T(tool.label);tabs.setAttribute('aria-label',T('数据分析'));}
    if(inEntry)originalChildren[0].textContent=T(tool.label);
    if(tool.fragment){
      const fragment=$id(tool.fragment);
      if(card&&fragment){
        // A fragment may depend on shared inputs living in a sibling row (e.g. the one-group
        // count type required by the Peters test); rows marked as shared stay reachable.
        const shared=child=>child.matches?.('[data-an-fragment-shared~="'+tool.fragment+'"]')
          || !!child.querySelector?.('[data-an-fragment-shared~="'+tool.fragment+'"]');
        for(const child of card.children)if(child!==heading&&child!==fragment&&!child.contains(fragment)&&!shared(child))hideFragment(child);
        fragment.open=true;fragment.classList.add('an-fragment-primary');fragmentNodes.push(fragment);
      }
    }
    (tool.exclude||[]).forEach(id=>hideFragment($id(id)));
    if(tool.modes)setAdvancedModes(tool);
    layout.classList.remove('an-directory-open');directoryToggle.setAttribute('aria-expanded','false');
    updateConfig();refreshNavigation();refreshLayout();
    viewport.scrollTop=options.restoreScroll?(state.scroll.get(tool.id)||0):0;
    if(options.anchor){
      const target=$id(options.anchor);let parent=target;
      while(parent&&parent!==page){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement;}
      if(target&&target.getClientRects().length&&options.scrollToAnchor)target.scrollIntoView({block:'start',behavior:'instant'});
    }
    animateSurface(card||effects);announceRoute();
  }
  function openCatalog(group=null,options={}){
    if(group&&!registry.group(group))group=null;
    savePosition();
    if(!state.catalog&&state.tool)state.catalogReturn={tool:state.tool.id,scroll:viewport.scrollTop,editing:state.editing,editor:state.editor};
    if(state.editing)closeEditor(false,false);
    resetFragments();state.catalog=true;state.catalogGroup=group;state.view='catalog';
    cards.forEach(card=>card.classList.add('an-view-hidden'));
    for(const node of [entryStart,studyField])node.classList.add('an-view-hidden');
    catalog.hidden=false;
    // A navigation overview is not a mega-form: every original tool remains mounted but hidden.
    refreshNavigation();refreshCatalog();refreshLayout();viewport.scrollTop=0;
    layout.classList.remove('an-directory-open');directoryToggle.setAttribute('aria-expanded','false');
    animateSurface(catalog);announceRoute();if(options.focus)catalog.focus({preventScroll:true});
  }
  function returnToTool(){
    const saved=state.catalogReturn;if(!saved)return;
    search.value=catalogSearch.value='';navigateRoute(registry.routeFor(registry.tool(saved.tool)),{restoreScroll:true});
    viewport.scrollTop=saved.scroll;
    if(saved.editing)openEditor(saved.editor);else allButton.focus({preventScroll:true});
  }
  function applyRoute(route,options={}){
    const tool=registry.tool(route.tool);
    if(tool){renderTool(tool,{...options,anchor:route.anchor||options.anchor});return;}
    openCatalog(route.group,options);
  }
  // Compatibility for existing internal callers and saved #an... links.
  function activate(view,restoreScroll=false){
    if(view==='all'||view==='catalog'){openCatalog();return;}
    const tool=registry.tool(view)||registry.fromView(view);
    if(tool)renderTool(tool,{restoreScroll});
  }
  function viewFor(id){return registry.forElement(id)?.view||null;}
  function reveal(id){
    if(id==='anAll'){navigateRoute({page:'analysis'});return;}
    const tool=registry.forElement(id);if(!tool)return;
    if(tool.view==='entry'&&!tool.entryMode){
      let node=$id(id);while(node&&!alternatives.includes(node))node=node.parentElement;
      if(node)entryMode.value=node.id;
    }
    navigateRoute({...registry.routeFor(tool),anchor:id},{scrollToAnchor:true});
  }
  function matches(tool,term){return !term||[tool.label,T(tool.label),T(registry.group(tool.group).label),registry.group(tool.group).label,tool.keywords||''].join(' ').toLocaleLowerCase().includes(term);}
  function refreshNavigation(){
    const term=search.value.trim().toLocaleLowerCase();let found=0;
    for(const link of links){
      const tool=registry.tool(link.dataset.anTool),match=matches(tool,term);link.hidden=!match;
      link.textContent=T(tool.label);if(match)found++;
      if(!state.catalog&&state.tool?.id===tool.id)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
    }
    for(const panel of groupPanels){
      const group=panel.dataset.anGroupPanel,match=registry.methods(group).some(tool=>matches(tool,term));
      panel.hidden=term?!match:group!==(state.catalog?state.catalogGroup:state.tool?.group);
      panel.closest('.an-category-node').hidden=term?!match:false;
    }
    for(const control of groupButtons){
      const group=registry.group(control.dataset.anGroup),active=group.id===(state.catalog?state.catalogGroup:state.tool?.group);
      control.replaceChildren(h('span',{text:T(group.label)}),h('span',{class:'an-category-count','aria-hidden':'true',text:String(registry.methods(group.id).length)}));
      control.setAttribute('aria-pressed',String(active));
    }
    allButton.replaceChildren(h('span',{text:T('全部方法')}),h('span',{class:'an-all-count','aria-hidden':'true',text:String(registry.tools.length)}));
    allButton.setAttribute('aria-label',T('全部方法')+' · '+T('共 {0} 个方法入口',registry.tools.length));
    allButton.setAttribute('aria-pressed',String(state.catalog&&!state.catalogGroup));
    allButton.classList.toggle('is-current',state.catalog&&!state.catalogGroup);
    noMatch.textContent=T('未找到匹配的方法');noMatch.hidden=found>0;
    navTitle.textContent=T('分析目录');search.placeholder=search.ariaLabel=T('查找分析方法');
    if(state.catalog)refreshCatalog();
  }
  function refreshCatalog(){
    const group=registry.group(state.catalogGroup),term=search.value.trim().toLocaleLowerCase();
    catalogTitle.textContent=T(group?.label||'全部分析方法');
    catalogDescription.textContent=T(group?.description||'只显示方法入口，不展开表单，也不运行分析。');
    catalogSearch.value=search.value;catalogSearch.placeholder=catalogSearch.ariaLabel=T('查找分析方法');
    clearSearch.textContent=T('清空搜索');clearSearch.hidden=!term;
    let count=0;
    for(const section of catalogSections){
      const groupId=section.dataset.category;let visible=0;
      const heading=section.querySelector('[data-catalog-group]');heading.textContent=T(registry.group(groupId).label);
      section.querySelector('header p').textContent=T(registry.group(groupId).description);
      for(const item of section.querySelectorAll('[data-catalog-tool]')){
        const tool=registry.tool(item.dataset.catalogTool),match=(!group||tool.group===group.id)&&matches(tool,term);item.hidden=!match;
        item.querySelector('a>span').textContent=T(tool.label);if(match){visible++;count++;}
      }
      section.hidden=!visible;
    }
    catalogCount.textContent=T(term?'找到 {0} 个方法':'共 {0} 个方法入口',count);catalogEmpty.hidden=count>0;
    returnButton.hidden=!state.catalogReturn;returnButton.textContent=T('返回当前方法');
    if(state.catalogReturn)returnButton.title=T('返回：{0}',T(registry.tool(state.catalogReturn.tool).label));
  }
  nav.addEventListener('click',event=>{
    const group=event.target.closest('[data-an-group]');
    if(group){event.preventDefault();search.value=catalogSearch.value='';navigateRoute({page:'analysis',group:group.dataset.anGroup});return;}
    const link=event.target.closest('[data-an-tool]');
    if(!link||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
    event.preventDefault();search.value=catalogSearch.value='';navigateRoute(registry.routeFor(registry.tool(link.dataset.anTool)),{restoreScroll:true});
  });
  catalog.addEventListener('click',event=>{
    const link=event.target.closest('a');if(!link||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
    const route=registry.parse(link.hash);if(!route)return;event.preventDefault();
    search.value=catalogSearch.value='';navigateRoute(route,{focus:event.detail===0});
  });
  allButton.addEventListener('click',()=>{search.value=catalogSearch.value='';navigateRoute({page:'analysis'},{focus:true});});
  returnButton.addEventListener('click',returnToTool);
  search.addEventListener('input',refreshNavigation);
  catalogSearch.addEventListener('input',()=>{search.value=catalogSearch.value;refreshNavigation();});
  clearSearch.addEventListener('click',()=>{search.value=catalogSearch.value='';refreshNavigation();catalogSearch.focus();});
  directoryToggle.addEventListener('click',()=>{const active=layout.classList.toggle('an-directory-open');directoryToggle.setAttribute('aria-expanded',String(active));if(active)search.focus();});
  entryMode.addEventListener('change',syncEntryMode);
  page.addEventListener('click',event=>{const link=event.target.closest('a[href^="#an"]');if(link&&!nav.contains(link)&&!event.ctrlKey&&!event.metaKey&&!event.shiftKey&&!event.altKey){event.preventDefault();reveal(link.hash.slice(1));}},true);
  document.addEventListener('reviewflow:analysis-reveal',event=>reveal(event.detail));
  document.addEventListener('keydown',event=>{
    if(event.defaultPrevented||event.key!=='Escape'||S.page!=='analysis'||!state.catalog||!state.catalogReturn)return;
    if(document.querySelector('.rf-level-menu:not([hidden]),dialog[open]')||layout.classList.contains('an-directory-open'))return;
    event.preventDefault();returnToTool();
  });
  document.addEventListener('reviewflow:analysis-task-changed',()=>{advancedResults.clear();lastAdvancedDimension.clear();lastAdvancedSubmethod.clear();lastAdvancedMode='';state.catalogReturn=null;state.scroll.clear();});
  const fromHash=()=>{
    const parsed=registry.parse(location.hash);
    if(parsed?.page==='analysis')applyRoute(parsed,{history:false});
    else if(/^#an/.test(location.hash)){let id;try{id=decodeURIComponent(location.hash.slice(1));}catch{}if(id&&registry.forElement(id))applyRoute(registry.routeFor(registry.forElement(id)),{history:false});else openCatalog();}
    else openCatalog();
  };
  // Exports remain explicit actions with their existing generation guards.
  const closeExport=()=>{exportPopover.hidden=true;exportTrigger.setAttribute('aria-expanded','false');};
  exportTrigger.addEventListener('click',()=>{exportPopover.hidden=!exportPopover.hidden;exportTrigger.setAttribute('aria-expanded',String(!exportPopover.hidden));if(!exportPopover.hidden)exportPopover.querySelector('button:not(:disabled)')?.focus();});
  document.addEventListener('pointerdown',event=>{if(!exportWrap.contains(event.target))closeExport();});
  exportPopover.addEventListener('click',event=>{if(event.target.closest('button'))closeExport();});
  document.addEventListener('keydown',event=>{if(event.key!=='Escape')return;if(!exportPopover.hidden){closeExport();exportTrigger.focus();}else if(layout.classList.contains('an-directory-open')){layout.classList.remove('an-directory-open');directoryToggle.setAttribute('aria-expanded','false');directoryToggle.focus();}});

  // Result data are never invented. Only formatting and information hierarchy change.
  function renderOverview(result,host=$id('anSynthesisOverview')) {
    const fmt=value=>Number.isFinite(Number(value))&&value!=null?Number(value).toPrecision(4):'—';
    const estimate=h('div',{class:'an-result-estimate'},
      h('span',{class:'an-result-label',text:T('合并效应')}),
      h('strong',{text:fmt(result.pooled)}),
      h('span',{class:'an-measure-code',translate:'no',text:result.measure}));
    const interval=h('div',{class:'an-result-interval'},
      h('span',{class:'an-result-label',text:T('95% 置信区间')}),
      h('strong',{text:`${fmt(result.ci_low)} — ${fmt(result.ci_high)}`}));
    const values=[[T('纳入研究'),result.n_studies],['I²',fmt(result.i2_percent)+'%'],
      ['τ²',result.model==='fixed'?'—':fmt(result.tau2)]];
    if(result.prediction_interval_95)values.push([T('预测区间'),result.prediction_interval_95.map(fmt).join(' — ')]);
    const facts=h('dl',{class:'an-result-facts'},...values.map(([label,value])=>
      h('div',{},h('dt',{text:label}),h('dd',{text:String(value)}))));
    host.replaceChildren(h('div',{class:'an-primary-result'},estimate,interval),facts);
    host.hidden=false;
  }
  function renderHeterogeneity(result) {
    const target=heterogeneityNote;
    const fmt=value=>value==null?'—':Number(value).toPrecision(4);
    target.replaceChildren(h('p',{text:T('异质性：I²={0}%，固定逆方差 Q={1}（自由度 {2}）。τ²={3}；分析尺度：{4}。',fmt(result.i2_percent),fmt(result.q),result.q_df,fmt(result.tau2),RFAnalysisLocale.term(result.effect_scale))}),h('p',{text:T('I² 按 max(0, (Q−df)/Q) 计算；Q=0 时记为 0。请结合所用模型解释，并引用实际估计方法。')}));
    if(state.snapshot){
      const scope=state.snapshot.scope;
      target.prepend(h('p',{class:'an-snapshot-context'},h('strong',{text:T(state.status==='ready'?'当前结果':'上次运行结果')}),document.createTextNode(' · '),plain([scope.anMeasure,T(modelLabels[result.model]||result.method||''),scope.anComparison,scope.anOutcome,scope.anTimepoint].filter(Boolean).join(' · '))));
    }
    const limits=result.heterogeneity_intervals;
    if(limits) {
      target.append(limits.available?h('p',{text:T('Higgins–Thompson 95% 区间：I² {0}%；H={1}（{2}）。',limits.i2_ci_percent.map(fmt).join(T(' 至 ')),fmt(limits.h),limits.h_ci.map(fmt).join(T(' 至 ')))}):RFAnalysisLocale.message(limits.reason));
      for(const warning of limits.warnings||[])target.append(RFAnalysisLocale.message(warning));
    }
  }
  function renderMethods(snapshot) {
    const container=$id('anUsedMethods'),sources=$id('anUsedSources');container.replaceChildren();sources.replaceChildren();
    if(!snapshot){container.append(h('p',{class:'hint',text:T('尚未运行；以下为候选方法说明。')}));return;}
    const result=snapshot.result;
    container.append(h('div',{class:'an-method-current'},h('strong',{text:T(modelLabels[result.model]||result.method)}),h('span',{text:T(ciLabels[snapshot.scope.anCiMethod]||result.ci_method)})));
    for(const source of result.method_sources||[]) container.append(h('div',{class:'an-method-row'},RFAnalysisLocale.message(source)));
    for(const effect of result.effects||[]) {
      const original={...anSourceFields(effect.source_key),...effect};
      sources.append(h('div',{class:'an-source-row'},h('strong',{translate:'no',text:effect.study_id}),h('div',{},
        h('p',{translate:'no',text:original.source_title||effect.source_key||'—'}),h('small',{translate:'no',text:[effect.source_locator,original.source_doi,`#${effect.result_id}`].filter(Boolean).join(' · ')}))));
    }
  }
  function snapshotView() {
    previous.replaceChildren();const snapshot=state.snapshot;if(!snapshot)return;
    const label=h('div',{class:'an-snapshot-heading'},h('strong',{text:T('上次运行结果')}),h('span',{text:T(modelLabels[snapshot.result.model]||snapshot.result.method)}),h('span',{text:T(ciLabels[snapshot.scope.anCiMethod]||snapshot.result.ci_method)}));
    previous.append(label,h('div',{class:'an-snapshot-scope'},h('span',{text:T('原分析范围')}),plain([snapshot.scope.anComparison,snapshot.scope.anOutcome,snapshot.scope.anTimepoint,snapshot.scope.anMeasure].filter(Boolean).join(' · '))));
    const summary=h('div',{class:'an-synthesis-overview'});renderOverview(snapshot.result,summary);previous.append(summary);
    if(snapshot.svg){const svg=createAnalysisForestSvg(snapshot.result,snapshot.scope,snapshot.appearance);svg.querySelectorAll('[id]').forEach(node=>{node.id='previous-'+node.id;});const labelled=svg.getAttribute('aria-labelledby');if(labelled)svg.setAttribute('aria-labelledby',labelled.split(' ').map(id=>'previous-'+id).join(' '));previous.append(h('div',{class:'an-snapshot-figure'},svg));}
    for(const warning of snapshot.result.warnings||[])previous.append(h('div',{class:'an-warning'},RFAnalysisLocale.message(warning)));
  }
  function updateResultUI() {
    heterogeneityEmpty.hidden=!!state.snapshot;
    if(state.snapshot)renderHeterogeneity(state.snapshot.result);
    const hasCurrent=state.status==='ready';
    empty.hidden=hasCurrent||!!state.snapshot;
    figure.hidden=!hasCurrent;
    stale.hidden=!state.snapshot||hasCurrent;
    previous.hidden=!state.snapshot||hasCurrent;
    status.dataset.state=state.status;
    status.textContent=T(({empty:'未运行',ready:'当前结果',stale:'上次运行结果',running:'正在计算',error:'运行失败'})[state.status]);
    if(state.status==='stale'||state.status==='running'||state.status==='error')snapshotView();
    if(!hasCurrent)warnings.replaceChildren();
    heterogeneityNote.hidden=!state.snapshot;
  }
  document.addEventListener('reviewflow:analysis-synthesis-invalidating',()=>{
    if(state.snapshot&&state.snapshot.taskId===S.task?.task_id){state.status='stale';updateResultUI();}
    else{state.status='empty';state.snapshot=null;updateResultUI();}
    // The legacy invalidator clears this node after dispatching its event. Restore only
    // the clearly labelled previous-run view after that synchronous invalidation ends.
    queueMicrotask(()=>{if(state.snapshot)renderHeterogeneity(state.snapshot.result);});
  });
  document.addEventListener('reviewflow:analysis-synthesis-running',()=>{state.status='running';errorHost.hidden=true;updateResultUI();});
  document.addEventListener('reviewflow:analysis-synthesis-failed',event=>{state.status='error';errorHost.replaceChildren(RFAnalysisLocale.message(event.detail));errorHost.hidden=false;updateResultUI();});
  document.addEventListener('reviewflow:analysis-synthesis-ready',event=>{
    const result=event.detail||anLastForestResult;if(!result)return;
    state.snapshot={taskId:S.task?.task_id,result:structuredClone({...result,effects:result.effects.map(effect=>({...anSourceFields(effect.source_key),...effect}))}),scope:readScope(),appearance:{color:anValue('anForestColor'),digits:Number(anValue('anForestDigits')),title:anValue('anForestCustomTitle'),prediction:$id('anForestShowPrediction').checked},svg:$id('anForest').querySelector('svg')?.cloneNode(true)};
    state.status='ready';errorHost.hidden=true;previous.replaceChildren();renderOverview(result);renderHeterogeneity(result);renderMethods(state.snapshot);
    warnings.replaceChildren(...(result.warnings||[]).map(warning=>h('div',{class:'an-warning'},RFAnalysisLocale.message(warning))));
    updateConfig();updateResultUI();
  });
  document.addEventListener('reviewflow:analysis-forest-rendered',()=>{if(state.status==='ready'&&state.snapshot){state.snapshot.appearance={color:anValue('anForestColor'),digits:Number(anValue('anForestDigits')),title:anValue('anForestCustomTitle'),prediction:$id('anForestShowPrediction').checked};}});
  document.addEventListener('reviewflow:analysis-task-changed',()=>{pendingTask=null;if(taskGuard.open)taskGuard.close();state.snapshot=null;state.status='empty';state.draft=null;errorHost.hidden=true;previous.replaceChildren();renderMethods(null);closeEditor(true);updateResultUI();});
  committed.forEach(input=>input.addEventListener('change',updateConfig));$id('anMeasure').addEventListener('change',updateConfig);
  document.addEventListener('reviewflow:analysis-core-loaded',()=>{updateConfig();if(!state.catalog&&state.tool?.modes)setAdvancedModes(state.tool);});
  document.addEventListener('reviewflow:analysis-task-changed',()=>queueMicrotask(()=>{if(S.task&&!state.catalog&&state.tool?.modes)setAdvancedModes(state.tool);}));
  document.addEventListener('reviewflow:language-changed',()=>{
    updateConfig();updateResultUI();renderMethods(state.snapshot);refreshNavigation();
    if(state.tool&&!state.catalog){const card=cards.find(card=>card.id===state.tool.view);const heading=cardHeadings.get(card);if(heading)heading.textContent=T(state.tool.label);if(state.tool.view==='synthesis')title.textContent=T(state.tool.label);if(state.tool.view==='entry')originalChildren[0].textContent=T(state.tool.label);}
    if(state.status==='ready'&&state.snapshot){renderOverview(state.snapshot.result);renderHeterogeneity(state.snapshot.result);}
    refreshLayout();
  });
  $id('anCopyMethods').addEventListener('click',async()=>{try {const text=$id('anUsedMethods').innerText;if(!text)return;await navigator.clipboard.writeText(text);toast(T('已复制'),'inc');}catch{toast(T('复制失败，请手动选择文本。'),'warn');}});
  // Exposed for integration tests and other existing deep-link helpers, not API state.
  window.RFAnalysisWorkspace=Object.freeze({activate,selectTab,openEditor,closeEditor,refreshLayout,applyRoute,currentRoute,reveal,returnToTool});
  updateResultUI();fromHash();
})();
