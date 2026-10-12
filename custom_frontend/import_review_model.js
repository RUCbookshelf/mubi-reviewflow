/* Shared, pure presentation rules. No decisions or activation are performed. */
(function(root,factory){
  const model=factory();
  if(typeof module==='object'&&module.exports)module.exports=model;
  else root.ImportReviewModel=model;
})(typeof window==='undefined'?globalThis:window,function(){
  'use strict';
  const fields=[['title','Title'],['abstract','Abstract'],['authors','Authors'],
    ['year','Year'],['journal','Journal'],['doi','DOI'],['item_type','Record type'],['url','URL']];
  const pending=value=>value==null||value==='pending';
  function reason(record){
    if(record.attention_reason)return record.attention_reason;
    if(record.candidate_statuses?.some(c=>['pending','undecided'].includes(c.status)))return 'duplicate';
    if(record.metadata&&pending(record.metadata.action))return 'metadata';
    return record.state==='active'?'completed':'ready';
  }
  function order(records){
    const priority={duplicate:0,metadata:1,dependency:2,waiting:2,ready:3,completed:4};
    return [...records].sort((a,b)=>(priority[reason(a)]??2)-(priority[reason(b)]??2)||
      Number(a.row_index||0)-Number(b.row_index||0)||String(a.record_id).localeCompare(String(b.record_id)));
  }
  function counts(batch){
    if(batch.review_counts)return batch.review_counts;
    const attention=Number(batch.pending_records||0),ready=0;
    return {attention,ready,needs_review:attention,completed:Number(batch.active_records||0)};
  }
  function comparison(metadata){
    const current=(metadata.action==='accept_incoming'?metadata.before:
      metadata.action==='keep_existing'?(metadata.accepted||metadata.before):
      (metadata.current||metadata.before))||{},incoming=metadata.incoming||{};
    const value=v=>v==null?'':Array.isArray(v)?v.join('; '):String(v);
    return fields.map(([key,label])=>({key,label,before:value(current[key]),incoming:value(incoming[key]),
      changed:value(current[key])!==value(incoming[key])}));
  }
  function guideState({imported,progress,summary,traceVisited,error}){
    const known=progress!=null&&summary!=null&&!error;
    const groups=Number(progress?.pending_groups||0)+Number(summary?.pending_candidate_groups||0);
    const metadata=Number(summary?.metadata_review_count||0);
    const review=!!imported&&known&&groups===0&&metadata===0;
    const activated=review&&Number(summary?.pending_records||0)===0;
    const done=[!!imported,review,activated,!!traceVisited];
    return {done,complete:done.every(Boolean),completed:done.filter(Boolean).length,groups,metadata,
      waiting:Number(summary?.pending_records||0),known};
  }
  return {reason,order,counts,comparison,guideState};
});
