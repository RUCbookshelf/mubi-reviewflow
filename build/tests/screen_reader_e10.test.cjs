const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../../custom_frontend/screen_reader.js'),'utf8');

test('an already visible ML hint follows asynchronously loaded/saved cluster strategy',()=>{
  const events=new Map(),storage=new Map();let currentTip=null,locale='en';
  function el(tag,attrs={},...children){return {tag,...attrs,textContent:attrs.text||'',children,style:{},hidden:false,remove(){currentTip=null;},querySelector(selector){return this.children.find(child=>selector==='span'?child.tag==='span':child.class?.split(' ').includes(selector.slice(1)));}};}
  const c={el,S:{al:{strategy:'max'}},T:key=>locale+':'+key,localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},document:{addEventListener:(name,handler)=>events.set(name,handler)},mlStatus:{before:node=>currentTip=node},aiButton:{click(){}}};
  const start=source.indexOf("  const TIP_KEY='rf_screen_ml_tip';");
  vm.runInNewContext(source.slice(start,source.indexOf('  /* ---- F59',start)),c);
  c.buildTip();assert.equal(currentTip.hidden,false);assert.match(currentTip.querySelector('span').textContent,/纳入概率/);
  c.S.al.strategy='cluster';assert.equal(typeof events.get('reviewflow:al-settings-loaded'),'function');events.get('reviewflow:al-settings-loaded')();
  assert.equal(currentTip.hidden,true);assert.equal(currentTip.style.display,'none');assert.match(currentTip.querySelector('span').textContent,/al.strategy.cluster.hint/);
  c.S.al.strategy='max';events.get('reviewflow:al-settings-saved')();assert.equal(currentTip.hidden,false);assert.equal(currentTip.style.display,'');assert.match(currentTip.querySelector('.btn').textContent,/试试机器学习排序/);
  locale='fr';events.get('reviewflow:language-changed')();assert.match(currentTip.querySelector('span').textContent,/^fr:/);
  currentTip.children[2].onclick();assert.equal(currentTip,null);events.get('reviewflow:al-settings-saved')();c.buildTip();assert.equal(currentTip,null,'A dismissed hint must not reappear on later setting loads.');
});
