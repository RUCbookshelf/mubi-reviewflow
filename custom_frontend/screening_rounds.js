/* Repeat rounds keep the saved maybe decision distinct and reviewer-owned. */
(function(){
  'use strict';
  let state=null, context='', busy=false, remaining=0;
  const token=()=>[S.task?.task_id,S.user?.user_id,S.screener].join('|');
  const R=window.ScreeningRounds={};
  R.pending=key=>context===token()&&state ? state.pending_keys.includes(key) : !S.decisions[key];
  R.saved=key=>{if(context===token()&&state){state.pending_keys=state.pending_keys.filter(k=>k!==key);state.completed=state.total-state.pending_keys.length;R.render();}};
  R.ingest=result=>{context=token();state=result.screening_round||null;remaining=result.remaining_uncertain||0;R.render();};
  async function change(action){
    if(busy||!await WorkflowControls.guard())return;
    const current=token();busy=true;R.render();
    try{
      await api(taskApi()+'/screening-rounds',{method:'POST',body:{action,source_id:WorkflowControls.selectedSource('screen')||null,expected_policy_version:WorkflowControls.version()}});
      if(current!==token())return;
      await loadScreen();
      if(action==='finish')await go('fulltext');
    }catch(error){WorkflowControls.error(error);}finally{busy=false;R.render();}
  }
  R.render=()=>{
    const page=document.querySelector('section[data-p="screen"]');if(!page)return;
    let host=document.getElementById('wf-round-controls');
    if(!host){host=el('div',{id:'wf-round-controls',class:'wf-round-controls',role:'region','aria-label':T('Screening rounds')});page.insertBefore(host,page.querySelector('#scrCard'));}
    host.replaceChildren();host.hidden=context!==token()||(!state&&!remaining);if(host.hidden)return;
    const label=state?T('Screening round {0}: {1} / {2} reviewed',state.number,state.completed,state.total):T('Not Sure Now articles can be reviewed again or proceed to full-text screening.');
    host.append(el('span',{text:label,role:'status'}));
    const hasPending=state?state.pending_keys.length>0:S.articles.some(a=>!S.decisions[a.zotero_key]);
    if(!hasPending&&remaining)host.append(el('button',{type:'button',class:'btn b-out sm',text:T('Review Not Sure Now again'),disabled:busy?"":null,onclick:()=>change('start')}));
    host.append(el('button',{type:'button',class:'btn b-out sm',text:T('Proceed to full-text screening'),disabled:busy||(!state&&hasPending)?'':null,onclick:()=>change('finish')}));
  };
  document.addEventListener('reviewflow:language-changed',R.render);
})();
