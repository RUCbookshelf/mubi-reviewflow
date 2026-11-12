'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm');
const {readFileSync}=require('node:fs'),{join}=require('node:path');
const read=name=>readFileSync(join(__dirname,'../../custom_frontend',name),'utf8');
class Element {
  constructor(tag,text,attrs={}){this.tag=tag;this.textContent=text||'';this.children=[];this.listeners={};Object.assign(this,attrs);}
  append(...nodes){this.children.push(...nodes.flat().filter(Boolean));}
  addEventListener(event,fn){this.listeners[event]=fn;}
  find(predicate){for(const child of this.children){if(predicate(child))return child;const nested=child.find?.(predicate);if(nested)return nested;}}
  all(predicate){return this.children.flatMap(child=>[...(predicate(child)?[child]:[]),...(child.all?.(predicate)||[])]);}
}
function mount(judgment){
 const body=new Element('div'),dialog={open:true,close(){this.open=false;}},calls=[],rows=[];
 const node=(tag,text,attrs,...nodes)=>{const e=new Element(tag,text,attrs);e.append(...nodes);return e;};
 const sandbox={components:['rob','inconsistency','imprecision','publication_bias'],levels:['none','serious','very_serious'],grades:['high','moderate','low','very_low'],
  popup:()=>({body,dialog}),node,raw:node,tr:(tag,key,attrs)=>node(tag,key,attrs),
  select:(items,value)=>node('select',null,{value}),controlsFor:(prefix,opts,value)=>node('select',null,{value}),
  field:(key,input)=>node('label',key,{},input),fields:(...nodes)=>node('div',null,{},nodes),
  button:(key,fn)=>node('button',key,{onclick:fn}),statusLine:()=>node('p'),suggestionsBlock:()=>node('dl'),
  resultMessage:error=>error.message,componentLabel:key=>key,
  T:(key)=>rows.find(row=>row.aliases?.includes(key))?.[1]||key,RFLang:{register:items=>rows.push(...items)},
  api:async(url,options)=>calls.push({url,options})};
 vm.createContext(sandbox);vm.runInContext(read('table_wizards_polish_locale.js'),sandbox);
 const source=read('table_wizards.js');vm.runInContext(source.slice(source.indexOf('  function openJudgment('),source.indexOf('  function sofPreview(')),sandbox);
 const wizard={path:'/tables',valid:()=>true,refresh:async()=>{}};
 sandbox.openJudgment({label:'Pain',comparison:'A vs B',outcome:'Pain',timepoint:'4 weeks',measure:'MD',judgment},{model:'fixed',ci_method:'normal',initial_design:'rct'},wizard);
 return {body,calls,source:body.all(el=>el.tag==='select')[0],save:body.find(el=>el.tag==='button'&&el.textContent==='tables.sof.judgment.submit')};
}
const adopted={source:'adopted',note:'Saved note',components:{rob:'none',inconsistency:'none',imprecision:'serious',publication_bias:'none'},overall:'moderate'};
test('E-07 reopens adopted judgment with adopted source and writes adoption payload',async()=>{
 const e=mount(adopted);assert.equal(e.source.value,'adopt');await e.save.onclick();assert.equal(e.calls.length,1);assert.equal(e.calls[0].options.body.adopt_from_suggestion,true);assert.equal(e.calls[0].options.body.note,'Saved note');
});
test('E-07 changed source cannot write until explicit acknowledgement',async()=>{
 const e=mount(adopted);e.source.value='manual';e.source.listeners.change();await e.save.onclick();assert.equal(e.calls.length,0);
 const ack=e.body.find(el=>el.tag==='input'&&el.type==='checkbox');ack.checked=true;await e.save.onclick();assert.equal(e.calls.length,1);assert.equal(e.calls[0].options.body.overall,'moderate');assert.equal(e.calls[0].options.body.adopt_from_suggestion,undefined);
});
test('E-18 previous values are a reviewable manual revision, not an immediate write',async()=>{
 const previous={...adopted,overall:'low',note:'Earlier decision'};
 const e=mount({...adopted,prev:previous});const restore=e.body.find(el=>el.tag==='button'&&el.textContent==='tables.polish.previous_values');await restore.onclick();
 assert.equal(e.calls.length,0);assert.equal(e.source.value,'manual');assert.equal(e.body.find(el=>el.tag==='textarea').value,'Earlier decision');
 const ack=e.body.find(el=>el.tag==='input'&&el.type==='checkbox');ack.checked=true;await e.save.onclick();assert.equal(e.calls[0].options.body.overall,'low');assert.equal(e.calls[0].options.body.adopt_from_suggestion,undefined);
});
test('new visible vocabulary has all ten translated locales',()=>{
 for(const file of ['table_wizards_polish_locale.js','analysis_rob_editor_locale.js']){const rows=[];vm.runInNewContext(read(file),{RFLang:{register:items=>rows.push(...items)}});assert.ok(rows.length);for(const row of rows){assert.equal(row.length,10);assert.ok(row.every(value=>typeof value==='string'&&value.trim()));assert.ok(row.aliases.length);}}
});
