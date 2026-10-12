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
  function batchLabel(batch){
    const files=(batch.files||batch.source_files||[]).map(f=>f.filename||f.name).filter(Boolean);
    const date=new Date(batch.created_at);
    const time=Number.isNaN(date.getTime())?'':date.toLocaleString(document.documentElement.lang||undefined,{dateStyle:'medium',timeStyle:'short'});
    return [files.length?files.join(' · '):T('Imported records'),time].filter(Boolean).join(' — ');
  }
  const M=ImportReviewModel;
  let busy=false,completedOffset=0,completedCandidateOffset=0,completedOpen=false;
  const reasonLabels={duplicate:'Duplicate review needed',metadata:'Metadata review needed',dependency:'Waiting for a linked record',waiting:'Waiting for review',ready:'Ready to activate',completed:'Completed — no action needed'};
  function writeButton(label,fn,blocked=false){
    const button=b(label,fn);button.dataset.importMutation='true';button.dataset.blocked=String(blocked);
    button.disabled=blocked||busy;return button;
  }
  function definitions(batch){
    const details=n('details',null,{class:'wf-import-counts'});
    details.append(n('summary','Import counts and what they mean'));
    const dl=n('dl');
    const terms=[
      ['Source records',batch.raw_source_records,'Original imported records in this batch; this is not a unique-article count.'],
      ['Activated',batch.active_records,'Processed source records, including new articles and links to existing articles.'],
      ['Staged',batch.pending_records,'Incoming records waiting for review or activation. Existing saved work stays available.'],
      ['Metadata proposals',batch.metadata_review_count,'Unreviewed differences between existing and incoming bibliographic information.'],
      ['Pending candidates',batch.pending_candidate_groups,'Unresolved duplicate groups. A group may contain several records.']];
    for(const [label,value,help] of terms)dl.append(n('dt',label),n('dd',null,{}));
    [...dl.children].filter(x=>x.tagName==='DD').forEach((dd,i)=>{dd.append(literal('b',terms[i][1]??0),n('span',' — '),n('span',terms[i][2]));});
    details.append(dl);return details;
  }
  function valueCell(value){
    const cell=n('td',null,{translate:'no'});cell.append(literal('span',value?value.slice(0,340):T('Not provided')));
    if(value.length>340){const full=n('details');full.append(n('summary','Read full value'),literal('p',value));cell.append(full);}
    return cell;
  }
  function metadataTable(metadata){
    const table=n('table',null,{class:'wf-metadata-table'}),head=n('thead'),tr=n('tr');
    for(const label of ['Field','Existing information','Incoming information'])tr.append(n('th',label,{scope:'col'}));
    head.append(tr);const body=n('tbody');
    for(const field of M.comparison(metadata)){
      const row=n('tr',null,{'data-changed':String(field.changed)}),label=n('th',field.label,{scope:'row'});
      if(field.changed)label.append(n('small','Changed',{class:'wf-change-tag'}));
      row.append(label,valueCell(field.before),valueCell(field.incoming));body.append(row);
    }
    table.append(head,body);return table;
  }
  function renderRecord(record,batch){
    const article=record.article||record.staged_article||record.original||{};
    const row=n('article',null,{class:'wf-import-record',id:'wf-record-'+record.record_id,tabindex:'-1'});
    row.append(n('span',reasonLabels[M.reason(record)]||'Waiting for review',{class:'wf-import-badge'}),literal('h5',article.title||record.incoming_key||T('Untitled record')),
      literal('p',[record.filename,record.row_locator].filter(Boolean).join(' · ')));
    const metadata=record.metadata,unreviewed=metadata&&(metadata.action==null||metadata.action==='pending');
    if(metadata){
      const section=unreviewed?n('section',null,{class:'wf-metadata-review'}):n('details',null,{class:'wf-metadata-review'});
      if(!unreviewed)section.append(n('summary','Reviewed metadata changes'));
      section.append(metadataTable(metadata));
      if(metadata.stale&&unreviewed)section.append(n('p','Existing information changed since this proposal. Keep the current information; accepting this older proposal is unavailable.',{class:'wf-import-warning'}));
      if(batch.can_manage&&unreviewed){
        const actions=n('div',null,{class:'wf-metadata-actions'});
        for(const [label,help,action,blocked] of [
          ['Keep existing metadata','Retains the article’s current information.','keep_existing',false],
          ['Accept incoming metadata','Replaces the listed bibliographic fields. Screening decisions and coding values are kept.','accept_incoming',!!metadata.stale]]){
          const unit=n('div'),effect=n('p',help,{id:'wf-effect-'+record.record_id+'-'+action});
          const button=writeButton(label,()=>mutate('/metadata/'+encodeURIComponent(record.record_id),{action},record.record_id),blocked);button.setAttribute('aria-describedby',effect.id);unit.append(button,effect);
          actions.append(unit);
        }
        section.append(actions);
      }else if(unreviewed)section.append(n('p','The task owner reviews metadata and activates records.'));
      else section.append(n('p',status(metadata.action)));
      row.append(section);
    }
    if(M.reason(record)==='ready')row.append(n('p','Review is complete. Activate this record to make it available in the task.'));
    if(M.reason(record)==='dependency'||M.reason(record)==='metadata'&&!metadata)row.append(n('p','A linked record must be activated or reviewed first. Existing articles remain available.'));
    if(record.related_batch_id)row.append(b('Open linked import',()=>I.open(record.related_batch_id)));
    const original=n('details',null,{class:'wf-source-original'});original.append(n('summary','View original source record'),literal('pre',JSON.stringify(record.original,null,2)));row.append(original);
    return row;
  }
  function recordPager(result,previous,next,label){
    const pager=n('nav',null,{class:'wf-import-pager','aria-label':T(label)});
    const prev=b('Previous records',previous),more=b('Next records',next);
    prev.disabled=(result.view==='completed'?completedOffset:offset)===0;more.disabled=result.next_offset==null;
    pager.append(prev,n('span','Showing {0}–{1} of {2} records',{class:'hint'}),more);
    pager.children[1].textContent=T('Showing {0}–{1} of {2} records',(result.view==='completed'?completedOffset:offset)+1,
      (result.view==='completed'?completedOffset:offset)+result.records.length,result.view_total??result.raw_source_records);
    if(result.view_total)pager.hidden=result.view_total<=20;
    return pager;
  }
  const reports=new Map(),keepers=new Map();
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
      host.replaceChildren(n('h3','Your imports'),n('p','Open a batch to review incoming records. Existing saved work stays available.'));
      const finished=n('details',null,{class:'wf-import-completed'});finished.append(n('summary','Completed imports'));
      for(const batch of result.batches){
        const row=n('article',null,{class:'wf-import-record'});row.append(literal('h4',batchLabel(batch)),counts(batch),
          b('Review / resume',()=>{offset=0;candidateOffset=0;completedOffset=0;completedCandidateOffset=0;completedOpen=false;return I.open(batch.batch_id||batch.id);}));
        (batch.pending_records||batch.pending_candidate_groups||batch.metadata_review_count||['preparing','failed'].includes(batch.status)?host:finished).append(row);
      }
      host.append(finished);
      if(!result.batches.length)host.append(n('p','Import a RIS file to begin.'));
      const previous=b('Previous page',()=>{listOffset=Math.max(0,listOffset-20);return I.list();}),next=b('Next page',()=>{listOffset=result.next_offset;return I.list();});
      previous.disabled=listOffset===0;next.disabled=result.next_offset==null;
      if(result.total>20)host.append(previous,next);
    }catch(error){if(t===ticket&&current===W.scope())host.replaceChildren(n('p',W.errorText(error)),b('Retry / refresh',I.list));}
  };
  async function mutate(path,body,recordId){
    if(busy)return;
    const current=W.scope(),id=active;busy=true;
    host.querySelectorAll('[data-import-mutation]').forEach(button=>button.disabled=true);
    try{
      await api(taskApi()+'/import-batches/'+encodeURIComponent(id)+path,{method:'POST',body:{...body,expected_policy_version:W.version()}});
      if(current!==W.scope()||id!==active)return;
      await W.refresh('import',false);if(current!==W.scope()||id!==active)return;
      await I.open(id);if(current!==W.scope()||id!==active)return;
      await I.notice();if(current!==W.scope()||id!==active)return;await refreshProgress();
      document.dispatchEvent(new CustomEvent('reviewflow:import-completed'));
      (document.getElementById('wf-record-'+recordId)||host.querySelector('h3'))?.focus({preventScroll:true});
    }finally{
      busy=false;host.querySelectorAll('[data-import-mutation]').forEach(button=>button.disabled=button.dataset.blocked==='true');
    }
  }
  I.open=async id=>{
    if(active!==id){offset=0;candidateOffset=0;completedOffset=0;completedCandidateOffset=0;completedOpen=false;}
    active=id;const t=++ticket,current=W.scope(),base=taskApi()+'/import-batches/'+encodeURIComponent(id);host.replaceChildren(n('p','Loading staged review…'));host.setAttribute('aria-busy','true');
    try{
      const [batch,review,legacy]=await Promise.all([api(base+'?view=attention&limit=20&offset='+offset),
        api(base+'/candidates?view=attention&limit=20&offset='+candidateOffset),api(taskApi()+'/title-duplicate-reviews?limit=1&offset=0')]);
      if(t!==ticket||current!==W.scope()||active!==id)return;
      let repage=false;
      if(batch.view_total&&offset>=batch.view_total){offset=Math.floor((batch.view_total-1)/20)*20;repage=true;}
      if(review.total&&candidateOffset>=review.total){candidateOffset=Math.floor((review.total-1)/20)*20;repage=true;}
      if(!batch.view_total)offset=0;if(!review.total)candidateOffset=0;
      if(repage)return I.open(id);
      const report={...(reports.get(id)||{}),...batch,source_files:batch.files,files:reports.get(id)?.files,screening_ready:!batch.pending_records&&!batch.pending_candidate_groups&&!Number(legacy.progress?.pending_groups||0)};
      I.remember(report);renderImportReport(report);
      $('#goScreenBtn').style.display=batch.pending_records||batch.pending_candidate_groups||Number(legacy.progress?.pending_groups||0)?'none':'';
      const preparing=batch.status==='preparing',failed=batch.status==='failed';
      const c=M.counts(batch),header=n('header',null,{class:'wf-import-header'}),heading=n('h3',null,{tabindex:'-1'});
      heading.textContent=preparing?T('Import is still being prepared'):failed?T('This import needs another attempt'):c.needs_review?T(c.needs_review===1?'1 imported record needs your review':'{0} imported records need your review',c.needs_review):
        c.ready?T('{0} reviewed records are ready to activate',c.ready):T('This import is complete');
      header.append(n('p','Import review',{class:'wf-import-eyebrow'}),heading,literal('p',batchLabel(batch)));
      const identity=n('details',null,{class:'wf-import-identity'});identity.append(n('summary','Import details'),n('p','Batch reference'),literal('code',id));header.append(identity);
      host.replaceChildren(header);
      const announcement=n('p',null,{class:'wf-import-summary',role:'status','aria-live':'polite'});
      announcement.textContent=T('{0} need attention · {1} ready to activate · {2} completed',c.attention,c.ready,c.completed);host.append(announcement);
      const bar=n('div',null,{class:'wf-import-actions',id:'wf-import-activation',tabindex:'-1'});
      bar.append(b('All batches',I.list),b('Export provenance CSV',()=>download(base+'/export','import-'+id+'.csv')));
      if(batch.can_manage){
        const activate=writeButton(T('Activate resolved records ({0})',c.ready),()=>mutate('/activate',{}),c.ready===0);
        activate.classList.add('wf-import-activate');activate.title=T('Activation adds resolved records or source links. Unresolved records stay staged; repeated activation does not add duplicate work.');bar.append(activate);
      }
      host.append(bar,n('p','Activation adds resolved records or source links. Unresolved records stay staged; repeated activation does not add duplicate work.',{class:'wf-action-explanation'}));
      if(!batch.can_manage)host.append(n('p','The task owner reviews metadata and activates records.'));
      host.append(definitions(batch));
      const attention=n('section',null,{class:'wf-import-attention',id:'wf-import-attention','aria-label':T('Needs your attention')});
      attention.append(n('h4','Needs your attention'));
      if(review.total){
        attention.append(n('h5','Duplicate checks'),n('p','Compare the records, then confirm whether they describe the same article.'));
        for(const candidate of review.candidates||[])attention.append(renderCandidate(candidate,batch));
        attention.append(candidatePager(review,id));
      }
      if(c.attention===0)attention.append(n('p',preparing?'Please wait for parsing to finish, then refresh this review.':failed?'Check the import report and upload the source file again.':'No records need review or activation. Completed records are below.',{class:preparing||failed?'wf-action-explanation':'wf-import-success'}));
      else{
        if(c.needs_review===0)attention.append(n('p','All checks are complete. Use Activate resolved records to finish this import.',{class:'wf-import-success'}));
        for(const record of M.order(batch.records||[]))attention.append(renderRecord(record,batch));
        if(batch.view_total)attention.append(recordPager(batch,()=>{offset=Math.max(0,offset-20);return I.open(id);},()=>{offset=batch.next_offset;return I.open(id);},'Records needing attention'));
      }
      host.append(attention);
      const completed=n('details',null,{class:'wf-import-completed'}),content=n('div');
      completed.append(n('summary',T('Completed / already active ({0})',c.completed)),n('p','These records are already processed and require no action.'),content);
      let loaded=false;
      completed.addEventListener('toggle',async()=>{
        completedOpen=completed.open;if(!completed.open||loaded)return;
        content.replaceChildren(n('p','Loading completed records…'));content.setAttribute('aria-busy','true');
        try{
          const [done,groups]=await Promise.all([api(base+'?view=completed&limit=20&offset='+completedOffset),api(base+'/candidates?view=completed&limit=20&offset='+completedCandidateOffset)]);
          if(t!==ticket||current!==W.scope()||active!==id)return;
          let repage=false;
          if(done.view_total&&completedOffset>=done.view_total){completedOffset=Math.floor((done.view_total-1)/20)*20;repage=true;}
          if(groups.total&&completedCandidateOffset>=groups.total){completedCandidateOffset=Math.floor((groups.total-1)/20)*20;repage=true;}
          if(!done.view_total)completedOffset=0;if(!groups.total)completedCandidateOffset=0;
          if(repage)return I.open(id);
          content.replaceChildren(...(done.records||[]).map(r=>renderRecord(r,batch)));
          if(!done.records?.length)content.append(n('p','No completed records yet.'));
          if(done.view_total)content.append(recordPager(done,()=>{completedOffset=Math.max(0,completedOffset-20);return I.open(id);},()=>{completedOffset=done.next_offset;return I.open(id);},'Completed records'));
          if(groups.total){content.append(n('h5',T('Reviewed duplicate groups ({0})',groups.total)));for(const group of groups.candidates||[])content.append(renderCandidate(group,batch));
            const navigation=n('nav',null,{class:'wf-import-pager','aria-label':T('Reviewed duplicate groups')});
            const previous=b('Previous candidates',()=>{completedCandidateOffset=Math.max(0,completedCandidateOffset-20);return I.open(id);}),next=b('Next candidates',()=>{completedCandidateOffset=groups.next_offset;return I.open(id);});
            previous.disabled=completedCandidateOffset===0;next.disabled=groups.next_offset==null;navigation.append(previous,next);navigation.hidden=groups.total<=20;content.append(navigation);}
          loaded=true;
        }catch(error){if(t===ticket&&current===W.scope()){content.replaceChildren(n('p',W.errorText(error)),b('Retry / refresh',()=>I.open(id)));}}
        finally{content.removeAttribute('aria-busy');}
      });
      host.append(completed);completed.open=completedOpen;
    }catch(error){if(t===ticket&&current===W.scope())host.replaceChildren(n('p',W.errorText(error)),b('Retry / refresh',()=>I.open(id)),b('All batches',I.list));}
    finally{if(t===ticket)host.removeAttribute('aria-busy');}
  };
  function renderCandidate(candidate,batch){
    const row=n('article',null,{class:'wf-import-candidate',id:'wf-candidate-'+candidate.review_id}),
      keeper=n('select',null,{class:'input','aria-label':T('Retained record')});
    row.append(n('h5',status(candidate.kind)),n('span',status(candidate.status),{class:'wf-import-badge'}));
    const members=candidate.members||[],activeMembers=members.filter(m=>m.ref_type==='active');
    for(const member of members){
      const article=member.article||member.record?.article||member.record?.original||{};
      row.append(literal('b',article.title||member.ref_id),literal('p',[article.authors,article.year,article.journal,article.doi].filter(Boolean).join(' · ')));
      const detail=n('details');detail.append(n('summary','View candidate record'),literal('pre',JSON.stringify(member,null,2)));row.append(detail);
      if(!activeMembers.length||member.ref_type==='active')keeper.append(el('option',{value:member.ref_type+':'+member.ref_id,text:article.title||member.ref_id,translate:'no'}));
    }
    const selectionKey=W.scope()+':'+candidate.review_id;
    if([...keeper.options].some(option=>option.value===keepers.get(selectionKey)))keeper.value=keepers.get(selectionKey);
    keeper.addEventListener('change',()=>keepers.set(selectionKey,keeper.value));
    if(['duplicate','not_duplicate'].includes(candidate.status))keepers.delete(selectionKey);
    if(batch.can_manage&&!['duplicate','not_duplicate'].includes(candidate.status)){
      const path='/candidates/'+encodeURIComponent(candidate.review_id),actions=n('div',null,{class:'wf-import-actions'});
      const duplicate=writeButton('Confirm duplicate',()=>{const separator=keeper.value.indexOf(':'),kind=keeper.value.slice(0,separator),key=keeper.value.slice(separator+1);return mutate(path,{status:'duplicate',[kind==='active'?'keeper_key':'keeper_record_id']:key});},activeMembers.length>1||!keeper.options.length);
      actions.append(keeper,duplicate,writeButton('Not duplicates',()=>mutate(path,{status:'not_duplicate'})),writeButton('Leave undecided',()=>mutate(path,{status:'undecided'})));
      row.append(actions,n('p','Confirm duplicate retains the selected record. Not duplicates keeps both; Leave undecided keeps the affected imports staged.'));
      if(activeMembers.length>1)row.append(n('p','Multiple active records cannot be merged here. Their saved work remains available.'));
    }
    return row;
  }
  function candidatePager(review,id){
    const nav=n('nav',null,{class:'wf-import-pager','aria-label':T('Duplicate checks')});
    const cp=b('Previous candidates',()=>{candidateOffset=Math.max(0,candidateOffset-20);return I.open(id);}),
      cn=b('Next candidates',()=>{candidateOffset=review.next_offset;return I.open(id);});
    cp.disabled=candidateOffset===0;cn.disabled=review.next_offset==null;nav.append(cp,cn);nav.hidden=review.total<=20;return nav;
  }
  document.addEventListener('reviewflow:workspace-changed',event=>{if(event.detail==='import')Promise.resolve(active?I.open(active):I.list()).catch(W.error);if(event.detail==='home')I.notice().catch(W.error);});
  document.addEventListener('reviewflow:workflow-changed',event=>{if(!busy&&S.page==='import'&&String(event.detail?.task_id)===String(S.task?.task_id)&&String(event.detail?.reason||'').startsWith('import-'))Promise.resolve(active?I.open(active):I.list()).catch(W.error);});
  document.addEventListener('reviewflow:language-changed',()=>{if(S.page==='import')Promise.resolve(active?I.open(active):I.list()).catch(W.error);});
  document.addEventListener('reviewflow:task-context-changed',()=>{if(context===W.scope())return;context=W.scope();ticket++;active=null;reports.clear();keepers.clear();offset=0;candidateOffset=0;listOffset=0;completedOffset=0;completedCandidateOffset=0;completedOpen=false;host.replaceChildren();if(S.task)I.notice().catch(W.error);});
})();
