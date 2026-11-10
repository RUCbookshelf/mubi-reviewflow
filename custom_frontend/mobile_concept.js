/* Mobile concept presentation. Existing controls, listeners and drafts are moved,
 * never cloned. No API or research decision logic is implemented here. */
(function () {
  'use strict';
  const media=matchMedia('(max-width:760px)'),top=document.querySelector('#appMain>.top');
  if(!top||!window.RFWorkspaceShell||!window.RFNavigationSheet)return;
  const q=(s,r=document)=>r?.querySelector(s),qa=(s,r=document)=>[...r.querySelectorAll(s)];
  const page=id=>q('.page[data-p="'+id+'"]');
  const moves=new Map(),added=new Set(),labels=new Map(),states=new Map();let frame=0;
  const tr=key=>typeof key==='function'?key():window.RFMobileLabels?.[key]?.[RFLang.get()]||T(key);
  function node(tag,cls='',key){const n=document.createElement(tag);n.className=cls;if(key){labels.set(n,key);n.textContent=tr(key);}return n;}
  function add(n,host,before=null){host.insertBefore(n,before);added.add(n);return n;}
  function move(n,host,before=null){
    if(!n||!host||n.parentNode===host&&(!before||n.nextSibling===before))return;
    if(!moves.has(n)){const a=document.createComment('mobile presentation anchor');n.before(a);moves.set(n,a);}host.insertBefore(n,before);
  }
  function restore(){
    for(const d of qa('.rf-mobile-sheet[open]'))d.close();
    for(const [n,a] of [...moves].reverse())if(a.isConnected)a.replaceWith(n);
    moves.clear();for(const fn of states.values())fn();states.clear();
    for(const n of added){labels.delete(n);n.remove();}added.clear();
  }
  function disclosure(n,key,cls='rf-mobile-disclosure'){
    if(!n)return;
    const heading=q(':scope>h2,:scope>h3,:scope>b,:scope>.row>h2,:scope>.row>h3',n);
    if(heading?.textContent.trim()===tr(key).trim())mobileClass(heading,'rf-mobile-repeated-heading');
    const existing=n.parentElement?.closest('.'+cls);if(existing)return existing;
    const d=add(node('details',cls),n.parentNode,n);d.append(node('summary','',key));move(n,d);
    return d;
  }
  function mobileClass(n,cls){
    if(!n)return;const previous=n.classList.contains(cls);
    if(!states.has(n))states.set(n,()=>n.classList.toggle(cls,previous));
    n.classList.add(cls);
  }
  function button(key,fn,cls='btn b-out'){const b=node('button',cls,key);b.type='button';b.addEventListener('click',fn);return b;}
  function block(host,cls){return q('.'+cls,host)||add(node('div',cls),host);}
  function sheet(host,key){
    let d=q(':scope>.rf-mobile-sheet',host);if(d)return d;
    d=add(node('dialog','rf-mobile-sheet'),host);const h=node('header');h.append(node('h2','',key),button('关闭',()=>d.close()));d.append(h);
    d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();}});return d;
  }
  function opener(d,key,cls='btn b-out'){return button(key,()=>d.showModal(),cls);}
  function home(){
    const host=page('home'),overview=q('#wf-panel-overview'),current=q('#wf-current-task');if(!overview||!current)return;
    if(!q('.rf-mobile-home-intro',overview)){
      add(node('p','rf-mobile-home-intro hint','Continue your current review.'),overview,current);
      const shortcuts=add(node('section','card rf-mobile-shortcuts'),overview,q('#wf-pending-imports'));shortcuts.append(node('h3','','Workflow shortcuts'));
      for(const id of ['import','screen','fulltext','merge','export','prisma','scheme','bench','analysis','trace','help']){
        const info=RFNavigation.pages.find(p=>p.id===id);if(!info)continue;
        const b=button(info.label,()=>go(id),'btn b-out rf-mobile-link');const arrow=node('span','rf-mobile-arrow');arrow.textContent='›';
        // Labels remain separate so language refresh cannot erase the arrow.
        labels.delete(b);b.textContent='';b.append(node('span','',info.label),arrow);shortcuts.append(b);
      }
      const metrics=add(node('div','rf-mobile-metrics'),overview,shortcuts);
      for(const key of ['Active records','Awaiting import review']){const box=node('div');box.append(node('b'),node('span','',key));metrics.append(box);}
      add(button('Review new imports',()=>go('import'),'btn b-out rf-mobile-full'),overview,shortcuts);
      const steps=q('.steps',overview);if(steps){const heading=steps.previousElementSibling,d=disclosure(steps,'Features at a glance');if(heading?.tagName==='H3'){move(heading,d);mobileClass(heading,'rf-mobile-repeated-heading');}overview.append(d);}
    }
    if(!q('.rf-mobile-task-progress',current)&&q('p[translate=no]',current)){
      const badge=add(node('span','rf-mobile-task-badge',()=>tr(WorkflowControls.settings()?.task_kind==='team'?'Team task':'Personal task')),current,q('button',current));
      const progress=add(node('div','rf-mobile-task-progress'),current,q('button',current));const bar=node('div','rf-mobile-progress');bar.append(node('i'));progress.append(bar,node('p','hint'));
    }
    const progress=q('.rf-mobile-task-progress',current);
    if(progress){const width=q('#barScreen')?.style.width||'0%';if(progress.firstChild.firstChild.style.width!==width)progress.firstChild.firstChild.style.width=width;
      const text=q('#capScreen')?.textContent||'';if(progress.lastChild.textContent!==text)progress.lastChild.textContent=text;}
    const metrics=q('.rf-mobile-metrics',overview);
    if(metrics){const total=q('#capScreen')?.textContent.match(/\d+\s*\/\s*(\d+)/)?.[1]||'—',pending=q('#wf-pending-imports'),count=pending?.hidden?'0':q('p[translate=no]',pending)?.textContent||'—';
      for(const [n,text] of [[metrics.children[0].firstChild,total],[metrics.children[1].firstChild,count]])if(n.textContent!==text)n.textContent=text;}
    const save=q('.wf-settings-save',host);
    if(save&&!q(':scope>.rf-mobile-settings-footer',host)){const d=add(node('section','rf-mobile-settings-footer'),host);d.append(node('h3','','Save current task workflow settings'));
      const hint=save.previousElementSibling;if(hint?.matches('p.hint'))move(hint,d);move(save,d);}
    for(const f of qa('#wf-policy .wf-policy-stage'))disclosure(f,()=>f.querySelector('legend')?.textContent||T('Work scope'),'rf-mobile-stage');
    for(const card of qa('#hubGrid>.hubcard'))disclosure(card,()=>q('h3',card)?.textContent||T('Current task'),'rf-mobile-task');
    for(const key of ['ml','coding','appearance']){const panel=q('#wf-panel-'+key),loose=qa(':scope>label,:scope>select',panel);
      if(loose.length){const c=block(panel,'rf-mobile-settings-card');c.classList.add('card');for(const n of loose)move(n,c);}}
  }
  function queue(host,options){
    const tools=q('.wf-queue-tools',host);if(!tools)return;
    // Queue services query their own host. Preserve that whole subtree.
    const row=block(host,'rf-mobile-source');move(tools,row);
    if(!q('.rf-mobile-retry',options))add(button('Retry / refresh',()=>q('button',tools)?.click(),'btn b-out rf-mobile-retry'),options);
    const hint=q('.hint',tools);if(hint&&!q('.rf-mobile-scope-help',options))add(node('p','hint rf-mobile-scope-help',()=>hint.textContent),options);
  }
  /* ---------- 移动端工作流阶段切换（F41 缓解半步） ----------
     复筛/编码等高频往返原要先「汉堡→抽屉→条目」两步。这里在三个阶段页顶给一条
     一级切换 tab（初筛↔复筛↔编码），仍复用现成的 go()，不动导航主干；
     首页快捷区收敛与完整导航收敛属可放弃包，不做。 */
  const stageOrder=[['screen','初筛'],['fulltext','复筛'],['bench','编码']];
  function stageTabs(host){
    if(!host)return;
    let strip=q(':scope>.rf-mobile-stage-tabs',host);
    if(!strip){strip=add(node('nav','rf-mobile-stage-tabs'),host,host.firstChild);strip.setAttribute('role','group');}
    /* Nodes added through add() land in `added`, so restore() removes the strip
       with the rest of the presentation layer; labels are refreshed by compose(). */
    if(strip.getAttribute('aria-label')!==tr('Workflow stages'))strip.setAttribute('aria-label',tr('Workflow stages'));
    for(const [id,key] of stageOrder){
      let b=q(':scope>[data-rf-stage="'+id+'"]',strip);
      if(!b){b=add(button(key,()=>go(id),'rf-mobile-stage-tab'),strip);b.dataset.rfStage=id;}
      const current=S.page===id;
      if(b.getAttribute('aria-current')!==(current?'page':null)){if(current)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');}
    }
  }
  /* ---------- 移动端排除理由行折叠（F38 缓解半步） ----------
     决策条原始终带一条全宽「排除理由」下拉行（实测 138–140px，占 751px 视口
     18.4%），而它只在按「3 排除」时才需要。触屏两段式：先点 3 → 展开理由行并
     聚焦；选好理由后再点 3 才提交（提交逻辑仍走原有 screenDecide，未改一行）。 */
  const reasonBars=new WeakSet();
  function reasonBar(host){
    const bar=q('.scr-decisions',host),select=q('#excReason',host);
    if(!bar||!select)return;
    if(!reasonBars.has(bar)){
      reasonBars.add(bar);
      const previous=bar.classList.contains('rf-reason-collapsed');
      states.set(bar,()=>bar.classList.toggle('rf-reason-collapsed',previous));
      if(!select.value)bar.classList.add('rf-reason-collapsed');
    }
  }
  function revealReason(){
    const host=page('screen'),bar=q('.scr-decisions',host),select=q('#excReason',host);
    if(!bar||!select||!bar.classList.contains('rf-reason-collapsed'))return false;
    bar.classList.remove('rf-reason-collapsed');
    return true;
  }
  /* F44 缓解半步：DOI/类型 chip 在窄屏 meta 行里被整条隐藏，查重与纳入核对失去
     依据。信息已在「文献信息」折叠区（与摘要同区）里，这里把 DOI 提到折叠区标题上，
     决策前不展开也能看到，展开后可见文献类型等全部字段。 */
  function articleDetails(host){
    const panel=q('#rfScreenMetadata',host);
    if(panel){const d=panel.closest('.rf-mobile-article-details'),s=d&&q(':scope>summary',d),doi=q('.scr-doi',host);
      if(s){labels.delete(s);const text=tr('Article details')+(doi?'. '+doi.textContent:'');if(s.textContent!==text)s.textContent=text;}}
  }
  function screening(){
    const host=page('screen'),mode=q('.al-mode',host),order=q('.rf-screen-order',host);if(!mode||!order)return;
    const options=sheet(host,'Screening options'),shell=block(host,'rf-mobile-queue');move(order,shell);move(q('.rf-screen-order-label',order),shell,order);
    for(const b of qa('button',order))if(!['alModeImport','alModeAI','alModeRandom'].includes(b.id))move(b,options);
    for(const n of [...mode.children])if(!n.matches('.rf-screen-tools'))move(n,options);
    queue(host,options);move(q('.rf-mobile-source',host),shell);move(q('.rf-mobile-scope-status',host),shell);
    move(q('.rf-stop-anchor',host),q('.rf-mobile-source',host));
    const stop=q('.rf-stop-summary',host);if(stop&&!q('.rf-mobile-stop-icon',stop)){
      const old=stop.getAttribute('aria-label');states.set(stop,()=>{if(old===null)stop.removeAttribute('aria-label');else stop.setAttribute('aria-label',old);});
      const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('class','rf-mobile-stop-icon');svg.setAttribute('aria-hidden','true');
      const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d','M9 11l3 3 7-7M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9');svg.append(path);add(svg,stop);
      /* F43：48px 纯图标按钮上的「方框＋对勾」与「停止」无语义映射，功能接近不可发现。
         保留图标，并在图标旁补两字短标签（图标＋文字并排，按钮高度仍是 48px）。 */
      add(node('span','rf-mobile-stop-label','Stop'),stop);
    }
    if(stop)stop.setAttribute('aria-label',T('停止建议'));
    reasonBar(host);
    const actions=block(host,'rf-mobile-screen-actions');move(q('#scrPos'),actions);move(q('[data-undo-stage="screen"]',q('.rf-screen-tools',host)),actions);
    if(!q('.rf-mobile-screen-options',actions)){const b=opener(options,'Screening options','btn b-out rf-mobile-screen-options');b.textContent='···';labels.delete(b);add(b,actions);}
    q('.rf-mobile-screen-options',actions).setAttribute('aria-label',tr('Screening options'));
    move(q('.rf-criteria-panel',host),actions,q('.rf-mobile-screen-options',actions));
    move(q('#rfPilotEntry',host),actions,q('.rf-criteria-panel',actions)||q('.rf-mobile-screen-options',actions));
    move(q('.scr-status',host),options);move(q('#rfScreenFocus'),options);
    if(!q('.rf-mobile-help-open',options))add(button('阅读帮助',()=>{options.close();q('#rfScreenHelp').click();},'btn b-out rf-mobile-help-open'),options);
    const card=q('#scrCard');if(shell.nextElementSibling!==actions||actions.nextElementSibling!==card){host.insertBefore(shell,card);host.insertBefore(actions,card);}
    const metadata=q('#rfScreenMetadata');if(metadata&&!metadata.parentElement.closest('.rf-mobile-article-details')){
      const d=disclosure(metadata,'Article details','rf-mobile-article-details'),wasHidden=metadata.hidden;
      states.set(metadata,()=>{metadata.hidden=wasHidden;});d.addEventListener('toggle',()=>{metadata.hidden=!d.open;});
    }
    articleDetails(host);
  }
  function documents(id){
    const host=page(id),coding=id==='bench',options=sheet(host,coding?'Coding options':'Reader options');queue(host,options);
    // A guarded queue load may be cancelled by a concurrent task refresh.
    // Keep its existing recovery action beside the error, not only in a sheet.
    const failure=coding?q('#cbForms>.card.hint',host):null;
    if(failure?.textContent.startsWith(T('载入失败：'))&&!q('.rf-mobile-queue-retry',failure))add(button('Retry / refresh',()=>q('.wf-queue-tools>button',host)?.click(),'btn b-out rf-mobile-queue-retry'),failure);
    const line=block(host,'rf-mobile-document-status'),actions=block(host,'rf-mobile-document-actions');move(q(coding?'#cbLabel':'#ftDocLabel'),line);
    move(q(coding?'#cbPrev':'#ftPrevDoc'),actions);move(q('[data-undo-stage="'+(coding?'coding':'fulltext')+'"]',host),actions);
    if(!q('.rf-mobile-options-open',actions))add(opener(options,coding?'Coding options':'Reader options','btn b-out rf-mobile-options-open'),actions);
    if(!coding){move(q('#ftNextDoc'),actions);move(q('#ftSideToggle'),line);}
    const layout=q(coding?'#benchLayout':'.ft-layout',host),title=q(coding?'#cbTitle':'#ftDocTitle');move(title,host,layout);
    for(const s of coding?['#cbPdfToggle','#cbExport','#wf-coding-entry']:['#ftReload','#rfFulltextFocus','#ftDocChip'])move(q(s),options);
    let before=title||layout;
    for(const n of [q('.rf-mobile-source',host),q('.rf-mobile-scope-status',host),line,actions].reverse())if(n){if(n.parentNode!==host||n.nextElementSibling!==before)host.insertBefore(n,before);before=n;}
    if(coding){disclosure(q('#benchPdfPane'),'Open source document','rf-mobile-document');move(q('#rfCodingDivider'),options);
      const heading=q('.rf-coding-header',host);if(heading&&!q('.rf-mobile-coding-title',heading))add(node('span','rf-mobile-coding-title','Dimensions & evidence'),heading,heading.firstChild);
      const save=block(host,'rf-mobile-coding-footer');move(q('.rf-coding-savebar',host),save);}
  }
  function imports(){
    if(page('import')?.classList.contains('rf-import-page'))return;
    const host=page('import'),upload=q('#dropZone')?.closest('.card');if(!upload)return;
    if(!q('.rf-mobile-upload-title',upload))add(node('h3','rf-mobile-upload-title','Import metadata'),upload,upload.firstChild);
    const guide=q('#rfImportGuide');if(guide&&!guide.parentElement.classList.contains('rf-mobile-guide-slot')){const slot=add(node('div','rf-mobile-guide-slot'),host,upload.nextSibling);move(guide,slot);}
    disclosure(q('.import-history',host),'Import history');const report=q('#reportBody')?.closest('.card');
    if(report&&!report.parentElement.closest('.rf-mobile-report')){const d=disclosure(report,'导入报告','rf-mobile-report');d.open=true;}
  }
  function exports(){
    if(page('export')?.classList.contains('rf-export-page'))return;
    const host=page('export');if(q('.rf-mobile-export-card',host))return;
    for(const [key,ids] of [['Screening decisions',['dlDecisions','dlDecisions2']],['Included literature',['dlRis','dlRis2']],['Coding & evidence',['dlMatrix','dlHighlights','dlScheme']]]){
      const c=add(node('section','card rf-mobile-export-card'),host);c.append(node('h3','',key));for(const id of ids)move(q('#'+id),c);}
    const trace=add(node('section','card rf-mobile-export-card'),host);trace.append(node('h3','','Trace & provenance'),button('轨迹',()=>go('trace'),'btn b-out rf-mobile-full'),button('Import history',()=>go('import'),'btn b-out rf-mobile-full'));
    // This short explanation does not need a separate disclosure step.
    const note=q(':scope>.card:not(.rf-mobile-export-card)',host);
    mobileClass(note,'rf-mobile-export-note');
    mobileClass(q(':scope>b',note),'rf-mobile-repeated-heading');
  }
  function tables(){
    for(const table of qa('.page[data-p="scheme"] table,.page[data-p="merge"] table,.trace-table')){
      const headings=qa('thead th',table).map(n=>n.textContent);
      for(const row of qa('tbody tr',table))qa('td',row).forEach((cell,i)=>{
        if(!states.has(cell)){const old=cell.getAttribute('data-mobile-column');states.set(cell,()=>{if(old===null)cell.removeAttribute('data-mobile-column');else cell.setAttribute('data-mobile-column',old);});}
        if(cell.dataset.mobileColumn!==headings[i])cell.dataset.mobileColumn=headings[i]||'';
      });}
    const metrics=q('#mergeMetrics');if(metrics?.textContent.trim())disclosure(metrics,'Agreement summary');
    const scheme=page('scheme'),schemeTools=block(scheme,'rf-mobile-scheme-tools');
    move(q('#schemeExport2'),schemeTools);move(q('#schemeImportBtn'),schemeTools);
    const dimensionList=q(':scope>.card',scheme);if(dimensionList&&schemeTools.nextElementSibling!==dimensionList)scheme.insertBefore(schemeTools,dimensionList);
    disclosure(q('#addDimBtn')?.closest('.card'),'新增维度');
  }
  function prisma(){
    const inputs=q('.rf-prisma-inputs');if(!inputs)return;
    const counts=block(inputs,'rf-mobile-flow-counts');counts.classList.add('card');if(!q(':scope>h3',counts))counts.prepend(node('h3','','Flow counts'));
    for(const c of qa(':scope>.card',inputs))if(c!==counts&&q('.rf-prisma-section',c)&&!q('#prMode',c))move(c,counts);
    for(const d of qa('.rf-prisma-section',counts))if(!states.has(d)){const previous=d.open;states.set(d,()=>{d.open=previous;});d.open=!!q('#prIncluded',d);}
    const setup=q('#prMode')?.closest('details'),generate=q('#prGenerate'),toolbar=q('.rf-prisma-toolbar');
    move(q('#prImport'),setup);move(generate,inputs);
    // Keep Auto-fill visible; the original guidance has its own disclosure.
    move(toolbar,inputs,generate);
  }
  function trace(){
    const host=q('#researchTracePage'),layout=q('.trace-layout',host);if(!host)return;
    const filters=q('.trace-filters',host);
    if(filters){
      const options=sheet(host,'Trace filters');move(filters,options);
      const tools=block(host,'rf-mobile-trace-tools');
      if(!q('.rf-mobile-trace-filters-open',tools))add(opener(options,'Trace filters','btn b-out rf-mobile-trace-filters-open'),tools);
      move(q('.rf-mobile-search-line',host),tools);
      const panel=q('.trace-panel',host);if(panel&&tools.nextElementSibling!==panel)host.insertBefore(tools,panel);
    }
    if(!layout)return;
    const recent=q('.trace-table',host)?.closest('.trace-panel');if(recent)move(recent,layout.parentNode,layout);
    for(const panel of qa('.trace-panel',host))if(panel!==recent)disclosure(panel,()=>q('h2',panel)?.textContent||T('轨迹'),'rf-mobile-trace-panel');
  }
  function help(){
    const host=page('help');for(const a of qa('.help-card',host))disclosure(a,()=>a.querySelector('h3')?.textContent||T('帮助'),'rf-mobile-help-topic');
    disclosure(q('.help-contents',host),'Find a topic');
    let search=q('.rf-mobile-help-search',host);
    if(!search){search=add(node('input','input rf-mobile-help-search'),host,q('.help-layout',host));search.type='search';
      search.addEventListener('input',()=>{const text=search.value.trim().toLocaleLowerCase();for(const topic of qa('.rf-mobile-help-topic',host))topic.hidden=!topic.textContent.toLocaleLowerCase().includes(text);});}
    if(!search.closest('.rf-mobile-search')){search.placeholder=tr('Find a topic');search.setAttribute('aria-label',tr('Find a topic'));}
  }
  function login(){
    const brand=q('#authCard>.auth-brand'),card=q('#authCard');if(brand)move(brand,card.parentNode,card);
    if(!q('.rf-mobile-auth-title',card))add(node('h2','rf-mobile-auth-title',()=>q('#submitText').textContent),card,q('#authForm'));
  }
  function compose(){
    observer.disconnect();
    for(const [n,a] of moves)if(!n.isConnected&&!a.isConnected)moves.delete(n);
    for(const n of added)if(!n.isConnected){added.delete(n);labels.delete(n);}
    for(const n of states.keys())if(!n.isConnected)states.delete(n);
    try{if(!media.matches)restore();else{home();screening();documents('fulltext');documents('bench');imports();exports();tables();prisma();trace();help();login();
      for(const [id] of stageOrder)stageTabs(page(id));}
      for(const [n,key] of labels)if(n.isConnected&&n.textContent!==tr(key))n.textContent=tr(key);refreshHeader();
    }finally{observer.observe(q('#appMain>.view'),{childList:true,subtree:true});observer.observe(q('#authCard'),{childList:true,subtree:true});observer.observe(q('#capScreen'),{childList:true,subtree:true});observer.observe(q('#barScreen'),{attributes:true,attributeFilter:['style']});}
  }
  function schedule(){if(!frame)frame=requestAnimationFrame(()=>{frame=0;compose();});}
  const menu=button('展开侧边栏',()=>RFNavigationSheet.setOpen(q('#rfSidebarToggle')?.getAttribute('aria-expanded')!=='true',{immediate:true}));
  menu.id='rfMobileNavigation';menu.setAttribute('aria-controls','rfSheetContent');labels.delete(menu);
  menu.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg>';
  const title=node('strong');title.id='rfMobilePageTitle';top.prepend(menu,title);
  function closeNavigation(){RFNavigationSheet.setOpen(false,{immediate:true});menu.focus({preventScroll:true});}
  q('#rfSidebarToggle').addEventListener('click',()=>{if(media.matches)closeNavigation();});
  document.addEventListener('keydown',e=>{if(media.matches&&e.key==='Escape'&&q('#rfNavigationSheet').contains(document.activeElement))closeNavigation();});
  /* F38 触屏两段式：折叠态下第一次点「3 排除」只展开理由行并聚焦，
     第二次点才提交（提交仍由原有 screenDecide 处理，行为/接口未改）。
     模型预填（StudyDesignBadge）或撤销回填已给理由时不再拦截。 */
  document.addEventListener('click',e=>{
    if(!media.matches||!e.target.closest?.('#btnExc'))return;
    const select=q('#excReason');if(!select||select.value)return;
    if(!revealReason())return;
    e.preventDefault();e.stopPropagation();select.focus({preventScroll:true});
  },true);
  /* 同一两段式也覆盖实体键盘/外接键盘：折叠态下按 3，原先只弹出「请先选择
     排除理由」而理由控件是 display:none——提示指向一个看不见的控件，是死路。
     这里在捕获阶段先把理由行展开，**不拦截事件**：原有的 screenDecide 照常执行，
     F08 的文字说明与「把焦点交给理由控件」因此都还在（此时控件已可见）。
     展开态的 3 与已带理由的 3 仍走原有全局键盘流，一行不改。 */
  document.addEventListener('keydown',e=>{
    if(!media.matches||e.isComposing||e.key!=='3'||e.ctrlKey||e.metaKey||e.altKey)return;
    const tag=(e.target.tagName||'').toLowerCase();
    if(tag==='input'||tag==='textarea'||e.target.isContentEditable)return;
    const select=q('#excReason');if(!select||select.value)return;
    revealReason();
  },true);
  function refreshHeader(){menu.hidden=title.hidden=!media.matches;const info=RFNavigation.pages.find(p=>p.id===S.page);title.textContent=T(info?.label||'');menu.setAttribute('aria-label',T('展开侧边栏'));menu.setAttribute('aria-expanded',q('#rfSidebarToggle')?.getAttribute('aria-expanded')||'false');}
  const observer=new MutationObserver(schedule);
  new MutationObserver(refreshHeader).observe(q('#rfSidebarToggle'),{attributes:true,attributeFilter:['aria-expanded']});
  for(const name of ['reviewflow:workspace-changed','reviewflow:language-changed','reviewflow:task-context-changed','reviewflow:coding-form-loaded'])document.addEventListener(name,()=>{
    if(name==='reviewflow:workspace-changed')for(const d of qa('.rf-mobile-sheet[open]'))d.close();schedule();});
  q('.help-contents')?.addEventListener('click',e=>{const link=e.target.closest('a[href^="#"]');if(media.matches&&link)q(link.getAttribute('href'))?.closest('details')?.setAttribute('open','');});
  media.addEventListener('change',compose);compose();
})();
