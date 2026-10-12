/* Optional task-assisted PRISMA. Manual entry and the original exporter remain usable. */
(function(){
  'use strict';
  const U=window.PrismaTaskUI={};
  const page=document.querySelector('[data-p="prisma"]');
  let request=0,lastTask=null,applying=false;
  let previousMode=$('#prMode').value,studyDraft=$('#prIncluded').value;
  let reportDraft=null;
  const n=(tag,text,attrs={})=>el(tag,{...attrs,...(text==null?{}:{text:T(text)})});
  const button=(label,fn)=>n('button',label,{type:'button',class:'btn b-out sm',onclick:fn});
  const context=()=>[S.task?.task_id,S.user?.user_id,S.screener].join('|');
  const block=n('div',null,{class:'pr-task-metrics'}),mode=n('select',null,{id:'prMetricMode',class:'input','aria-label':T('Task metric source')});
  mode.append(n('option','Current reviewer',{value:'reviewer'}),n('option','Team consolidated outcomes',{value:'team'}));
  const notice=n('p','Manual entry works without importing task metrics.',{id:'prMetricNotice',class:'hint',role:'status'});
  block.append(n('label','Task metric source',{class:'lbl',for:'prMetricMode'}),mode,notice);
  const setup=$('#prMode').closest('details')||$('#prMode').closest('.card');
  setup.append(block);
  const includedCard=$('#prIncluded').closest('.card'),inputHost=includedCard.parentElement;
  const field=(id,label,blank=false)=>{
    const box=n('div'),input=el('input',{id,class:'input prn',type:'number',min:'0',...(blank?{}:{value:'0'})});
    box.append(n('label',label,{class:'lbl',for:id}),input);return box;
  };
  const other=n('section',null,{id:'prOtherAssessment',class:'card',hidden:''});
  other.append(n('h3','Other sources — retrieval and eligibility'));
  const otherRow=n('div',null,{class:'row'});
  otherRow.append(field('prOtherSought','Reports sought — other sources'),field('prOtherNotRetrieved','Reports not retrieved — other sources'),field('prOtherAssessed','Reports assessed — other sources'));
  const otherReasons=n('div',null,{id:'prOtherReasons'});
  other.append(otherRow,otherReasons,button('Add full-text exclusion reason',()=>{otherReasons.append(prReasonRow());prAutoCalc();}));
  inputHost.insertBefore(other,includedCard);
  const updated=n('section',null,{id:'prUpdatedCounts',class:'card',hidden:''});
  updated.append(n('h3','Updated review — previous, new and total counts'));
  const updatedRow=n('div',null,{class:'row'});
  for(const [id,label,blank] of [['prPreviousStudies','Previously included studies',true],['prPreviousReports','Previously included reports',true],['prNewStudies','Newly included studies',true],['prNewReports','Reports of newly included studies',false],['prTotalStudies','Total included studies',false],['prTotalReports','Total included reports',false]])updatedRow.append(field(id,label,blank));
  updated.append(updatedRow);inputHost.insertBefore(updated,includedCard);
  document.getElementById('prIncluded').parentElement.append(button('One report per study',()=>{
    const updatedMode=$('#prMode').value==='updated',input=$(updatedMode?'#prNewStudies':'#prIncluded');
    input.value=String(prNum(updatedMode?'prNewReports':'prIncludedReports'));
    input.dispatchEvent(new Event('input',{bubbles:true}));
  }));
  const extras=[
    ['prOtherAssessed',()=>prNum('prOtherSought')-prNum('prOtherNotRetrieved')],
    ['prNewReports',()=>prNum('prAssessed')+($('#prScope').value==='db_reg_other'?prNum('prOtherAssessed'):0)-prSumPrn($('#prReasonsFull'))-($('#prScope').value==='db_reg_other'?prSumPrn(otherReasons):0)],
    ['prTotalStudies',()=>prNum('prPreviousStudies')+prNum('prNewStudies')],
    ['prTotalReports',()=>prNum('prPreviousReports')+prNum('prNewReports')]
  ];
  for(const [id,derive] of extras){
    const input=$('#'+id),tag=n('span','Automatic',{class:'pr-tag'}),restore=button('Restore automatic',()=>{PR_AUTO.manual.delete(id);prAutoCalc();});
    input.parentElement.append(tag,restore);input.wfAuto={tag,restore,derive};
    input.addEventListener('input',()=>{PR_AUTO.manual.add(id);prAutoCalc();});
  }
  for(const id of ['prPreviousStudies','prPreviousReports','prNewStudies','prOtherSought','prOtherNotRetrieved'])$('#'+id).addEventListener('input',()=>prAutoCalc());
  otherReasons.addEventListener('input',()=>prAutoCalc());
  const report=PR_CHAIN.find(f=>f.id==='prIncludedReports');
  report.derive=v=>$('#prMode').value==='updated'?prNum('prTotalReports'):v.prAssessed-v.rsnFull+($('#prScope').value==='db_reg_other'?prNum('prOtherAssessed')-prSumPrn(otherReasons):0);
  const originalCalc=prAutoCalc;
  prAutoCalc=function(){
    if(applying)return;
    originalCalc();
    for(const [id] of extras){const input=$('#'+id),manual=PR_AUTO.manual.has(id);
      if(!manual)input.value=String(Math.max(0,input.wfAuto.derive()));
      input.wfAuto.tag.textContent=T(manual?'Manual':'Automatic');input.wfAuto.restore.hidden=!manual;
    }
    originalCalc();
    if($('#prMode').value==='updated')$('#prIncluded').value=['prPreviousStudies','prNewStudies'].every(id=>$('#'+id).value.trim())?String(prNum('prTotalStudies')):'';
    if($('#prMode').value==='updated')$('#prIncludedReports').value=String(prNum('prTotalReports'));
  };
  function modes(){
    const currentMode=$('#prMode').value,input=$('#prIncluded');
    if(previousMode!=='updated'&&currentMode==='updated'){
      studyDraft=input.value;reportDraft={value:$('#prIncludedReports').value,manual:PR_AUTO.manual.has('prIncludedReports')};
      PR_AUTO.manual.delete('prIncludedReports');
    }
    if(previousMode==='updated'&&currentMode!=='updated'){
      input.value=studyDraft;
      if(reportDraft){$('#prIncludedReports').value=reportDraft.value;if(reportDraft.manual)PR_AUTO.manual.add('prIncludedReports');else PR_AUTO.manual.delete('prIncludedReports');}
    }
    previousMode=currentMode;input.readOnly=currentMode==='updated';
    $('#prIncludedReports').readOnly=currentMode==='updated';
    other.hidden=$('#prScope').value!=='db_reg_other';updated.hidden=currentMode!=='updated';prAutoCalc();
  }
  $('#prMode').addEventListener('change',modes);$('#prScope').addEventListener('change',modes);modes();
  $('#prAutoFill').addEventListener('click',()=>{for(const [id] of extras)PR_AUTO.manual.delete(id);prAutoCalc();});
  U.completeForm=form=>{
    const updatedMode=form.review_mode==='updated';
    const required=updatedMode?['prPreviousStudies','prPreviousReports','prNewStudies']:['prIncluded'];
    if(required.some(id=>!$('#'+id).value.trim()||!$('#'+id).checkValidity())){toast(T('Enter confirmed study counts; use One report per study only when appropriate.'),'warn');return false;}
    for(const [key,id] of Object.entries({reports_sought_other:'prOtherSought',reports_not_retrieved_other:'prOtherNotRetrieved',reports_assessed_other:'prOtherAssessed',previous_studies:'prPreviousStudies',previous_reports:'prPreviousReports',new_studies:'prNewStudies',new_reports:'prNewReports',total_studies:'prTotalStudies',total_reports:'prTotalReports'}))form[key]=prNum(id);
    form.exclusion_reasons_other=Array.from(otherReasons.children).map(row=>row._collect?.()).filter(Boolean);
    if(updatedMode){form.studies_included=prNum('prTotalStudies');form.reports_of_included_studies=prNum('prTotalReports');}
    return true;
  };
  function status(text){notice.textContent=T(text);}
  U.importMetrics=async()=>{
    if(!S.task){toast(T('Select a task to import metrics. Manual entry is still available.'),'warn');return;}
    const ticket=++request,scope=context(),btn=$('#prImport');btn.disabled=true;
    status('Loading task metrics…');
    try{
      const result=await api(taskApi()+'/prisma-metrics?mode='+encodeURIComponent(mode.value));
      if(ticket!==request||context()!==scope)return;
      const dialog=n('dialog',null,{class:'wf-source-dialog','aria-label':T('Review task metrics')});
      dialog.append(n('h3','Review task metrics'),n('p','Choose values to apply. Existing manual entries are preserved unless selected explicitly.'));
      for(const warning of result.warnings||[])dialog.append(n('p',warning,{class:'hint'}));
      const fieldIds={duplicates_removed:'prDedup',records_screened:'prScreened',records_excluded_screening:'prExcScreen',reports_sought_db:'prSought',reports_not_retrieved_db:'prNotRetrieved',reports_assessed_db:'prAssessed',reports_of_included_studies:$('#prMode').value==='updated'?'prNewReports':'prIncludedReports',studies_included:$('#prMode').value==='updated'?'prNewStudies':'prIncluded',reports_sought_other:'prOtherSought',reports_not_retrieved_other:'prOtherNotRetrieved',reports_assessed_other:'prOtherAssessed'};
      const lists={identified_rows_db:['prRowsDb',()=>prIdentifiedRow('db',['database','register'])],identified_rows_other:['prRowsOther',()=>prIdentifiedRow('other',['website','organisation','citation_searching','other'])],screening_exclusion_reasons:['prReasonsScreen',prReasonRow],exclusion_reasons_db:['prReasonsFull',prReasonRow],exclusion_reasons_other:['prOtherReasons',prReasonRow]};
      const labels={identified_rows_db:'Database/register identification counts',identified_rows_other:'Other-source identification counts',screening_exclusion_reasons:'Title-and-abstract exclusion reasons',exclusion_reasons_db:'Full-text exclusion reasons',exclusion_reasons_other:'Other-source full-text exclusion reasons'};
      const choices=[];
      for(const [key,value] of Object.entries(result.fields)){
        if(!(key in fieldIds)&&!(key in lists))continue;
        const check=el('input',{type:'checkbox'}),id=fieldIds[key],list=lists[key],existing=id?$('#'+id).value:list?document.getElementById(list[0]).children.length:0;
        check.disabled=value==null||Array.isArray(value)&&!value.length||key.startsWith('identified_')&&!result.coverage_complete;
        check.checked=!check.disabled&&!(id?PR_AUTO.manual.has(id)||existing!==''&&Number(existing)!==0:existing);
        const label=id?$('#'+id).parentElement.querySelector('label')?.textContent:labels[key];
        dialog.append(el('label',{class:'wf-field-option'},check,n('span',labels[key]||label||key),el('span',{text:value==null?T('Needs confirmation'):Array.isArray(value)?String(value.reduce((s,r)=>s+r.n,0)):String(value),translate:'no'})));
        choices.push({key,value,check,id,list});
      }
      const finish=()=>{dialog.close();dialog.remove();};dialog.addEventListener('cancel',e=>{e.preventDefault();finish();});
      dialog.append(button('Export metric records CSV',()=>runExport(scope,result.mode)),button('Apply selected metrics',()=>{
        if(context()!==scope){finish();status('The task changed. Reload the queue.');return;}
        applying=true;
        try{
          U.provenance={task_id:S.task.task_id,policy_version:result.version,metric_mode:result.mode,applied_at:new Date().toISOString(),fields:choices.filter(c=>c.check.checked).map(c=>c.key),snapshot:Object.fromEntries(choices.filter(c=>c.check.checked).map(c=>[c.key,c.value])),warnings:result.warnings,pending:result.pending};
          for(const choice of choices.filter(c=>c.check.checked)){
            if(choice.id){$('#'+choice.id).value=String(choice.value);PR_AUTO.manual.add(choice.id);}
            else{
              const [hostId,make]=choice.list,host=$('#'+hostId);host.replaceChildren();
              for(const entry of choice.value){const row=make();host.append(row);const inputs=row.querySelectorAll('input');inputs[0].value=entry.name||entry.reason;inputs[1].value=String(entry.n);if(entry.source_type)row.querySelector('select').value=entry.source_type;}
            }
          }
          if(choices.some(c=>c.check.checked&&c.key==='identified_rows_other'))$('#prScope').value='db_reg_other';
          lastTask=scope;status('Selected task metrics applied. Review unresolved counts before generating.');
          $('#prImportCap').style.display='none';
        }finally{applying=false;modes();finish();}
      }),button('Cancel',finish));document.body.append(dialog);dialog.showModal();status('Task metrics ready for review.');
    }catch(error){status('Task metric import failed. Manual entries are unchanged.');toast(error.message,'warn');}
    finally{if(ticket===request)btn.disabled=false;}
  };
  async function runExport(scope,metricMode){
    if(context()!==scope)return;
    try{
      const response=await fetch(API_BASE+taskApi()+'/prisma-metrics/records?mode='+encodeURIComponent(metricMode),{headers:{Authorization:'Bearer '+AUTH_TOKEN}});
      if(!response.ok)throw Error(await response.text());
      const blob=await response.blob();if(context()!==scope)return;
      const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='prisma-task-records.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(error){toast(error.message,'warn');}
  }
  document.addEventListener('reviewflow:task-context-changed',()=>{request++;$('#prImport').disabled=false;if(lastTask&&lastTask!==context())status('The form contains a previous task snapshot. Import explicitly to update it.');});
  document.addEventListener('reviewflow:workflow-changed',()=>{if(lastTask)status('Task data changed. Refresh explicitly to review new metrics.');});
})();
