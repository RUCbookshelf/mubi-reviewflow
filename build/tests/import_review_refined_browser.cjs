'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.REVIEWFLOW_PLAYWRIGHT||'playwright');
const root=path.resolve(__dirname,'../..');
(async()=>{
 const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.setContent('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>:root{--bg:#f4f8f7;--surface:#fff;--ink:#172b32;--muted:#60757c;--primary:#2f6f6a;--border:#d5e3e1;--danger:#ac573e}[data-theme=dark]{--bg:#0e1217;--surface:#171d23;--ink:#e7eef0;--muted:#a1b0b9;--primary:#78a7a2;--border:#333d49}body{margin:0;background:var(--bg);color:var(--ink);font:16px system-ui}.view{height:100vh;overflow:auto;padding:16px;box-sizing:border-box}button,input,select{font:inherit}pre{white-space:pre-wrap;overflow-wrap:anywhere}[hidden]{display:none!important}</style></head><body><div id="appMain" class="view"><section class="page rf-import-page"><div id="review"></div></section></div></body></html>');
 await page.evaluate(()=>{
  window.localeRows=[];window.localeIndex=1;window.RFLang={get:()=>['zh','en','fr','ru','es','ja','pt','de','sr','ko'][localeIndex],register:rows=>localeRows.push(...rows)};
  window.T=(s,...args)=>{const row=localeRows.findLast(r=>r[1]===s||r[0]===s);return(row?.[localeIndex]||s).replace(/\{(\d+)\}/g,(_,i)=>args[i]??'{'+i+'}');};
  window.el=(tag,attrs={})=>{const x=document.createElement(tag);for(const[k,v]of Object.entries(attrs)){if(k==='text')x.textContent=v;else if(k==='onclick')x.onclick=v;else x.setAttribute(k,v);}return x;};
  window.S={page:'import',task:{task_id:'fixture'}};window.taskApi=()=>'/fixture';window.scrQ=()=>'';window.renderImportReport=()=>{};window.refreshProgress=()=>new Promise(r=>setTimeout(r,2000));window.toast=()=>{};window.go=async()=>true;
  window.version=1;window.failure=false;window.calls=[];window.phase=0;window.before=null;
  window.WorkflowControls={scope:()=> 'fixture',settings:()=>({can_manage:true}),version:()=>version,acceptImportVersion:v=>version=v,refresh:async()=>{},error:e=>{},errorText:e=>e.message};
  window.recordData=Array.from({length:200},(_,i)=>({record_id:'r'+i,state:'staged',attention_reason:'duplicate',filename:'01-Wiley.ris',article:{title:'Literature '+Math.floor(i/2),doi:'10.test/'+Math.floor(i/2)},original:{title:'Literature '+Math.floor(i/2)}}));
  window.groupData=Array.from({length:100},(_,i)=>({review_id:'g'+i,status:'pending',kind:'title',members:[0,1].map(j=>({ref_type:'record',ref_id:'r'+(i*2+j),record:structuredClone(recordData[i*2+j])}))}));
  window.summary=()=>({batch_id:'b',created_at:'2026-10-13T10:00:00',files:[{filename:'01-Wiley.ris'},{filename:'02-ERIC.ris'}],can_manage:true,undo:{available:!!before},policy_version:version});
  window.api=async(url,options={})=>{
   calls.push({url,method:options.method||'GET'});
   if(options.method==='POST'){
    await new Promise(r=>setTimeout(r,40));if(failure)throw Error('Fixture: save failed');
    if(url.endsWith('/undo')){recordData=before.records;groupData=before.groups;before=null;version++;return {...summary(),review_updates:{records:structuredClone(recordData),candidates:structuredClone(groupData)}};}
    before={records:structuredClone(recordData),groups:structuredClone(groupData)};
    const actions=options.body.actions||[{kind:'candidate',id:url.split('/').pop()}];const changed=[];
    for(const a of actions){const g=groupData.find(g=>g.review_id===a.id);g.status=options.body.status||'duplicate';changed.push(g);for(const m of g.members){const r=recordData.find(r=>r.record_id===m.ref_id);r.attention_reason='ready';}}
    version++;return{...summary(),review_updates:{records:structuredClone(changed.flatMap(g=>g.members.map(m=>recordData.find(r=>r.record_id===m.ref_id)))),candidates:structuredClone(changed)}};
   }
   if(url.includes('/import-history')){await new Promise(r=>setTimeout(r,2000));return{pending_import_records:200};}
   if(url.includes('title-duplicate'))return{groups:[],total:0,undo_available:false};
   const u=new URL(url,'http://fixture'),offset=Number(u.searchParams.get('offset')||0),isGroups=url.includes('/candidates');
   // Preserve a stale response captured before a save, delivered during background loading.
   const list=structuredClone(isGroups?groupData:recordData),base=summary();await new Promise(r=>setTimeout(r,offset?80:5));
   return isGroups?{candidates:list.slice(offset,offset+20),total:list.length}:{...base,records:list.slice(offset,offset+20),view_total:list.length};
  };
 });
 for(const file of ['interface_locale.js','workflow_locale.js','import_review_locale.js','import_export_locale.js','import_export_help_locale.js','import_review_refined_locale.js'])await page.addScriptTag({path:path.join(root,'custom_frontend',file)});
 // Use the product's exact UI helpers without its unrelated page-bootstrap code.
 const ui=fs.readFileSync(path.join(root,'custom_frontend/import_export_workspace.js'),'utf8');
 await page.addScriptTag({content:ui.slice(0,ui.indexOf(' UI.tabs='))+'})();'});
 await page.evaluate(()=>{ImportExportWorkspace.reviewHost=document.getElementById('review');ImportExportWorkspace.importPolicy=()=>{};ImportExportWorkspace.showImport=()=>{};});
 for(const file of ['import_review.css','import_export_workspace.css','mobile_concept.css','mobile_motion.css','disclosure_controls.css','import_review_refined.css'])await page.addStyleTag({path:path.join(root,'custom_frontend',file)});
 for(const file of ['import_review_model.js','import_review_updates.js','import_review_loading.js','unified_import_workspace.js'])await page.addScriptTag({path:path.join(root,'custom_frontend',file)});
 await page.evaluate(()=>WorkflowImports.open('b'));
 await page.waitForSelector('[id="wf-queue-candidate%3Ag0"]');
 const start=Date.now();await page.locator('.card-actions button').first().click();await page.waitForFunction(()=>document.querySelector('.card-head h3')?.textContent==='Literature 1'&&!document.querySelector('.card-actions button').disabled);
 const nextMs=Date.now()-start;assert.ok(nextMs<1000,'Next enabled too late: '+nextMs);console.log('Next record enabled ms (2 s auxiliary requests still pending):',nextMs);
 assert.equal(await page.evaluate(()=>calls.filter(x=>x.method==='GET'&&x.url.includes('offset=0')&&!x.url.includes('import-history')).length),3,'Save must not reload initial pages');
 await page.waitForFunction(()=>document.querySelector('.load-state')?.dataset.phase==='complete');
 assert.equal(await page.locator('[id="wf-queue-candidate%3Ag0"]').count(),0,'Background data must not restore saved item');
 await page.getByRole('button',{name:'Undo last review',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.card-head h3')?.textContent==='Literature 0');
 await page.evaluate(()=>failure=true);await page.locator('.card-body input[type=radio]').last().check();await page.locator('.card-body details').first().evaluate(x=>x.open=true);
 const kept=await page.locator('.card-body input:checked').inputValue();await page.locator('.card-actions button').first().click();await page.waitForFunction(()=>document.querySelector('.status-message')?.textContent.includes('save failed'));
 assert.equal(await page.locator('.card-body input:checked').inputValue(),kept);assert.equal(await page.locator('.card-body details').first().evaluate(x=>x.open),true);
 await page.evaluate(()=>failure=false);await page.getByRole('button',{name:'Confirm eligible duplicates',exact:true}).click();assert.equal(await page.locator('dialog[open] .bulk-list p').count(),100);
 await page.locator('dialog[open]').getByRole('button',{name:'Confirm eligible duplicates',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.card.empty')&&document.querySelector('.status-message')?.textContent==='Saved');
 await page.getByRole('button',{name:'Undo last review',exact:true}).click();await page.waitForSelector('.card-actions button');
 const measures=[];
 for(const width of [320,390,760,820,1024,1440]){await page.setViewportSize({width,height:950});for(const dark of [false,true]){await page.evaluate(d=>document.documentElement.dataset.theme=d?'dark':'light',dark);for(let lang=0;lang<10;lang++){
  await page.evaluate(i=>{localeIndex=i;document.dispatchEvent(new CustomEvent('reviewflow:language-changed'));},lang);
  const sizes=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth||document.querySelector('.view').scrollWidth>innerWidth,nav:document.querySelector('.record-nav').getBoundingClientRect().width,queue:getComputedStyle(document.querySelector('.queue-side')).display,tabWidths:[...document.querySelectorAll('.tabs .tab')].map(x=>x.getBoundingClientRect().width),header:document.querySelector('.compact-header').getBoundingClientRect().height}));
  if(sizes.overflow)console.log(await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>innerWidth).map(e=>({tag:e.tagName,cls:e.className,text:e.textContent.slice(0,80),right:e.getBoundingClientRect().right})).slice(0,12)));assert.equal(sizes.overflow,false,JSON.stringify({width,dark,lang,sizes}));assert.ok(Math.max(...sizes.tabWidths)-Math.min(...sizes.tabWidths)<1);if(width<=760)assert.equal(sizes.queue,'none');
  measures.push({width,dark,lang,...sizes});
 }}}
 assert.deepEqual(errors,[]);console.log('PASS 120 responsive theme/language combinations; single save, stale background overlay, undo, failure retention, bulk dialog, bulk undo.');
 await page.evaluate(()=>{localeIndex=0;document.dispatchEvent(new CustomEvent('reviewflow:language-changed'));});await page.setViewportSize({width:1440,height:950});await page.waitForTimeout(500);await page.screenshot({path:path.join(root,'build/tests/import-review-desktop.png')});await page.setViewportSize({width:390,height:950});await page.screenshot({path:path.join(root,'build/tests/import-review-mobile.png')});
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
