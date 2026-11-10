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
  const orderLabel=el('span',{class:'rf-screen-order-label'});
  const orderNow=el('span',{class:'rf-screen-order-now'});
  const order=el('div',{class:'rf-screen-order',role:'group'});mode.prepend(order);order.append(orderLabel,importButton,aiButton,orderNow);
  const focusLabel=el('span'),focusButton=el('button',{type:'button',id:'rfScreenFocus'},icon('M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5'),focusLabel);
  const helpButton=el('button',{type:'button',id:'rfScreenHelp','aria-expanded':'false','aria-controls':'rfScreenHelpPanel'},icon('M5 12h.01M12 12h.01M19 12h.01'));
  const tools=el('div',{class:'rf-screen-tools'},focusButton,helpButton);mode.append(tools);
  const helpTitle=el('strong'),helpClose=el('button',{type:'button'},icon('M6 6l12 12M18 6 6 18'));
  const escapeHint=el('p',{class:'hint'});
  const help=el('section',{id:'rfScreenHelpPanel',class:'rf-screen-help',hidden:'','aria-labelledby':'rfScreenHelpTitle'},el('header',{},helpTitle,helpClose));
  helpTitle.id='rfScreenHelpTitle';
  if(hint)help.append(hint);
  help.append(escapeHint);
  page.append(help);

  /* ---- F25：机器学习状态（已训练条数徽标 / 标签不足提示）常驻卡片上方 ----
     批准单 F25：alBadge 与 alHint 原先被收进默认隐藏的「阅读帮助」弹层，切 ML
     排序后 toast 宣称已开启、队列却仍是导入顺序且页面无可见变化。两者改为常态
     状态行（卡片正上方），仅在确有内容时占位；排序逻辑与队列不动。 */
  const mlStatus=el('div',{class:'rf-screen-ml-status',role:'status'});
  for(const id of ['alBadge','alHint']){const node=document.getElementById(id);if(node)mlStatus.append(node);}
  card.before(mlStatus);
  /* 幂等：只在值真的变化时才写 hidden——写入 hidden 会再次触发下方的
     MutationObserver，无条件赋值会让观察者自我激发成死循环（浏览器卡死）。 */
  function syncMlStatus(){
    const badge=document.getElementById('alBadge'),hintNode=document.getElementById('alHint');
    const showBadge=!!(badge&&badge.style.display!=='none'&&badge.textContent.trim());
    const showHint=!!(hintNode&&hintNode.textContent.trim());
    const hide=!(showBadge||showHint);
    if(mlStatus.hidden!==hide)mlStatus.hidden=hide;
  }

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
  /* F09（随版缓解）：标签旁常显实际顺序（「当前：导入顺序」等）。顺序取值来自
     既有按钮状态，不改排序。 */
  function syncOrderNow(){
    const ai=aiButton.classList.contains('on'),random=document.getElementById('alModeRandom')?.classList.contains('on');
    const label=ai?T('机器学习排序'):random?T('随机'):T('导入顺序');
    orderNow.textContent=T('当前：{0}',label);
    orderNow.setAttribute('data-rf-order',ai?'ai':random?'random':'import');
  }
  function syncPosition(){focusPosition.textContent=document.getElementById('scrPos').textContent;}
  function refreshText(){
    focusLabel.textContent=T('专注阅读');exitLabel.textContent=T('退出专注');helpTitle.textContent=T('阅读帮助');escapeHint.textContent=T('按 Esc 退出专注阅读');
    helpButton.setAttribute('aria-label',T('阅读帮助'));helpClose.setAttribute('aria-label',T('关闭'));
    orderLabel.textContent=T('Screening order');order.setAttribute('aria-label',T('Screening order'));syncMode();syncPosition();syncMlStatus();syncOrderNow();
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
  const randomButton=document.getElementById('alModeRandom');
  if(randomButton)new MutationObserver(syncOrderNow).observe(randomButton,{attributes:true,attributeFilter:['class']});
  new MutationObserver(syncPosition).observe(document.getElementById('scrPos'),{childList:true,characterData:true,subtree:true});
  new MutationObserver(enhanceMetadata).observe(body,{childList:true});
  /* 只观察子节点与文本：alBadge 的 style/hidden 变化由下方 badge 专属观察者
     覆盖（写的是 mlStatus 自身，不会与 badge 的观察者互相激发）。 */
  new MutationObserver(syncMlStatus).observe(mlStatus,{childList:true,subtree:true,characterData:true});
  const mlBadge=document.getElementById('alBadge');
  if(mlBadge)new MutationObserver(syncMlStatus).observe(mlBadge,{attributes:true,attributeFilter:['style','hidden'],childList:true,characterData:true,subtree:true});
  const mlHint=document.getElementById('alHint');
  if(mlHint)new MutationObserver(syncMlStatus).observe(mlHint,{childList:true,characterData:true,subtree:true});

  /* =====================================================================
     F08 / F22 / F28（r10-u1）：初筛决策栏的就地说明、理由默认值与历史浮层
     三者都只在呈现层工作——不触发保存、不改队列、不改判定逻辑。
     ===================================================================== */

  /* ---- F08：无理由按「3」时给出可见文字说明 ----
     批准单 F08：拦截时原先只有理由下拉出现绿色焦点框，页面上没有任何文字说明
     哪里出错、该做什么。此处在决策行上方渲染一行微文案；2 秒后自动淡出，焦点
     仍留在原因控件（键盘流不被打断），下一次渲染自然清除。 */
  let reasonErrorTimer=0;
  function clearReasonError(){
    if(reasonErrorTimer){clearTimeout(reasonErrorTimer);reasonErrorTimer=0;}
    for(const node of body.querySelectorAll('.scr-reason-error'))node.remove();
  }
  function reasonError(kind){
    clearReasonError();
    const row=body.querySelector('.scr-decisions');
    const node=el('p',{class:'scr-reason-error',role:'alert',text:T(kind==='custom'?'请输入自定义排除理由':'请先选择排除理由')});
    if(row)row.before(node);else body.append(node);
    reasonErrorTimer=setTimeout(()=>{node.remove();reasonErrorTimer=0;},2000);
  }

  /* ---- F22：沿用上次理由（会话记忆）+ 最近使用分组 + 多于 8 条时输入过滤 ----
     批准单 F22：每排除一篇都要重开下拉重选、无「沿用上次」默认值。默认值有促成
     「顺手排除错理由」的风险，因此默认项**必须带可见提示**（选项文案后缀「沿用
     上次」+ 决策行上方一行说明 + 下拉高亮），用户改选即消失；不静默套用。 */
  const LAST_REASON_KEY='rf_screen_last_reason',RECENT_REASONS_KEY='rf_screen_recent_reasons';
  const readSession=(key,fallback)=>{try{const raw=sessionStorage.getItem(key);return raw==null?fallback:raw;}catch(e){return fallback;}};
  const writeSession=(key,value)=>{try{sessionStorage.setItem(key,value);}catch(e){/* 隐私模式/不透明源：仅本页内存生效 */}};
  /* 内存镜像：sessionStorage 不可用（隐私模式、sandbox、不透明源）时记忆仍在
     本页会话内生效——「沿用上次理由」不能因存储被禁而悄悄失效。 */
  const sessionMemory=new Map();
  function readMemory(key,fallback){
    if(sessionMemory.has(key))return sessionMemory.get(key);
    const stored=readSession(key,null);
    if(stored===null)return fallback;
    sessionMemory.set(key,stored);return stored;
  }
  function writeMemory(key,value){sessionMemory.set(key,value);writeSession(key,value);}
  let memoryReasons=null;
  function lastReason(){return String(readMemory(LAST_REASON_KEY,'')||'');}
  function recentReasons(){
    if(memoryReasons)return memoryReasons;
    try{const list=JSON.parse(readMemory(RECENT_REASONS_KEY,'[]'));memoryReasons=Array.isArray(list)?list.filter(v=>typeof v==='string'&&v.trim()):[];}
    catch(e){memoryReasons=[];}
    return memoryReasons;
  }
  function rememberReason(reason){
    const text=String(reason||'').trim();if(!text)return;
    writeMemory(LAST_REASON_KEY,text);
    const list=[text,...recentReasons().filter(v=>v!==text)].slice(0,5);
    memoryReasons=list;writeMemory(RECENT_REASONS_KEY,JSON.stringify(list));
  }
  /* 标记串必须走词典：它直接出现在下拉选项文案里（规则「用户可见文本十语言同步」）。
     原始选项文案另存在 data 属性上，切换语言后仍能精确剥离——按当前语言的后缀
     做 endsWith 会在切过语言后认不出旧语言的残留标记。 */
  const carryMark=()=>T('（沿用上次）');
  function stripCarryMark(select){
    for(const option of select.options){
      if(option.dataset.rfCarryBase===undefined)continue;
      option.textContent=option.dataset.rfCarryBase;
      delete option.dataset.rfCarryBase;
    }
  }
  function markCarried(select,value){
    for(const option of select.options){
      if(option.value!==value||!option.value)continue;
      if(option.dataset.rfCarryBase!==undefined)continue;
      option.dataset.rfCarryBase=option.textContent;
      option.textContent=option.textContent+carryMark();
    }
  }
  let carryNote=null,reasonTools=null;
  function clearAuxiliary(){
    carryNote?.remove();carryNote=null;
    reasonTools?.remove();reasonTools=null;
  }
  function showCarry(select,value){
    clearCarryNote();
    const row=body.querySelector('.scr-decisions');if(!row)return;
    carryNote=el('p',{class:'scr-reason-carry'},
      el('span',{text:T('已沿用上次理由：{0}',value)}),
      el('button',{type:'button',class:'rf-link',text:T('不复用'),onclick:()=>{select.value='';select.dispatchEvent(new Event('change',{bubbles:true}));clearCarryNote();}}));
    row.before(carryNote);
  }
  function clearCarryNote(){carryNote?.remove();carryNote=null;}
  /* 「最近使用」分组：把已存在的选项移到下拉顶部（同值不重复、不丢翻译属性）。 */
  function buildRecentGroup(select){
    select.querySelectorAll('[data-wf-recent-group]').forEach(group=>{
      for(const option of [...group.options]){const sentinel=select.querySelector('[value="__custom__"]')||select.querySelector('[data-wf-save-reason]');if(sentinel)select.insertBefore(option,sentinel);else select.append(option);}
      group.remove();
    });
    const recents=recentReasons().filter(value=>[...select.options].some(option=>option.value===value));
    if(recents.length<2)return;
    const group=el('optgroup',{label:T('最近使用'),'data-wf-recent-group':''});
    for(const value of recents){const option=[...select.options].find(item=>item.value===value);if(option)group.append(option);}
    const placeholder=select.options[0];
    if(placeholder&&!placeholder.value)placeholder.after(group);else select.prepend(group);
  }
  /* 多于 8 条理由时给一行过滤输入（原生 select 的逐字匹配只做前缀，长理由库
     无法定位）；过滤只隐藏选项，不改变取值语义。 */
  function buildFilter(select){
    const eligible=[...select.options].filter(option=>option.value&&option.value!=='__custom__'&&!option.hasAttribute('data-wf-save-reason'));
    if(eligible.length<=8)return;
    const row=body.querySelector('.scr-decisions');if(!row)return;
    const input=el('input',{type:'search',class:'input scr-reason-filter',placeholder:T('筛选理由…'),'aria-label':T('筛选理由…')});
    let lastValue=select.value;
    const apply=()=>{
      const query=input.value.trim().toLowerCase();
      for(const option of select.options){
        const fixed=!option.value||option.value==='__custom__'||option.hasAttribute('data-wf-save-reason');
        option.hidden=!fixed&&!!query&&!option.textContent.toLowerCase().includes(query);
      }
      for(const group of select.querySelectorAll('optgroup'))group.hidden=[...group.options].every(option=>option.hidden);
      if(!query){if([...select.options].some(option=>option.value===lastValue))select.value=lastValue;return;}
      const first=[...select.options].find(option=>!option.hidden&&option.value);
      if(first)select.value=first.value;
    };
    input.addEventListener('input',apply);
    input.addEventListener('keydown',event=>{
      if(event.key!=='Enter')return;
      event.preventDefault();
      apply();select.focus();
    });
    select.addEventListener('change',()=>{if(select.value!=='__custom__')lastValue=select.value;});
    reasonTools=el('div',{class:'scr-reason-tools'},input);
    row.before(reasonTools);
  }
  /* decorate：在每次 renderScreen 建好下拉后调用（saved 回填之后、决策基线之前）。
     只在「未在回看历史、未选任何理由」时套用默认值，避免覆盖已保存决定。 */
  function decorate(select,custom,{carried=true}={}){
    if(!select)return;
    clearAuxiliary();clearReasonError();
    stripCarryMark(select);
    buildRecentGroup(select);
    buildFilter(select);
    const remembered=lastReason();
    const selectable=remembered&&[...select.options].some(option=>option.value===remembered);
    const apply=carried&&selectable&&!select.value&&!S.screenEditingKey;
    if(apply){
      select.value=remembered;
      if(custom){custom.hidden=true;custom.value='';}
      markCarried(select,remembered);
      showCarry(select,remembered);
    }
    if(!select.dataset.rfCarryBound){
      select.dataset.rfCarryBound='1';
      select.addEventListener('change',()=>{
        clearCarryNote();
        stripCarryMark(select);
        if(select.value&&select.value!=='__custom__'&&select.value!=='__wf_save_reason__')markCarried(select,'');
      });
    }
  }

  /* ---- F09（随版缓解）：ML 排序的一次性提示条 ----
     批准单 F09：默认排序=「导入」、分数默认隐藏，界面里可见的 ML 痕迹只有「筛选
     顺序」切换与「停止建议」，新手不知道还有 ML 排序。这里在初筛页首次出现时给
     一条可关闭提示条；点「试试」直接切到 ML 排序，点「知道了」/✕ 关闭，两种操作
     都写入 localStorage（与 rfpb_theme/rf_al_blind 同族）后不再出现——限频是批准
     单写明的风险项（常驻噪音）。排序策略与队列完全不动。 */
  const TIP_KEY='rf_screen_ml_tip';
  let tipSeen=()=>{try{return localStorage.getItem(TIP_KEY)==='1';}catch(e){return true;}/* 存储不可用：不打扰 */};
  let tipBar=null;
  function buildTip(){
    if(tipBar||tipSeen())return;
    const close=(dismiss)=>{try{localStorage.setItem(TIP_KEY,'1');}catch(e){}tipBar?.remove();tipBar=null;if(dismiss==='try')aiButton.click();};
    tipBar=el('div',{class:'rf-screen-ml-tip',role:'note'},
      el('span',{text:T('可以切换为机器学习排序：按模型预测的纳入概率排列待筛文献')}),
      el('button',{type:'button',class:'btn b-out sm',text:T('试试机器学习排序'),onclick:()=>close('try')}),
      el('button',{type:'button',class:'rf-screen-ml-tip-close','aria-label':T('关闭提示'),text:'✕',onclick:()=>close('dismiss')}));
    mlStatus.before(tipBar);
  }

  /* ---- F59（随版缓解）：重排期间的轻量指示 ----
     批准单 F59：每到更新间隔触发重排时卡片关键路径无任何反馈，界面停在退场
     画面数秒。这里在卡片区上方挂一条「排序更新中…」状态行；返回的结束函数
     幂等（失票、失败、成功都调用），重排落定后静默恢复。指示条只存在有重排
     的时段，避免常驻噪音。 */
  let rankingRow=null,rankingTickets=0;
  function ranking(on){
    if(on){
      rankingTickets++;
      if(!rankingRow){
        rankingRow=el('div',{class:'rf-screen-ranking',role:'status','aria-live':'polite'},
          el('i',{class:'rf-screen-ranking-dot','aria-hidden':'true'}),
          el('span',{text:T('排序更新中…')}));
        mlStatus.after(rankingRow);
      }
      return ()=>{
        rankingTickets=Math.max(0,rankingTickets-1);
        if(rankingTickets)return;
        rankingRow?.remove();rankingRow=null;
      };
    }
    rankingTickets=0;rankingRow?.remove();rankingRow=null;
    return null;
  }

  /* ---- F28：被省略的「…」可点开完整已决列表（只读；多步撤销不在本期范围）---- */
  function decisionsInOrder(){
    const rows=[];
    (S.articles||[]).forEach((article,index)=>{
      const saved=S.decisions?.[article.zotero_key];
      if(!saved)return;
      rows.push({key:article.zotero_key,index,decision:saved.decision,title:saved.title||article.title||'',at:saved.screened_at||saved.updated_at||''});
    });
    /* 队列外的已决记录（换过来源范围）也要能看到，避免「看不到历史」。 */
    for(const [key,saved] of Object.entries(S.decisions||{})){
      if(rows.some(row=>row.key===key))continue;
      const index=(S.articles||[]).findIndex(article=>article.zotero_key===key);
      rows.push({key,index,decision:saved.decision,title:saved.title||saved.exclusion_reason||'',at:saved.screened_at||saved.updated_at||''});
    }
    return rows;
  }
  function openHistory(){
    const rows=decisionsInOrder();
    const dialog=el('dialog',{class:'rf-hist-dialog','aria-labelledby':'rfHistTitle'});
    const list=el('ol',{class:'rf-hist-list'});
    const filter=el('select',{class:'input','aria-label':T('决策类型')});
    for(const [value,label] of [['','全部'],['include','纳入'],['exclude','排除']])filter.append(el('option',{value,text:T(label)}));
    function render(){
      list.textContent='';
      const shown=rows.filter(row=>!filter.value||row.decision===filter.value);
      if(!shown.length){list.append(el('li',{class:'hint',text:T('没有符合筛选条件的已决文献。')}));return;}
      for(const row of shown){
        const open=el('button',{type:'button',class:'btn b-out sm',text:T('打开'),onclick:()=>{
          dialog.close();dialog.remove();
          openRow(row).catch(error=>toast(String(error?.message||error),'warn'));
        }});
        open.disabled=row.index<0;
        list.append(el('li',{class:'rf-hist-row'},
          el('span',{class:'chip c-'+({include:'inc',maybe:'may',exclude:'exc'}[row.decision]||'') ,text:T({include:'纳入',maybe:'待定',exclude:'排除'}[row.decision]||'未决')}),
          el('span',{class:'rf-hist-title',text:row.title||row.key,'data-rf-verbatim':''}),
          row.at?el('time',{class:'rf-hist-time',text:String(row.at)}):null,
          open));
      }
    }
    async function openRow(row){
      if(typeof WorkflowControls==='undefined')return;
      if(!await WorkflowControls.guard())return;
      if(row.index<0){toast(T('该文献不在当前队列中，请重新载入初筛后再查看。'),'warn');return;}
      S.screenEditingKey=row.key;S.scrIdx=row.index;await renderScreen();
    }
    dialog.append(
      el('header',{},el('strong',{id:'rfHistTitle',text:T('全部已决文献')}),
        el('button',{type:'button',class:'rf-hist-close','aria-label':T('关闭'),text:'✕',onclick:()=>{dialog.close();dialog.remove();}})),
      el('div',{class:'rf-hist-tools'},filter,el('span',{class:'hint',text:T('只读列表；撤销请用「撤销上次保存」。')})),
      list);
    filter.addEventListener('change',render);
    dialog.addEventListener('cancel',()=>{dialog.remove();});
    render();
    document.body.append(dialog);
    dialog.showModal();
  }

  window.RFScreenReason=Object.freeze({
    decorate:(select,custom,options)=>decorate(select,custom,options),
    remember:rememberReason,
    missing:kind=>reasonError(kind),
    clearMessage:clearReasonError,
    ranking,
    lastReason,recentReasons
  });
  window.RFScreenHistory=Object.freeze({open:openHistory});

  /* ---- F12（随版缓解）：批注面板展开/收起时给 PDF 重排 300ms 过渡缓冲 ----
     批准单 F12：打开批注面板时 PDF 自动适宽由 175% 跳到 139%，阅读字号与位置
     突变。这里只在面板开合后的 320ms 内为 pdf.js 页面加过渡类（宽度/高度与
     缩放同步补间），不改缩放策略、不自动重算——手动适宽按钮照常可用。 */
  const sideToggle=document.getElementById('ftSideToggle');
  if(sideToggle){
    let settle=0;
    sideToggle.addEventListener('click',()=>{
      const stage=document.querySelector('#pdfWrap')?.closest('.pdfstage')||document.querySelector('.pdfstage');
      if(!stage)return;
      stage.classList.add('rf-zoom-settle');
      if(settle)clearTimeout(settle);
      settle=setTimeout(()=>{stage.classList.remove('rf-zoom-settle');settle=0;},320);
    });
  }

  page.classList.add('rf-reader-page');refreshText();enhanceMetadata();syncMlStatus();buildTip();
})();
