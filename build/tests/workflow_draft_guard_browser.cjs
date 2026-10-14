'use strict';
// Actual application and synthetic catalogue only. All settings writes are intercepted.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const {chromium}=require(process.env.REVIEWFLOW_PLAYWRIGHT||'playwright');
const root=path.resolve(__dirname,'../..'),url=process.env.REVIEWFLOW_GUARD_URL||'http://127.0.0.1:18747';
const session=JSON.parse(fs.readFileSync(process.env.REVIEWFLOW_GUARD_SESSION||'C:/Users/16073/.codex/preview-data/refined-session.json','utf8'));
const baseline=process.argv.includes('--baseline'),artifacts=process.env.REVIEWFLOW_GUARD_ARTIFACTS||'C:/Users/16073/.codex/preview-data/guard-fix-validation';
fs.mkdirSync(artifacts,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true});let checks=0;const errors=[];
 async function fixture(width=1440){
  const page=await browser.newPage({viewport:{width,height:950}});page.on('pageerror',e=>errors.push(e.message));
  const state={al:{strategy:'max',seed:42,min_labeled:5,stop_threshold:10},puts:[],version:0,counts:0,policy:false,failure:false,delay:0};
  await page.addInitScript(token=>sessionStorage.setItem('rf_token',token),session.token);
  if(baseline)for(const file of ['index.html','workflow_controls.js','workflow_controls.css'])await page.route('**/'+file+'*',route=>route.fulfill({contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html',body:execFileSync('git',['show','HEAD:custom_frontend/'+file],{cwd:root,encoding:'utf8'})}));
  await page.route('**/al/settings*',async route=>{if(route.request().method()==='PUT'){state.puts.push(route.request().postDataJSON());if(state.delay)await new Promise(r=>setTimeout(r,state.delay));if(state.failure)return route.fulfill({status:500,json:{detail:'Fixture save failed'}});Object.assign(state.al,state.puts.at(-1));}await route.fulfill({json:state.al});});
  await page.route('**/reason-library*',route=>route.fulfill({json:{entries:[{id:'reason',text:'Existing reason'}],can_manage:true,task_kind:'personal'}}));
  await page.route('**/workflow-settings*',async route=>{if(route.request().method()!=='GET')throw Error('Unexpected policy write in presentation test');const response=await route.fetch(),data=await response.json();data.version+=state.version;if(data.sources?.length)data.sources[0].record_count=(data.sources[0].record_count||0)+state.counts;if(state.policy)data.stages.screen.show_maybe=!data.stages.screen.show_maybe;await route.fulfill({response,json:data});});
  await page.goto(url+'/#workspace/home');await page.waitForFunction(()=>S.user&&S.task&&WorkflowControls.settings());
  await page.evaluate(async()=>{await go('home',{guarded:true});WorkflowControls.homeTab('ml');await loadALSettingsUI();await WorkflowControls.refresh('fixture',false);});
  return {page,state};
 }
 const check=async(name,fn)=>{await fn();checks++;console.log('PASS',name);};
 const navigate=async(page,target='import')=>{await page.evaluate(target=>{window.fixtureNavigation=go(target);},target);};
 const dialog=page=>page.locator('.wf-dirty-dialog[open]');
 const save=page=>dialog(page).getByRole('button',{name:'保存',exact:true});
 const close=async(page)=>{await page.unrouteAll({behavior:'wait'});await page.close();};
 await check('Reverted ML input does not prompt on import or screening navigation',async()=>{
  const {page}=await fixture();await page.locator('#alStopThreshold').fill('11');await page.locator('#alStopThreshold').fill('10');assert.equal(await page.evaluate(()=>WorkflowControls.anyDirty()),false);
  assert.equal(await page.evaluate(()=>go('import')),true);assert.equal(await dialog(page).count(),0);assert.equal(await page.evaluate(()=>go('screen')),true);assert.equal(await dialog(page).count(),0);await close(page);
 });
 await check('Reverted workflow selection and blank new reason are clean',async()=>{
  const {page}=await fixture();await page.evaluate(()=>WorkflowControls.homeTab('tasks'));const select=page.locator('#wf-policy select').first(),value=await select.inputValue();await select.selectOption('paused');await select.selectOption(value);assert.equal(await page.evaluate(()=>WorkflowControls.anyDirty()),false);
  await page.evaluate(()=>WorkflowControls.homeTab('screening'));const reason=page.locator('#wf-reasons input[placeholder]').first();await reason.fill('Temporary reason');await reason.fill('');assert.equal(await page.evaluate(()=>WorkflowControls.anyDirty()),false);assert.equal(await page.evaluate(()=>go('import')),true);await close(page);
 });
 await check('Version and source-count changes do not demand policy inspection',async()=>{
  const {page,state}=await fixture();await page.locator('#alStopThreshold').fill('11');state.version=1;state.counts=1;await page.evaluate(()=>WorkflowControls.refresh('fixture',false));assert.equal(await page.locator('#wf-policy-recovery').isVisible(),false);assert.equal(await page.locator('#alStopThreshold').inputValue(),'11');assert.equal(await page.evaluate(()=>WorkflowControls.anyDirty()),true);await close(page);
 });
 await check('Actual policy change still protects a pending workflow draft',async()=>{
  const {page,state}=await fixture();await page.evaluate(()=>WorkflowControls.homeTab('tasks'));await page.locator('#wf-policy select').first().selectOption('paused');state.policy=true;state.version=1;await page.evaluate(()=>WorkflowControls.refresh('fixture',false));assert.equal(await page.locator('#wf-policy-recovery').isVisible(),true);assert.equal(await page.locator('#wf-policy select').first().inputValue(),'paused');await close(page);
 });
 await check('Pending workflow controls are editable inside the save dialog',async()=>{
  const {page}=await fixture();await page.evaluate(()=>WorkflowControls.homeTab('tasks'));const mode=page.locator('#wf-policy select').first(),original=await mode.inputValue();await mode.selectOption('selected');await page.evaluate(()=>WorkflowControls.homeTab('overview'));await navigate(page);await dialog(page).waitFor({state:'visible'});const editor=dialog(page).locator('.wf-policy-stage select').first();assert.equal(await editor.isVisible(),true);assert.equal(await editor.inputValue(),'selected');await editor.selectOption(original);await save(page).click();await page.waitForFunction(()=>S.page==='import');assert.equal(await page.evaluate(()=>WorkflowControls.anyDirty()),false);await close(page);
 });
 for(const width of [1440,390])await check('Hidden invalid ML field is visible and correctable at '+width+'px',async()=>{
  const {page,state}=await fixture(width);await page.locator('#alStopThreshold').fill('10001');await page.evaluate(()=>WorkflowControls.homeTab('overview'));await navigate(page);await dialog(page).waitFor({state:'visible'});
  const field=dialog(page).locator('input[type=number][max="10000"]');assert.equal(await field.isVisible(),true);await save(page).click();assert.equal(state.puts.length,0);assert.equal(await field.evaluate(el=>el.validity.rangeOverflow),true);assert.equal(await field.evaluate(el=>el===document.activeElement),true);
  assert.equal(await dialog(page).evaluate(el=>el.scrollWidth<=el.clientWidth+1),true);await page.screenshot({path:path.join(artifacts,'guard-fixed-'+width+'.png')});
  await field.fill('10');await save(page).click();await page.waitForFunction(()=>S.page==='import');assert.equal(state.puts.length,0);assert.equal(await page.evaluate(()=>WorkflowControls.anyDirty()),false);await close(page);
 });
 await check('Unchanged invalid legacy AL values do not prompt or silently save',async()=>{
  const {page,state}=await fixture();state.al.min_labeled=1001;await page.evaluate(()=>loadALSettingsUI());assert.equal(await page.evaluate(()=>WorkflowControls.anyDirty()),false);assert.equal(await page.evaluate(()=>go('import')),true);assert.equal(state.puts.length,0);await close(page);
 });
 await check('Hidden retained settings are correctable while leaving screening',async()=>{
  const {page,state}=await fixture();await page.evaluate(()=>go('screen'));await page.evaluate(()=>{const input=document.querySelector('#alStopThreshold');input.value='10001';input.dispatchEvent(new Event('input',{bubbles:true}));});await navigate(page,'export');await dialog(page).waitFor({state:'visible'});const field=dialog(page).locator('input[max="10000"]');assert.equal(await field.isVisible(),true);await save(page).click();assert.equal(state.puts.length,0);assert.match(await page.locator('#wf-guard-validation').innerText(),/0.*10000/);await field.fill('10');await save(page).click();await page.waitForFunction(()=>S.page==='export');await close(page);
 });
 await check('Empty, decimal and oversized fields stop before any API write',async()=>{
  const {page,state}=await fixture();await page.locator('#alStopThreshold').fill('11');await page.evaluate(()=>WorkflowControls.homeTab('overview'));await navigate(page);await dialog(page).waitFor({state:'visible'});
  const min=dialog(page).locator('input[max="10000"]'),seed=dialog(page).locator('input[max="2147483648"]'),stop=dialog(page).locator('input[max="10000"]');
  for(const [field,value]of[[min,''],[min,'5.5'],[min,'-1'],[seed,'2147483649'],[stop,'10001']]){await min.fill('11');await seed.fill('42');await stop.fill('10');await field.fill(value);await save(page).click();assert.equal(state.puts.length,0);assert.equal(await dialog(page).count(),1);}await close(page);
 });
 await check('Valid dialog settings save and finish navigation',async()=>{
  const {page,state}=await fixture();await page.locator('#alStopThreshold').fill('11');await page.evaluate(()=>WorkflowControls.homeTab('overview'));await navigate(page);await dialog(page).waitFor({state:'visible'});await save(page).click();await page.waitForFunction(()=>S.page==='import');assert.equal(state.puts.length,1);assert.equal(state.puts[0].stop_threshold,11);assert.equal('min_labeled' in state.puts[0],false);assert.equal(await page.evaluate(()=>WorkflowControls.anyDirty()),false);await close(page);
 });
 await check('Discard restores ML values rather than only clearing a flag',async()=>{
  const {page,state}=await fixture();await page.locator('#alStopThreshold').fill('10001');await page.evaluate(()=>WorkflowControls.homeTab('overview'));await navigate(page);await dialog(page).getByRole('button',{name:'放弃更改',exact:true}).click();await page.waitForFunction(()=>S.page==='import');assert.equal(await page.locator('#alStopThreshold').inputValue(),'10');assert.equal(state.puts.length,0);assert.equal(await page.evaluate(()=>WorkflowControls.anyDirty()),false);await close(page);
 });
 await check('Save failure retains visible draft and supports retry',async()=>{
  const {page,state}=await fixture();await page.locator('#alStopThreshold').fill('11');state.failure=true;await navigate(page);await save(page).click();await page.waitForFunction(()=>document.querySelector('.toast')?.textContent.includes('Fixture'));assert.equal(await dialog(page).count(),1);assert.equal(await dialog(page).locator('input[max="10000"]').inputValue(),'11');state.failure=false;await save(page).click();await page.waitForFunction(()=>S.page==='import');assert.equal(state.puts.length,2);await close(page);
 });
 await check('Edits made during save remain dirty against the saved baseline',async()=>{
  const {page,state}=await fixture();state.delay=250;await page.locator('#alStopThreshold').fill('11');await navigate(page);await save(page).click();const field=dialog(page).locator('input[max="10000"]');await field.fill('10');await page.waitForFunction(()=>document.querySelector('.toast')?.textContent.includes('已保存'));assert.equal(await page.evaluate(()=>WorkflowControls.anyDirty()),true);assert.equal(await dialog(page).count(),1);assert.equal(state.puts[0].stop_threshold,11);assert.equal('min_labeled' in state.puts[0],false);await close(page);
 });
 await check('Library validation is editable in the same dialog',async()=>{
  const {page}=await fixture();await page.evaluate(()=>WorkflowControls.homeTab('screening'));const field=page.locator('#wf-reasons input').first();await field.fill('New reason');await page.evaluate(()=>WorkflowControls.homeTab('overview'));await navigate(page);const editor=dialog(page).locator('input').first();assert.equal(await editor.isVisible(),true);await editor.fill(' ');await save(page).click();assert.equal(await dialog(page).count(),1);assert.equal(await editor.evaluate(el=>el.validity.customError),true);await editor.fill('');await dialog(page).getByRole('button',{name:'放弃更改',exact:true}).click();await page.waitForFunction(()=>S.page==='import');await close(page);
 });
 assert.deepEqual(errors,[]);console.log(JSON.stringify({checks,errors,baseline}));await browser.close();
})().catch(error=>{console.error(String(error.stack||error).replace(/Bearer [^\s'"\u001b]+/g,'Bearer [redacted]'));process.exit(1)});
