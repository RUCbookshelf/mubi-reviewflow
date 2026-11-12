/* Actor-private evidence editor. Canonical submission access never sends local values. */
(function () {
  'use strict';
  const W=WorkflowControls,E=window.WorkflowEvidence={};
  let mode='personal', discovery=null, current=null, ticket=0, baseline='', pending=0, saving=false, readOnlyKey=null, context='', uploadFailed=false, uploadStatus=false;
  let loadFailure=null;
  const images=new Map(), urls=new Set();
  /* r9 L3（b11·F3-23 的前端面）：服务端语义是「keep_image_ids 列出即精确集合，
     不带该键即全部保留」。这里记住每个维度服务端已确认的图片 id —— 只有用户
     真的删过图才下发列表；没删时省掉该键，单备注累积超过 100 张也不会撞
     接口的 max_length=100（旧实现每次都把全部 id 塞进去）。 */
  const savedIds=new Map();
  const remember=(dim,list)=>savedIds.set(dim,new Set((list||[]).filter(i=>i.id!=null).map(i=>i.id)));
  const keepFor=(dim,list)=>{
    const kept=list.filter(i=>i.id!=null).map(i=>i.id),known=savedIds.get(dim)||new Set();
    /* 只有"服务端已确认的某张图不在当前列表里"才算剔除；否则返回 null 表示
       不带该键（= 全部保留）。 */
    return [...known].some(id=>!kept.includes(id))?kept:null;};
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
  function clear(){loadFailure=null;for(const url of urls)URL.revokeObjectURL(url);urls.clear();images.clear();savedIds.clear();current=null;baseline='';uploadFailed=false;uploadStatus=false;}
  E.discovery=value=>{
    discovery=value;
    const show=!!value?.available && W.settings()?.task_kind==='team';
    let host=$('#wf-coding-entry');
    if(!host){host=node('div',null,{id:'wf-coding-entry',class:'row'});document.querySelector('section[data-p="bench"] .wf-queue-tools').after(host);}
    if(host.dataset.available===String(show)){for(const entry of host.querySelectorAll('[data-evidence-mode]')){const selected=entry.dataset.evidenceMode===mode;entry.setAttribute('aria-pressed',String(selected));entry.classList.toggle('b-inc',selected);}return;}
    host.dataset.available=String(show);host.replaceChildren();
    if(show){
      for(const [value,label] of [['personal','Personal coding'],['canonical','Shared submission evidence']]){
        const entry=btn(label,()=>switchMode(value));entry.dataset.evidenceMode=value;
        entry.setAttribute('aria-pressed',String(mode===value));entry.classList.toggle('b-inc',mode===value);host.append(entry);
      }
      host.append(node('span','Evidence-only editing uses your private notes and images. It does not submit shared coding values.',{class:'hint'}));
    }
  };
  async function switchMode(next){if(next===mode||!await W.guard())return;mode=next;const entry=$('#wf-coding-entry');if(entry)delete entry.dataset.available;E.discovery(discovery);await loadBench();}
  E.queue=async()=>{
    readOnlyKey=null;
    if(mode!=='canonical')return W.queue('coding');
    const requested=W.scope();
    await W.refresh('entry',false);
    if(requested!==W.scope())throw Error(T('The task changed. Reload the queue.'));
    if(!discovery?.available){mode='personal';return W.queue('coding');}
    return W.queue('coding',{url:discovery.queue_url,evidenceOnly:true});
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
    window.AppraisalWorkspace?.setArticle(cur,E.context());
    clear();S.codingFormReady=false;
    if($('#rfExtractPanel'))$('#rfExtractPanel').hidden=mode==='canonical';
    $('#cbSave').textContent=T(mode==='canonical'?'polish.save_shared_evidence':'polish.save_article');
    $('#cbSave').setAttribute('aria-label',T(mode==='canonical'?'polish.save_shared_evidence':'polish.save_article'));
    $('#cbLabel').textContent=T('第 {0} / {1} 篇',S.cbQueue.length?S.cbIdx+1:0,S.cbQueue.length);
    $('#cbTitle').textContent=cur?.title||cur?.zotero_key||'—';
    const forms=$('#cbForms');forms.replaceChildren();$('#cbSave').disabled=true;
    if(!cur){openBenchPdf();forms.append(node('p',mode==='canonical'?'No canonical evidence targets are available in this scope.':'编码队列为空（复筛/初筛纳入后自动进入队列）。'),btn('polish.open_screening',()=>go('fulltext')),btn('polish.open_scheme',()=>go('scheme')));return;}
    openBenchPdf();$('#cbStatus').textContent=T('Loading current saved work…');
    try{
      const item=mode==='canonical'?await api(discovery.read_url_template.replace('{zotero_key}',encodeURIComponent(cur.zotero_key))):await W.item('coding',cur.zotero_key);
      if(!item||scope!==identity()||t!==ticket||version!==S.taskVersion||screener!==S.screener||!W.renderAllowed())return;
      if(readOnlyKey===cur.zotero_key)item.writable=false;
      current=item;const dims=mode==='canonical'?item.dimensions:S.dims;
      if(!dims.length){forms.append(node('p','方案为空 —— 请先到「编码方案」添加维度。'),btn('polish.open_scheme',()=>go('scheme')));baseline=signature();$('#cbStatus').textContent='';return;}
      if(!item.writable)forms.append(node('p','Read-only: current policy or source scope does not allow saving.',{class:'wf-readonly'}),node('p',W.reason(item.reason)));
      for(const d of dims){
        const dim=String(d.id),saved=item.evidence?.[dim]||{},value=item.saved?.[dim]||'',writable=item.writable&&(mode!=='canonical'||d.writable);
        const card=node('div',null,{class:'card wf-coding-unit','data-field-id':d.field_id||'','data-field-definition':JSON.stringify(['name','dtype','options','multi_select','unit','description'].map(key=>d[key])),id:'wf-field-'+dim});card.append(literal('b',d.name),node('span',d.multi_select?'多选':d.dtype==='text'?'填空':'单选',{class:'chip'}));
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
        const host=node('div',null,{class:'wf-evidence-images'});images.set(dim,[...(saved.images||[])]);remember(dim,saved.images);details.append(host);drawImages(dim,host,scope,t,writable);
        if(writable){const file=el('input',{type:'file',accept:'image/png,image/jpeg,image/webp',multiple:'','aria-label':T('Upload evidence images')});file.addEventListener('change',()=>{upload([...file.files],dim,host,writable);file.value='';});details.append(file);text.addEventListener('paste',event=>{const files=[...event.clipboardData?.files||[]].filter(f=>f.type.startsWith('image/'));if(files.length){event.preventDefault();upload(files,dim,host,writable);}});}
        card.append(details);
        if(forms.querySelector(':scope>.wf-coding-unit'))forms.append(node('hr',null,{class:'wf-coding-unit-divider','aria-hidden':'true'}));
        forms.append(card);
        for(const c of card.querySelectorAll('input,select,button')){c.disabled=!writable;c.dataset.wfReadonly=String(!writable);}
      }
      if(item.mapping_issues?.length)forms.append(node('p','Evidence field mapping needs owner review. Existing values are preserved.',{class:'wf-evidence-gap'}));
      S.codingFormReady=true;baseline=signature();updateGaps();updateBusy();
      document.dispatchEvent(new Event('reviewflow:coding-form-loaded'));
    }catch(error){if(t!==ticket||scope!==identity()||!W.renderAllowed())return;const line=node('p',W.errorText(error));loadFailure={error,line,scope,t};forms.append(line,btn('Retry / refresh',()=>E.render()));$('#cbStatus').textContent=T('Load failed');}
  };
  E.save=async()=>{
    if(W.refreshing()||!current?.writable||current?.policy_version!==W.version()||pending||saving||window.WorkspaceUndo?.isBusy()||!S.codingFormReady)return false;
    const scope=identity(),t=ticket,key=current.article.zotero_key||current.article.key;
    const notes={},evidence={},values={};
    for(const text of $$('#cbForms .wf-note'))if(text.dataset.wfReadonly!=='true')notes[text.dataset.dim]=text.value;
    for(const [dim,list] of images){if(!Object.hasOwn(notes,dim))continue;
      const intent={upload_ids:list.filter(i=>i.upload_id).map(i=>i.upload_id)},keep=keepFor(dim,list);
      /* 真的删过图才带列表（显式集合语义）；否则省略 = 全部保留（b11·F3-23）。 */
      if(keep)intent.keep_image_ids=keep;
      evidence[dim]=intent;}
    for(const control of $$('#cbForms .cb-v')){const dim=control.dataset.dim;if(control.type==='checkbox'){values[dim]??=[];if(control.checked)values[dim].push(control.value);}else values[dim]=[control.value];}
    const body={zotero_key:key,notes,evidence,expected_policy_version:current.policy_version,draft:false};
    if(mode!=='canonical')body.values=Object.fromEntries(Object.entries(values).map(([dim,v])=>[dim,v.join('|')]));
    if(mode==='canonical'&&!Object.keys(notes).length){toast(T('No mapped evidence fields are writable.'),'warn');return false;}
    saving=true;WorkspaceUndo?.beginSave();updateBusy();
    try{
      const result=await api(mode==='canonical'?discovery.save_url:taskApi()+'/coding/values-batch'+scrQ(),{method:'POST',body});
      if(scope!==identity()||t!==ticket)return false;
      // 保存后只合并附件关联，不重建表单/PDF；未改变的图片复用已下载的对象 URL。
      for(const [dim,saved] of Object.entries(result.evidence||{})){
        const previous=images.get(dim)||[];
        const attached=(saved.images||[]).map(image=>{
          const existing=previous.find(old=>image.id!=null&&old.id===image.id);
          return existing?Object.assign(existing,image):image;
        });
        images.set(dim,attached);remember(dim,attached);
        const changed=previous.length!==attached.length||previous.some((image,index)=>image!==attached[index]);
        if(changed){
          for(const old of previous)if(!attached.includes(old)&&old.objectURL){URL.revokeObjectURL(old.objectURL);urls.delete(old.objectURL);}
          const section=$$('#cbForms .wf-evidence').find(section=>section.dataset.dim===dim);
          const gallery=section?.querySelector('.wf-evidence-images');
          if(gallery)drawImages(dim,gallery,scope,t,current.writable);
        }
      }
      current={...current,evidence:{...current.evidence,...result.evidence}};
      baseline=signature();if(result.progress)applyCodingProgress(result.progress);
      document.dispatchEvent(new CustomEvent('reviewflow:coding-form-saved',{detail:key}));
      updateGaps();
      if(mode==='personal')await W.updateProgress('coding');
      else await W.queue('coding',{url:discovery.queue_url,evidenceOnly:true});
      $('#cbStatus').textContent=T(mode==='canonical'?'Evidence saved':result.complete?'Saved — complete':'Saved as draft — required values or evidence are missing');
      toast($('#cbStatus').textContent,'inc');return true;
    }catch(error){if(scope===identity())W.error(error);return false;}finally{saving=false;WorkspaceUndo?.endSave('coding');updateBusy();}
  };
  // 保存错误对象，切换界面语言时重译消息键、参数与详情，不重新请求当前文献。
  document.addEventListener('reviewflow:language-changed',()=>{if(loadFailure&&loadFailure.scope===identity()&&loadFailure.t===ticket&&loadFailure.line.isConnected){loadFailure.line.textContent=W.errorText(loadFailure.error);$('#cbStatus').textContent=T('Load failed');}});
  E.setContext=access=>{mode=access==='coding_submission_evidence'?'canonical':'personal';};
  document.addEventListener('reviewflow:task-context-changed',()=>{if(context===W.scope())return;context=W.scope();ticket++;clear();mode='personal';readOnlyKey=null;E.discovery(null);});
  document.addEventListener('reviewflow:workspace-changed',event=>{if(event.detail!=='bench'&&!E.dirty()){ticket++;clear();}});
  $('#cbForms').addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.tagName==='INPUT')event.stopPropagation();});
})();
