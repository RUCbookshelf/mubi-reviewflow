/* E-11/E-16：执行真实保存路径，验证笔记保存不重建 PDF 或重取已存图片。 */
'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const original=fs.readFileSync(path.join(__dirname,'../../custom_frontend/workflow_evidence.js'),'utf8');
function fixture(){
  const notes=[{dataset:{dim:'1'},value:'Initial note'}],values=[{dataset:{dim:'1'},value:'value',type:'text'}];
  const savedImage={id:1,url:'/image/1',objectURL:'blob:existing'};
  const elements={cbForms:{addEventListener(){}},cbSave:{disabled:false},cbStatus:{textContent:''}};
  const calls=[],revoked=[],events=[];
  const sandbox={
    WorkflowControls:{scope:()=> 'task|owner',refreshing:()=>false,version:()=>1,settings:()=>({}),updateProgress:async()=>{},error:error=>{throw error;}},
    WorkspaceUndo:{isBusy:()=>false,beginSave(){},endSave(){}},
    S:{cbQueue:[{zotero_key:'R1'}],cbIdx:0,codingFormReady:true},
    $:selector=>elements[selector.slice(1)],
    $$:selector=>selector==='#cbForms .wf-note'?notes:selector==='#cbForms .cb-v'?values:[],
    T:text=>text,el:()=>({}),toast:()=>{},taskApi:()=>'/api/tasks/task',scrQ:()=>'',applyCodingProgress:()=>{},
    document:{addEventListener(){},dispatchEvent:event=>events.push(event.type)},
    CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},
    URL:{revokeObjectURL:url=>revoked.push(url)},
    api:async(url,options)=>{calls.push({url,options});return {complete:true,evidence:{'1':{text:notes[0].value,images:[{id:1,url:'/image/1'}]}}};}
  };
  sandbox.window=sandbox;
  // 仅测试装配注入状态；保存动作和 dirty 判断均执行产品代码。
  const source=original.replace('  E.save=async()=>{',`  E.__init=item=>{current=item;images.set('1',[item.image]);remember('1',[item.image]);baseline=signature();};E.__images=()=>images.get('1');\n  E.save=async()=>{`);
  vm.runInNewContext(source,sandbox);
  const E=sandbox.WorkflowEvidence;E.__init({article:{zotero_key:'R1'},writable:true,policy_version:1,image:savedImage});
  let renders=0;E.render=async()=>{renders++;};
  return {sandbox,E,notes,values,calls,revoked,savedImage,events,renders:()=>renders};
}
test('note-only save keeps form/PDF instance and existing image object URL, including repeat save',async()=>{
  const f=fixture();f.notes[0].value='Updated note';assert.equal(f.E.dirty(),true);
  assert.equal(await f.E.save(),true);
  assert.equal(f.E.dirty(),false);
  assert.equal(f.renders(),0);
  assert.equal(f.E.__images()[0],f.savedImage);
  assert.equal(f.E.__images()[0].objectURL,'blob:existing');
  assert.equal(f.revoked.length,0);
  assert.equal(f.calls.length,1);assert.equal(f.calls[0].options.method,'POST');
  assert.equal(f.calls[0].options.body.notes['1'],'Updated note');
  assert.deepEqual(f.events,['reviewflow:coding-form-saved']);
  f.notes[0].value='Updated again';assert.equal(await f.E.save(),true);
  assert.equal(f.renders(),0);assert.equal(f.calls.length,2);assert.equal(f.revoked.length,0);
});
test('failure retains evidence dirty state and does not replace PDF',async()=>{
  const f=fixture();f.notes[0].value='Unsaved';let caught;
  f.sandbox.WorkflowControls.error=error=>{caught=error.message;};f.sandbox.api=async()=>{throw new Error('offline');};
  assert.equal(await f.E.save(),false);assert.equal(f.E.dirty(),true);assert.equal(caught,'offline');
  assert.equal(f.renders(),0);assert.equal(f.E.__images()[0].objectURL,'blob:existing');
});
test('shared evidence mode is exposed through pressed state even with cached discovery',()=>{
  const f=fixture();
  const makeNode=(tag,attrs={})=>({tag,attrs,dataset:{},children:[],classList:{toggle(name,value){this[name]=value;}},setAttribute(name,value){this.attrs[name]=value;},append(...children){this.children.push(...children);},replaceChildren(){this.children=[];},querySelectorAll(){return this.children.filter(child=>child.dataset.evidenceMode);}});
  const host=makeNode('div');f.sandbox.$=()=>host;f.sandbox.el=makeNode;
  f.sandbox.WorkflowControls.settings=()=>({task_kind:'team'});
  f.E.discovery({available:true});
  let buttons=host.querySelectorAll('[data-evidence-mode]');
  assert.equal(buttons.length,2);assert.equal(buttons[0].attrs['aria-pressed'],'true');assert.equal(buttons[1].attrs['aria-pressed'],'false');
  f.E.setContext('coding_submission_evidence');f.E.discovery({available:true});
  buttons=host.querySelectorAll('[data-evidence-mode]');
  assert.equal(buttons[0].attrs['aria-pressed'],'false');assert.equal(buttons[1].attrs['aria-pressed'],'true');
  assert.equal(buttons[1].classList['b-inc'],true);assert.equal(f.E.context(),'coding_submission_evidence');
});
for(const mode of ['personal','coding_submission_evidence'])test(`empty ${mode} schema renders clean without a lingering loading state`,async()=>{
  const makeNode=(tag,attrs={})=>({tag,attrs,dataset:{},textContent:attrs.text||'',children:[],classList:{toggle(){}},setAttribute(name,value){this.attrs[name]=value;},append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},querySelectorAll(){return[];},addEventListener(){}});
  const elements=Object.fromEntries(['cbForms','cbSave','cbLabel','cbTitle','cbStatus','wf-coding-entry'].map(id=>[id,makeNode('div')]));
  const item={article:{zotero_key:'R1'},writable:true,policy_version:1,dimensions:[]};
  const requests=[];
  const sandbox={
    WorkflowControls:{scope:()=> 'task|owner',renderAllowed:()=>true,refreshing:()=>false,version:()=>1,settings:()=>({task_kind:'team'}),item:async()=>item},
    S:{cbQueue:[{zotero_key:'R1'}],cbIdx:0,codingFormReady:true,taskVersion:1,screener:'owner',dims:[]},
    $:selector=>elements[selector.slice(1)]||null,$$:()=>[],
    T:text=>text,el:makeNode,document:{addEventListener(){}},URL:{revokeObjectURL(){}},openBenchPdf(){},
    api:async(url,options)=>{requests.push({url,options});return item;}
  };
  sandbox.window=sandbox;vm.runInNewContext(original,sandbox);
  const E=sandbox.WorkflowEvidence;E.setContext(mode);
  E.discovery({available:true,read_url_template:'/shared/{zotero_key}'});
  await E.render();
  assert.equal(E.dirty(),false,'an empty schema has no editable draft');
  assert.equal(elements.cbStatus.textContent,'','loading feedback is cleared');
  assert.equal(elements.cbSave.disabled,true);
  assert.equal(sandbox.S.codingFormReady,false,'empty schema cannot be submitted');
  assert.ok(elements.cbForms.children.some(child=>child.textContent==='polish.open_scheme'));
  assert.equal(await E.save(),false);
  assert.equal(requests.filter(request=>request.options?.method==='POST').length,0);
  await E.render();assert.equal(E.dirty(),false,'returning to the empty schema stays clean');
});
