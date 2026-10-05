/* Hierarchical application navigation. Task context is not a navigation level.
 * This module changes routes/presentation only; task selection keeps its original handler. */
(function () {
  'use strict';
  const registry=window.RFNavigation,shell=document.getElementById('appShell'),main=document.getElementById('appMain');
  if(!registry||!shell||!main)return;
  const top=main.querySelector('.top');
  const stageLast={};let navigating=false,lastRenderKey='',openNode=null,closeTimer=null,hoverTimer=null,pinned=false,suppressed=null;
  const crumb=el('nav',{class:'rf-breadcrumb','aria-label':T('当前位置')});
  const trail=el('ol',{class:'rf-breadcrumb-list'});crumb.append(trail);
  const taskContext=el('button',{id:'rfTaskContext',class:'rf-task-context',type:'button'});
  const taskLabel=el('span',{class:'rf-task-label',text:T('当前任务')});
  const taskName=el('span',{id:'rfCurrentTask',translate:'no'});taskContext.append(taskLabel,taskName);
  const appearanceControls=top.lastElementChild;
  top.insertBefore(crumb,top.querySelector('.tabs')||appearanceControls);top.insertBefore(taskContext,appearanceControls);
  const sidebarToggle=el('button',{id:'rfSidebarToggle',class:'btn b-out',type:'button','aria-controls':'appShell'});
  top.prepend(sidebarToggle);
  function syncSidebar(){
    const collapsed=document.body.classList.contains('rf-sidebar-collapsed');
    sidebarToggle.textContent=T(collapsed?'展开侧边栏':'收起侧边栏');
    sidebarToggle.setAttribute('aria-expanded',String(!collapsed));
    shell.inert=collapsed&&window.innerWidth>760;
  }
  function setSidebarCollapsed(collapsed){document.body.classList.toggle('rf-sidebar-collapsed',collapsed);syncSidebar();}
  sidebarToggle.addEventListener('click',()=>setSidebarCollapsed(!document.body.classList.contains('rf-sidebar-collapsed')));
  window.addEventListener('resize',syncSidebar);
  document.addEventListener('reviewflow:language-changed',syncSidebar);
  syncSidebar();
  // Reuse the original controls and icons; no second global navigation is introduced.
  const home=shell.querySelector('[data-nav="home"]'),firstGroup=shell.querySelector('.grp');
  const homeNav=el('nav',{class:'nav rf-home-navigation','aria-label':T('首页与设置')},home);firstGroup.before(homeNav);
  const stageGroups=[...shell.querySelectorAll('.grp')].slice(0,3);
  registry.stages.forEach((stage,index)=>{
    const label=stageGroups[index],nav=label?.nextElementSibling;if(!nav?.classList.contains('nav'))return;
    label.dataset.rfStage=stage.id;nav.setAttribute('aria-label',T(stage.label));
    for(const page of registry.pages.filter(item=>item.stage===stage.id)){
      const control=shell.querySelector('[data-nav="'+page.id+'"]');if(control)nav.append(control);
    }
  });
  for(const control of shell.querySelectorAll('[data-nav]')){
    [...control.childNodes].filter(node=>node.nodeType===3).forEach(node=>node.remove());
    control.append(el('span',{'data-rf-nav-label':control.dataset.nav}));
  }
  const homeHeading=el('h2',{class:'rf-page-title','data-rf-page-title':'home'});main.querySelector('section[data-p="home"]').prepend(homeHeading);
  function icon(){
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 16 16');svg.setAttribute('aria-hidden','true');
    const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d','m4 6 4 4 4-4');path.setAttribute('fill','none');path.setAttribute('stroke','currentColor');path.setAttribute('stroke-width','1.5');path.setAttribute('stroke-linecap','round');path.setAttribute('stroke-linejoin','round');svg.append(path);return svg;
  }
  function getPage(){return registry.page(S.page)||registry.page('home');}
  function currentRoute(){return getPage().id==='analysis'?(window.RFAnalysisWorkspace?.currentRoute()||{page:'analysis'}):{page:getPage().id};}
  function close(restore=false){
    clearTimeout(closeTimer);clearTimeout(hoverTimer);
    if(!openNode)return;
    const node=openNode;node.menu.hidden=true;node.trigger.setAttribute('aria-expanded','false');openNode=null;pinned=false;
    if(restore&&node.trigger.isConnected)node.trigger.focus();
  }
  function place(node){
    const rect=node.item.getBoundingClientRect(),menu=node.menu;
    const width=Math.min(310,window.innerWidth-24),available=window.innerHeight-rect.bottom-18;
    menu.style.width=width+'px';menu.style.left=Math.max(12,Math.min(rect.left,window.innerWidth-width-12))+'px';
    menu.style.top=(rect.bottom+7)+'px';menu.style.maxHeight=Math.max(100,available)+'px';
  }
  function open(node,focus=false,hover=false){
    clearTimeout(closeTimer);clearTimeout(hoverTimer);if(openNode!==node)close();
    openNode=node;pinned=!hover;node.menu.hidden=false;node.trigger.setAttribute('aria-expanded','true');place(node);
    if(focus)(node.menu.querySelector('[aria-current="page"]')||node.menu.querySelector('a'))?.focus();
  }
  function makeLevel({id,label,route,items,menuLabel,current=false,stage=false}){
    const item=el('li',{class:'rf-crumb-level'}),container=el('div',{class:'rf-crumb-control'});
    const name=route?el('a',{class:'rf-crumb-link',href:registry.href(route),text:T(label)}):el('button',{type:'button',class:'rf-stage-trigger',text:T(label)});
    if(route&&current)name.setAttribute('aria-current','page');
    container.append(name);item.append(container);
    if(!items?.length){trail.append(item);return;}
    const trigger=stage?name:el('button',{type:'button',class:'rf-level-trigger'},icon());
    if(stage)trigger.append(icon());else container.append(trigger);
    trigger.setAttribute('aria-expanded','false');trigger.setAttribute('aria-controls','rfLevelMenu-'+id);trigger.setAttribute('aria-label',T(menuLabel));
    const menu=el('div',{id:'rfLevelMenu-'+id,class:'rf-level-menu',hidden:true,'aria-label':T(menuLabel)});
    menu.append(el('p',{class:'rf-menu-heading',text:T(menuLabel)}));
    const list=el('ul',{});menu.append(list);
    for(const entry of items){
      const link=el('a',{href:registry.href(entry.route),text:T(entry.label)});
      if(entry.current)link.setAttribute('aria-current','page');
      list.append(el('li',{},link));
    }
    item.append(menu);trail.append(item);
    const node={item,trigger,menu};
    trigger.addEventListener('click',()=>{if(openNode===node&&pinned){close();suppressed=node;}else open(node,true);});
    trigger.addEventListener('keydown',event=>{
      if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();open(node,true);if(event.key==='ArrowUp')menu.querySelector('li:last-child a')?.focus();}
    });
    item.addEventListener('pointerenter',event=>{if(event.pointerType!=='mouse'||suppressed===node)return;clearTimeout(closeTimer);hoverTimer=setTimeout(()=>open(node,false,true),170);});
    item.addEventListener('pointerleave',()=>{clearTimeout(hoverTimer);suppressed=null;if(!item.contains(document.activeElement))closeTimer=setTimeout(()=>close(),240);});
    item.addEventListener('focusout',()=>setTimeout(()=>{if(!item.contains(document.activeElement)&&!item.matches(':hover')&&openNode===node)close();},0));
    menu.addEventListener('keydown',event=>{
      const links=[...menu.querySelectorAll('a')],index=links.indexOf(document.activeElement);if(index<0)return;let next=null;
      if(event.key==='ArrowDown')next=(index+1)%links.length;if(event.key==='ArrowUp')next=(index+links.length-1)%links.length;
      if(event.key==='Home')next=0;if(event.key==='End')next=links.length-1;
      if(next!==null){event.preventDefault();links[next].focus();}
    });
  }
  function syncVocabulary(){
    for(const page of registry.pages){
      shell.querySelector('[data-rf-nav-label="'+page.id+'"]')?.replaceChildren(document.createTextNode(T(page.label)));
      const section=main.querySelector('section.page[data-p="'+page.id+'"]');
      const heading=page.id==='home'?homeHeading:section.querySelector(':scope>h2,:scope>.an-page-header>h2,:scope>.page-cap>b');
      if(heading)heading.textContent=T(page.label);
      const button=shell.querySelector('[data-nav="'+page.id+'"]');button?.setAttribute('title',T(page.label));
    }
    for(const group of stageGroups){const stage=registry.stage(group.dataset.rfStage);if(stage){group.textContent=T(stage.label);group.nextElementSibling?.setAttribute('aria-label',T(stage.label));}}
    homeNav.setAttribute('aria-label',T('首页与设置'));
  }
  function refresh(){
    const page=getPage(),route=currentRoute(),name=S.task?.name||S.task?.task_name||S.task?.task_id||T('未选择任务');
    taskLabel.textContent=T('当前任务');taskName.textContent=name;taskContext.title=T('在侧栏切换任务')+' · '+name;taskContext.setAttribute('aria-label',taskContext.title);
    crumb.setAttribute('aria-label',T('当前位置'));document.getElementById('rfCloseNavigation')?.setAttribute('aria-label',T('关闭导航'));
    if(S.page)document.title=T(page.label)+' · ReviewFlow';
    const key=JSON.stringify([route,RFLang.get()]);
    if(key===lastRenderKey)return;
    lastRenderKey=key;close();syncVocabulary();trail.replaceChildren();
    if(page.stage){
      const stage=registry.stage(page.stage);
      makeLevel({id:'stage',label:stage.label,stage:true,menuLabel:'切换阶段',items:registry.stages.map(item=>({label:item.label,route:{page:stageLast[item.id]||item.defaultPage},current:item.id===stage.id}))});
    }
    const siblings=page.stage?registry.siblings(page.id):[];
    makeLevel({id:'page',label:page.label,route:{page:page.id},current:!route.group,menuLabel:'切换同级页面',items:siblings.length>1?siblings.map(item=>({label:item.label,route:{page:item.id},current:item.id===page.id})):null});
    if(page.id==='analysis'&&route.group){
      const group=registry.group(route.group);
      makeLevel({id:'group',label:group.label,route:{page:'analysis',group:group.id},current:!route.tool,menuLabel:'切换分析模块',items:registry.groups.map(item=>({label:item.label,route:{page:'analysis',group:item.id},current:item.id===group.id}))});
      if(route.tool){const tool=registry.tool(route.tool);makeLevel({id:'tool',label:tool.label,route:registry.routeFor(tool),current:true,menuLabel:'切换分析方法',items:registry.methods(tool.group).map(item=>({label:item.label,route:registry.routeFor(item),current:item.id===tool.id}))});}
    }
    if(S.page)document.title=T(page.label)+' · ReviewFlow';
  }
  function focusDestination(route){
    const target=route.page==='analysis'?(route.tool?document.getElementById(['entry','synthesis'].includes(registry.tool(route.tool)?.view)?'anJumpEffects':registry.tool(route.tool)?.view):document.getElementById('anMethodCatalog')):main.querySelector('section.page.on');
    if(target){if(!target.hasAttribute('tabindex'))target.tabIndex=-1;target.focus({preventScroll:true});}
  }
  function navigate(value,options={}){
    const candidate=typeof value==='string'?{page:value}:value;
    if(!candidate?.page)return;
    const parsed=registry.parse(registry.href(candidate));if(!parsed)return;
    const route={...parsed,...(candidate.anchor?{anchor:candidate.anchor}:{})};
    close();setMobileNav(false);navigating=true;
    try{
      const destination=registry.href(route);
      if(options.history!==false&&location.hash!==destination)history.pushState({workspace:route.page},'',destination);
      if(getPage().id!==route.page||!S.page)go(route.page);
      else if(route.page!=='analysis'&&!options.restoreScroll)main.querySelector('.view').scrollTop=0;
      if(route.page==='analysis')window.RFAnalysisWorkspace?.applyRoute(route,options);
      const stage=registry.page(route.page).stage;if(stage)stageLast[stage]=route.page;
      refresh();if(options.focus)focusDestination(route);
    }finally{navigating=false;}
  }
  crumb.addEventListener('click',event=>{
    const link=event.target.closest('a[href]');if(!link||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
    const route=registry.parse(link.hash);if(!route)return;event.preventDefault();navigate(route,{focus:event.detail===0});
  });
  shell.addEventListener('click',event=>{const button=event.target.closest('[data-nav]');if(!button)return;event.preventDefault();event.stopImmediatePropagation();navigate(button.dataset.nav,{focus:event.detail===0});},true);
  taskContext.addEventListener('click',()=>{setSidebarCollapsed(false);if(window.innerWidth<=760)setMobileNav(true);const selector=document.getElementById('taskSel');selector.scrollIntoView({block:'nearest'});selector.focus({preventScroll:true});});
  document.addEventListener('pointerdown',event=>{if(openNode&&!openNode.item.contains(event.target))close();});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&openNode){event.preventDefault();suppressed=openNode;close(true);}});
  function fromHistory(){
    if(!S.user)return;
    let route=registry.parse(location.hash);
    if(!route&&/^#an/.test(location.hash)){
      let id;try{id=decodeURIComponent(location.hash.slice(1));}catch{}const tool=id&&registry.forElement(id);if(tool)route={...registry.routeFor(tool),anchor:id};
    }
    if(!route)route={page:'home'};
    navigate(route,{history:false,restoreScroll:true});
  }
  window.addEventListener('popstate',fromHistory);window.addEventListener('hashchange',fromHistory);
  document.addEventListener('reviewflow:workspace-changed',()=>{
    if(!navigating&&S.page){
      const expected=registry.parse(location.hash);
      if(expected?.page!==S.page)history.replaceState({workspace:S.page},'',registry.href(currentRoute()));
    }
    refresh();
  });
  document.addEventListener('reviewflow:analysis-view-changed',()=>{if(S.page==='analysis')refresh();});
  document.addEventListener('reviewflow:task-context-changed',refresh);
  document.addEventListener('reviewflow:analysis-core-loaded',refresh);
  document.addEventListener('reviewflow:language-changed',()=>{lastRenderKey='';refresh();});
  window.addEventListener('resize',()=>close());main.querySelector('.view').addEventListener('scroll',()=>{if(openNode)close();},{passive:true});
  const mobileClose=el('button',{id:'rfCloseNavigation',type:'button','aria-label':T('关闭导航')});
  const closeSvg=icon();closeSvg.querySelector('path').setAttribute('d','m4 4 8 8M12 4l-8 8');mobileClose.append(closeSvg);shell.prepend(mobileClose);
  mobileClose.addEventListener('click',()=>setMobileNav(false));
  document.addEventListener('keydown',event=>{
    if(event.key!=='Tab'||!document.body.classList.contains('mobile-nav-open'))return;
    const controls=[...shell.querySelectorAll('button:not(:disabled),select:not(:disabled),a[href],input:not(:disabled),[tabindex="0"]')].filter(node=>node.getClientRects().length&&!node.closest('[hidden],[inert]'));
    if(!controls.length)return;const first=controls[0],last=controls.at(-1);
    if(event.shiftKey&&(document.activeElement===first||!shell.contains(document.activeElement))){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&(document.activeElement===last||!shell.contains(document.activeElement))){event.preventDefault();first.focus();}
  });
  window.RFWorkspaceShell=Object.freeze({refresh,navigate,close,currentRoute});refresh();
})();
