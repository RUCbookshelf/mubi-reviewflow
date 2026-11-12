'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const repo = process.env.RF_CORE_REPO || path.join(__dirname,'../..');
const html = fs.readFileSync(path.join(repo,'custom_frontend/index.html'),'utf8');
const between = (start,end,from=0) => { const at=html.indexOf(start,from); assert.ok(at>=0,start);const to=html.indexOf(end,at);assert.ok(to>at,end);return html.slice(at,to); };

test('screen and fulltext numeric shortcuts cannot submit beneath a native or app modal',()=>{
  const code=between("document.addEventListener('keydown', e => {",'/* ---------- 登录',html.indexOf('/* ---------- 全局键盘快捷键'));
  let handler,native=false,modal=false;const writes=[];
  const context={window:{RFModalActive:()=>modal},document:{addEventListener:(_,fn)=>handler=fn,querySelector:()=>native?{}:null},S:{page:'screen'},$ :()=>({style:{}}),screenDecide:value=>writes.push(['screen',value]),fullDecide:value=>writes.push(['fulltext',value])};
  vm.runInNewContext(code,context);
  for(const page of ['screen','fulltext']) for(const key of ['1','3']) {
    context.S.page=page;const event={key,target:{tagName:'BODY'},preventDefault(){}};
    modal=true;native=false;handler(event);modal=false;native=true;handler(event);
    assert.equal(writes.length,0);
  }
  native=modal=false;context.S.page='screen';handler({key:'1',target:{tagName:'BODY'}});
  assert.deepEqual(writes,[['screen','include']]);
});

function confirmHarness() {
  const dialogs=[]; let focus;
  class Element {
    constructor(tag,attrs={},...children){this.tag=tag;Object.assign(this,attrs);this.children=children;this.events={};this.isConnected=true;this.classList={add(){}};}
    append(...children){this.children.push(...children)} prepend(...children){this.children.unshift(...children)}
    addEventListener(name,fn){this.events[name]=fn} showModal(){this.open=true} close(){this.open=false}
    remove(){this.isConnected=false} focus(){focus=this} select(){}
    querySelectorAll(){return this.children.flatMap(child=>child.tag==='div'?child.children:child.tag==='input'?[child]:[])}
    contains(node){return this.querySelectorAll().includes(node)}
  }
  const opener=new Element('button');focus=opener;
  const document={get activeElement(){return focus},body:{append:dialog=>dialogs.push(dialog)}};
  const context={document,HTMLElement:Element,el:(...args)=>new Element(...args),T:value=>value,rfWarningIcon:()=>new Element('svg'),RF_CONFIRM_FOCUSABLE:'button,input'};
  vm.runInNewContext(between('function RFConfirm(options)','window.RFConfirm = RFConfirm;'),context);
  return {context,dialogs,opener,getFocus:()=>focus};
}

test('input confirmation submits Enter only from the input; Cancel Enter retains cancel semantics and focus',async()=>{
  const x=confirmHarness();let pending=x.context.RFConfirm({input:true,value:'  new task  ',danger:false});
  let dialog=x.dialogs.at(-1),input=dialog.children.find(child=>child.tag==='input'),prevented=false;
  dialog.events.keydown({target:input,key:'Enter',preventDefault(){prevented=true}});
  assert.equal(await pending,'new task');assert.equal(prevented,true);assert.equal(x.getFocus(),x.opener);assert.equal(dialog.isConnected,false);
  pending=x.context.RFConfirm({input:true,value:'must not submit',danger:false});dialog=x.dialogs.at(-1);
  const cancel=dialog.children.at(-1).children[0];cancel.focus();prevented=false;
  dialog.events.keydown({target:cancel,key:'Enter',preventDefault(){prevented=true}});
  assert.equal(prevented,false,'native Enter must reach the cancel button click');assert.equal(dialog.open,true);
  cancel.onclick();assert.equal(await pending,false);assert.equal(x.getFocus(),x.opener);
});

function animationHarness({stale=false,fail=false}={}) {
  const classes=new Set(['out']),calls={advance:0,error:0,render:0};let callback;
  const card={classList:{remove:value=>classes.delete(value)}};
  const context={screenAnimationVersion:7,animation:7,screenSaving:{},ticket:1,screenRequestVersion:stale?2:1,taskId:'task',screener:'A',focusId:null,
    S:{task:{task_id:'task'},screener:'A',al:{mode:'ai'},page:'screen'},window:{RFModalActive:()=>false},
    WorkflowControls:{renderAllowed:()=>true,error:()=>calls.error++},advanceALQueue:async()=>{calls.advance++;if(fail)throw Error('synthetic');},renderScreen:()=>calls.render++,nextUndecided:()=>1,
    d:{progress:{total:1,screened:1,include:1}},T:value=>value,document:{getElementById:()=>null},$ :selector=>selector==='#scrCard'?card:{style:{}},setTimeout:fn=>callback=fn};
  vm.createContext(context);
  vm.runInContext(between('function clearScreenAnimation()', 'function workspacePositionKey'),context);
  const at=html.indexOf('const animation = ++screenAnimationVersion;',html.indexOf('async function screenDecide'));
  vm.runInContext(between('setTimeout(async () => {','}, 220);',at)+'}, 220);',context);
  return {context,calls,classes,run:()=>callback()};
}

test('screen animation removes out on stale scope or failed advancement',async()=>{
  for(const options of [{stale:true},{fail:true}]){
    const x=animationHarness(options);await x.run();assert.equal(x.classes.has('out'),false);assert.equal(x.calls.error,options.fail?1:0);
    assert.equal(x.calls.render,options.fail?1:0,'failed advancement must rebuild disabled controls or expose the render retry state');
  }
});

test('clearing a screen animation invalidates the old callback and cannot clear a newer animation',async()=>{
  const x=animationHarness();x.context.clearScreenAnimation();assert.equal(x.classes.has('out'),false);assert.equal(x.context.screenSaving,null);
  x.classes.add('out');await x.run();assert.equal(x.calls.advance,0);assert.equal(x.classes.has('out'),true);
});

test('workspace positions isolate account, task, screener, source, order and coding context',()=>{
  let source='personal',order='import',coding='personal';
  const view={scrollTop:120},abstract={scrollTop:35};
  const context={workspacePositions:new Map(),S:{user:{username:'user'},task:{task_id:'task'},screener:'A',page:'screen',articles:[{zotero_key:'paper'}],scrIdx:0},
    window:{WorkflowControls:{selectedSource:()=>source,order:()=>order},WorkflowEvidence:{context:()=>coding}},document:{activeElement:null,getElementById:()=>null},$ :selector=>selector==='.view'?view:abstract};
  vm.createContext(context);vm.runInContext(between('function workspacePositionKey(', 'async function go('),context);
  const original=context.workspacePositionKey();context.rememberWorkspacePosition();view.scrollTop=0;abstract.scrollTop=0;context.restoreWorkspacePosition('screen');assert.equal(view.scrollTop,120);assert.equal(abstract.scrollTop,35);
  source='canonical';assert.notEqual(context.workspacePositionKey(),original);view.scrollTop=9;context.restoreWorkspacePosition('screen');assert.equal(view.scrollTop,9);
  source='personal';order='ai';assert.notEqual(context.workspacePositionKey(),original);order='import';context.S.screener='B';assert.notEqual(context.workspacePositionKey(),original);
  context.S.screener='A';context.S.task.task_id='other';assert.notEqual(context.workspacePositionKey(),original);context.S.task.task_id='task';context.S.user.username='other';assert.notEqual(context.workspacePositionKey(),original);
  const personal=context.workspacePositionKey('bench');coding='coding_submission_evidence';assert.notEqual(context.workspacePositionKey('bench'),personal);
});

test('toast container remains transparent to pointers while actionable toast contents receive clicks',()=>{
  assert.match(html,/#toasts\{[^}]*pointer-events:none/);
  assert.match(html,/\.toast\{[^}]*pointer-events:auto/);
});

test('fulltext and coding loaders use the target source memory, then prefer live position only within the same source',async()=>{
  for(const kind of ['fulltext','bench']) {
    const queue=[{zotero_key:'first'},{zotero_key:'old-source-current'},{zotero_key:'target-source-memory'}];
    const S={task:{task_id:'task'},taskVersion:1,screener:'A',ftQueue:[queue[1]],ftIdx:0,fulltextLoadedTask:'task',fulltextPositionScope:'old-source',cbQueue:[queue[1]],cbIdx:0,codingPositionScope:'old-source'};
    const control={renderAllowed:()=>true,queue:async()=>({queue})},evidence={queue:async()=>({queue}),context:()=> 'personal'};
    const context={S,window:{WorkflowControls:control},WorkflowControls:control,WorkflowEvidence:evidence,fulltextLoadVersion:0,benchLoadVersion:0,benchRenderVersion:0,
      workspacePositionKey:()=> 'target-source',workspacePositions:new Map([['target-source',{key:'target-source-memory'}]]),$ :()=>({style:{},append(){},replaceChildren(){}}),
      api:async()=>({decisions:[]}),taskApi:()=>'/api/tasks/task',scrQ:()=>'',refreshScreeningState:async()=>{},renderFtDoc:async()=>{},openFtPdf:async()=>{},loadDims:async()=>{},renderBench:async()=>{},applyCodingProgress(){},toast(){},T:value=>value};
    vm.createContext(context);
    const source=kind==='fulltext'?between('async function loadFulltext()', '\nS.ftDec = {};'):between('async function loadBench()', 'let benchRenderVersion =');
    vm.runInContext(source,context);
    const load=kind==='fulltext'?context.loadFulltext:context.loadBench;
    const index=kind==='fulltext'?'ftIdx':'cbIdx';
    await load();assert.equal(S[index],2,kind+' must not restore the previous source current item');
    S[index]=0;await load();assert.equal(S[index],0,kind+' keeps latest position during a same-source reload');
  }
});

test('recovery commands quote the entered username as a literal on every desktop platform',()=>{
  const nodes=Object.fromEntries(['authResetUser','authResetSteps','authResetCommand','authResetCopy','authResetCopyStatus'].map(id=>['#'+id,{value:"synthetic'$(echo inert)"}]));
  const context={$:id=>nodes[id],navigator:{userAgentData:{platform:'Windows'}},T:value=>value};
  vm.createContext(context);vm.runInContext(between('function updateResetCommand()',"$('#authResetUser').addEventListener"),context);
  context.updateResetCommand();assert.ok(nodes['#authResetCommand'].textContent.endsWith("'synthetic''$(echo inert)'"));
  for(const platform of ['Linux','macOS']){context.navigator.userAgentData.platform=platform;context.updateResetCommand();assert.ok(nodes['#authResetCommand'].textContent.endsWith("'synthetic'\"'\"'$(echo inert)'"));}
  nodes['#authResetUser'].value='';context.updateResetCommand();assert.equal(nodes['#authResetCommand'].textContent,'');assert.equal(nodes['#authResetCopy'].disabled,true);
});

test('remembered-session loading preserves the submit label element needed by logout and reset mode',async()=>{
  const start=html.lastIndexOf('AUTH_TOKEN = restoreToken();');
  const branch=between('if (AUTH_TOKEN) {','/* r8 W5',start);
  for(const fail of [false,true]){
    let labelAttached=true;
    const label={textContent:'登录'},form={setAttribute(){},removeAttribute(){}},error={textContent:''};
    const button={};Object.defineProperty(button,'textContent',{get:()=>label.textContent,set:value=>{labelAttached=false;label.textContent=value;}});
    const nodes={'#authForm':form,'#authSubmit':button,'#submitText':label,'#authErr':error};
    const context={AUTH_TOKEN:'synthetic-token',T:value=>value,$:selector=>selector==='#submitText'&&!labelAttached?null:nodes[selector],clearAuthMessage:()=>error.textContent='',enterApp:async()=>{if(fail)throw {status:401};}};
    vm.createContext(context);await vm.runInContext('(async()=>{'+branch+'})()',context);
    assert.equal(labelAttached,true,'setting button.textContent destroys the nested submit label and icon');
    assert.equal(label.textContent,'登录');assert.equal(form.inert,false);
  }
});

test('remember-me explanation shares the credentials fieldset and is visible only in login mode',()=>{
  const credentials=between('<fieldset class="auth-fields" id="authCredentials"','</fieldset>');
  assert.ok(credentials.includes('id="authRememberNote"'),'the login explanation must not live in the hidden reset fieldset');
  const nodes=new Map();
  const context={T:value=>value,runtimeMode:'desktop',authMode:'login',clearAuthMessage(){},updateResetCommand(){},setPasswordVisibility(){},
    $:selector=>{if(!nodes.has(selector))nodes.set(selector,{dataset:{},value:'',type:'password'});return nodes.get(selector);}};
  vm.createContext(context);vm.runInContext(between('function setAuthMode(', 'function updateResetCommand()'),context);
  for(const mode of ['login','reset','register','login']){
    context.setAuthMode(mode);
    const visible=!nodes.get('#authRememberNote').hidden&&!nodes.get('#authCredentials').hidden;
    assert.equal(visible,mode==='login',mode+' must show only its own explanation');
  }
});
