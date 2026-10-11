/* Presentation only: existing ordering, model switching and decision handlers stay in place. */
(function () {
  'use strict';
  const page=document.querySelector('.page[data-p="screen"]'),card=document.getElementById('scrCard'),body=document.getElementById('scrBody');
  if(!page||!card||!body||typeof el!=='function'||typeof T!=='function')return;
  const mode=page.querySelector('.al-mode'),hint=page.querySelector(':scope>p.hint');
  const importButton=document.getElementById('alModeImport'),aiButton=document.getElementById('alModeAI');
  if(!mode||!importButton||!aiButton)return;
  function icon(path){
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('class','rf-screen-icon');svg.setAttribute('aria-hidden','true');
    const line=document.createElementNS(svg.namespaceURI,'path');line.setAttribute('d',path);svg.append(line);return svg;
  }
  const order=el('div',{class:'rf-screen-order',role:'group'});mode.prepend(order);order.append(importButton,aiButton);
  const focusLabel=el('span'),focusButton=el('button',{type:'button',id:'rfScreenFocus'},icon('M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5'),focusLabel);
  const helpButton=el('button',{type:'button',id:'rfScreenHelp','aria-expanded':'false','aria-controls':'rfScreenHelpPanel'},icon('M5 12h.01M12 12h.01M19 12h.01'));
  const tools=el('div',{class:'rf-screen-tools'},focusButton,helpButton);mode.append(tools);
  const helpTitle=el('strong'),helpClose=el('button',{type:'button'},icon('M6 6l12 12M18 6 6 18'));
  const escapeHint=el('p',{class:'hint'});
  const help=el('section',{id:'rfScreenHelpPanel',class:'rf-screen-help',hidden:'','aria-labelledby':'rfScreenHelpTitle'},el('header',{},helpTitle,helpClose));
  helpTitle.id='rfScreenHelpTitle';
  if(hint)help.append(hint);
  help.append(escapeHint);
  for(const id of ['alBadge','alHint']){const node=document.getElementById(id);if(node)help.append(node);}
  page.append(help);
  const focusPosition=el('span'),exitLabel=el('span');
  const exitButton=el('button',{type:'button',class:'btn b-out'},icon('M3 8h5V3m13 5h-5V3M8 21v-5H3m13 5v-5h5'),exitLabel);
  const focusStrip=el('div',{class:'rf-focus-strip'},focusPosition,exitButton);card.prepend(focusStrip);
  let focused=false,observedTask=S.task?.task_id;
  function closeHelp(restore=false){help.hidden=true;helpButton.setAttribute('aria-expanded','false');if(restore)helpButton.focus();}
  function placeHelp(){const r=helpButton.getBoundingClientRect(),w=Math.min(340,window.innerWidth-24);help.style.width=w+'px';help.style.left=Math.max(12,Math.min(r.right-w,window.innerWidth-w-12))+'px';help.style.top=Math.max(12,Math.min(r.bottom+8,window.innerHeight-help.offsetHeight-12))+'px';}
  function setFocus(value){
    focused=!!value;closeHelp();window.RFStoppingGuidance?.close();
    document.body.classList.toggle('rf-screen-focus',focused);focusButton.setAttribute('aria-pressed',String(focused));
    if(focused)exitButton.focus();else if(S.page==='screen')focusButton.focus();
  }
  function syncMode(){page.classList.toggle('rf-screen-ai',aiButton.classList.contains('on'));}
  function syncPosition(){focusPosition.textContent=document.getElementById('scrPos').textContent;}
  function refreshText(){
    focusLabel.textContent=T('专注阅读');exitLabel.textContent=T('退出专注');helpTitle.textContent=T('阅读帮助');escapeHint.textContent=T('按 Esc 退出专注阅读');
    helpButton.setAttribute('aria-label',T('阅读帮助'));helpClose.setAttribute('aria-label',T('关闭'));
    order.setAttribute('aria-label',T('文献显示顺序'));syncMode();syncPosition();
  }
  function enhanceMetadata(){
    const meta=body.querySelector('.scr-meta');if(!meta||meta.querySelector('.rf-screen-details'))return;
    const article=S.articles?.[S.scrIdx];if(!article)return;
    const label=el('span',{text:T('详情')});
    const button=el('button',{type:'button',class:'rf-screen-details','aria-expanded':'false','aria-controls':'rfScreenMetadata'},label,icon('M5 12h.01M12 12h.01M19 12h.01'));
    const details=el('div',{id:'rfScreenMetadata',class:'rf-screen-metadata',hidden:''});
    for(const [key,value]of [['作者',article.authors],['期刊',article.journal],['年份',article.year],['文献类型',article.item_type],['DOI',article.doi]]){
      if(value!==null&&value!==undefined&&String(value).trim())details.append(el('p',{},el('strong',{text:T(key)+'：'}),el('span',{'data-rf-verbatim':'',text:String(value)})));
    }
    meta.append(button);meta.after(details);
    button.addEventListener('click',()=>{details.hidden=!details.hidden;button.setAttribute('aria-expanded',String(!details.hidden));});
  }
  focusButton.addEventListener('click',()=>setFocus(!focused));exitButton.addEventListener('click',()=>setFocus(false));
  helpButton.addEventListener('click',()=>{help.hidden=!help.hidden;helpButton.setAttribute('aria-expanded',String(!help.hidden));if(!help.hidden)placeHelp();});
  helpClose.addEventListener('click',()=>closeHelp(true));
  document.addEventListener('click',event=>{if(!help.hidden&&!help.contains(event.target)&&!helpButton.contains(event.target))closeHelp();});
  document.addEventListener('keydown',event=>{if(event.key!=='Escape'||S.page!=='screen'||event.defaultPrevented)return;if(!help.hidden){event.preventDefault();closeHelp(true);}else if(focused){event.preventDefault();setFocus(false);}});
  document.addEventListener('reviewflow:workspace-changed',event=>{closeHelp();if(event.detail!=='screen'&&focused)setFocus(false);});
  document.addEventListener('reviewflow:task-context-changed',()=>{const id=S.task?.task_id;if(id===observedTask)return;observedTask=id;closeHelp();if(focused)setFocus(false);});
  document.addEventListener('reviewflow:language-changed',refreshText);
  document.getElementById('logoutBtn')?.addEventListener('click',()=>{if(focused)setFocus(false);closeHelp();});
  window.addEventListener('resize',()=>{if(!help.hidden)placeHelp();});
  document.querySelector('#appMain>.view')?.addEventListener('scroll',()=>closeHelp(),{passive:true});
  new MutationObserver(syncMode).observe(aiButton,{attributes:true,attributeFilter:['class']});
  new MutationObserver(syncPosition).observe(document.getElementById('scrPos'),{childList:true,characterData:true,subtree:true});
  new MutationObserver(enhanceMetadata).observe(body,{childList:true});
  page.classList.add('rf-reader-page');refreshText();enhanceMetadata();
})();
