'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../../custom_frontend/import_review_ai.js'),'utf8');
class Element extends EventTarget{
  constructor(tag,text,attrs={}){super();this.tag=tag;this.textContent=text||'';this.attrs=attrs;this.children=[];this.disabled=!!attrs.disabled;this.open=false;this.value=attrs.value||'';this.checked=!!attrs.checked;}
  append(...nodes){this.children.push(...nodes);for(const node of nodes)node.parent=this;if(this.tag==='select'&&!this.value)this.value=this.children[0]?.value||'';}
  replaceChildren(...nodes){this.children=[];this.append(...nodes);}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(node=>node!==this);}
  close(){this.open=false;this.dispatchEvent(new Event('close'));}
  showModal(){this.open=true;}
  focus(){}
  querySelectorAll(){return this.controls||[];}
  setAttribute(key,value){this.attrs[key]=value;}
  removeAttribute(key){delete this.attrs[key];}
}
function scene(api){
  const node=(tag,text,attrs)=>new Element(tag,text,attrs);
  const context={window:{},AbortController,Event,Map,WeakMap,Set,Date,setInterval,clearInterval,
    ImportExportWorkspace:{node,literal:node,button:(text,click)=>Object.assign(node('button',text),{click})},
    WorkflowControls:{scope:()=> 'one-user-one-task',version:()=>4,acceptImportVersion(){},errorText:error=>error.messageKey||error.message},
    taskApi:()=>'/api/tasks/fixture',api,T:(key,...args)=>key.replace(/\{(\d+)\}/g,(_,i)=>args[i]),
    S:{page:'import'},document:{},console};
  const expose=`window.qa={request,badge,bulkCandidates,bulkPreview,settingsDialog,identity,selected,suggestions,conflicts,setSettings(value){settings=value;}};`;
  vm.runInNewContext(source.replace(/\}\)\(\);\s*$/,expose+'})();'),context);
  context.window.qa.setSettings({enabled:true,has_key:true,can_manage:true,version:1,sent_fields:[]});
  return context.window.qa;
}
test('cancel waiting aborts request, retains selection, and restores original disabled state',async()=>{
  let signal,applied=false,redraws=0;
  const qa=scene((url,options)=>new Promise((resolve,reject)=>{signal=options.signal;signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')));}));
  const panel=new Element('dialog');panel.open=true;panel.controls=[new Element('input'),new Element('select','',{disabled:true})];
  const view={panel,content:new Element('div'),status:new Element('p'),actions:new Element('div'),close:()=>panel.close()};
  const generate=new Element('button'),item={batch_id:'batch',kind:'duplicate_group',item_id:'row'};qa.selected.set(qa.identity(item),item);
  const pending=qa.request(view,generate,[item],()=>applied=true,()=>redraws++,()=>{});
  assert.equal(panel.open,true);assert.equal(panel.controls[0].disabled,true);
  assert.match(view.status.textContent,/1/);assert.equal(view.actions.children[0].textContent,'取消等待');
  view.actions.children[0].click();await pending;
  assert.equal(signal.aborted,true);assert.equal(applied,false);assert.equal(qa.selected.size,1);
  assert.equal(panel.controls[0].disabled,false);assert.equal(panel.controls[1].disabled,true);assert.equal(redraws,1);
});
test('different suggestions remain visible without becoming bulk duplicate candidates',()=>{
  const qa=scene(async()=>({}));
  const item={review_id:'row',status:'pending',members:[{ref_id:'one'}],bulk_eligible:true};
  const ref={batch_id:'batch',kind:'duplicate_group',item_id:'row'};
  qa.suggestions.set(qa.identity(ref),{...ref,verdict:'different',confidence:.97,audit_id:1});
  const badge=qa.badge('batch',item);
  assert.ok(badge);assert.match(badge.textContent,/verdict.different/);assert.match(badge.textContent,/0.97/);
  assert.match(badge.attrs.class,/neutral/);assert.equal(qa.bulkCandidates('batch',[item]).length,0);
});
test('field-only suggestions cannot enter duplicate-only bulk actions',()=>{
  const qa=scene(async()=>({}));
  const item={review_id:'row',status:'pending',members:[{ref_id:'one'}],bulk_eligible:true};
  qa.conflicts.set('field',{fingerprint:JSON.stringify(item.members),row:{batch_id:'batch',item_id:'row',verdict:'same',confidence:.97,audit_id:1}});
  assert.equal(qa.bulkCandidates('batch',[item]).length,0);
  const ref={batch_id:'batch',kind:'duplicate_group',item_id:'row'};
  qa.suggestions.set(qa.identity(ref),{...ref,verdict:'same',confidence:.97,audit_id:2});
  const rows=qa.bulkCandidates('batch',[item]);assert.equal(rows.length,1);assert.equal(rows[0].duplicate.audit_id,2);
  assert.equal(Object.hasOwn(rows[0],'currentConflicts'),false);
});
test('settings version conflict refreshes and allows a retry with the new version',async()=>{
  const versions=[];let attempts=0,redraws=0;
  const qa=scene(async(url,opts)=>{
    if(!opts)return {enabled:true,has_key:true,can_manage:true,version:2,sent_fields:[]};
    versions.push(opts.body.expected_version);
    if(++attempts===1)throw Object.assign(new Error('Conflict'),{status:409,messageKey:'import_ai_assist.settings_version_conflict'});
    return {enabled:false,has_key:true,can_manage:true,version:3};
  });
  const host=new Element('div');qa.settingsDialog(host,()=>redraws++);
  const panel=host.children[0],button=panel.children[2].children[0];
  await button.click();assert.equal(button.disabled,false);assert.equal(button.textContent,'已刷新设置，重试更改');
  await button.click();assert.deepEqual(versions,[1,2]);assert.equal(panel.open,false);assert.ok(redraws>0);
});
test('bulk submission reports first 100 successes while second chunk is pending and preserves them on failure',async()=>{
  const requests=[];let failSecond;
  const qa=scene(async(url,options)=>{
    requests.push(options.body);
    if(requests.length===1)return {policy_version:5};
    return new Promise((resolve,reject)=>{failSecond=reject;});
  });
  const rows=Array.from({length:102},(_,index)=>({item:{review_id:String(index),members:[{ref_id:'r'+index,ref_type:'record',article:{title:'Same title',doi:'10.999/test'}}]},duplicate:{audit_id:index+1,verdict:'same',confidence:.97}}));
  const host=new Element('div');qa.bulkPreview(host,'batch',rows,()=>{},item=>item.members[0].ref_id);
  const panel=host.children[0],status=panel.children[1].children[1],submit=panel.children[2].children[0];
  const pending=submit.click();await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(requests.map(request=>request.actions.length),[100,2]);
  assert.deepEqual(requests.map(request=>request.expected_policy_version),[4,5]);
  assert.match(status.textContent,/100\/102/);assert.match(status.textContent,/2\/2/);
  assert.ok(requests.every(request=>request.actions.every(action=>!Object.hasOwn(action,'conflict_picks'))));
  failSecond(Object.assign(new Error('Ineligible'),{status:409,messageKey:'import_merge.bulk_ineligible',params:{reason:'doi_conflict'}}));await pending;
  assert.match(status.textContent,/importMerge\.ineligible\.doi_conflict/);assert.match(status.textContent,/100/);
  assert.equal(submit.disabled,true);assert.equal(panel.open,true);
});
