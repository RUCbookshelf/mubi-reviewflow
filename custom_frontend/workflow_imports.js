/* Explicit provenance assignment and owner review of protected staged batches. */
(function(){
  'use strict';
  const W=WorkflowControls,I=window.WorkflowImports={};
  const page=document.querySelector('section[data-p="import"]');
  const host=el('section',{class:'card wf-import-review',id:'wf-import-review'});page.append(host);
  let active=null,offset=0,candidateOffset=0,listOffset=0,ticket=0,context='',assigning=false;
  const n=(tag,text,attrs={})=>el(tag,{...attrs,...(text==null?{}:{text:T(text)})});
  const b=(text,fn)=>n('button',text,{type:'button',class:'btn b-out sm',onclick:()=>Promise.resolve().then(fn).catch(W.error)});
  const literal=(tag,text)=>el(tag,{text:String(text??''),translate:'no'});
  const statusLabels={active:'Active',partially_active:'Partially active',staged:'Staged',preparing:'Preparing',failed:'Failed',pending:'Pending review',undecided:'Undecided',duplicate:'Duplicate confirmed',confirmed_duplicate:'Duplicate confirmed',not_duplicate:'Not duplicates',new:'New record',identity_conflict:'Identity conflict',exact_doi:'Exact DOI match',exact_key:'Exact identifier match',exact_staged:'Exact staged match',title:'Title candidate',fuzzy:'Fuzzy candidate',record:'Source record',keep_existing:'Keep existing metadata',accept_incoming:'Accept incoming metadata'};
  const status=value=>T(statusLabels[value]||value||'');
  const reports=new Map();
  I.remember=result=>{if(result?.batch_id)reports.set(result.batch_id,result);};
  I.active=()=>!!active;
  I.assign=async files=>{
    if(assigning)return null;
    assigning=true;const current=W.scope();
    try{
      await W.refresh('import',false);
      if(current!==W.scope())throw Error(T('The task changed. Reload the queue.'));
      if(!W.settings()?.can_manage)throw Error(T('Only the task owner can import sources.'));
      return files.map(()=> 'auto');
    }finally{assigning=false;}
  };
  async function download(url,name){const blob=await api(url,{blob:true}),object=URL.createObjectURL(blob),a=document.createElement('a');a.href=object;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(object),1000);}
  function counts(batch){
    const p=n('p',null,{class:'hint'});
    p.textContent=T('Source records: {0} · Activated: {1} · Staged: {2} · Metadata proposals: {3} · Pending candidates: {4}',batch.raw_source_records||0,batch.active_records??batch.activated_source_records??0,batch.pending_records||0,batch.metadata_review_count||0,batch.pending_candidate_groups??batch.candidate_groups??0);
    return p;
  }
  I.received=async result=>{I.remember(result);if(result.batch_id){active=result.batch_id;offset=0;candidateOffset=0;await I.open(active);}await I.notice().catch(W.error);};
  I.notice=async()=>{
    if(!S.task)return;
    const current=W.scope(),result=await api(taskApi()+'/import-history?limit=1&offset=0');if(current!==W.scope())return;
    const pending=Number(result.pending_import_records||0),notice=$('#wf-pending-imports');notice.hidden=!pending;
    if(pending)notice.replaceChildren(n('b','Imports awaiting review and activation'),n('p','Existing saved work remains available while additional records are staged.'),literal('p',pending),b('Open import review',async()=>{if(await go('import'))return I.list();}));
    return result;
  };
  I.list=async()=>{
    if(!S.task){host.replaceChildren();return;}
    active=null;const t=++ticket,current=W.scope();host.replaceChildren(n('p','Loading import batches…'));
    try{
      const result=await api(taskApi()+'/import-batches?limit=20&offset='+listOffset);if(t!==ticket||current!==W.scope())return;
      host.replaceChildren(n('h3','Source batches'),n('p','Parsing, activation and screening are separate steps. Unresolved affected records remain staged.'));
      for(const batch of result.batches){const row=n('section',null,{class:'wf-import-record'});row.append(literal('b',batch.batch_id||batch.id),n('span',' · ',{'aria-hidden':'true'}),n('span',status(batch.status)),counts(batch));for(const file of batch.files||[])row.append(literal('p',file.filename));row.append(b('Review / resume',()=>{offset=0;candidateOffset=0;return I.open(batch.batch_id||batch.id);}));host.append(row);}
      const previous=b('Previous page',()=>{listOffset=Math.max(0,listOffset-20);return I.list();}),next=b('Next page',()=>{listOffset=result.next_offset;return I.list();});previous.disabled=listOffset===0;next.disabled=result.next_offset==null;host.append(previous,next);
    }catch(error){if(t===ticket&&current===W.scope())host.replaceChildren(n('p',W.errorText(error)),b('Retry / refresh',I.list));}
  };
  async function mutate(path,body){
    const current=W.scope(),id=active;
    await api(taskApi()+'/import-batches/'+encodeURIComponent(id)+path,{method:'POST',body:{...body,expected_policy_version:W.version()}});
    if(current!==W.scope()||id!==active)return;
    await W.refresh('import',false);if(current!==W.scope()||id!==active)return;
    await I.open(id);if(current!==W.scope()||id!==active)return;
    await I.notice();if(current!==W.scope()||id!==active)return;await refreshProgress();
    document.dispatchEvent(new CustomEvent('reviewflow:import-completed'));
  }
  I.open=async id=>{
    active=id;const t=++ticket,current=W.scope(),base=taskApi()+'/import-batches/'+encodeURIComponent(id);host.replaceChildren(n('p','Loading staged review…'));
    try{
      const [batch,review,legacy]=await Promise.all([api(base+'?limit=20&offset='+offset),api(base+'/candidates?limit=20&offset='+candidateOffset),api(taskApi()+'/title-duplicate-reviews?limit=1&offset=0')]);
      if(t!==ticket||current!==W.scope()||active!==id)return;
      const report={...(reports.get(id)||{}),...batch,source_files:batch.files,files:reports.get(id)?.files,screening_ready:!batch.pending_records&&!batch.pending_candidate_groups&&!Number(legacy.progress?.pending_groups||0)};
      I.remember(report);renderImportReport(report);
      $('#goScreenBtn').style.display=batch.pending_records||batch.pending_candidate_groups||Number(legacy.progress?.pending_groups||0)?'none':'';
      host.replaceChildren(n('h3','Staged import review'),literal('p',id),counts(batch),b('All batches',I.list),b('Export provenance CSV',()=>download(base+'/export','import-'+id+'.csv')));
      if(batch.can_manage)host.append(b('Activate resolved records',()=>mutate('/activate',{})));
      host.append(n('p','Activation makes resolved records available. Unresolved candidates and unreviewed metadata remain staged.'));
      host.append(n('h4','Metadata and source records'));
      for(const record of batch.records||[]){
        const row=n('section',null,{class:'wf-import-record'}),article=record.article||record.staged_article||record.original||{};
        row.append(literal('b',article.title||record.incoming_key||record.record_id),literal('p',[record.filename,record.row_locator].filter(Boolean).join(' · ')),n('span',status(record.state)),n('span',' · ',{'aria-hidden':'true'}),n('span',status(record.outcome)));
        const original=n('details');original.append(n('summary','View original source record'),literal('pre',JSON.stringify(record.original,null,2)));row.append(original);
        const metadata=record.metadata;
        if(metadata){row.append(n('h4','Metadata proposal'),n('b','Keep existing'),literal('pre',JSON.stringify(metadata.before,null,2)),n('b','Incoming metadata'),literal('pre',JSON.stringify(metadata.incoming,null,2)));
          if(batch.can_manage&&(!metadata.action||metadata.action==='pending'))row.append(b('Keep existing metadata',()=>mutate('/metadata/'+encodeURIComponent(record.record_id),{action:'keep_existing'})),b('Accept incoming metadata',()=>mutate('/metadata/'+encodeURIComponent(record.record_id),{action:'accept_incoming'})));
          else row.append(n('p',status(metadata.action)));
        }
        host.append(row);
      }
      const prev=b('Previous records',()=>{offset=Math.max(0,offset-20);return I.open(id);}),next=b('Next records',()=>{offset=batch.next_offset;return I.open(id);});prev.disabled=offset===0;next.disabled=batch.next_offset==null;host.append(prev,next,n('h4','Candidate review'));
      for(const candidate of review.candidates||[]){
        const row=n('section',null,{class:'wf-import-candidate'}),keeper=n('select',null,{class:'input','aria-label':T('Retained record')});
        row.append(n('b',status(candidate.kind)),n('span',' · ',{'aria-hidden':'true'}),n('span',status(candidate.status)));
        const members=candidate.members||[],activeMembers=members.filter(m=>m.ref_type==='active');
        for(const member of members){
          const article=member.article||member.record?.article||member.record?.original||{};
          row.append(literal('p',article.title||member.ref_id),n('span',status(member.ref_type)));
          const detail=n('details');detail.append(n('summary','View candidate record'),literal('pre',JSON.stringify(member,null,2)));row.append(detail);
          if(!activeMembers.length||member.ref_type==='active')keeper.append(el('option',{value:member.ref_type+':'+member.ref_id,text:article.title||member.ref_id,translate:'no'}));
        }
        if(batch.can_manage&&!['duplicate','not_duplicate'].includes(candidate.status)){
          const path='/candidates/'+encodeURIComponent(candidate.review_id);
          const duplicate=b('Confirm duplicate',()=>{const separator=keeper.value.indexOf(':'),kind=keeper.value.slice(0,separator),key=keeper.value.slice(separator+1);return mutate(path,{status:'duplicate',[kind==='active'?'keeper_key':'keeper_record_id']:key});});duplicate.disabled=activeMembers.length>1||!keeper.options.length;
          row.append(keeper,duplicate,b('Not duplicates',()=>mutate(path,{status:'not_duplicate'})),b('Leave undecided',()=>mutate(path,{status:'undecided'})));
          if(activeMembers.length>1)row.append(n('p','Multiple active records cannot be merged here. Their saved work remains available.'));
        }
        host.append(row);
      }
      const cp=b('Previous candidates',()=>{candidateOffset=Math.max(0,candidateOffset-20);return I.open(id);}),cn=b('Next candidates',()=>{candidateOffset=review.next_offset;return I.open(id);});cp.disabled=candidateOffset===0;cn.disabled=review.next_offset==null;host.append(cp,cn);
    }catch(error){if(t===ticket&&current===W.scope())host.replaceChildren(n('p',W.errorText(error)),b('Retry / refresh',()=>I.open(id)),b('All batches',I.list));}
  };
  document.addEventListener('reviewflow:workspace-changed',event=>{if(event.detail==='import')Promise.resolve(active?I.open(active):I.list()).catch(W.error);if(event.detail==='home')I.notice().catch(W.error);});
  document.addEventListener('reviewflow:task-context-changed',()=>{if(context===W.scope())return;context=W.scope();ticket++;active=null;reports.clear();offset=0;candidateOffset=0;listOffset=0;host.replaceChildren();if(S.task)I.notice().catch(W.error);});
})();
