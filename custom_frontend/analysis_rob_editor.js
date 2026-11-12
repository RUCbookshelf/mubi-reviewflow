/* E-13 / E-15 / E-22：旧版 RoB 的可读记录、范围明确的编辑、暂存及字段校验。 */
(function () {
  'use strict';
  const host = document.getElementById('anJumpRob');
  if (!host) return;
  const ids = {study_id:'anRobStudy',comparison:'anRobComparison',outcome:'anRobOutcome',timepoint:'anRobTimepoint',
    framework:'anRobFramework',source_key:'anRobSource',source_locator:'anRobSourceLocator',overall:'anRobOverall',overall_rationale:'anRobOverallWhy'};
  const keyOf = row => JSON.stringify(['study_id','comparison','outcome','timepoint','framework'].map(key => row?.[key] || ''));
  const scope = () => S.task && S.user ? [S.task.task_id,S.user.user_id,S.user.username].join('|') : '';
  const prefix = 'rf_analysis_rob_draft:';
  const pick = el('select',{class:'input',id:'anRobSaved','aria-label':T('robEditor.saved')});
  const status = el('p',{class:'hint',role:'status',id:'anRobStatus'});
  const records = el('div',{id:'anRobRecords'});
  const newButton = el('button',{class:'btn b-out',type:'button',text:T('robEditor.new')});
  const controls = el('div',{class:'row'},el('label',{for:'anRobSaved',text:T('robEditor.saved')}),pick,newButton);
  // E-04：结果级人工 RoB 与逐文献完整评价分开存储，入口互链不复制或合并判断。
  const provenance=el('p',{class:'hint',text:T('robEditor.provenance')});
  const appraisalButton=el('button',{id:'anRobOpenAppraisal',class:'btn b-out',type:'button',text:T('robEditor.open_appraisal')});
  appraisalButton.addEventListener('click',openCompleteAppraisal);
  host.querySelector('b').after(provenance,appraisalButton,controls,status,records);
  async function openCompleteAppraisal(){
    if(appraisalButton.disabled)return false;
    const identity=scope(),source=field('anRobSource').value;
    appraisalButton.disabled=true;
    try {
      // go 的现有守卫处理当前未保存评估；只有导航完成后才切换到逐文献个人评价。
      if(await go('bench')===false||scope()!==identity||S.page!=='bench')return false;
      if(window.WorkflowEvidence?.context()!=='personal'){
        window.WorkflowEvidence?.setContext('personal');
        await loadBench();
        if(scope()!==identity||S.page!=='bench')return false;
      }
      const index=(S.cbQueue||[]).findIndex(row=>row.zotero_key===source);
      if(index<0){
        const text=T('robEditor.choose_coding_report');
        const line=field('cbStatus');if(line)line.textContent=text;
        toast(text,'info',8000);return false;
      }
      if(S.cbIdx!==index){S.cbIdx=index;await renderBench();}
      if(scope()!==identity||S.page!=='bench'||S.cbQueue?.[S.cbIdx]?.zotero_key!==source)return false;
      await window.AppraisalWorkspace?.open();
      return true;
    }catch(error){toast(window.WorkflowControls?.errorText?.(error)||error.message,'warn');return false;}
    finally{appraisalButton.disabled=false;}
  }
  let bound='',items=[],baseline=null,remembered=null,editing=null,busy=false,registered=false;
  const field = id => document.getElementById(id);
  const message = (key,...args) => {status.textContent=T(key,...args);};
  function capture() {
    const value=Object.fromEntries(Object.entries(ids).map(([key,id])=>[key,field(id).value.trim()]));
    value.domains={};
    host.querySelectorAll('#anRobDomains [data-domain]').forEach(row=>{
      value.domains[row.dataset.domain]={judgement:row.querySelector('select').value,rationale:row.querySelector('input').value.trim()};
    });
    return value;
  }
  function dirty() {return Boolean(bound && bound===scope() && baseline && JSON.stringify(capture())!==JSON.stringify(baseline));}
  function persist() {
    if(bound!==scope())return;
    remembered=capture();
    try {
      if(dirty()) sessionStorage.setItem(prefix+bound,JSON.stringify({value:remembered,baseline,editing}));
      else sessionStorage.removeItem(prefix+bound);
    } catch (_) {}
  }
  function fill(value) {
    for(const [key,id] of Object.entries(ids))field(id).value=value?.[key]||'';
    anReportOptions('anRobStudy','anRobSource');
    field('anRobSource').value=value?.source_key||'';
    renderRobDomains();
    field('anRobOverall').value=value?.overall||'';
    host.querySelectorAll('#anRobDomains [data-domain]').forEach(row=>{
      const domain=value?.domains?.[row.dataset.domain];
      row.querySelector('select').value=domain?.judgement||'';
      row.querySelector('input').value=domain?.rationale||'';
    });
  }
  function bind() {
    const next=scope();if(next===bound)return;
    bound=next;items=[];editing=null;remembered=null;fill({});baseline=capture();status.textContent='';
    try {
      const saved=JSON.parse(sessionStorage.getItem(prefix+bound)||'null');
      if(saved?.value && saved.baseline){remembered=saved.value;baseline=saved.baseline;editing=saved.editing;fill(remembered);message('robEditor.restored');}
    } catch (_) {}
  }
  async function choose(row) {
    const identity=scope();
    if(dirty() && !await RFConfirm({title:'robEditor.discard_title',message:T('robEditor.discard'),danger:false}))return false;
    if(identity!==scope())return false;
    editing=row?keyOf(row):null;fill(row||{});baseline=capture();remembered=capture();persist();
    pick.value=editing||'';message(row?'robEditor.editing':'robEditor.new_hint');return true;
  }
  pick.addEventListener('change',async()=>{const selected=pick.value;const row=items.find(item=>keyOf(item)===selected&&item.reviewer===S.user?.username);if(!await choose(row))pick.value=editing||'';});
  newButton.addEventListener('click',()=>choose(null));
  host.addEventListener('input',event=>{if(event.target.id!=='anRobSaved')persist();});
  host.addEventListener('change',event=>{if(event.target.id!=='anRobSaved')persist();});
  function failure(key,id,...args) {const input=field(id);input?.focus();input?.setAttribute('aria-invalid','true');throw Error(T(key,...args));}
  function validate(value) {
    host.querySelectorAll('[aria-invalid]').forEach(input=>input.removeAttribute('aria-invalid'));
    for(const key of ['study_id','outcome','timepoint','framework','source_key','overall','overall_rationale'])
      if(!value[key])failure('robEditor.required',ids[key],T('robEditor.'+key));
    for(const row of host.querySelectorAll('#anRobDomains [data-domain]')) {
      const domain=value.domains[row.dataset.domain];
      if(!domain.judgement){row.querySelector('select').focus();throw Error(T('robEditor.domain_judgment',T(row.dataset.domain)));}
      if(!domain.rationale){row.querySelector('input').focus();throw Error(T('robEditor.domain_reason',T(row.dataset.domain)));}
    }
  }
  function serverError(error) {
    const text=error?.message||'';
    for(const [pattern,key] of [[/^domain rationale is required: (.+)$/,'robEditor.domain_reason'],[/^invalid domain judgement: (.+)$/,'robEditor.domain_judgment']]){
      const match=text.match(pattern);if(match)return T(key,T(match[1]));
    }
    const known={'overall rationale is required':'robEditor.overall_required','all framework domains are required for a completed assessment':'robEditor.domains_required',
      'all result, reviewer, judgement and source fields are required':'robEditor.fields_required','unsupported risk-of-bias framework':'robEditor.framework_required',
      'invalid overall judgement':'robEditor.overall_invalid'};
    return known[text]?T(known[text]):(window.WorkflowControls?.errorText?.(error)||text);
  }
  async function save() {
    if(busy||!scope()||bound!==scope())return false;
    const identity=bound,body=capture(),before=JSON.stringify(body),selected=editing;
    try {
      validate(body);
      // 后端键包含 reviewer（由认证用户决定），改变任何研究范围字段均会产生另一条记录。
      if(selected && keyOf(body)!==selected){
        const existing=items.some(row=>keyOf(row)===keyOf(body)&&row.reviewer===S.user?.username);
        if(!await RFConfirm({title:'robEditor.scope_title',message:T(existing?'robEditor.scope_existing':'robEditor.scope_new'),danger:false}))return false;
        if(identity!==scope()||JSON.stringify(capture())!==before)return false;
      }
      busy=true;field('anRobSave').disabled=true;message('robEditor.saving');
      await api(taskApi()+'/analysis/risk-of-bias',{method:'PUT',body});
      if(identity!==scope())return true;
      editing=keyOf(body);baseline=body;
      if(JSON.stringify(capture())===before){remembered=body;persist();message('robEditor.saved_notice');}
      else {persist();message('robEditor.saved_earlier');}
      const current=await api(taskApi()+'/analysis/risk-of-bias');
      if(identity===scope())loaded(current.assessments);
      return true;
    } catch(error) {if(identity===scope()){status.textContent=serverError(error);toast(status.textContent,'warn');}return false;}
    finally {busy=false;field('anRobSave').disabled=false;}
  }
  function literal(tag,text) {return el(tag,{translate:'no',text:String(text??'')});}
  function renderDta(results) {
    if(!Array.isArray(results))return;
    let area=field('anDtaRecords');if(!area){area=el('div',{id:'anDtaRecords'});const raw=field('anDtaResult');(raw.closest('details')||raw).before(area);}
    area.replaceChildren(el('h4',{text:T('robEditor.dta_records')}));
    for(const row of results){const article=el('article',{class:'rf-qual-record'});
      article.append(literal('b',[row.study_id,row.index_test,row.target_condition,row.threshold,row.reference_standard].filter(Boolean).join(' · ')),
        literal('p',['TP: '+row.tp,'FP: '+row.fp,'FN: '+row.fn,'TN: '+row.tn].join(' · ')),
        el('p',{text:T('robEditor.source')}),literal('p',[row.source_key,row.source_locator].filter(Boolean).join(' · ')));area.append(article);}
  }
  function loaded(rows,dta) {
    bind();items=Array.isArray(rows)?rows:[];
    if(remembered)fill(remembered);else baseline=capture();
    pick.replaceChildren(el('option',{value:'',text:T('robEditor.new')}));records.replaceChildren();
    for(const row of items){
      const label=[row.study_id,row.comparison,row.outcome,row.timepoint,row.framework,row.reviewer].filter(Boolean).join(' · ');
      if(row.reviewer===S.user?.username)pick.append(el('option',{value:keyOf(row),translate:'no',text:label}));
      const card=el('details',{class:'rf-plain-disclosure'},el('summary',{translate:'no',text:label}));
      card.append(el('p',{text:T('robEditor.overall')+': '+T(row.overall)}),literal('p',row.overall_rationale));
      for(const [name,domain] of Object.entries(row.domains||{}))card.append(el('b',{text:T(name)+' · '+T(domain.judgement)}),literal('p',domain.rationale));
      card.append(el('p',{text:T('robEditor.source')}),literal('p',[row.source_key,row.source_locator].filter(Boolean).join(' · ')));
      if(row.reviewer===S.user?.username){const edit=el('button',{class:'btn b-out',type:'button',text:T('robEditor.edit')});edit.addEventListener('click',()=>choose(row));card.append(edit);}
      records.append(card);
    }
    pick.value=editing||'';renderDta(dta);
  }
  function register(){if(registered||!window.WorkflowControls?.registerDraft)return;registered=true;WorkflowControls.registerDraft('analysis-rob',{dirty,save,discard:()=>{fill(baseline||{});remembered=baseline;persist();},saving:()=>busy});}
  window.AnalysisRobEditor={save,loaded};
  document.addEventListener('reviewflow:task-context-changed',bind);
  document.addEventListener('reviewflow:workspace-changed',()=>{bind();register();});
  document.addEventListener('reviewflow:language-changed',()=>{loaded(items);newButton.textContent=T('robEditor.new');provenance.textContent=T('robEditor.provenance');appraisalButton.textContent=T('robEditor.open_appraisal');controls.querySelector('label').textContent=T('robEditor.saved');pick.setAttribute('aria-label',T('robEditor.saved'));});
  bind();register();
})();
