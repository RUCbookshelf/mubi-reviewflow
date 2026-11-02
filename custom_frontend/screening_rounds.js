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
    }catch(error){
      WorkflowControls.error(error);
      /* F63（UX 批准单 §七）：screening-rounds 的版本冲突现在回
         {code:'workflow_version_conflict', version, workflow_settings}（与
         write_guard 同形）。W.error 只是“发起”刷新，未等待完成——这里显式等它
         落地，保证用户看到冲突提示后立刻重试时手里的 version 已是新值，不再
         「重试必再 409」。（W.refresh 内部对同一轮已有 flight 去重，不会重复拉取
         两次相同的策略；返回后 WorkflowControls.version() 即最新。） */
      if(error?.code==='workflow_version_conflict'&&current===token())await WorkflowControls.refresh('conflict').catch(()=>{});
    }finally{busy=false;R.render();}
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
