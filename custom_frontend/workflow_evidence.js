/* Actor-private evidence editor. Canonical submission access never sends local values. */
(function () {
  'use strict';
  const W=WorkflowControls,E=window.WorkflowEvidence={};
  let mode='personal', discovery=null, current=null, ticket=0, baseline='', pending=0, saving=false, readOnlyKey=null, context='', uploadFailed=false, uploadStatus=false;
  const images=new Map(), urls=new Set();
  const node=(tag,text,attrs={})=>el(tag,{...attrs,...(text==null?{}:{text:T(text)})});
  const btn=(text,fn)=>node('button',text,{type:'button',class:'btn b-out sm',onclick:fn});
  const literal=(tag,text)=>el(tag,{text:String(text??''),translate:'no'});
  const identity=()=>W.scope()+'|'+mode+'|'+(S.cbQueue[S.cbIdx]?.zotero_key||'');
  const signature=()=>JSON.stringify({values:$$('#cbForms .cb-v').map(c=>[c.dataset.dim,c.value,c.type==='checkbox'?c.checked:null]),notes:$$('#cbForms .wf-note').map(c=>[c.dataset.dim,c.value]),images:[...images].map(([dim,list])=>[dim,list.map(i=>[i.id||null,i.upload_id||null])])});
  E.dirty=()=>!!current&&baseline!==signature();
  E.uploading=()=>pending>0||saving;
  E.discard=()=>{baseline=signature();};
  E.context=()=>mode==='canonical'?'coding_submission_evidence':'personal';
  E.readOnly=key=>{readOnlyKey=key;};
  function clear(){for(const url of urls)URL.revokeObjectURL(url);urls.clear();images.clear();current=null;baseline='';uploadFailed=false;uploadStatus=false;}
  E.discovery=value=>{
    discovery=value;
    const show=!!value?.available && W.settings()?.task_kind==='team';
    let host=$('#wf-coding-entry');
    if(!host){host=node('div',null,{id:'wf-coding-entry',class:'row'});document.querySelector('section[data-p="bench"] .wf-queue-tools').after(host);}
    if(host.dataset.available===String(show))return;
    host.dataset.available=String(show);host.replaceChildren();
    if(show)host.append(btn('Personal coding',()=>switchMode('personal')),btn('Shared submission evidence',()=>switchMode('canonical')),node('span','Evidence-only editing uses your private notes and images. It does not submit shared coding values.',{class:'hint'}));
  };
  async function switchMode(next){if(!await W.guard())return;mode=next;await loadBench();}
  E.queue=async()=>{
    readOnlyKey=null;
    const scopeTools=document.querySelector('section[data-p="bench"] .wf-queue-tools');scopeTools.querySelector('select').disabled=mode==='canonical';
    if(mode!=='canonical')return W.queue('coding');
    await W.refresh('entry',false);
    if(!discovery?.available){mode='personal';scopeTools.querySelector('select').disabled=false;return W.queue('coding');}
    scopeTools.querySelector('select').replaceChildren(node('option','All permitted sources',{value:''}));
    const result=await api(discovery.queue_url);
    scopeTools.querySelector('.wf-scope-progress').textContent=T('Canonical evidence targets: {0}',result.total);
    scopeTools.querySelector('.wf-readonly').textContent=T('All canonical targets permitted by the owner coding policy.');
    return result;
  };
  E.revalidate=async()=>{
    if(!current)return;
    const scope=identity(),key=S.cbQueue[S.cbIdx]?.zotero_key;
    const item=await api(mode==='canonical'?discovery.read_url_template.replace('{zotero_key}',encodeURIComponent(key)):taskApi()+'/work-items/coding/'+encodeURIComponent(key));
    if(scope!==identity())return;
    current={...current,policy_version:item.policy_version,writable:item.writable,reason:item.reason,mapping_issues:item.mapping_issues};
    for(const section of $$('#cbForms .wf-evidence')){const required=!!item.evidence?.[section.dataset.dim]?.required;section.dataset.required=String(required);section.querySelector('summary').textContent=T(required?'Notes & evidence · Required':'Notes & evidence');}
    updateGaps();updateBusy();
  };
  async function imageURL(image,scope,t){
    try{const blob=await api(image.url||image.preview_url,{blob:true});if(scope!==identity()||t!==ticket)return null;const url=URL.createObjectURL(blob);urls.add(url);return url;}catch(error){if(scope===identity()&&t===ticket)toast(T('Image preview unavailable. Retry by reopening this article.')+' '+W.errorText(error),'warn');return null;}
  }
  function updateGaps(){
    if(!current)return;
    let missing=0;
    for(const section of $$('#cbForms .wf-evidence')){
      const dim=section.dataset.dim,required=section.dataset.required==='true';
      const gap=required&&!section.querySelector('textarea').value.trim()&&!(images.get(dim)||[]).length;
      section.querySelector('.wf-evidence-gap').textContent=gap?T('Required evidence missing — you can save a draft.'):'';
      if(gap)missing++;
    }
    const mapping=current.mapping_issues?.length||0;
    if(pending){$('#cbStatus').textContent=T('Uploading evidence… Wait before saving or leaving.');return;}
    if(uploadFailed){$('#cbStatus').textContent=T('Image upload failed. Your notes and attached images are retained. Choose the image again to retry.');return;}
    $('#cbStatus').textContent=missing||mapping?T('Evidence gaps: {0}; mapping issues: {1}',missing,mapping):T('Editing');
  }
  function drawImages(dim,host,scope,t,writable){
    host.replaceChildren();
    for(const image of images.get(dim)||[]){
      const figure=node('figure'),img=node('img',null,{alt:T(image.upload_id?'Pending evidence image':'Saved evidence image')});
      figure.append(img,node('figcaption',image.upload_id?'Pending attachment':'Saved evidence image'));
      if(writable)figure.append(btn('Remove image',()=>{images.set(dim,(images.get(dim)||[]).filter(x=>x!==image));drawImages(dim,host,scope,t,writable);updateGaps();}));
      host.append(figure);
      if(image.objectURL)img.src=image.objectURL;
      else imageURL(image,scope,t).then(url=>{if(url&&img.isConnected){image.objectURL=url;img.src=url;}});
    }
  }
  async function upload(files,dim,host,writable){
    if(!writable||!current||saving)return;
    const scope=identity(),t=ticket,key=current.article.zotero_key||current.article.key,base=taskApi();
    if(!pending)uploadFailed=false;
    for(const file of files){
      if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>10*1024*1024){toast(T('Use PNG, JPEG or WebP images up to 10 MB.'),'warn');continue;}
      pending++;updateBusy();
      try{
        const form=new FormData();form.append('file',file);form.append('zotero_key',key);form.append('dim_id',dim);form.append('expected_policy_version',current.policy_version);
        const result=await api(mode==='canonical'?discovery.upload_url:base+'/coding/evidence/uploads',{method:'POST',form});
        if(scope!==identity()||t!==ticket)continue;
        const list=images.get(dim)||[];if(!list.some(image=>image.upload_id===result.upload_id))list.push(result);images.set(dim,list);drawImages(dim,host,scope,t,writable);updateGaps();
      }catch(error){if(scope===identity()&&t===ticket){uploadFailed=true;W.error(error);}}finally{pending--;if(scope===identity()&&t===ticket)updateBusy();}
    }
  }
  function updateBusy(){
    const undoBusy=!!window.WorkspaceUndo?.isBusy()||W.settings()?.stages?.coding?.mode==='paused';
    $('#cbSave').disabled=!current?.writable||current?.policy_version!==W.version()||pending>0||saving||undoBusy||!S.codingFormReady;
    for(const control of $$('#cbForms input,#cbForms textarea,#cbForms select,#cbForms button'))control.disabled=!current?.writable||control.dataset.wfReadonly==='true'||saving||undoBusy;
    if(pending){uploadStatus=true;$('#cbStatus').textContent=T('Uploading evidence… Wait before saving or leaving.');}
    else if(uploadStatus){uploadStatus=false;updateGaps();}
  }
  E.syncBusy=updateBusy;
  E.render=async()=>{
    if(!W.renderAllowed())return;
    const t=++ticket,scope=identity(),version=S.taskVersion,screener=S.screener,cur=S.cbQueue[S.cbIdx];
    clear();S.codingFormReady=false;
    if($('#rfExtractPanel'))$('#rfExtractPanel').hidden=mode==='canonical';
    $('#cbLabel').textContent=T('第 {0} / {1} 篇',S.cbQueue.length?S.cbIdx+1:0,S.cbQueue.length);
    $('#cbTitle').textContent=cur?.title||cur?.zotero_key||'—';
    const forms=$('#cbForms');forms.replaceChildren();$('#cbSave').disabled=true;
    if(!cur){forms.append(node('p',mode==='canonical'?'No canonical evidence targets are available in this scope.':'编码队列为空（复筛/初筛纳入后自动进入队列）。'));return;}
    openBenchPdf();$('#cbStatus').textContent=T('Loading current saved work…');
    try{
      const item=mode==='canonical'?await api(discovery.read_url_template.replace('{zotero_key}',encodeURIComponent(cur.zotero_key))):await W.item('coding',cur.zotero_key);
      if(!item||scope!==identity()||t!==ticket||version!==S.taskVersion||screener!==S.screener||!W.renderAllowed())return;
      if(readOnlyKey===cur.zotero_key)item.writable=false;
      current=item;const dims=mode==='canonical'?item.dimensions:S.dims;
      if(!dims.length){forms.append(node('p','方案为空 —— 请先到「编码方案」添加维度。'));return;}
      if(!item.writable)forms.append(node('p','Read-only: current policy or source scope does not allow saving.',{class:'wf-readonly'}),node('p',W.reason(item.reason)));
      for(const d of dims){
        const dim=String(d.id),saved=item.evidence?.[dim]||{},value=item.saved?.[dim]||'',writable=item.writable&&(mode!=='canonical'||d.writable);
        const card=node('div',null,{class:'card','data-field-id':d.field_id||'',id:'wf-field-'+dim,style:'margin-bottom:10px'});card.append(literal('b',d.name),node('span',d.multi_select?'多选':d.dtype==='text'?'填空':'单选',{class:'chip'}));
        if(mode!=='canonical'){
          const row=node('div',null,{class:'row'});
          if(d.dtype==='choice'&&d.multi_select){for(const option of d.options){const c=el('input',{type:'checkbox',class:'cb-v','data-dim':dim,value:option});c.checked=String(value).split('|').includes(option);row.append(el('label',{},c,literal('span',option)));}}
          else if(d.dtype==='choice'){const c=el('select',{class:'input cb-v','data-dim':dim,translate:'no'});c.append(el('option',{value:'',text:'—'}));for(const option of d.options)c.append(el('option',{value:option,text:option,translate:'no'}));c.value=value;row.append(c);}
          else row.append(el('input',{class:'input cb-v','data-dim':dim,value,translate:'no'}));card.append(row);
        }
        const required=!!(saved.required||d.required),details=node('details',null,{class:'wf-evidence','data-dim':dim,'data-required':String(required)});
        details.open=!!saved.text||!!saved.images?.length||required||localStorage.getItem('rf_evidence_display')==='all';
        details.append(node('summary',required?'Notes & evidence · Required':'Notes & evidence'));
        const text=el('textarea',{class:'input wf-note','data-dim':dim,translate:'no','data-wf-aria':'Notes & evidence','aria-label':T('Notes & evidence')});text.value=saved.text||'';text.disabled=!writable;text.dataset.wfReadonly=String(!writable);text.addEventListener('input',updateGaps);
        details.append(text,node('p',null,{class:'wf-evidence-gap',role:'status'}));
        const host=node('div',null,{class:'wf-evidence-images'});images.set(dim,[...(saved.images||[])]);details.append(host);drawImages(dim,host,scope,t,writable);
        if(writable){const file=el('input',{type:'file',accept:'image/png,image/jpeg,image/webp',multiple:'','aria-label':T('Upload evidence images')});file.addEventListener('change',()=>{upload([...file.files],dim,host,writable);file.value='';});details.append(file);text.addEventListener('paste',event=>{const files=[...event.clipboardData?.files||[]].filter(f=>f.type.startsWith('image/'));if(files.length){event.preventDefault();upload(files,dim,host,writable);}});}
        card.append(details);forms.append(card);
        for(const c of card.querySelectorAll('input,select,button')){c.disabled=!writable;c.dataset.wfReadonly=String(!writable);}
      }
      if(item.mapping_issues?.length)forms.append(node('p','Evidence field mapping needs owner review. Existing values are preserved.',{class:'wf-evidence-gap'}));
      S.codingFormReady=true;baseline=signature();updateGaps();updateBusy();
      document.dispatchEvent(new Event('reviewflow:coding-form-loaded'));
    }catch(error){if(t!==ticket||scope!==identity()||!W.renderAllowed())return;forms.append(node('p',W.errorText(error)),btn('Retry / refresh',()=>E.render()));$('#cbStatus').textContent=T('Load failed');}
  };
  E.save=async()=>{
    if(W.refreshing()||!current?.writable||current?.policy_version!==W.version()||pending||saving||window.WorkspaceUndo?.isBusy()||!S.codingFormReady)return false;
    const scope=identity(),t=ticket,key=current.article.zotero_key||current.article.key;
    const notes={},evidence={},values={};
    for(const text of $$('#cbForms .wf-note'))if(text.dataset.wfReadonly!=='true')notes[text.dataset.dim]=text.value;
    for(const [dim,list] of images){if(!Object.hasOwn(notes,dim))continue;evidence[dim]={keep_image_ids:list.filter(i=>i.id!=null).map(i=>i.id),upload_ids:list.filter(i=>i.upload_id).map(i=>i.upload_id)};}
    for(const control of $$('#cbForms .cb-v')){const dim=control.dataset.dim;if(control.type==='checkbox'){values[dim]??=[];if(control.checked)values[dim].push(control.value);}else values[dim]=[control.value];}
    const body={zotero_key:key,notes,evidence,expected_policy_version:current.policy_version,draft:false};
    if(mode!=='canonical')body.values=Object.fromEntries(Object.entries(values).map(([dim,v])=>[dim,v.join('|')]));
    if(mode==='canonical'&&!Object.keys(notes).length){toast(T('No mapped evidence fields are writable.'),'warn');return false;}
    saving=true;WorkspaceUndo?.beginSave();updateBusy();
    try{
      const result=await api(mode==='canonical'?discovery.save_url:taskApi()+'/coding/values-batch'+scrQ(),{method:'POST',body});
      if(scope!==identity()||t!==ticket)return false;
      // Rehydrate server association IDs only after the one form transaction succeeds.
      for(const [dim,saved] of Object.entries(result.evidence||{}))images.set(dim,[...(saved.images||[])]);
      baseline=signature();if(result.progress)applyCodingProgress(result.progress);
      document.dispatchEvent(new CustomEvent('reviewflow:coding-form-saved',{detail:key}));
      await E.render();
      if(mode==='personal')await W.updateProgress('coding');
      $('#cbStatus').textContent=T(mode==='canonical'?'Evidence saved':result.complete?'Saved — complete':'Saved as draft — required values or evidence are missing');
      toast($('#cbStatus').textContent,'inc');return true;
    }catch(error){if(scope===identity())W.error(error);return false;}finally{saving=false;WorkspaceUndo?.endSave('coding');updateBusy();}
  };
  E.setContext=access=>{mode=access==='coding_submission_evidence'?'canonical':'personal';};
  document.addEventListener('reviewflow:task-context-changed',()=>{if(context===W.scope())return;context=W.scope();ticket++;clear();mode='personal';readOnlyKey=null;E.discovery(null);});
  document.addEventListener('reviewflow:workspace-changed',event=>{if(event.detail!=='bench'&&!E.dirty()){ticket++;clear();}});
  $('#cbForms').addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.tagName==='INPUT')event.stopPropagation();});
})();
