'use strict';
const assert=require('node:assert/strict'),path=require('node:path');
const {chromium}=require(process.env.REVIEWFLOW_PLAYWRIGHT||'playwright');
const root=path.resolve(__dirname,'../..');
(async()=>{
 const browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('file:///'+path.join(__dirname,'import_review_fixture.html').replaceAll('\\','/'));
 for(const file of ['import_guide.css','import_review.css'])await page.addStyleTag({path:path.join(root,'custom_frontend',file)});
 for(const file of ['import_review_model.js','workflow_imports.js','import_guide.js'])await page.addScriptTag({path:path.join(root,'custom_frontend',file)});
 await page.evaluate(()=>WorkflowImports.open(bid));
 assert.equal(await page.locator('#wf-import-review h3').textContent(),'1 imported record needs your review');
 assert.equal(await page.locator('.wf-import-completed').getAttribute('open'),null);
 assert.equal(await page.locator('.wf-metadata-table tbody tr').count(),8);
 assert.equal(await page.locator('.wf-import-completed .wf-import-record').count(),0);
 await page.getByRole('button',{name:/^Review: 0 duplicate groups/}).click();
 await page.waitForFunction(()=>document.activeElement.id==='wf-import-attention');
 await page.getByRole('button',{name:/^Activate reviewed imports:/}).click();
 await page.waitForFunction(()=>document.activeElement.id==='wf-import-activation');
 await page.getByRole('button',{name:'Keep existing metadata',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('.wf-import-activate')?.textContent.includes('(2)'));
 assert.equal(await page.evaluate(()=>records.find(r=>r.record_id==='meta').article.year),2023);
 await page.waitForFunction(()=>!document.querySelector('.wf-import-activate')?.disabled);
 await page.evaluate(()=>{window.originalApi=api;window.activationCalls=0;api=async(url,options={})=>{if(options.method==='POST'&&url.endsWith('/activate'))window.activationCalls++;return window.originalApi(url,options);};const button=document.querySelector('.wf-import-activate');button.click();button.click();});
 await page.waitForFunction(()=>document.querySelector('#wf-import-review h3')?.textContent==='This import is complete');
 assert.equal(await page.locator('.wf-import-activate').isDisabled(),true);
 assert.equal(await page.evaluate(()=>window.activationCalls),1);
 await page.waitForFunction(()=>document.querySelector('#rfImportGuide .rf-guide-count')?.textContent==='3/4 completed');
 await page.getByRole('button',{name:/^Open Trace/}).click();
 assert.equal(await page.locator('#rfImportGuide').isVisible(),false);
 await page.locator('.wf-import-completed>summary').focus();await page.keyboard.press('Enter');
 await page.waitForFunction(()=>document.querySelectorAll('.wf-import-completed .wf-import-record').length===8);
 assert.deepEqual(errors,[]);
 for(const width of [1440,1024,820,768]){await page.setViewportSize({width,height:1000});for(const dark of [false,true]){await page.evaluate(d=>document.body.classList.toggle('dark',d),dark);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);}}
 await page.evaluate(()=>{reset();records.find(r=>r.record_id==='meta').metadata.stale=true;return WorkflowImports.open(bid);});
 assert.equal(await page.getByRole('button',{name:'Accept incoming metadata',exact:true}).isDisabled(),true);
 await page.evaluate(()=>{records.find(r=>r.record_id==='meta').metadata.stale=false;return WorkflowImports.open(bid);});
 await page.getByRole('button',{name:'Accept incoming metadata',exact:true}).click();
 await page.waitForFunction(()=>records.find(r=>r.record_id==='meta').metadata.action==='accept_incoming');
 assert.equal(await page.evaluate(()=>records.find(r=>r.record_id==='meta').article.year),2024);
 // Candidate safeguards and all completed-group pages remain available.
 await page.evaluate(()=>{
  api=async(url,options={})=>{
   if(url.includes('/candidates')){
    const completed=url.includes('view=completed'),offset=Number(new URL(url,'http://fixture').searchParams.get('offset')||0);
    const candidates=completed?Array.from({length:Math.min(20,25-offset)},(_,i)=>({review_id:'done-group-'+(i+offset),kind:'title',status:'not_duplicate',members:[]})):[{review_id:'pending-group',kind:'title',status:'pending',members:[{ref_type:'active',ref_id:'a',article:{title:'Existing A'}},{ref_type:'active',ref_id:'b',article:{title:'Existing B'}}]}];
    return {total:completed?25:1,candidates,next_offset:completed&&offset===0?20:null};
   }
   return window.originalApi(url,options);
  };return WorkflowImports.open(bid);
 });
 assert.equal(await page.getByRole('button',{name:'Confirm duplicate',exact:true}).isDisabled(),true);
 await page.locator('#wf-candidate-pending-group select').selectOption('active:b');
 await page.evaluate(()=>document.dispatchEvent(new CustomEvent('reviewflow:language-changed')));
 await page.waitForFunction(()=>document.querySelector('#wf-candidate-pending-group select')?.value==='active:b');
 const finished=page.locator('.wf-import-completed');if(!await finished.evaluate(x=>x.open))await finished.locator(':scope>summary').click();
 await page.waitForFunction(()=>document.querySelectorAll('.wf-import-completed .wf-import-candidate').length===20);
 await finished.getByRole('button',{name:'Next candidates',exact:true}).click();
 await page.waitForFunction(()=>document.querySelectorAll('.wf-import-completed .wf-import-candidate').length===5);
 assert.equal(await finished.getByRole('button',{name:'Next candidates',exact:true}).isDisabled(),true);
 await page.evaluate(()=>{api=window.originalApi;reset();records[0].metadata.incoming.abstract='<img id="untrusted" src=x onerror="window.unsafe=true">'+'long text '.repeat(60);return WorkflowImports.open(bid);});
 assert.equal(await page.locator('#untrusted').count(),0);
 assert.equal(await page.getByText('Read full value',{exact:true}).count(),1);
 assert.equal(await page.evaluate(()=>!!window.unsafe),false);
 await page.evaluate(()=>{reset();api=async(url,options={})=>{const result=await window.originalApi(url,options);if(url.includes('/import-batches/'))result.can_manage=false;return result;};return WorkflowImports.open(bid);});
 assert.equal(await page.locator('.wf-import-activate').count(),0);
 assert.equal(await page.getByRole('button',{name:'Keep existing metadata',exact:true}).count(),0);
 await page.evaluate(()=>{api=window.originalApi;window.localeRows=[];window.RFLang={register:rows=>window.localeRows.push(...rows)};});
 for(const file of ['interface_locale.js','workflow_locale.js','import_guide_locale.js','import_review_locale.js'])await page.addScriptTag({path:path.join(root,'custom_frontend',file)});
 for(let language=0;language<10;language++){
  await page.evaluate(index=>{window.fixtureTranslate=key=>{const row=window.localeRows.findLast(row=>row[0]===key||row[1]===key);return row?.[index];};reset();return WorkflowImports.open(bid);},language);
  const expected=await page.evaluate(index=>window.localeRows.findLast(row=>row[1]==='1 imported record needs your review')[index],language);
  assert.equal(await page.locator('#wf-import-review h3').textContent(),expected);
  assert.equal(await page.locator('.wf-metadata-table tbody tr').count(),8);
 }
 // When review removes the last page, return to a valid attention page.
 await page.evaluate(()=>{
  window.remainingCount=35;api=async(url,options={})=>{
   const result=await window.originalApi(url,options);
   if(url.includes('/import-batches/'+bid)&&!url.includes('/candidates')&&url.includes('view=attention')){
    const offset=Number(new URL(url,'http://fixture').searchParams.get('offset')||0),all=Array.from({length:window.remainingCount},(_,i)=>({record_id:'page-'+i,state:'staged',attention_reason:'ready',article:{title:'Fictional pending '+i},original:{}}));
    Object.assign(result,{records:all.slice(offset,offset+20),view_total:all.length,next_offset:offset+20<all.length?offset+20:null,review_counts:{attention:all.length,ready:all.length,needs_review:0,completed:6}});
   }return result;
  };window.fixtureTranslate=null;return WorkflowImports.open(bid);
 });
 await page.locator('#wf-import-attention').getByRole('button',{name:'Next records',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('#wf-record-page-20'));
 await page.evaluate(()=>{window.remainingCount=19;document.dispatchEvent(new CustomEvent('reviewflow:workflow-changed',{detail:{task_id:'fictional',reason:'import-candidate-reviewed'}}));});
 await page.waitForFunction(()=>document.querySelectorAll('#wf-import-attention .wf-import-record').length===19);
 assert.equal(await page.locator('#wf-import-attention .wf-import-record').count(),19);
 assert.equal(await page.locator('#wf-record-page-0').count(),1);
 await page.evaluate(()=>{api=window.originalApi;return WorkflowImports.open(bid);});
 // Zero records are not a success while preparation has not finished.
 await page.evaluate(()=>{reset();records=[];api=async(url,options={})=>{const result=await window.originalApi(url,options);if(url.includes('/import-batches/'))result.status='preparing';return result;};window.fixtureTranslate=null;return WorkflowImports.open(bid);});
 assert.equal(await page.locator('#wf-import-review h3').textContent(),'Import is still being prepared');
 await page.evaluate(()=>{api=async(url,options={})=>{const result=await window.originalApi(url,options);if(url.includes('/import-batches/'))result.status='failed';return result;};return WorkflowImports.open(bid);});
 assert.equal(await page.locator('#wf-import-review h3').textContent(),'This import needs another attempt');
 await page.evaluate(()=>{reset();api=window.originalApi;return WorkflowImports.open(bid);});
 // Retain the previous resolved/total title-group progress information.
 await page.evaluate(()=>{api=async(url,options={})=>url.includes('title-duplicate-reviews')?{progress:{candidate_groups:8,pending_groups:3}}:window.originalApi(url,options);document.dispatchEvent(new CustomEvent('reviewflow:import-completed'));});
 await page.waitForFunction(()=>document.querySelector('#rfImportGuide .rf-guide-detail')?.textContent==='Title checks: 5/8 groups resolved');
 await page.evaluate(()=>{api=window.originalApi;document.dispatchEvent(new CustomEvent('reviewflow:import-completed'));});
 // Failed reads expose a keyboard-operable retry and clear loading state.
 await page.evaluate(()=>{api=async(url,options={})=>{if(url.includes('view=attention')&&!url.includes('/candidates'))throw Error('Fixture service unavailable');return window.originalApi(url,options);};return WorkflowImports.open(bid);});
 assert.match(await page.locator('#wf-import-review').innerText(),/Fixture service unavailable/);
 assert.equal(await page.locator('#wf-import-review').getAttribute('aria-busy'),null);
 await page.evaluate(()=>{api=window.originalApi;return WorkflowImports.open(bid);});
 await page.evaluate(()=>{api=async(url,options={})=>{if(url.includes('import-review-summary'))throw Error('Fixture summary unavailable');return window.originalApi(url,options);};document.dispatchEvent(new CustomEvent('reviewflow:import-completed'));});
 await page.waitForFunction(()=>document.querySelector('#rfImportGuide .rf-guide-error'));
 assert.equal(await page.locator('#rfImportGuide .rf-guide-body>button').isVisible(),true);
 await page.evaluate(()=>api=window.originalApi);
 await page.locator('#rfImportGuide .rf-guide-body>button').click();
 await page.waitForFunction(()=>!document.querySelector('#rfImportGuide .rf-guide-error'));
 await page.emulateMedia({reducedMotion:'reduce'});
 await page.evaluate(()=>{document.dispatchEvent(new CustomEvent('reviewflow:import-completed'));});
 await page.waitForFunction(()=>document.querySelector('#rfImportGuide li:nth-child(2) button')&&!document.querySelector('#rfImportGuide').hidden);
 await page.locator('#rfImportGuide li:nth-child(2) button').click();
 await page.waitForFunction(()=>document.activeElement.id==='wf-import-attention');
 assert.equal(await page.locator('#wf-import-attention').evaluate(x=>getComputedStyle(x).animationName),'none');
 assert.deepEqual(errors,[]);
 console.log('PASS: current production components; dynamic summary, collapsed/lazy completed records, eight-field diff, Keep/Accept effects, activation count/state, keyboard expansion, stale proposal disabled, four widths × two themes, guide navigation and completion, double-click guard, owner-only controls, ten languages, no runtime errors');
 await browser.close();
})().catch(error=>{console.error(error);process.exit(1);});
