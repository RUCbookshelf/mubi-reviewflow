/* Suggestions and key settings for the import review workspace. */
(function () {
  'use strict';
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
  const verdicts=new Set(['same','different','unsure']);
  let settings=null,settingsError=null,loading=null,epoch=0,submitting=false,counts=null,authDenied=false;
  const suggestions=new Map(),audits=new Map(),selected=new Map(),conflicts=new Map();
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
    const panel=n('dialog',null,{'aria-label':T(title)});
    const dismiss=b('Close',()=>panel.close());
    const content=n('div'),status=n('p',null,{role:'status',class:'hint'}),actions=n('div',null,{class:'buttons'});
    actions.append(dismiss);
    panel.append(n('h2',title),content,status,actions);
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

  function selectionDialog(host,batchId,items,all,redraw,refresh){
    const refs=items.map(item=>[item,reference(batchId,item,true)]).filter(([,ref])=>ref);
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
    view.content.append(indicator,list);
    const selectedRefs=()=>[...selected.values()].filter(ref=>ref.batch_id===batchId);
    const generate=b(ui+'generate',async()=>{
      const refs=selectedRefs();
      if(refs.length===0||refs.length>maxItems())return;
      await request(view,generate,refs,result=>{
        for(const ref of refs)selected.delete(identity(ref));
        counts={batchId,values:result.counts};
      },redraw,refresh);
    },'primary');
    generate.title=disclosure().join('\n');
    view.actions.append(generate);
    function update(){
      const size=selectedRefs().length;
      indicator.textContent=T(ui+'selected',{n:size,max:maxItems()});
      view.status.textContent=size>maxItems()?T(lang+'too_many_items',{max_items:maxItems()}):'';
      generate.disabled=!size||size>maxItems()||submitting;
    }
    update();
  }

  async function request(view,generate,items,apply,redraw,refresh){
    if(submitting||!available())return;
    const scope=scopeKey();
    submitting=true;generate.disabled=true;view.lock(true);
    const inputs=[...view.panel.querySelectorAll('input,select')];
    for(const input of inputs)input.disabled=true;
    view.status.textContent=T('AI 建议生成中…');
    try{
      const result=await api(apiPath(),{method:'POST',body:{items}});
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
    const row=rowSuggestion(batchId,item);
    return verdicts.has(row?.verdict)?n('span',lang+'verdict.'+row.verdict,{class:'badge amber'}):null;
  }

  function summary(row,link=true){
    const section=n('section',null,{class:'rf-import-ai-suggestion'});
    if(verdicts.has(row.verdict))section.append(n('strong',lang+'verdict.'+row.verdict));
    if(typeof row.confidence==='number')section.append(l('span',T(lang+'confidence',{value:row.confidence.toFixed(2)})));
    if(row.rationale_zh)section.append(l('p',row.rationale_zh,{class:'rf-import-ai-rationale',title:row.rationale_zh}));
    const warnings=new Set((row.warnings||[]).map(token=>Object.hasOwn(warningKeys,token)?warningKeys[token]:'generic'));
    for(const warning of warnings)section.append(n('p',lang+'degraded.'+warning,{class:'hint'}));
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
    const view=dialog(host,ui+'keys'),scope=scopeKey();
    const personal='/api/ai/key',shared=taskApi()+'/ai-suggest/shared-key';
    const owner=settings?.can_manage===true;
    view.status.textContent=T('Loading…');
    Promise.allSettled([api(personal),owner?api(shared):Promise.resolve(null)]).then(([personalMeta,sharedMeta])=>{
      if(scope!==scopeKey()||!view.panel.open)return;
      view.status.textContent='';
      if(personalMeta.status==='fulfilled')view.content.append(keyForm(personal,personalMeta.value,false));
      else view.content.append(l('p',message(personalMeta.reason)));
      if(owner){
        if(sharedMeta.status==='fulfilled')view.content.append(keyForm(shared,sharedMeta.value,true));
        else view.content.append(l('p',message(sharedMeta.reason)));
      }
    });

    function keyForm(endpoint,meta,isShared){
      const section=n('section',null,{class:'rf-import-key-form'});
      const title=isShared?'任务共享钥匙（全员可用）':'个人钥匙（全局）';
      const configured=isShared?meta?.enabled:meta?.configured;
      section.append(n('h3',title),n('p',ui+(configured?'key_present':'key_absent')));
      const kind=n('select');
      for(const option of ['llm','decision'])kind.append(n('option','ai_keys.kind.'+option,{value:option}));
      const knownKind=isShared?(settings?.key_source==='shared'?settings.key_kind:null):meta?.kind;
      kind.value=knownKind==='decision'?'decision':'llm';
      if(configured)section.append(n('p','ai_keys.kind.'+kind.value,{hidden:isShared&&!knownKind}));
      const test=()=>execute(async()=>{
        const result=await api(personal+'/test',{method:'POST'});
        return T('连接正常 · 延迟 {0} ms',String(result.latency_ms??'—'));
      });
      if(!isShared&&meta?.source==='env'){
        section.append(n('p','由管理员通过环境变量配置，仅可在服务器端更换或移除。'),b('测试连接',test));
        return section;
      }
      const provider=n('input',null,{class:'input',maxlength:'50',translate:'no'});
      provider.value=(isShared?meta?.provider_name:meta?.provider)||'';
      const secret=n('input',null,{class:'input',type:'password',autocomplete:'new-password',minlength:'8',maxlength:'4096',translate:'no'});
      const url=n('input',null,{class:'input',maxlength:'500',translate:'no'});
      const model=n('input',null,{class:'input',maxlength:'100',translate:'no'});
      url.value=isShared?'':meta?.base_url||'';model.value=isShared?'':meta?.model||'';
      const verify=n('input',null,{type:'checkbox'}),hint=n('p','ai_keys.kind.decision_hint',{class:'hint'});
      const sync=()=>{hint.hidden=kind.value!=='decision';url.placeholder=kind.value==='decision'?'https://api.typesafe.ai':'https://api.openai.com/v1';};
      kind.onchange=()=>{url.value='';model.value='';sync();};sync();
      const add=(label,input)=>{
        input.setAttribute('aria-label',T(label));
        const control=n('label');control.append(n('span',label),input);section.append(control);
      };
      add(ui+'model_kind',kind);section.append(hint);
      add('服务商',provider);add('API Key（仅保存在数据目录）',secret);add(ui+'endpoint',url);
      add('模型名（如 gpt-4o-mini）',model);add('保存前验证连通性',verify);
      const save=b(isShared?'保存共享钥匙':'保存钥匙',()=>{
        if(!provider.value.trim()||!secret.value.trim()){
          view.status.textContent=T('服务商与 API Key 不能为空。');return;
        }
        return execute(async()=>{
          await api(endpoint,{method:'PUT',body:{provider:provider.value.trim(),key:secret.value,base_url:url.value.trim()||null,model:model.value.trim()||null,kind:kind.value,verify:verify.checked}});
          secret.value='';
          await keysChanged();
          return T('已保存。');
        });
      },'primary');
      const actions=n('div',null,{class:'buttons'});
      actions.append(save);
      if(configured&&!isShared)actions.append(b('测试连接',test));
      if(configured)actions.append(b(isShared?'删除共享钥匙':'删除钥匙',()=>{
        if(!confirm(T(isShared?'确认删除任务共享钥匙？':'确认删除个人 AI 钥匙？')))return;
        return execute(async()=>{
          await api(endpoint,{method:'DELETE'});
          await keysChanged();
          return T('已删除。');
        });
      }));
      section.append(actions);
      return section;
    }

    async function execute(action){
      if(scope!==scopeKey()||view.panel.getAttribute('aria-busy')==='true')return;
      const buttons=[...view.content.querySelectorAll('button')];
      view.panel.setAttribute('aria-busy','true');view.lock(true);
      for(const button of buttons)button.disabled=true;
      view.status.textContent=T('Saving…');
      try{
        const result=await action();
        if(scope===scopeKey()&&view.panel.open)view.status.textContent=result;
      }catch(error){
        if(scope===scopeKey()&&view.panel.open)view.status.textContent=message(error);
      }finally{
        view.panel.removeAttribute('aria-busy');view.lock(false);
        for(const button of buttons)button.disabled=false;
      }
    }
    async function keysChanged(){
      if(scope!==scopeKey())return;
      await load();
      if(scope!==scopeKey())return;
      document.dispatchEvent(new Event('reviewflow:ai-keys-changed'));
      view.close();redraw();
    }
  }

  function toolbar({host,batchId,visible,busy,redraw,refresh}){
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
      const choose=b(ui+'entry',()=>selectionDialog(host,batchId,visible,false,redraw,refresh));
      const page=b(ui+'this_page',()=>selectionDialog(host,batchId,visible,true,redraw,refresh));
      choose.title=page.title=disclosure().join('\n');
      choose.disabled=page.disabled=busy||submitting;
      tools.append(choose,page);
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
      tools.append(U.details(ui+'audit',links));
    }
    if(tools.childNodes.length)host.append(tools);
  }

  window.ImportReviewAI={
    load,toolbar,badge,suggestion:rowSuggestion,summary,conflict,
    reset(){epoch++;settings=null;settingsError=null;loading=null;submitting=false;counts=null;authDenied=false;suggestions.clear();audits.clear();selected.clear();conflicts.clear();}
  };
})();
