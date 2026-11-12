const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const front=path.join(__dirname,'../../custom_frontend');
const source=fs.readFileSync(path.join(front,'workflow_controls.js'),'utf8');
const section=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
function policy(){return {version:1,can_manage:true,stages:{screen:{mode:'all',source_ids:[]},fulltext:{mode:'all',source_ids:[]},coding:{mode:'all',source_ids:[]}},sources:[{id:'one',active:true,linked_count:20}],evidence_required_field_ids:[],evidence_fields:[]};}
function signatures(context){vm.runInNewContext(section('  const draftSignature=','  let editRevision'),context);}

test('unrelated coding policy preserves screen signature; relevant active sources invalidate it',()=>{
  const c={stages:['screen','fulltext','coding']};signatures(c);
  const signature=vm.runInNewContext('stageSignature',c);
  const initial=policy(),changed=structuredClone(initial);changed.version++;changed.stages.coding.mode='paused';
  assert.equal(signature(initial,'screen'),signature(changed,'screen'));
  assert.notEqual(signature(initial,'coding'),signature(changed,'coding'));
  changed.sources[0].active=false;
  assert.notEqual(signature(initial,'screen'),signature(changed,'screen'));
});

test('unchanged policy editor preserves focused source controls and rebases its version',()=>{
  const c={stages:['screen','fulltext','coding'],settings:policy(),draft:{version:1},W:{settingsDirty:()=>false},scope:()=> 'task',RFLang:{get:()=> 'en'},$:()=>{throw Error('unexpected DOM rebuild');}};
  signatures(c);
  vm.runInNewContext("editorRendered=JSON.stringify([scope(),policySignature(settings),settings.sources,RFLang.get()]);",c);
  c.settings.version=2;
  vm.runInNewContext(section('  function policyEditor()','  W.saveSettings='),c);
  c.policyEditor();assert.equal(c.draft.version,2);
});

test('same reason library does not rebuild settings controls',async()=>{
  const results=[{entries:[{id:'a',text:'Reason'}]},{entries:[]}];
  const c={scope:()=> 'task',reasonsTicket:0,refreshTicket:1,reasons:{},libraryDirty:false,RFLang:{get:()=> 'en'},reasonsRendered:JSON.stringify(['task',results,'en']),W:{decorateReasons:()=>{}},$:()=>{throw Error('unexpected reasons rebuild');}};
  vm.runInNewContext(section('  async function loadReasons(','  function mutateReason('),c);
  await c.loadReasons(1,results);assert.equal(c.reasons.screen[0].text,'Reason');
});

test('background failures back off without throwing repeated toast errors and recover',async()=>{
  let calls=0,now=1000,failing=true;
  const c={W:{},S:{task:{},user:{}},scope:()=> 'task',settings:{version:1},autoReload:null,policyFlight:null,pollFailures:0,nextPollAt:0,deferredRefresh:false,Date:{now:()=>now},T:x=>x,pollNotice:()=>{},refreshPolicy:async()=>{calls++;if(failing)throw Error('offline');}};
  vm.runInNewContext(section('  W.refresh=async','  W.guard=async'),c);
  await c.W.refresh();await c.W.refresh('focus');assert.equal(calls,1);assert.equal(c.nextPollAt,31000);
  now=32000;await c.W.refresh();assert.equal(calls,2);assert.equal(c.nextPollAt,92000);
  failing=false;await c.W.refresh('retry');assert.equal(c.pollFailures,0);assert.equal(c.nextPollAt,0);
});

test('modal boundary covers native and custom dialogs and undo cannot cross it',()=>{
  let dialogs=[];const c={window:{},document:{querySelectorAll:()=>dialogs}};
  vm.runInNewContext(section('  const W =','  const stages ='),c);
  dialogs=[{hidden:false,getClientRects:()=>[{}]}];assert.equal(c.window.RFModalActive(),true);
  dialogs[0].hidden=true;assert.equal(c.window.RFModalActive(),false);
  let keyHandler;const undo=fs.readFileSync(path.join(front,'workspace_undo.js'),'utf8');
  const start=undo.indexOf("  document.addEventListener('keydown'");
  vm.runInNewContext(undo.slice(start,undo.indexOf('\n  });',start)+6),{document:{addEventListener:(_name,fn)=>keyHandler=fn},window:{RFModalActive:()=>true},undo:()=>{throw Error('undo crossed modal');}});
  keyHandler({key:'z',ctrlKey:true,defaultPrevented:false});
});

test('registered workspace drafts participate without being erased by task setup',()=>{
  const c={window:{},document:{querySelectorAll:()=>[]}};
  vm.runInNewContext(section('  const W =','  const stages ='),c);
  const unregister=c.window.WorkflowControls.registerDraft('grade',{dirty:()=>true});
  assert.equal(vm.runInNewContext('extraDirty()',c),true);unregister();assert.equal(vm.runInNewContext('extraDirty()',c),false);
});

test('workflow and training prose has genuine Chinese and translated note/maybe labels',()=>{
  const rows=[],RFLang={register:r=>rows.push(...r)};for(const name of ['workflow_locale.js','pilot_screening_locale.js'])vm.runInNewContext(fs.readFileSync(path.join(front,name),'utf8'),{window:{RFLang},RFLang});
  assert.equal(rows.some(row=>row[0]===row[1]&&row[0].length>=4),false);
  assert.equal(rows.find(row=>row.aliases?.includes('pilot.note'))[1],'Notes');
  assert.equal(rows.find(row=>row.aliases?.includes('pilot.decision_maybe'))[1],'Not Sure Now');
  assert.equal(rows.find(row=>row[0]==='页码')[1],'Page');
});

test('curated wizard pages 1500 records and uses selected count at final step',async()=>{
  class Node{constructor(tag,attrs={}){this.tag=tag;Object.assign(this,attrs);this.value=attrs.value||'';this.children=[];}append(...nodes){this.children.push(...nodes.flat().filter(Boolean));}replaceChildren(...nodes){this.children=[];this.append(...nodes);}reportValidity(){return true;}}
  const make=(tag,attrs={},...kids)=>{const node=new Node(tag,attrs);node.append(...kids);if(tag==='select')node.value=node.children[0]?.value;return node;};
  const controls=[];const c={el:make,tr:(tag,key,_p,attrs)=>make(tag,{...attrs,text:key}),literal:(tag,text)=>make(tag,{text}),field:(key,input)=>make('label',{text:key},input),row:(...nodes)=>make('div',{},...nodes),T:(key,p)=>key+JSON.stringify(p||{}),button:(key,click)=>{const node=make('button',{text:key,click});controls.push(node);return node;},view:()=>{},loadGold:()=>{}};
  const pilot=fs.readFileSync(path.join(front,'pilot_screening.js'),'utf8');
  vm.runInNewContext(pilot.slice(pilot.indexOf('  async function wizard('),pilot.indexOf('  function articleCard(')),c);
  let submitted;const session={body:make('div'),notice:make('p'),live:true,run:fn=>fn(),catalogue:async()=>Array.from({length:1500},(_,i)=>({zotero_key:String(i),title:'Record '+i})),call:async(_url,_method,payload)=>{submitted=payload;return {set:{id:1}};}};
  await c.wizard(session);
  const sampling=session.body.children[1].children[0].children[0];sampling.value='curated';
  const next=controls.filter(node=>node.text==='pilot.next').at(-1);await next.click();await next.click();
  const pool=session.body.children[3].children[2];assert.equal(pool.children.length,50);
  for(const label of pool.children.slice(0,20)){label.children[0].checked=true;label.children[0].onchange();}
  await next.click();assert.match(session.body.children[0].textContent,/20/);
  await controls.find(node=>node.text==='pilot.create').click();assert.equal(submitted.size,20);assert.equal(submitted.curated_keys.length,20);
});

test('sampling state restores per account/task/screener without crossing account ownership',()=>{
  const src=fs.readFileSync(path.join(front,'al_stop_panel.js'),'utf8'),storage=new Map();
  const c={S:{user:{id:'a'},task:{task_id:'task'},screener:'member',page:'home'},location:{origin:'test'},localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},taskId:()=>c.S.task.task_id,spot:{keys:['article'],look:.5},result:null,doneLook:-1,data:null,version:0,trackedTask:null,anchor:{},setOpen:()=>{}};
  const start=src.indexOf('    const spotScope=');vm.runInNewContext(src.slice(start,src.indexOf('    /* ---- 桥接',start)),c);
  vm.runInNewContext('trackedScope=spotScope();persistSpot();',c);
  c.spot=null;vm.runInNewContext("trackedScope='';onTask();",c);assert.equal(c.spot.keys[0],'article');
  c.S.user.id='b';c.onTask();assert.equal(c.spot,null);
  c.S.user.id='a';c.onTask();assert.equal(c.spot.keys[0],'article');
});

test('trace deletion awaits application confirmation and cancels safely after context changes',async()=>{
  const src=fs.readFileSync(path.join(front,'research_trace.js'),'utf8');const start=src.indexOf(' async function clearHistory()'),end=src.indexOf('\n }',start)+3;
  let scope='task-one',answer,writes=0;
  const c={state:{clearing:false,ticket:1,settings:{version:1}},actorScope:()=>scope,T:x=>x,RFConfirm:()=>new Promise(resolve=>answer=resolve),crypto:{randomUUID:()=> 'operation'},base:()=>'/task-one',api:async()=>{writes++;return {status:'complete'};},load:async()=>{},error:e=>{throw e;}};
  vm.runInNewContext(src.slice(start,end),c);
  let pending=c.clearHistory();await c.clearHistory();assert.equal(writes,0);answer(false);await pending;assert.equal(writes,0);assert.equal(c.state.clearing,false);
  pending=c.clearHistory();scope='task-two';c.state={clearing:false,ticket:2};answer(true);await pending;assert.equal(writes,0);
  pending=c.clearHistory();answer(true);await pending;assert.equal(writes,1);
});

test('update status uses nonblocking notifications for current, unsupported and failed checks',async()=>{
  const src=fs.readFileSync(path.join(front,'update_check.js'),'utf8');const start=src.indexOf('  async function check('),end=src.indexOf("  notice.addEventListener",start);
  const messages=[],control={setAttribute(){},removeAttribute(){},querySelector:()=>({}),hidden:true};let result={supported:false};
  const c={notice:control,checkButton:control,latest:null,api:async()=>{if(result instanceof Error)throw result;return result;},T:x=>x,toast:(message,type)=>messages.push([message,type]),showDialog:()=>{throw Error('unexpected update dialog');}};
  vm.runInNewContext(src.slice(start,end),c);await c.check();result={supported:true,has_update:false};await c.check();result=Error('offline');await c.check();assert.equal(messages.length,3);assert.equal(messages[2][1],'warn');assert.equal(control.disabled,false);
});
