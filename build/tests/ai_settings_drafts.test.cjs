'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const dir = process.env.RF_WIZARD_SOURCE || path.join(__dirname,'../../custom_frontend');
const flush = () => new Promise(resolve => setImmediate(resolve));

class Element {
  constructor(tag, attrs = {}, ...children) {
    this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.events = {}; this.value = ''; this.textContent = ''; this.style = {};
    for (const [key,value] of Object.entries(attrs)) this.setAttribute(key,value);
    this.append(...children);
  }
  setAttribute(key,value) {
    if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g,(_,x)=>x.toUpperCase())] = value;
    else if (key === 'text') this.textContent = value;
    else this[key] = value;
  }
  getAttribute(key) { return key.startsWith('data-') ? this.dataset[key.slice(5).replace(/-([a-z])/g,(_,x)=>x.toUpperCase())] : this[key]; }
  append(...children) { for (const child of children.flat().filter(Boolean)) { child.parent = this; this.children.push(child); } }
  appendChild(child) { this.append(child); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(child=>child !== this); }
  addEventListener(name,listener) { this.events[name] = listener; }
  dispatchEvent(event) { return this.events[event.type]?.(event); }
  get options() { return this.children; }
  get childElementCount() { return this.children.length; }
  matches(selector) {
    const tag = selector.match(/^[a-z]+/i)?.[0]; if (tag && tag.toUpperCase() !== this.tagName) return false;
    const id = selector.match(/#([\w-]+)/)?.[1]; if (id && this.id !== id) return false;
    const cls = selector.match(/\.([\w-]+)/)?.[1]; if (cls && !String(this.class || '').split(' ').includes(cls)) return false;
    const attr = selector.match(/\[([^=\]]+)(?:="([^"]*)")?\]/); if (attr && (this.getAttribute(attr[1]) === undefined || (attr[2] !== undefined && this.getAttribute(attr[1]) !== attr[2]))) return false;
    if (selector.includes(':checked') && !this.checked) return false;
    return true;
  }
  querySelectorAll(selector) { const parts = selector.split(','); return this.children.flatMap(child => [...(parts.some(part=>child.matches(part)) ? [child] : []), ...child.querySelectorAll(selector)]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return this.matches(selector) ? this : this.parent?.closest(selector); }
  focus() {}
}

function setup() {
  const body = new Element('body'), events = {}, calls = [], storage = new Map(), notices = [];
  body.append(new Element('div',{id:'wf-panel-ml'}),new Element('div',{id:'wf-panel-coding'}),new Element('div',{id:'cbForms'}));
  const document = { body,head:new Element('head'),createElement:tag=>new Element(tag),getElementById:id=>body.querySelector('#'+id),querySelector:selector=>body.querySelector(selector),addEventListener:(name,fn)=>events[name]=fn };
  let saved = {llm:{configured:true,provider:'custom',base_url:'https://stored.test',model:'stored-model',format:'openai_chat'},decision:null};
  let generateRequest;
  const S = { user:{user_id:'user-a'},task:{task_id:'task-a'},screener:'A',dims:[{id:'sample',name:'Sample'}],cbQueue:[{zotero_key:'paper-a'}],cbIdx:0 };
  const context = {window:{RFConfirm:async()=>true},document,S,Event:class {constructor(type){this.type=type}},AbortController,
    localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},
    el:(...args)=>new Element(...args),T:(key,...args)=>String(key).replace(/\{(\d+)\}/g,(_,n)=>args[n]),
    RFLang:{register(){}},toast:(...args)=>notices.push(args),setTimeout,clearTimeout,
    taskApi:()=>'/api/tasks/'+S.task.task_id,scrQ:()=>'',
    api:async(url,opts={})=>{
      calls.push({url,opts});
      if (url === '/api/ai/providers') return {providers:[],supported_formats:{openai_chat:true,anthropic:true,systemone:true}};
      if (url === '/api/runtime') return {mode:'server'};
      if (url.startsWith('/api/ai/key')) {
        if (opts.method === 'PUT') saved = {...saved,[opts.body.kind]:{...opts.body,configured:true}};
        if (opts.method === 'DELETE') saved = {...saved,[url.split('kind=')[1]]:null};
        return saved;
      }
      if (url.endsWith('/ai-suggest/settings')) return {enabled:false,key_mode:'personal',allowed_field_ids:['sample_size'],context_prompt:'stored context',version:1};
      if (url.endsWith('/coding/ai-suggestions')) return new Promise((resolve,reject)=>{generateRequest={resolve,reject,signal:opts.signal}; opts.signal.addEventListener('abort',()=>reject(Object.assign(new Error('abort'),{name:'AbortError'})));});
      return {};
    }
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(dir,'ai_providers_catalog.js'),'utf8'),context);
  const source = fs.readFileSync(path.join(dir,'ai_suggest.js'),'utf8').replace(
    "if (typeof S !== 'undefined' && S.user) detect().then(safe(renderAll)).catch(() => {});",
    'window.testUI = {detect,renderAll,renderTaskCard,renderSuggestions,resetForScopeChange,generate,articleState,setDetection:value=>detection=value,getGenerating:()=>generating};');
  vm.runInContext(source,context);
  assert.ok(context.window.testUI,'AI module initializes');
  return {...context,ui:context.window.testUI,events,calls,storage,notices,getRequest:()=>generateRequest,
    key:()=>document.getElementById('rfAiKeyCard'),task:()=>document.getElementById('rfAiTaskCard')};
}
const input = (host,name) => host.querySelector('[data-rf-name="'+name+'"]');
const button = (host,text) => host.querySelectorAll('button').find(item=>item.textContent === text);

test('saving and deleting a key preserve sibling and task drafts, including return navigation',async()=>{
  const x=setup(); await x.ui.detect(true); await flush();
  assert.equal(input(x.key(),'llm_model').value,'stored-model','initial metadata survives the loading render');
  input(x.key(),'decision_provider').value='unsaved-provider'; input(x.key(),'decision_key').value='synthetic-secret'; input(x.key(),'decision_model').value='unsaved-model';
  input(x.task(),'context_prompt').value='task draft'; input(x.task(),'key_mode').value='shared';
  input(x.key(),'llm_key').value='synthetic-saved-key';
  await button(x.key(),'保存 API Key').events.click(); await flush();
  assert.equal(input(x.key(),'llm_key').value,'');
  assert.equal(input(x.key(),'decision_key').value,'synthetic-secret');
  assert.equal(input(x.task(),'context_prompt').value,'task draft');
  assert.equal(input(x.task(),'key_mode').value,'shared');
  await button(x.key(),'删除 API Key').events.click(); await flush();
  assert.equal(input(x.key(),'llm_model').value,'');
  assert.equal(input(x.key(),'decision_model').value,'unsaved-model');
  assert.equal(input(x.task(),'context_prompt').value,'task draft');
  x.events['reviewflow:workspace-changed']({detail:'home'}); await flush();
  assert.equal(input(x.key(),'decision_key').value,'synthetic-secret');
  assert.ok([...x.storage.values()].every(value=>!value.includes('synthetic-secret')),'secrets never enter browser storage');
});

test('task drafts restore only to their original scope and an account change clears secrets',async()=>{
  const x=setup(); await x.ui.detect(true); await flush();
  input(x.key(),'decision_key').value='scope-secret'; input(x.task(),'context_prompt').value='A draft';
  x.S.task={task_id:'task-b'}; x.events['reviewflow:task-context-changed'](); await flush();
  assert.equal(input(x.key(),'decision_key').value,''); assert.equal(input(x.task(),'context_prompt').value,'stored context');
  x.S.task={task_id:'task-a'}; x.events['reviewflow:task-context-changed'](); await flush();
  assert.equal(input(x.key(),'decision_key').value,'scope-secret'); assert.equal(input(x.task(),'context_prompt').value,'A draft');
  x.S.user={user_id:'user-b'}; x.events['reviewflow:task-context-changed'](); await flush();
  assert.equal(input(x.key(),'decision_key').value,'');
  x.S.user={user_id:'user-a'}; x.events['reviewflow:task-context-changed'](); await flush();
  assert.equal(input(x.key(),'decision_key').value,'');
});

test('an update from the other key entry point refreshes untouched fields while retaining actual edits',async()=>{
  const x=setup(); await x.ui.detect(true); await flush();
  input(x.key(),'decision_model').value='actual unsaved edit';
  await x.api('/api/ai/key',{method:'PUT',body:{kind:'llm',provider:'other',model:'new externally saved model',base_url:'https://new.test',format:'openai_chat'}});
  await x.ui.detect(true); await flush();
  assert.equal(input(x.key(),'llm_model').value,'new externally saved model');
  assert.equal(input(x.key(),'llm_base_url').value,'https://new.test');
  assert.equal(input(x.key(),'decision_model').value,'actual unsaved edit');
});

test('external key mutation clears an obsolete local deletion receipt',async()=>{
  const x=setup();await x.ui.detect(true);await flush();
  await button(x.key(),'删除 API Key').events.click();await flush();
  assert.ok(x.key().querySelectorAll('[role="status"]').some(node=>node.textContent==='已删除。'));
  await x.api('/api/ai/key',{method:'PUT',body:{kind:'llm',provider:'other',model:'external',format:'openai_chat'}});
  x.events['reviewflow:ai-keys-changed']();await x.ui.detect(true);await flush();
  assert.ok(!x.key().querySelectorAll('[role="status"]').some(node=>node.textContent==='已删除。'));
});

test('members receive their own persisted mapping and empty Methods copy is disabled',async()=>{
  const x=setup(); x.storage.set('rfAiFieldMap.task-a.user-a',JSON.stringify({sample_size:'sample'}));
  x.ui.setDetection({scope:'task-a|user-a|A',isOwner:false,active:false,personal:{},allowedFields:[]}); x.ui.renderTaskCard(true); await flush();
  assert.equal(input(x.task(),'map_sample_size').value,'sample');
  assert.equal(button(x.task(),'复制中文').disabled,true); assert.equal(button(x.task(),'复制英文').disabled,true);
  assert.equal(x.storage.get('rfAiFieldMap.task-a.user-a'),'{"sample_size":"sample"}');
});

test('aborting generation releases the pending state and passes the abort signal to transport',async()=>{
  const x=setup(); x.ui.setDetection({scope:'task-a|user-a|A',active:true,keyAvailable:true,allowedFields:['sample_size'],prefs:{}});
  const pending=x.ui.generate(); await flush();
  assert.equal(x.ui.getGenerating(),true); assert.ok(x.getRequest().signal);
  x.ui.resetForScopeChange(); await pending;
  assert.equal(x.getRequest().signal.aborted,true); assert.equal(x.ui.getGenerating(),false);
});

test('rerendering one suggestion preserves the open state of the other field',async()=>{
  const x=setup(); x.ui.setDetection({scope:'task-a|user-a|A',active:true,keyAvailable:true,prefs:{presentation:'card'}});
  x.ui.articleState('paper-a').items=[{id:1,field_id:'sample_size',value:'12'},{id:2,field_id:'study_design',value:'trial'}];
  x.ui.renderSuggestions();
  x.document.querySelector('[data-field="study_design"]').open=false;
  x.ui.articleState('paper-a').items[0].status='ignored'; x.ui.renderSuggestions();
  assert.equal(x.document.querySelector('[data-field="study_design"]').open,false);
  assert.equal(x.document.querySelector('[data-field="sample_size"]').open,true);
});
