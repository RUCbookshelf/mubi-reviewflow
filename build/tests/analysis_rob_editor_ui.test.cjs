'use strict';
// E-13 / E-15 / E-22：执行真实编辑器模块，覆盖回填、范围变化、保存及本地草稿边界。
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm');
const {readFileSync}=require('node:fs'),{join}=require('node:path');
const source=name=>readFileSync(join(__dirname,'../../custom_frontend',name),'utf8');
class Element {
  constructor(tag,attrs={}) {this.tagName=tag;this.children=[];this.dataset={};this.listeners={};this.attrs={};this.value='';Object.entries(attrs).forEach(([k,v])=>{if(k==='text')this.textContent=v;else if(k.startsWith('data-'))this.dataset[k.slice(5)]=v;else this[k]=v;});}
  set textContent(v){this.text=String(v);this.children=[];} get textContent(){return (this.text||'')+this.children.map(c=>c.textContent||'').join('');}
  append(...nodes){for(const node of nodes.flat().filter(Boolean)){this.children.push(node);node.parent=this;}}
  replaceChildren(...nodes){this.children=[];this.text='';this.append(...nodes);}
  after(...nodes){const index=this.parent.children.indexOf(this);this.parent.children.splice(index+1,0,...nodes);nodes.forEach(n=>n.parent=this.parent);}
  before(...nodes){const index=this.parent.children.indexOf(this);this.parent.children.splice(index,0,...nodes);nodes.forEach(n=>n.parent=this.parent);}
  matches(selector){if(selector.startsWith('#'))return this.id===selector.slice(1);if(selector==='[data-domain]')return Boolean(this.dataset.domain);if(selector==='[aria-invalid]')return this.attrs['aria-invalid'];return this.tagName===selector;}
  querySelectorAll(selector){const parts=selector.split(' ');if(parts.length>1)return this.querySelector(parts[0])?.querySelectorAll(parts.slice(1).join(' '))||[];return this.children.flatMap(n=>[...(n.matches(selector)?[n]:[]),...n.querySelectorAll(selector)]);}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  closest(selector){for(let n=this;n;n=n.parent)if(n.matches(selector))return n;return null;}
  addEventListener(type,fn){(this.listeners[type]||=[]).push(fn);} async emit(type){for(const fn of this.listeners[type]||[])await fn({target:this});}
  setAttribute(k,v){this.attrs[k]=v;} removeAttribute(k){delete this.attrs[k];} focus(){this.focused=true;}
}
const fields=['anRobStudy','anRobComparison','anRobOutcome','anRobTimepoint','anRobFramework','anRobSource','anRobSourceLocator','anRobOverall','anRobOverallWhy'];
const saved={study_id:'S1',comparison:'A vs B',outcome:'Pain',timepoint:'4 weeks',framework:'RoB 2',source_key:'R1',source_locator:'p. 7',overall:'low',overall_rationale:'All checked',reviewer:'alice',domains:{randomization:{judgement:'low',rationale:'Random sequence'},missing:{judgement:'low',rationale:'Complete follow-up'}}};
function mount(){
  const body=new Element('body'),host=new Element('div',{id:'anJumpRob'}),heading=new Element('b');body.append(host);host.append(heading);
  const elements=new Map(fields.map(id=>[id,new Element('input',{id})]));for(const el of elements.values())host.append(el);
  const domainHost=new Element('div',{id:'anRobDomains'}),save=new Element('button',{id:'anRobSave'});host.append(domainHost,save);body.append(new Element('pre',{id:'anDtaResult'}));
  const dom=id=>body.querySelector('#'+id),rows=[],storage=new Map(),calls=[],prompts=[],drafts=[];
  let consent=false;
  const sandbox={S:{task:{task_id:'T1'},user:{user_id:1,username:'alice'}},document:{getElementById:dom,addEventListener(){}},
    el:(tag,attrs,...nodes)=>{const e=new Element(tag,attrs);e.append(...nodes);return e;},
    T:(key,...args)=>{let text=rows.find(row=>row.aliases?.includes(key)||row.includes(key))?.[1]||key;args.forEach((arg,i)=>text=text.replace('{'+i+'}',arg));return text;},
    RFLang:{register:r=>rows.push(...r)},taskApi:()=>'/tasks/'+sandbox.S.task.task_id,
    api:async(url,options)=>{calls.push({url,options});return options?{assessments:[saved]}:{assessments:[saved]};},toast(){},
    RFConfirm:async opts=>{prompts.push(opts);return consent;},anReportOptions(){},
    renderRobDomains:()=>{domainHost.replaceChildren();for(const name of ['randomization','missing']){const row=new Element('div',{'data-domain':name});row.append(new Element('select'),new Element('input'));domainHost.append(row);}},
    sessionStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    WorkflowControls:{registerDraft:(name,entry)=>drafts.push(entry)}
  };
  sandbox.window=sandbox;vm.createContext(sandbox);vm.runInContext(source('analysis_rob_editor_locale.js'),sandbox);vm.runInContext(source('analysis_rob_editor.js'),sandbox);
  const edit=async()=>{sandbox.AnalysisRobEditor.loaded([saved]);const pick=dom('anRobSaved');pick.value=JSON.stringify(['S1','A vs B','Pain','4 weeks','RoB 2']);await pick.emit('change');};
  return {sandbox,host,dom,calls,prompts,drafts,storage,edit,setConsent:value=>consent=value};
}
test('E-15 loads all saved fields and revises the same backend identity',async()=>{
  const e=mount();await e.edit();assert.equal(e.dom('anRobSourceLocator').value,'p. 7');assert.equal(e.dom('anRobOverallWhy').value,'All checked');
  assert.equal(e.dom('anRobDomains').children[0].querySelector('input').value,'Random sequence');
  e.dom('anRobOverallWhy').value='Reviewed again';await e.host.emit('input');
  assert.equal(e.drafts[0].dirty(),true);assert.equal(await e.sandbox.AnalysisRobEditor.save(),true);
  const put=e.calls.find(c=>c.options);assert.equal(put.options.body.study_id,'S1');assert.equal(put.options.body.overall_rationale,'Reviewed again');assert.equal(e.prompts.length,0);
  assert.equal(e.storage.size,0);assert.equal(e.drafts[0].dirty(),false);
});
test('E-15 changed scope requires explicit confirmation and leaves original record identity intact',async()=>{
  const e=mount();await e.edit();e.dom('anRobTimepoint').value='8 weeks';await e.host.emit('input');
  assert.equal(await e.sandbox.AnalysisRobEditor.save(),false);assert.equal(e.calls.length,0);assert.match(e.prompts[0].message,/creates another record/);
  e.setConsent(true);assert.equal(await e.sandbox.AnalysisRobEditor.save(),true);assert.equal(e.calls[0].options.body.timepoint,'8 weeks');assert.equal(saved.timepoint,'4 weeks');
});
test('E-22 missing domain rationale is localized and focuses the offending input before a request',async()=>{
  const e=mount();await e.edit();const input=e.dom('anRobDomains').children[0].querySelector('input');input.value='';
  assert.equal(await e.sandbox.AnalysisRobEditor.save(),false);assert.equal(e.calls.length,0);assert.equal(input.focused,true);assert.match(e.dom('anRobStatus').textContent,/Enter the rationale for randomization/);
});
test('E-15 session draft survives reload within scope and stays separate from another task',async()=>{
  const e=mount();await e.edit();e.dom('anRobOverallWhy').value='Draft note';await e.host.emit('input');
  assert.equal(e.storage.size,1);e.sandbox.AnalysisRobEditor.loaded([saved]);assert.equal(e.dom('anRobOverallWhy').value,'Draft note');
  e.sandbox.S.task.task_id='T2';e.sandbox.AnalysisRobEditor.loaded([]);assert.equal(e.dom('anRobOverallWhy').value,'');
  e.sandbox.S.task.task_id='T1';e.sandbox.AnalysisRobEditor.loaded([saved]);assert.equal(e.dom('anRobOverallWhy').value,'Draft note');
});
test('E-13 renders readable RoB evidence and DTA source records without interpreting literal data as HTML',async()=>{
  const e=mount();e.sandbox.AnalysisRobEditor.loaded([{...saved,overall_rationale:'<img onerror=bad>'}],[{study_id:'S1',index_test:'Test',tp:10,fp:2,fn:1,tn:8,source_key:'R1',source_locator:'Table 2'}]);
  assert.match(e.dom('anRobRecords').textContent,/<img onerror=bad>/);assert.match(e.dom('anDtaRecords').textContent,/TP: 10/);assert.match(e.dom('anDtaRecords').textContent,/Table 2/);
});

test('E-04 reverse appraisal link honors the guard and opens only the exact source report',async()=>{
  const e=mount();await e.edit();let allow=false,opened=0,rendered=0;
  e.sandbox.go=async page=>{if(!allow)return false;e.sandbox.S.page=page;return true;};
  e.sandbox.S.cbQueue=[{zotero_key:'OTHER'},{zotero_key:'R1'}];e.sandbox.S.cbIdx=0;
  e.sandbox.WorkflowEvidence={context:()=> 'personal'};
  e.sandbox.renderBench=async()=>{rendered++;};
  e.sandbox.AppraisalWorkspace={open:async()=>{opened++;}};
  await e.dom('anRobOpenAppraisal').emit('click');assert.equal(opened,0);assert.equal(e.sandbox.S.cbIdx,0);
  allow=true;await e.dom('anRobOpenAppraisal').emit('click');assert.equal(opened,1);assert.equal(rendered,1);assert.equal(e.sandbox.S.cbIdx,1);
  e.sandbox.S.cbQueue=[{zotero_key:'OTHER'}];e.sandbox.S.cbIdx=0;
  await e.dom('anRobOpenAppraisal').emit('click');assert.equal(opened,1,'missing report must not open another article');assert.equal(rendered,1);
});
