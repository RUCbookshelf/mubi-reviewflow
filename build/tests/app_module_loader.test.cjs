'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../custom_frontend/app_module_loader.js'),'utf8');
function scene({fail='',loading=false}={}){
  const attempts=[],loaded=[],events={},markers=['one','two','three'].map(name=>({dataset:{},getAttribute(attribute){assert.equal(attribute,'src');return name;},after(script){assert.equal(script.src,name);attempts.push(name);queueMicrotask(()=>{if(fail===name){fail='';script.onerror();}else{loaded.push(name);script.onload();}});}}));
  const context={window:{},document:{readyState:loading?'loading':'complete',addEventListener:(name,fn)=>events[name]=fn,querySelectorAll:()=>markers,createElement:()=>({remove(){}})}};
  vm.runInNewContext(source,context);return {ready:context.window.RFAppModules.ready,attempts,loaded,events};
}
test('workspace scripts do not load before requested and preserve order',async()=>{
  const state=scene();assert.deepEqual(state.attempts,[]);await state.ready();assert.deepEqual(state.loaded,['one','two','three']);await state.ready();assert.equal(state.attempts.length,3);
});
test('failed loading resumes at failed module without reexecuting successful modules',async()=>{
  const state=scene({fail:'two'});await assert.rejects(state.ready(),/重试登录/);assert.deepEqual(state.loaded,['one']);await state.ready();assert.deepEqual(state.attempts,['one','two','two','three']);assert.deepEqual(state.loaded,['one','two','three']);
});
test('concurrent entry attempts share one initialization',async()=>{
  const state=scene();await Promise.all([state.ready(),state.ready()]);assert.deepEqual(state.attempts,['one','two','three']);
});
test('restored login waits until footer manifest has been parsed',async()=>{
  const state=scene({loading:true});const pending=state.ready();await Promise.resolve();assert.deepEqual(state.attempts,[]);state.events.DOMContentLoaded();await pending;assert.deepEqual(state.loaded,['one','two','three']);
});
