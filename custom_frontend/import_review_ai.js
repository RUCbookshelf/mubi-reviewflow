/* Suggestions and key settings for the import review workspace. */
const RFImportAIConfidence = (() => {
  const HIGH_CONFIDENCE = 0.9;
  function confidenceGrade(row) {
    if (row?.degraded) return 'gray';
    if (row?.verdict === 'unsure') return 'amber';
    if (row?.verdict !== 'same' || typeof row.confidence !== 'number') return null;
    if (row.confidence >= HIGH_CONFIDENCE) return 'green';
    return row.confidence >= 0.6 ? 'amber' : null;
  }
  function* bulkChunks(actions) {
    for (let offset = 0; offset < actions.length; offset += 100) {
      yield actions.slice(offset, offset + 100);
    }
  }
  return {confidenceGrade, bulkChunks};
})();
if (typeof module === 'object' && module.exports) module.exports = RFImportAIConfidence;
(function () {
  'use strict';
  if (typeof window === 'undefined') return;
  const {confidenceGrade,bulkChunks}=RFImportAIConfidence;
  const U=ImportExportWorkspace,W=WorkflowControls,n=U.node,l=U.literal,b=U.button;
  const apiPath=()=>taskApi()+'/import-review/ai-assist';
  const ui='import.review.ai.',lang='import_ai_assist.',merge='import_merge.conflict.';
  const fieldLabels={title:'Title',authors:'Authors',year:'Year',journal:'Journal',doi:'DOI',abstract:'Abstract',item_type:'Record type',url:'URL'};
  const warningKeys={
    low_confidence:'low_confidence',confidence_missing:'confidence_missing',invalid_verdict:'invalid_verdict',
    provider_response_not_json:'parse_failed',provider_error:'provider_error',provider_unavailable:'provider_unavailable',
    field_pick_dropped:'field_picks_ignored',field_picks_invalid:'field_picks_ignored',field_picks_incomplete:'field_picks_incomplete',
    decision_response_invalid:'decision_response_invalid',decision_kind_not_supported:'decision_kind_not_supported'
  };
  const crossCheckWarnings={
    cross_check_unavailable:'第二个 AI 来源不可用。',
    cross_check_second_failed:'第二个 AI 来源调用失败。',
    cross_check_disagree:'两个 AI 来源的判定不一致。'
  };
  const verdicts=new Set(['same','different','unsure']);
  let settings=null,settingsError=null,loading=null,epoch=0,submitting=false,counts=null,authDenied=false;
  const suggestions=new Map(),audits=new Map(),selected=new Map(),conflicts=new Map();
  const requestedKeepers=new WeakMap(),appliedByBatch=new Map();
  const scopeKey=()=>W.scope()+':'+epoch;
  const identity=item=>JSON.stringify([item.batch_id,item.kind,item.item_id]);
  const available=()=>settings?.enabled===true&&settings.has_key===true;
  const maxItems=()=>Math.min(20,Number(settings?.max_items)||20);
  const message=error=>W.errorText(error);
  const fieldLabel=field=>T(fieldLabels[field]||'Field');

  function reference(batchId,item,pendingOnly=false){
    if(!batchId||item.legacy)return null;
    if(item.members&&(!pendingOnly||['pending','undecided'].includes(item.status))){
      return {batch_id:batchId,kind:'duplicate_group',item_id:item.review_id};
    }
    if(item.metadata&&(!pendingOnly||item.metadata.action==null||item.metadata.action==='pending')){
      return {batch_id:batchId,kind:'metadata_diff',item_id:item.record_id};
    }
    return null;
  }

  async function load(){
    const scope=scopeKey();
    if(loading?.scope===scope)return loading.promise;
    const request={scope,promise:null};
    request.promise=(async()=>{
      const before=JSON.stringify([settings,settingsError]);
      try{
        const result=await api(apiPath()+'/settings');
        if(scope!==scopeKey())return false;
        settings=result;settingsError=null;authDenied=false;
      }catch(error){
        if(scope!==scopeKey())return false;
        settings=null;
        authDenied=error.status===403&&!error.messageKey;
        settingsError=message(error);
        if(authDenied&&S.page==='import'&&typeof go==='function'){
          toast(settingsError,'warn');
          void go('home',{guarded:true});
          if(typeof loadTasks==='function')void loadTasks().catch(W.error);
        }
      }finally{if(loading===request)loading=null;}
      return before!==JSON.stringify([settings,settingsError]);
    })();
    loading=request;
    return request.promise;
  }

  function disclosure(conflict=false){
    const keys=settings?.sent_fields||[];
    const fields=keys.filter(key=>Object.hasOwn(fieldLabels,key)).map(fieldLabel).join(' · ');
    return [
      T(lang+'disclosure.title'),
      T(lang+'disclosure.sent_fields',{fields}),
      T(lang+'disclosure.never_sent'),
      T(lang+'disclosure.explicit_trigger'),
      T(lang+'disclosure.'+(settings?.key_source==='shared'?'byok_shared':'byok_personal')),
      T(lang+'disclosure.low_confidence'),
      T(lang+'disclosure.audit'),
      T(lang+'disclosure.confirm_required'),
      ...(conflict?[T(ui+'conflict_disclosure')]:[])
    ];
  }

  function dialog(host,title){
    const panel=n('dialog',null,{class:'rf-import-ai-dialog','aria-label':T(title)});
    const dismiss=b('Close',()=>panel.close());
    const content=n('div'),status=n('p',null,{role:'status',class:'hint'}),actions=n('div',null,{class:'buttons'});
    const header=n('header'),body=n('div',null,{class:'rf-import-ai-dialog-body'});
    header.append(n('h2',title),dismiss);
    body.append(content,status);
    panel.append(header,body,actions);
    let pending=false;
    panel.addEventListener('cancel',event=>{if(pending)event.preventDefault();});
    panel.addEventListener('close',()=>panel.remove(),{once:true});
    host.append(panel);
    panel.showModal();
    dismiss.focus();
    return {panel,content,status,actions,lock(value){pending=value;dismiss.disabled=value;},close(){panel.close();}};
  }

  function showDisclosure(parent,conflict=false){
    const list=n('ul',null,{class:'rf-import-ai-disclosure'});
    for(const line of disclosure(conflict))list.append(l('li',line));
    parent.append(list);
  }

  function settingsDialog(host,redraw){
    const view=dialog(host,ui+'settings'),snapshot=settings,scope=scopeKey();
    const enable=!snapshot.enabled;
    if(enable)showDisclosure(view.content);
    const change=b(ui+(enable?'enable':'disable'),async()=>{
      if(scope!==scopeKey())return;
      change.disabled=true;view.lock(true);
      try{
        const result=await api(apiPath()+'/settings',{method:'PUT',body:{expected_version:snapshot.version,enabled:enable}});
        if(scope!==scopeKey())return;
        settings=result;settingsError=null;
        view.close();redraw();
      }catch(error){
        if(scope!==scopeKey())return;
        view.status.textContent=message(error);
        if(error.messageKey===lang+'settings_version_conflict'||error.messageKey===lang+'settings_owner_only'){
          await load();
          view.panel.addEventListener('close',redraw,{once:true});
          return;
        }
        change.disabled=false;
      }finally{view.lock(false);}
    },'primary');
    view.actions.append(change);
  }

  function selectionDialog(host,batchId,items,all,redraw,refresh,currentKeeper,candidates){
    const refs=items.map(item=>[item,reference(batchId,item,true)]).filter(([,ref])=>ref);
    const allItems=candidates.map(item=>[item,reference(batchId,item,true)]).filter(([,ref])=>ref);
    const known=refs.map(([,ref])=>ref);
    if(all)for(const ref of known)selected.set(identity(ref),ref);
    const view=dialog(host,ui+'entry');
    showDisclosure(view.content);
    const list=n('div',null,{class:'rf-import-ai-selection'});
    const indicator=n('p',null,{role:'status'});
    for(const [item,ref] of refs){
      const key=identity(ref),checkbox=n('input',null,{type:'checkbox',checked:selected.has(key)});
      const label=n('label');
      checkbox.onchange=()=>{
        if(checkbox.checked)selected.set(key,ref);else selected.delete(key);
        update();
      };
      label.append(checkbox,l('span',item.members?.[0]?.article?.title||item.members?.[0]?.record?.article?.title||item.article?.title||item.original?.title||T('Untitled record')));
      list.append(label);
    }
    const crossCheck=n('input',null,{type:'checkbox','aria-describedby':'rf-import-cross-check-help'});
    const crossLabel=n('label');
    crossLabel.append(crossCheck,n('span','双源交叉验证'));
    view.content.append(indicator,list,crossLabel,n('p','需配置不同厂商的个人和任务共享 LLM 钥匙。结果一致时提高置信度，不一致时标记为不确定。',{
      class:'hint',id:'rf-import-cross-check-help'
    }));
    const selectedRefs=()=>[...selected.values()].filter(ref=>ref.batch_id===batchId);
    const generate=b(ui+'generate',async()=>{
      const refs=selectedRefs();
      if(refs.length===0||refs.length>maxItems())return;
      const keepers=new Map();
      for(const [item,ref] of allItems){
        if(ref.kind==='duplicate_group')keepers.set(identity(ref),currentKeeper(item));
      }
      await request(view,generate,refs,result=>{
        for(const ref of refs)selected.delete(identity(ref));
        for(const row of result.suggestions||[])requestedKeepers.set(row,keepers.get(identity(row)));
        counts={batchId,values:result.counts};
      },redraw,refresh,!crossCheck.disabled&&crossCheck.checked);
      if(view.panel.open)crossCheck.disabled=!selectedRefs().some(ref=>ref.kind==='duplicate_group');
    },'primary');
    generate.title=disclosure().join('\n');
    view.actions.append(generate);
    function update(){
      const size=selectedRefs().length;
      crossCheck.disabled=!selectedRefs().some(ref=>ref.kind==='duplicate_group');
      indicator.textContent=T(ui+'selected',{n:size,max:maxItems()});
      view.status.textContent=size>maxItems()?T(lang+'too_many_items',{max_items:maxItems()}):'';
      generate.disabled=!size||size>maxItems()||submitting;
    }
    update();
  }

  async function request(view,generate,items,apply,redraw,refresh,crossCheck=false){
    if(submitting||!available())return;
    const scope=scopeKey();
    submitting=true;generate.disabled=true;view.lock(true);
    const inputs=[...view.panel.querySelectorAll('input,select')];
    for(const input of inputs)input.disabled=true;
    view.status.textContent=T('AI 建议生成中…');
    try{
      const result=await api(apiPath(),{method:'POST',body:{items,cross_check:crossCheck}});
      if(scope!==scopeKey())return;
      for(const row of result.suggestions||[]){
        if(row.audit_id!=null)audits.set(row.audit_id,row);
        if(row.kind!=='merge_conflict')suggestions.set(identity(row),row);
      }
      apply(result);
      view.close();
    }catch(error){
      if(scope!==scopeKey())return;
      const key=error.messageKey;
      view.status.textContent=message(error);
      if([lang+'disabled',lang+'key_missing','coding.collaboration_owner_unset',lang+'owner_required'].includes(key)||(error.status===403&&!key)){
        await load();
        view.panel.addEventListener('close',redraw,{once:true});
      }else if([lang+'item_not_found',lang+'item_unavailable'].includes(key)){
        for(const item of items)selected.delete(identity(item));
        view.actions.append(b('Refresh',()=>{view.close();refresh();}));
      }else if(key===lang+'too_many_items'){
        const excess=items.slice(maxItems());
        for(const item of excess)selected.delete(identity(item));
        view.panel.addEventListener('close',redraw,{once:true});
      }else generate.disabled=false;
    }finally{
      if(scope===scopeKey()){
        submitting=false;view.lock(false);
        for(const input of inputs)input.disabled=false;
        if(!view.panel.open)redraw();
      }
    }
  }

  function rowSuggestion(batchId,item){
    const ref=reference(batchId,item);
    return ref?suggestions.get(identity(ref)):null;
  }

  function badge(batchId,item){
    const ref=reference(batchId,item);
    const row=rowSuggestion(batchId,item)||[...conflicts.values()].find(entry=>
      ref?.kind==='duplicate_group'&&entry.row.batch_id===batchId&&entry.row.item_id===ref.item_id&&
      entry.fingerprint===JSON.stringify(item.members))?.row;
    const grade=confidenceGrade(row);
    if(!grade)return null;
    const label={green:'高置信（≥0.90）',amber:'待复核（置信度 ≥0.60 且 <0.90，或判定为不确定）',gray:'已降级'}[grade];
    const attrs={class:'badge '+grade,title:T(label)};
    if(grade==='gray')attrs.style='color:var(--muted);background:var(--border)';
    return n('span',grade==='amber'?lang+'verdict.'+row.verdict:label,attrs);
  }

  function summary(row,link=true){
    const section=n('section',null,{class:'rf-import-ai-suggestion'});
    if(verdicts.has(row.verdict))section.append(n('strong',lang+'verdict.'+row.verdict));
    if(typeof row.confidence==='number')section.append(l('span',T(lang+'confidence',{value:row.confidence.toFixed(2)})));
    if(row.rationale_zh)section.append(l('p',row.rationale_zh,{class:'rf-import-ai-rationale'}));
    const warnings=new Set((row.warnings||[]).map(token=>Object.hasOwn(crossCheckWarnings,token)?crossCheckWarnings[token]:
      lang+'degraded.'+(Object.hasOwn(warningKeys,token)?warningKeys[token]:'generic')));
    for(const warning of warnings)section.append(n('p',warning,{class:'hint'}));
    if(link&&row.audit_id!=null)section.append(b(ui+'audit',()=>openAudit(section.closest('#wf-import-review'),row.audit_id)));
    return section;
  }

  function openAudit(host,id){
    if(!host)return;
    const scope=scopeKey(),view=dialog(host,ui+'audit');
    view.status.textContent=T('Loading…');
    api(apiPath()+'/audit/'+encodeURIComponent(id)).then(audit=>{
      if(scope!==scopeKey()||!view.panel.open)return;
      view.status.textContent='';
      view.content.append(l('p',[audit.model,audit.prompt_version].filter(Boolean).join(' · ')),summary(audit,false));
      if(audit.fields?.length){
        const headings=[merge+'column.field',merge+'source.rule',merge+'source.ai',ui+'audit_human'];
        const {wrap,body}=makeTable(headings);
        for(const field of audit.fields){
          const row=n('tr');
          const final=field.human_final===null||field.undone?T(ui+'audit_pending'):(field.human_final_value??'—');
          row.append(l('th',fieldLabel(field.field),{scope:'row'}),
            l('td',pickLabel(field,field.rule_pick)),l('td',pickLabel(field,field.ai_pick)),l('td',final));
          body.append(row);
        }
        view.content.append(wrap);
      }
      view.actions.append(b(ui+'audit_export',()=>{
        const url=URL.createObjectURL(new Blob([JSON.stringify(audit,null,2)],{type:'application/json'}));
        const link=n('a',null,{href:url,download:'import-ai-audit-'+audit.audit_id+'.json'});
        link.click();
        setTimeout(()=>URL.revokeObjectURL(url),1000);
      }));
    }).catch(error=>{
      if(scope===scopeKey()&&view.panel.open)view.status.textContent=message(error);
    });
  }

  function makeTable(headings){
    const wrap=n('div',null,{class:'table-wrap rf-import-ai-table'}),table=n('table'),head=n('thead'),header=n('tr'),body=n('tbody');
    for(const heading of headings)header.append(n('th',heading,{scope:'col'}));
    head.append(header);table.append(head,body);wrap.append(table);
    return {wrap,body};
  }

  function pickLabel(field,pick){
    return pick==='left'?(field.left||'—'):pick==='right'?(field.right||'—'):'—';
  }

  function conflictKey(ref,keeper){
    return JSON.stringify([identity(ref),keeper?.keeper_key||null,keeper?.keeper_record_id||null]);
  }

  function conflict({batchId,item,keeper,busy,redraw,refresh,apply}){
    if(!available()||!item.members.some(member=>member.ref_type==='record'&&member.ref_id!==keeper.keeper_record_id))return null;
    const ref={batch_id:batchId,kind:'merge_conflict',item_id:item.review_id,...keeper};
    const key=conflictKey(ref,keeper),fingerprint=JSON.stringify(item.members);
    const previous=conflicts.get(key);
    const result=previous?.fingerprint===fingerprint?previous:null;
    const box=n('section',null,{class:'rf-import-ai-conflict'});
    const generate=b(ui+'conflicts',()=>{
      const view=dialog(box.closest('#wf-import-review'),ui+'conflicts');
      showDisclosure(view.content,true);
      const submit=b(ui+'generate',()=>request(view,submit,[ref],response=>{
        const row=response.suggestions?.find(entry=>conflictKey(entry,entry.keeper)===key);
        if(row)conflicts.set(key,{row,picks:Object.create(null),fingerprint});
      },redraw,refresh),'primary');
      submit.title=disclosure(true).join('\n');
      view.actions.append(submit);
    });
    generate.title=disclosure(true).join('\n');
    generate.disabled=busy||submitting;
    box.append(generate);
    if(!result)return box;
    const {row,picks}=result;
    box.append(n('h4',merge+'title'),summary(row));
    if(!row.fields?.length){box.append(n('p',ui+'no_conflicts'));return box;}
    const headings=['field','keeper','merged','prefill','source','rationale','editable'].map(name=>merge+'column.'+name);
    const {wrap,body}=makeTable(headings);
    for(const field of row.fields){
      const tr=n('tr'),name=fieldLabel(field.field);
      const candidates=field.candidates?.length?field.candidates:field.right_record_id?[{record_id:field.right_record_id,value:field.right}]:[];
      const merged=n('td');
      for(const candidate of candidates){
        const source=item.members.find(member=>member.ref_id===candidate.record_id);
        const title=source?.record?.article?.title||source?.article?.title||candidate.record_id;
        merged.append(l('p',title+' · '+(candidate.value||'—')));
      }
      const rationale=n('td');
      const reason=field.rationale_key?T(field.rationale_key):'';
      rationale.append(l('p',reason&&reason!==field.rationale_key?reason:field.rationale));
      if(['url','item_type'].includes(field.field))rationale.append(n('small',merge+'not_sent',{class:'badge'}));
      const select=n('select',null,{'aria-label':name});
      select.append(n('option',merge+'column.keeper',{value:'keeper'}));
      for(const candidate of candidates)select.append(l('option',candidate.value||'—',{value:candidate.record_id}));
      select.value=picks[field.field]||(!row.degraded&&field.prefill==='right'?field.right_record_id:'keeper');
      if(!select.value)select.value='keeper';
      select.disabled=busy;
      select.onchange=()=>{picks[field.field]=select.value;};
      const choice=n('td');choice.append(select);
      tr.append(l('th',name,{scope:'row'}),l('td',field.left||'—'),merged,
        field.fallback==='default'?n('td',merge+'fallback_default'):l('td',pickLabel(field,field.prefill)),
        field.source==='rule'||field.source==='ai'?n('td',merge+'source.'+field.source):l('td','—'),rationale,choice);
      body.append(tr);
    }
    box.append(wrap,n('p',merge+'confirm_hint',{class:'hint'}));
    const confirm=b(row.degraded?'Confirm duplicate and retain selected':merge+'apply_all',()=>{
      const changes=Object.create(null);
      for(const field of row.fields){
        if(Object.hasOwn(picks,field.field))changes[field.field]=picks[field.field];
        else if(!row.degraded&&field.prefill){
          changes[field.field]=field.prefill==='left'?'keeper':field.right_record_id;
        }
      }
      return apply({conflict_picks:changes,ai_suggestion_id:String(row.audit_id)});
    },row.degraded?'soft':'primary');
    confirm.disabled=busy;
    box.append(confirm);
    return box;
  }

  function keyDialog(host,redraw){
    const view=dialog(host,ui+'keys'),scope=scopeKey(),keys=window.RFAIProviders;
    const personal='/api/ai/key',shared=taskApi()+'/ai-suggest/shared-key';
    const owner=settings?.can_manage===true;
    let changed=false;
    view.panel.addEventListener('close',()=>{
      if(changed&&scope===scopeKey())redraw();
    },{once:true});
    view.status.textContent=T('Loading…');
    void renderForms().then(()=>{
      if(scope===scopeKey()&&view.panel.open)view.status.textContent='';
    });

    async function renderForms(){
      const [personalMeta,sharedMeta]=await Promise.allSettled([api(personal),owner?api(shared):Promise.resolve(null)]);
      if(scope!==scopeKey()||!view.panel.open)return;
      view.content.replaceChildren();
      renderSection(personal,personalMeta,false);
      if(owner)renderSection(shared,sharedMeta,true);
    }

    function renderSection(endpoint,result,isShared){
      view.content.append(n('h3',isShared?'任务共享钥匙（全员可用）':'个人钥匙（全局）'));
      if(result.status==='rejected'){
        view.content.append(l('p',message(result.reason)));
        return;
      }
      const aggregate=isShared&&!result.value?.llm&&!result.value?.decision;
      if(aggregate)view.content.append(n('p',ui+(result.value?.enabled?'key_present':'key_absent')));
      for(const kind of ['llm','decision']){
        const meta=keys.slot(result.value,kind);
        view.content.append(keyForm(endpoint,meta,isShared,kind,aggregate&&result.value?.enabled));
      }
    }

    function keyForm(endpoint,meta,isShared,kind,aggregateConfigured){
      const section=n('section',null,{class:'rf-import-key-form'});
      section.append(n('h4',kind==='llm'?'通用 LLM 钥匙':'专用判定钥匙'));
      if(aggregateConfigured)section.append(n('p','共享钥匙状态由任务统一显示。',{class:'hint'}));
      else section.append(n('p',ui+(meta?.configured?'key_present':'key_absent')));
      keys.appendMeta(section,meta);
      const actions=n('div',null,{class:'buttons'}),status=n('p',null,{role:'status',class:'hint'});
      const addTest=()=>actions.append(keys.testButton({endpoint,kind,status}));
      if(meta?.source==='env'){
        addTest();
        section.append(actions,status);
        return section;
      }
      const provider=n('input',null,{class:'input',maxlength:'50',translate:'no'});
      const secret=n('input',null,{class:'input',type:'password',autocomplete:'new-password',minlength:'8',maxlength:'4096',translate:'no'});
      const url=n('input',null,{class:'input',maxlength:'500',translate:'no'});
      const model=n('input',null,{class:'input',maxlength:'100',translate:'no'});
      const verify=n('input',null,{type:'checkbox'});
      provider.value=meta?.provider||'';
      url.value=meta?.base_url||'';model.value=meta?.model||'';
      const wizard=keys.mount({kind,provider,baseUrl:url,model,format:meta?.format});
      const add=(label,input)=>{
        input.setAttribute('aria-label',T(label));
        const control=n('label');control.append(n('span',label),input);section.append(control);
      };
      section.append(wizard.element);
      if(kind==='decision')section.append(n('p','ai_keys.kind.decision_hint',{class:'hint'}));
      add('服务商',provider);add('API Key（仅保存在数据目录）',secret);add(ui+'endpoint',url);
      add('模型名（如 gpt-4o-mini）',model);add('保存前验证连通性',verify);
      actions.append(b(isShared?'保存共享钥匙':'保存钥匙',()=>{
        if(!provider.value.trim()||!secret.value.trim()){
          view.status.textContent=T('服务商与 API Key 不能为空。');return;
        }
        return execute(async()=>{
          await api(endpoint,{method:'PUT',body:{provider:provider.value.trim(),key:secret.value,base_url:url.value.trim()||null,
            model:model.value.trim()||null,kind,format:wizard.getFormat(),verify:verify.checked}});
          secret.value='';
          await keysChanged();
          return T('已保存，可测试连接。');
        });
      },'primary'));
      if(meta?.configured||aggregateConfigured)addTest();
      if(meta?.configured||aggregateConfigured)actions.append(b(isShared?'删除共享钥匙':'删除钥匙',()=>{
        if(!confirm(T(isShared?'确认删除任务共享钥匙？':'确认删除个人 AI 钥匙？')))return;
        return execute(async()=>{
          await api(endpoint+'?kind='+kind,{method:'DELETE'});
          await keysChanged();
          return T('已删除。');
        });
      }));
      section.append(actions,status);
      return section;
    }

    async function execute(action){
      if(scope!==scopeKey()||view.panel.getAttribute('aria-busy')==='true')return;
      const controls=[...view.content.querySelectorAll('button,input,select')];
      const disabled=controls.map(control=>control.disabled);
      view.panel.setAttribute('aria-busy','true');view.lock(true);
      for(const control of controls)control.disabled=true;
      view.status.textContent=T('Saving…');
      try{
        const result=await action();
        if(scope===scopeKey()&&view.panel.open)view.status.textContent=result;
      }catch(error){
        if(scope===scopeKey()&&view.panel.open)view.status.textContent=message(error);
      }finally{
        view.panel.removeAttribute('aria-busy');view.lock(false);
        controls.forEach((control,index)=>{control.disabled=disabled[index];});
      }
    }

    async function keysChanged(){
      if(scope!==scopeKey())return;
      changed=true;
      await Promise.all([load(),renderForms()]);
      if(scope===scopeKey())document.dispatchEvent(new Event('reviewflow:ai-keys-changed'));
    }
  }
  function reportDialog(host,batchId){
    const view=dialog(host,'导出 AI 使用报告'),scope=scopeKey();
    for(const format of ['csv','json']){
      view.actions.append(b(format.toUpperCase(),async()=>{
        view.lock(true);
        const buttons=[...view.actions.querySelectorAll('button')];
        for(const button of buttons)button.disabled=true;
        try{
          const params=new URLSearchParams({format,batch_id:batchId});
          const blob=await api(apiPath()+'/audit/export?'+params,{blob:true});
          if(scope!==scopeKey()||!view.panel.open)return;
          const url=URL.createObjectURL(blob);
          n('a',null,{href:url,download:'import-ai-usage.'+format}).click();
          setTimeout(()=>URL.revokeObjectURL(url),1000);
          view.close();
        }catch(error){
          if(scope===scopeKey())view.status.textContent=message(error);
        }finally{
          view.lock(false);
          for(const button of buttons)button.disabled=false;
        }
      }));
    }
  }

  function scorecard(){
    const box=n('details',null,{class:'rf-plain-disclosure'}),body=n('div',null,{role:'status'});
    const scope=scopeKey();
    box.append(n('summary','AI-人工一致率'),body);
    box.addEventListener('toggle',async()=>{
      if(!box.open)return;
      body.textContent=T('Loading…');
      try{
        const data=await api(apiPath()+'/scorecard');
        if(scope!==scopeKey()||!box.isConnected||!box.open)return;
        const byKind=data.by_kind||{};
        const percent=new Intl.NumberFormat(document.documentElement.lang||'en',{maximumFractionDigits:1});
        const rows=[
          ['AI-人工一致率',data.agreement_rate,data.total_compared],
          ['重复组',byKind.duplicate_group?.agreement_rate,byKind.duplicate_group?.total_compared],
          ['元数据差异',byKind.metadata_diff?.agreement_rate,byKind.metadata_diff?.total_compared],
          ['合并冲突字段',byKind.merge_conflict?.field_agreement_rate,byKind.merge_conflict?.fields_compared]
        ];
        body.replaceChildren(...rows.map(([title,rate,count])=>l('p',T(title)+' · '+
          (rate==null?T('暂无已判定样本'):T('{0}% · {1} 个样本',percent.format(rate*100),count)))));
      }catch(error){
        if(scope===scopeKey()&&box.isConnected)body.textContent=message(error);
      }
    });
    return box;
  }

  const memberKeeper=member=>member.ref_type==='active'?{keeper_key:member.ref_id}:{keeper_record_id:member.ref_id};
  const keeperId=keeper=>keeper?.keeper_key||keeper?.keeper_record_id;
  const batchPath=batchId=>taskApi()+'/import-batches/'+encodeURIComponent(batchId);

  function bulkCandidates(batchId,items){
    const rows=[];
    for(const item of items){
      if(item.bulk_eligible!==true||reference(batchId,item,true)?.kind!=='duplicate_group')continue;
      const suggestion=rowSuggestion(batchId,item);
      const currentConflicts=[...conflicts.values()].filter(entry=>entry.row.batch_id===batchId&&
        entry.row.item_id===item.review_id&&entry.fingerprint===JSON.stringify(item.members)&&
        confidenceGrade(entry.row)==='green'&&entry.row.audit_id!=null).map(entry=>entry.row);
      const duplicate=confidenceGrade(suggestion)==='green'&&suggestion.audit_id!=null?suggestion:null;
      if(duplicate||currentConflicts.length)rows.push({item,duplicate,currentConflicts});
    }
    return rows;
  }

  function bulkPreview(host,batchId,rows,refresh,currentKeeper){
    const scope=scopeKey(),path=batchPath(batchId),view=dialog(host,'Confirm bulk review');
    let policyVersion=W.version(),applied=0,steps=0,failed=false;
    const list=n('div',null,{class:'bulk-list'}),entries=[];
    view.content.append(n('p','import.review.bulk_authority'),list);
    const submit=b('Confirm bulk review',async()=>{
      if(scope!==scopeKey()||failed)return;
      const chosen=entries.filter(entry=>entry.check.checked&&!entry.done);
      if(!chosen.length||chosen.some(entry=>!entry.action))return;
      view.lock(true);submit.disabled=true;
      const controls=[...view.content.querySelectorAll('input,select')];
      for(const control of controls)control.disabled=true;
      view.status.textContent=T('Saving…');
      try{
        for(const chunk of bulkChunks(chosen)){
          const result=await api(path+'/bulk-review',{method:'POST',body:{
            expected_policy_version:policyVersion,actions:chunk.map(entry=>entry.action)
          }});
          if(scope!==scopeKey())return;
          policyVersion=result.policy_version;
          W.acceptImportVersion(policyVersion,W.scope());
          applied+=chunk.length;steps++;
          appliedByBatch.set(batchId,{applied,steps,policyVersion});
          for(const entry of chunk){entry.done=true;entry.check.checked=false;}
        }
        view.close();
      }catch(error){
        if(scope!==scopeKey())return;
        const detail=error.messageKey==='import_merge.bulk_ineligible'&&error.params?.reason?
          ' '+T('import_merge.ineligible.'+error.params.reason):'';
        failed=error.status===409;
        view.status.textContent=[message(error)+detail,
          applied?T('部分操作已完成：已应用 {0} 条。',applied):'',
          failed?T('请刷新批次后重新预览。'):''].filter(Boolean).join(' ');
      }finally{
        view.lock(false);
        for(const entry of entries){
          entry.check.disabled=entry.select.disabled=entry.done;
        }
        updateSubmit();
      }
    },'primary');
    function updateSubmit(){
      const chosen=entries.filter(entry=>entry.check.checked&&!entry.done);
      submit.disabled=failed||!chosen.length||chosen.some(entry=>!entry.action);
    }
    for(const {item,duplicate,currentConflicts} of rows){
      const active=item.members.filter(member=>member.ref_type==='active');
      const members=active.length?active:item.members;
      const title=member=>member?.article?.title||member?.record?.article?.title||member?.ref_id||T('Untitled record');
      const row=n('div'),check=n('input',null,{type:'checkbox',checked:true});
      const label=n('label');label.append(check,l('strong',title(item.members[0])));
      const select=n('select',null,{'aria-label':T('Retain selected record')});
      for(const member of members)select.append(l('option',title(member),{value:member.ref_id}));
      const defaultId=requestedKeepers.get(duplicate)??keeperId(currentConflicts[0]?.keeper)??currentKeeper(item);
      if(members.some(member=>member.ref_id===defaultId))select.value=defaultId;
      const choice=n('label');choice.append(n('span','Retain selected record'),select);
      const details=n('div'),entry={check,select,done:false,action:null};
      function update(){
        const member=members.find(candidate=>candidate.ref_id===select.value);
        const conflict=currentConflicts.find(candidate=>keeperId(candidate.keeper)===select.value);
        const suggestion=conflict||duplicate;
        details.replaceChildren();
        entry.action=member&&suggestion?{
          kind:'candidate',id:item.review_id,...memberKeeper(member),
          ai_suggestion_id:String(suggestion.audit_id),...(conflict?{conflict_picks:conflict.field_picks}:{})
        }:null;
        if(suggestion)details.append(l('p',T(lang+'verdict.'+suggestion.verdict)+' · '+suggestion.confidence.toFixed(2)));
        if(conflict&&Object.keys(conflict.field_picks||{}).length){
          const summary=n('details'),fields=n('ul');
          for(const [field,pick] of Object.entries(conflict.field_picks)){
            const info=conflict.fields?.find(entry=>entry.field===field);
            const value=pick==='keeper'?info?.left:info?.candidates?.find(entry=>entry.record_id===pick)?.value??info?.right;
            fields.append(l('li',fieldLabel(field)+' · '+(value||'—')));
          }
          summary.append(n('summary','合并冲突字段'),fields);details.append(summary);
        }
        updateSubmit();
      }
      check.onchange=updateSubmit;select.onchange=update;
      entries.push(entry);update();
      row.append(label,choice,details);list.append(row);
    }
    view.actions.append(submit);
    view.panel.addEventListener('close',()=>{if(scope===scopeKey()&&(applied||failed))void refresh();},{once:true});
  }

  function toolbar({host,batchId,visible,busy,redraw,refresh,candidates=visible,canManage=false,undoAvailable=false,
    currentKeeper=item=>keeperId(item.suggested_keeper)}){
    if(authDenied){
      if(!host.querySelector('.rf-import-ai-denied')){
        const fallback=n('div',null,{class:'rf-import-ai-denied'});
        fallback.append(l('p',settingsError),b('All batches',()=>WorkflowImports.list()));
        host.append(fallback);
      }
      return;
    }
    const tools=n('div',null,{class:'rf-import-ai-toolbar'});
    if(settingsError)tools.append(l('p',settingsError,{role:'status'}));
    if(settings?.can_manage)tools.append(b(ui+'settings',()=>settingsDialog(host,redraw)));
    if(settings)tools.append(b(ui+'keys',()=>keyDialog(host,redraw)));
    const hasPending=visible.some(item=>reference(batchId,item,true));
    if(available()&&hasPending){
      const choose=b(ui+'entry',()=>selectionDialog(host,batchId,visible,false,redraw,refresh,currentKeeper,candidates));
      const page=b(ui+'this_page',()=>selectionDialog(host,batchId,visible,true,redraw,refresh,currentKeeper,candidates));
      choose.title=page.title=disclosure().join('\n');
      choose.disabled=page.disabled=busy||submitting;
      tools.append(choose,page);
    }
    if(settings)tools.append(b('导出 AI 使用报告',()=>reportDialog(host,batchId)),scorecard());
    const eligible=canManage?bulkCandidates(batchId,candidates):[];
    if(eligible.length){
      const apply=b('应用 {0} 条高置信建议',()=>bulkPreview(host,batchId,eligible,refresh,currentKeeper));
      apply.textContent=T('应用 {0} 条高置信建议',eligible.length);
      apply.disabled=busy||submitting;
      tools.append(apply);
    }
    const latest=appliedByBatch.get(batchId);
    if(latest&&undoAvailable&&latest.policyVersion===W.version()){
      const banner=n('div',null,{role:'status'});
      const undo=b('已应用 {0} 条 · 撤销',async()=>{
        undo.disabled=true;
        const scope=scopeKey();
        try{
          const result=await api(batchPath(batchId)+'/undo',{method:'POST',body:{expected_policy_version:latest.policyVersion}});
          if(scope!==scopeKey())return;
          W.acceptImportVersion(result.policy_version,W.scope());
          appliedByBatch.delete(batchId);
          void refresh();
        }catch(error){
          if(scope!==scopeKey())return;
          toast(message(error),'warn');
          if(error.status===409)void refresh();else undo.disabled=false;
        }
      });
      undo.disabled=busy;
      undo.textContent=T('已应用 {0} 条 · 撤销',latest.applied);
      banner.append(undo);
      if(latest.steps>1)banner.append(n('p','每次只能撤销最近一步；更早的步骤无法通过此操作撤销。',{class:'hint'}));
      tools.append(banner);
    }
    if(counts?.batchId===batchId&&counts.values)tools.append(l('p',T(ui+'counts',counts.values),{role:'status'}));
    const recent=[...audits.values()].filter(row=>row.batch_id===batchId).slice(-20).reverse();
    if(recent.length){
      const links=n('div',null,{class:'buttons'});
      for(const row of recent){
        const link=b(ui+'audit',()=>openAudit(host,row.audit_id));
        link.append(l('span',' · #'+row.audit_id));
        links.append(link);
      }
      tools.append(el('details',{class:'rf-plain-disclosure rf-import-ai-audit'},n('summary',ui+'audit'),links));
    }
    if(tools.childNodes.length)host.append(tools);
  }

  window.ImportReviewAI={
    load,toolbar,badge,suggestion:rowSuggestion,summary,conflict,
    reset(){epoch++;settings=null;settingsError=null;loading=null;submitting=false;counts=null;authDenied=false;suggestions.clear();audits.clear();selected.clear();conflicts.clear();appliedByBatch.clear();}
  };
})();
