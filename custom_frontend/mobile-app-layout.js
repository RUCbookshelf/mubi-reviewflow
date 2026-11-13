/* Mobile preview presentation. Original controls and all save/decision logic stay
 * owned by the product. Nodes its mobile composer owns are styled in place. */
(function () {
  'use strict';
  const media = matchMedia('(max-width:760px)'), added = new Set(), moves = new Map(), attrs = new Map();
  let active = false, frame = 0, more = null, source = null, returnFocus = null;
  const inertStates = new Map();
  const q = (s, r = document) => r?.querySelector(s);
  const qa = (s, r = document) => [...(r?.querySelectorAll(s) || [])];
  const page = key => q('section.page[data-p="' + key + '"]');
  const tr = s => typeof T === 'function' ? T(s) : s;
  const labels = {zh:['首页','初筛','复筛','编码','更多','来源与排序','关闭','阅读工具','布局对比','文献','编码表','返回首页'],en:['Home','Screen','Full text','Coding','More','Sources & order','Close','Reading tools','Compare layout','Document','Coding form','Back to home']};
  Object.assign(labels,{fr:['Accueil','Tri initial','Texte intégral','Codage','Plus','Sources et ordre','Fermer','Outils de lecture','Comparer les vues','Document','Formulaire de codage','Retour à l’accueil'],ru:['Главная','Отбор','Полный текст','Кодирование','Ещё','Источники и порядок','Закрыть','Инструменты чтения','Сравнить макеты','Документ','Форма кодирования','На главную'],es:['Inicio','Cribado','Texto completo','Codificación','Más','Fuentes y orden','Cerrar','Herramientas de lectura','Comparar diseños','Documento','Formulario de codificación','Volver al inicio'],ja:['ホーム','初期選別','全文','コード化','その他','ソースと並び順','閉じる','閲覧ツール','レイアウト比較','文献','コード化フォーム','ホームに戻る'],pt:['Início','Triagem','Texto completo','Codificação','Mais','Fontes e ordem','Fechar','Ferramentas de leitura','Comparar layouts','Documento','Formulário de codificação','Voltar ao início'],de:['Start','Erstsichtung','Volltext','Kodierung','Mehr','Quellen und Reihenfolge','Schließen','Lesewerkzeuge','Layouts vergleichen','Dokument','Kodierformular','Zur Startseite'],sr:['Почетна','Одабир','Пуни текст','Кодирање','Више','Извори и редослед','Затвори','Алати за читање','Упореди распореде','Документ','Образац за кодирање','На почетну'],ko:['홈','초기 선별','원문','코딩','더 보기','출처 및 순서','닫기','읽기 도구','레이아웃 비교','문헌','코딩 양식','홈으로 돌아가기']});
  const text = i => {const language=typeof RFLang!=='undefined'?RFLang.get():'zh-CN';return (labels[language==='zh-CN'?'zh':language]||labels.en)[i];};
  const paths = ['M3 10 12 3l9 7v11h-6v-7H9v7H3Z','M4 5h16M4 12h10M4 19h6m5-3 3 3 4-6','M4 4h7a2 2 0 0 1 2 2v15a4 4 0 0 0-4-2H4Zm16 0h-5m5 0v15h-3','M7 3h10v4H7zM5 5H3v16h18V5h-2M7 12h10M7 16h6','M5 12h.01M12 12h.01M19 12h.01'];
  function node(tag, cls, label) { const n = document.createElement(tag); n.className = cls; if(label!=null)n.textContent=label; added.add(n); return n; }
  function button(label, fn, cls = 'btn b-out') { const b=node('button',cls,label);b.type='button';b.addEventListener('click',fn);return b; }
  function attr(n,key,value) { if(!n)return;if(!attrs.has(n))attrs.set(n,new Map());const old=attrs.get(n);if(!old.has(key))old.set(key,n.getAttribute(key));if(value==null)n.removeAttribute(key);else if(n.getAttribute(key)!==value)n.setAttribute(key,value); }
  function move(n,host,before=null) { if(!n||!host||n.parentNode===host)return;if(!moves.has(n)){const a=document.createComment('mobile app preview anchor');n.before(a);moves.set(n,a);}host.insertBefore(n,before); }
  function icon(index) { const s=document.createElementNS('http://www.w3.org/2000/svg','svg');s.setAttribute('viewBox','0 0 24 24');s.setAttribute('aria-hidden','true');const p=document.createElementNS(s.namespaceURI,'path');p.setAttribute('d',paths[index]);s.append(p);return s; }
  function dismissSource() { if(source){source.classList.remove('rfma-source-open');source=null;}for(const [n,previous]of inertStates)n.inert=previous;inertStates.clear();document.body.classList.remove('rfma-source-modal');const previous=returnFocus;returnFocus=null;previous?.focus({preventScroll:true}); }
  function openSource(host) {
    const target=q('.rf-mobile-queue',host)||q('.rf-mobile-source',host);if(!target)return;
    qa('dialog[open]').forEach(d=>d.close());returnFocus=document.activeElement;source=target;
    target.classList.add('rfma-source-open');document.body.classList.add('rfma-source-modal');
    for(let branch=target;branch&&branch!==document.body;branch=branch.parentElement)for(const sibling of branch.parentElement?.children||[]){if(sibling===branch||sibling.classList.contains('rfma-source-backdrop'))continue;if(!inertStates.has(sibling))inertStates.set(sibling,sibling.inert);sibling.inert=true;}
    q('button,select,input',target)?.focus({preventScroll:true});
  }
  function openTools() { const host=page(typeof S!=='undefined'?S.page:'home'),sheet=q(':scope>dialog.rf-mobile-sheet',host);if(sheet)sheet.showModal();else more?.showModal(); }
  function mount() {
    active=true;document.body.classList.add('rf-mobile-app-preview');
    const nav=node('nav','rfma-bottom-nav');nav.setAttribute('aria-label',tr('Workflow stages'));
    ['home','screen','fulltext','bench','more'].forEach((key,i)=>{const b=button(text(i),()=>{dismissSource();if(key==='more')more.showModal();else go(key);},'rfma-nav-item');b.dataset.rfmaPage=key;b.dataset.rfmaLabel=i;const span=node('span','',text(i));b.textContent='';b.append(icon(i),span);nav.append(b);});document.body.append(nav);
    more=node('dialog','rfma-more');more.setAttribute('aria-label',text(4));const head=node('header','rfma-sheet-heading');head.append(node('h2','',text(4)),button(text(6),()=>more.close()));more.append(head);
    const links=node('div','rfma-more-links');
    const training=button(tr('筛选训练'),()=>{more.close();q('#rfPilotEntry')?.click();},'btn b-out rfma-training-proxy');links.append(training);
    for(const info of window.RFNavigation?.pages||[]){if(['home','screen','fulltext','bench'].includes(info.id))continue;links.append(button(tr(info.label),()=>{more.close();go(info.id);}));}
    links.append(button(tr('工作区与账号'),()=>{more.close();window.RFNavigationSheet?.setOpen(true,{immediate:true});}));
    more.append(links);const prefs=node('div','rfma-preferences');more.append(prefs);
    for(const n of [q('#themeBtn'),q('#rf-app-language'),q('#rf-restart-button')])move(n,prefs);
    document.body.append(more);
    const tools=button(text(7),openTools,'rfma-app-tools');tools.id='rfmaAppTools';q('#appMain>.top')?.append(tools);
    const shade=node('div','rfma-source-backdrop');shade.addEventListener('click',dismissSource);document.body.append(shade);
    const home=page('home'),back=button(text(11),()=>window.WorkflowControls?.homeTab('overview'),'rfma-home-back');back.id='rfmaHomeBack';home?.prepend(back);
    const tabs=q('.wf-tabs',home),overview=q('#wf-panel-overview',home);if(tabs&&overview){const a=document.createComment('mobile settings categories');tabs.before(a);moves.set(tabs,a);overview.after(tabs);}
  }
  function configureReader(key) {
    const host=page(key),sheet=q(':scope>dialog.rf-mobile-sheet',host);if(!host||!sheet)return;
    if(!q('.rfma-source-entry',sheet)){const b=button(text(5),()=>openSource(host),'btn b-out rfma-source-entry');sheet.insertBefore(b,q('header',sheet)?.nextSibling||null);}
    const sourceHost=key==='screen'?q('.rf-mobile-queue',host):q('.rf-mobile-source',host);
    if(sourceHost&&!q('.rfma-sheet-heading',sourceHost)){
      attr(sourceHost,'data-rfma-source','');attr(sourceHost,'role','dialog');attr(sourceHost,'aria-label',text(5));attr(sourceHost,'aria-modal','true');
      const h=node('header','rfma-sheet-heading');h.append(node('h2','',text(5)),button(text(6),dismissSource));sourceHost.prepend(h);
    }
    // These modules are not reparented by mobile_concept; their own complete
    // controls remain together inside the original page's native options sheet.
    for(const selector of key==='fulltext'?['#rf-retraction-tools',':scope>div:has(>.rf-criteria-panel)']:key==='screen'?['.rf-screen-ml-tip',':scope>.rf-help-entry']:[':scope>.rf-help-entry'])move(q(selector,host),sheet);
    if(!q('.rfma-undo-entry',sheet)){
      const original=q('[data-undo-stage]',host);if(original){const b=button(original.textContent,()=>original.click(),'btn b-out rfma-undo-entry');b.disabled=original.disabled;sheet.append(b);}
    }else {const original=q('[data-undo-stage]',host);q('.rfma-undo-entry',sheet).disabled=!!original?.disabled;}
    if(key==='fulltext'&&!q('.rfma-comments-entry',sheet))sheet.append(button(tr('批注'),()=>{sheet.close();q('#ftSideToggle')?.click();},'btn b-out rfma-comments-entry'));
    if(key==='screen'&&!q('.rfma-training-entry',sheet))sheet.append(button(tr('筛选训练'),()=>{sheet.close();q('#rfPilotEntry')?.click();},'btn b-out rfma-training-entry'));
    if(key==='bench'&&!q('.rfma-coding-switch',host)){
      const sw=node('div','rfma-coding-switch');
      for(const [mode,index]of [['form',10],['pdf',9]])sw.append(button(text(index),()=>{attr(host,'data-rfma-coding',mode);if(mode==='pdf'){const d=q('.rf-mobile-document',host);if(d){attr(d,'open','');}}syncCoding();},'btn b-out'));
      q('#benchLayout',host)?.before(sw);attr(host,'data-rfma-coding','form');syncCoding();
    }
  }
  function syncCoding(){const host=page('bench'),buttons=qa('.rfma-coding-switch button',host);buttons.forEach((b,i)=>b.setAttribute('aria-pressed',String(host?.dataset.rfmaCoding===(i?'pdf':'form'))));}
  function restore() {
    dismissSource();more?.close();document.body.classList.remove('rf-mobile-app-preview','rfma-compare-open','rfma-keyboard');
    for(const [n,a]of [...moves].reverse()){if(a.isConnected){if(n.isConnected)a.replaceWith(n);else a.remove();}}moves.clear();
    for(const [n,values]of attrs)for(const [key,value]of values){if(value==null)n.removeAttribute(key);else n.setAttribute(key,value);}attrs.clear();
    for(const n of added)n.remove();added.clear();more=null;active=false;
    qa('.rfma-reason-open').forEach(n=>n.classList.remove('rfma-reason-open'));
  }
  function compose() {
    observer.disconnect();
    try{
      if(!media.matches||!document.body.classList.contains('rf-layout-preview-compact')||document.body.classList.contains('rf-workspace-presentation-signedout')){if(active)restore();return;}
      if(!q('.rf-mobile-document-actions',page('fulltext')))return;
      if(!active)mount();
      ['screen','fulltext','bench'].forEach(configureReader);
      const tools=q('#rfmaAppTools'),toolText=['screen','fulltext','bench'].includes(typeof S!=='undefined'?S.page:'')?text(7):text(4);if(tools&&tools.textContent!==toolText)tools.textContent=toolText;
      const training=q('#rfPilotEntry');qa('.rfma-training-proxy,.rfma-training-entry').forEach(b=>{b.hidden=!training||training.hidden;b.disabled=!training||training.disabled;});
      qa('.rfma-nav-item').forEach(b=>{const on=b.dataset.rfmaPage===(typeof S!=='undefined'?S.page:'home');if(b.getAttribute('aria-current')!==(on?'page':null)){if(on)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');}});
      const label=q('#ftDocLabel'),progress=q('.rf-mobile-document-status',page('fulltext'));if(label&&progress)attr(progress,'data-rfma-progress',label.textContent);
    }finally{observer.observe(q('#appMain'),{childList:true,subtree:true});}
  }
  function schedule(){if(!frame)frame=requestAnimationFrame(()=>{frame=0;compose();});}
  const observer=new MutationObserver(schedule);
  document.addEventListener('click',e=>{
    if(!active)return;
    if(e.target.closest('#ftExc')){const bar=q('.ft-decide'),reason=q('#ftReason');if(bar&&!bar.classList.contains('rfma-reason-open')&&!reason?.value){bar.classList.add('rfma-reason-open');e.preventDefault();e.stopImmediatePropagation();reason?.focus();}}
  },true);
  document.addEventListener('keydown',e=>{
    if(!active)return;
    if(e.key==='Escape'){dismissSource();document.body.classList.remove('rfma-compare-open');}
    if(source&&e.key!=='Tab'&&e.key!=='Escape'&&(e.ctrlKey||e.metaKey||!/INPUT|TEXTAREA|SELECT/.test(e.target.tagName))){if(!['Enter',' '].includes(e.key))e.preventDefault();e.stopImmediatePropagation();return;}
    if(e.key==='3'&&!e.ctrlKey&&!e.altKey&&!e.metaKey&&!/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)&&typeof S!=='undefined'&&S.page==='fulltext')q('.ft-decide')?.classList.add('rfma-reason-open');
    if(source&&e.key==='Tab'){const list=qa('button:not(:disabled),select:not(:disabled),input:not(:disabled),[tabindex="0"]',source).filter(n=>n.getClientRects().length);if(!list.length)return;const first=list[0],last=list.at(-1);if(e.shiftKey&&document.activeElement===first){last.focus();e.preventDefault();}else if(!e.shiftKey&&document.activeElement===last){first.focus();e.preventDefault();}}
  },true);
  document.addEventListener('focusin',e=>{if(active&&e.target.matches('#ftReason,#ftCustomReason'))q('.ft-decide')?.classList.add('rfma-reason-open');});
  function keyboardViewport(){document.body.classList.toggle('rfma-keyboard',active&&!!window.visualViewport&&innerHeight-window.visualViewport.height>150&&/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName));}
  window.visualViewport?.addEventListener('resize',keyboardViewport);
  document.addEventListener('focusin',keyboardViewport);document.addEventListener('focusout',()=>requestAnimationFrame(keyboardViewport));
  document.addEventListener('reviewflow:layout-preview-mode',compose);
  document.addEventListener('reviewflow:workspace-changed',()=>{dismissSource();more?.close();schedule();});
  document.addEventListener('reviewflow:language-changed',()=>{if(active){observer.disconnect();restore();}schedule();});
  // Before the product media listener restores its own anchors, return ours.
  window.addEventListener('resize',()=>{if(innerWidth>760&&active){observer.disconnect();restore();}schedule();});
  media.addEventListener('change',schedule);
  window.RFMobileAppPreview=Object.freeze({refresh:schedule,get ready(){return active;}});
  compose();
})();
