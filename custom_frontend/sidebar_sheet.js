/* Presentation only. Reuse the original controls and routes; reverse one timeline. */
(function () {
  'use strict';
  const shell=document.getElementById('appShell'),main=document.getElementById('appMain');
  const toggle=document.getElementById('rfSidebarToggle'),lockup=shell?.querySelector('.lockup');
  if(!shell||!main||!toggle||!lockup||typeof el!=='function'||typeof T!=='function')return;
  const sheet=el('div',{id:'rfNavigationSheet',hidden:'','aria-label':'ReviewFlow'});
  const header=el('div',{class:'rf-sheet-header'}),content=el('div',{id:'rfSheetContent'});
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
  const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d','M4 4h16v16H4zM9 4v16M5.5 8h1M5.5 12h1M5.5 16h1');svg.append(path);
  toggle.replaceChildren(svg);toggle.setAttribute('aria-controls','rfSheetContent');
  header.append(toggle,lockup);content.append(shell);sheet.append(header,content);document.body.append(sheet);
  const revealGroups=[[lockup]];
  // Group headings share their following first item; every navigation item gets one delay.
  for(const node of shell.children){
    if(node.classList.contains('grp'))continue;
    if(node.classList.contains('nav')){
      const buttons=[...node.querySelectorAll('button')];
      const group=node.previousElementSibling?.classList.contains('grp')?node.previousElementSibling:null;
      if(group&&buttons[0])revealGroups.push([group,buttons.shift()]);
      revealGroups.push(...buttons.map(button=>[button]));
    }else if(node.id!=='rfCloseNavigation')revealGroups.push([node]);
  }
  const reveals=revealGroups.flat();
  reveals.forEach(node=>node.setAttribute('data-rf-sheet-reveal',''));
  let animations=[],time=0,total=0,target=false,raf=0,last=0,pendingTaskFocus=false;
  let lockedMain=false,previousMainInert=false,dimensions={width:264,height:600};
  const mobile=matchMedia('(max-width:760px)'),reduced=matchMedia('(prefers-reduced-motion:reduce)');
  let work=0;
  function loggedIn(){return main.style.display!=='none' && !!S.user;}
  function syncAccess(){
    const ready=target && time>=total;
    content.inert=!ready;shell.inert=!ready;content.setAttribute('aria-hidden',String(!ready));
    lockup.setAttribute('aria-hidden',String(!ready));
    toggle.setAttribute('aria-expanded',String(target));
    const label=T(target?'收起侧边栏':'展开侧边栏');toggle.setAttribute('aria-label',label);toggle.title=label;
    const modal=target&&mobile.matches&&!sheet.hidden;
    if(document.body.classList.contains('mobile-nav-open')!==modal)document.body.classList.toggle('mobile-nav-open',modal);
    document.getElementById('mobileMenuBtn')?.setAttribute('aria-expanded',String(modal));
    if(modal&&!lockedMain){previousMainInert=main.inert;main.inert=true;lockedMain=true;}
    else if(!modal&&lockedMain){main.inert=previousMainInert;lockedMain=false;}
    if(ready&&pendingTaskFocus){pendingTaskFocus=false;const selector=document.getElementById('taskSel');selector?.scrollIntoView({block:'nearest'});selector?.focus({preventScroll:true});}
  }
  function paint(value){
    time=Math.max(0,Math.min(total,value));animations.forEach(animation=>{animation.currentTime=time;});
    if(!sheet.animate){
      const open=time>0;sheet.style.width=(open?dimensions.width:44)+'px';sheet.style.height=(open?dimensions.height:44)+'px';sheet.style.borderRadius=open?'14px':'22px';sheet.style.background=open?'var(--surface)':'var(--primary)';sheet.style.borderColor=open?'var(--border)':'transparent';toggle.style.color=open?'var(--ink)':'var(--surface)';reveals.forEach(node=>{node.style.opacity=open?'1':'0';});
    }
  }
  function frame(stamp){
    if(!last)last=stamp;const delta=stamp-last;last=stamp;
    paint(time+(target?delta:-delta));syncAccess();
    if((target&&time<total)||(!target&&time>0))raf=requestAnimationFrame(frame);else{raf=0;last=0;}
  }
  function setOpen(open,options={}){
    target=!!open&&loggedIn()&&!document.body.classList.contains('rf-screen-focus');
    if(!target){pendingTaskFocus=false;if(options.focus || content.contains(document.activeElement))toggle.focus({preventScroll:true});}
    syncAccess();
    if(options.immediate||reduced.matches||!sheet.animate){if(raf)cancelAnimationFrame(raf);raf=0;last=0;paint(target?total:0);syncAccess();return;}
    if(!raf && ((target&&time<total)||(!target&&time>0))){last=0;raf=requestAnimationFrame(frame);}
  }
  function rebuild(){
    const ratio=total?time/total:0;animations.forEach(animation=>animation.cancel());animations=[];
    const width=Math.max(44,Math.min(mobile.matches ? window.innerWidth-24 : 264,window.innerWidth-24));
    header.style.width=width+'px';content.style.width=width+'px';content.style.height='auto';
    const height=Math.max(44,Math.min(46+shell.scrollHeight+2,mobile.matches ? window.innerHeight*.82 : window.innerHeight-24));
    dimensions={width,height};content.style.height=Math.max(0,height-46)+'px';
    total=360+(revealGroups.length-1)*(1000/60)*4+150;
    if(sheet.animate){
      const style=getComputedStyle(document.documentElement),ease='cubic-bezier(.22,1,.36,1)';
      const add=(node,keys,timing)=>{const animation=node.animate(keys,{...timing,fill:'both'});animation.pause();animations.push(animation);};
      add(sheet,[{width:'44px',height:'44px',borderRadius:'22px',backgroundColor:style.getPropertyValue('--primary').trim(),borderColor:'transparent'},{width:width+'px',height:height+'px',borderRadius:'14px',backgroundColor:style.getPropertyValue('--surface').trim(),borderColor:style.getPropertyValue('--border').trim()}],{duration:360,easing:ease});
      add(toggle,[{color:style.getPropertyValue('--surface').trim()},{color:style.getPropertyValue('--ink').trim()}],{duration:360,easing:ease});
      revealGroups.forEach((group,index)=>group.forEach(node=>add(node,[{opacity:0,transform:'translateY(5px)'},{opacity:1,transform:'translateY(0)'}],{delay:360+index*(1000/60)*4,duration:150,easing:'ease-out'})));
    }
    paint(ratio*total);syncAccess();
  }
  function refresh(){
    sheet.hidden=!loggedIn()||document.body.classList.contains('rf-screen-focus');
    if(sheet.hidden)setOpen(false,{immediate:true});else syncAccess();
  }
  function scheduleLayout(){if(work)return;work=requestAnimationFrame(()=>{work=0;rebuild();refresh();});}
  function focusTask(){pendingTaskFocus=true;setOpen(true);}
  sheet.addEventListener('click',event=>{
    // Route clicks are already handled by the shared workspace registry.
    if(event.target.closest('#logoutBtn,#rf-update-check'))setOpen(false);
  });
  document.addEventListener('pointerdown',event=>{if(target&&!sheet.contains(event.target)&&!document.getElementById('rfTaskContext')?.contains(event.target))setOpen(false);});
  document.addEventListener('keydown',event=>{
    if(sheet.hidden)return;
    if(event.key==='Escape'&&(target||time>0)){event.preventDefault();setOpen(false,{focus:true});return;}
    if(event.key!=='Tab'||!target||!mobile.matches)return;
    const controls=[...sheet.querySelectorAll('button:not(:disabled),select:not(:disabled),a[href],input:not(:disabled)')].filter(node=>node.getClientRects().length&&!node.closest('[hidden],[inert]'));
    const first=controls[0],lastControl=controls.at(-1);if(!first)return;
    if(event.shiftKey&&(document.activeElement===first||!sheet.contains(document.activeElement))){event.preventDefault();lastControl.focus();}
    else if(!event.shiftKey&&(document.activeElement===lastControl||!sheet.contains(document.activeElement))){event.preventDefault();first.focus();}
  });
  window.addEventListener('resize',scheduleLayout);
  document.addEventListener('reviewflow:language-changed',scheduleLayout);
  document.addEventListener('reviewflow:task-context-changed',scheduleLayout);
  document.addEventListener('reviewflow:workspace-changed',()=>{refresh();scheduleLayout();});
  new MutationObserver(scheduleLayout).observe(shell,{childList:true,characterData:true,subtree:true});
  new MutationObserver(scheduleLayout).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme','data-density']});
  new MutationObserver(refresh).observe(main,{attributes:true,attributeFilter:['style']});
  new MutationObserver(refresh).observe(document.body,{attributes:true,attributeFilter:['class']});
  reduced.addEventListener('change',()=>setOpen(target,{immediate:true}));
  // Mark the presentation mode only after the original controls have been moved.
  document.body.classList.remove('rf-sidebar-collapsed');document.body.classList.add('rf-navigation-sheet');
  window.RFNavigationSheet=Object.freeze({setOpen,focusTask,refresh});rebuild();refresh();
})();
