'use strict';
/* MM7-03 (R1 rework) + MM7-04: this harness drives the LIVE import-review panel.
   Every injected module is resolved from the online load list parsed out of
   custom_frontend/index.html: the panel under test is the manifest's unique
   window.WorkflowImports producer, its dependency modules are discovered from
   the global identifiers that module actually references, and the onboarding
   guide is discovered as the manifest module that both reads the panel global
   and creates #rfImportGuide. The dead duplicate panel was deleted (MM7-04), so
   only online modules exist to inject. DOM/stub mechanics follow
   import_review_refined_browser.cjs, the proven harness for this panel. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.REVIEWFLOW_PLAYWRIGHT||'playwright');
const {loadScriptManifest,findWorkspaceModule,manifestFiles,manifestModuleDefining,manifestModulesMatching}=require('../script_manifest.cjs');
const root=path.resolve(__dirname,'../..'),frontend=path.join(root,'custom_frontend');
const manifest=loadScriptManifest(root);
assert.ok(manifest.includes('unified_import_workspace.js'),'online manifest must load unified_import_workspace.js');
const workspace=findWorkspaceModule(root,manifest);
const workspaceSource=fs.readFileSync(path.join(frontend,workspace),'utf8');
// Globals this harness stubs itself (see page.evaluate below); their defining
// modules are deliberately NOT injected - the same isolation the refined
// harness uses. ImportExportWorkspace keeps its real helper prefix (sliced
// below), because the panel builds its DOM through those helpers.
const stubbed=new Set(['WorkflowControls','ImportExportWorkspace','RFLang']);
const dependencies=[...new Set([...workspaceSource.match(/\b[A-Z][A-Za-z0-9_]*\b/g)||[]]
  .filter(name=>name.length>1&&!stubbed.has(name))
  .map(name=>manifestModuleDefining(root,manifest,name))
  .filter(file=>file&&file!==workspace))];
const guides=manifestModulesMatching(root,manifest,[/window\.WorkflowImports/,/id:\s*'rfImportGuide'/]);
assert.equal(guides.length,1,'expected exactly one online guide module owning #rfImportGuide: '+guides.join(', '));
const exportHelpers=manifestModuleDefining(root,manifest,'ImportExportWorkspace');
assert.ok(exportHelpers,'ImportExportWorkspace helpers must come from an online module');
const injected=[...new Set([...dependencies,workspace,...guides])]
  .sort((a,b)=>manifest.indexOf(a)-manifest.indexOf(b));
// Stability tripwire: if the live panel's online dependency set drifts, this
// fails and the list is updated consciously - never silently re-pointed.
assert.deepEqual([...injected].sort(),['import_guide.js','import_review_loading.js','import_review_model.js','import_review_updates.js',workspace].sort(),'injected live modules: '+injected.join(', '));
const locales=manifestFiles(manifest,['interface_locale.js','workflow_locale.js','import_review_locale.js','import_export_locale.js','import_export_help_locale.js','import_review_refined_locale.js','import_guide_locale.js']);
(async()=>{
 const browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.setContent('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>:root{--bg:#f4f8f7;--surface:#fff;--ink:#172b32;--muted:#60757c;--primary:#2f6f6a;--border:#d5e3e1;--danger:#ac573e}[data-theme=dark]{--bg:#0e1217;--surface:#171d23;--ink:#e7eef0;--muted:#a1b0b9;--primary:#78a7a2;--border:#333d49}body{margin:0;background:var(--bg);color:var(--ink);font:16px system-ui}.view{height:100vh;overflow:auto;padding:16px;box-sizing:border-box}button,input,select{font:inherit}pre{white-space:pre-wrap;overflow-wrap:anywhere}[hidden]{display:none!important}</style></head><body><div id="appMain" class="view"><section class="page rf-import-page"><div id="review"></div></section></div></body></html>');
 await page.evaluate(()=>{
  window.localeRows=[];window.localeIndex=1;window.RFLang={get:()=>['zh','en','fr','ru','es','ja','pt','de','sr','ko'][localeIndex],register:rows=>localeRows.push(...rows)};
  window.T=(s,...args)=>{const row=localeRows.findLast(r=>r[1]===s||r[0]===s);return(row?.[localeIndex]||s).replace(/\{(\d+)\}/g,(_,i)=>args[i]??'{'+i+'}');};
  window.el=(tag,attrs={})=>{const x=document.createElement(tag);for(const[k,v]of Object.entries(attrs)){if(k==='text')x.textContent=v;else if(k==='onclick')x.onclick=v;else x.setAttribute(k,v);}return x;};
  window.S={user:{username:'Preview owner'},task:{task_id:'fixture'},screener:'Preview owner',page:'import'};
  window.taskApi=()=>'/fixture';window.scrQ=()=>'';window.renderImportReport=()=>{};window.refreshProgress=()=>new Promise(r=>setTimeout(r,20));window.toast=()=>{};window.go=async()=>true;
  window.version=1;window.failure=false;window.calls=[];
  window.WorkflowControls={scope:()=>'fixture',settings:()=>({can_manage:true}),version:()=>version,acceptImportVersion:v=>version=v,refresh:async()=>{},error:e=>{},errorText:e=>e.message};
  const before={title:'Teachers professional identity: a longitudinal study',authors:'Rivera, Ana; Chen, Mei',year:2023,journal:'Teaching and Teacher Education',doi:'10.example/identity',abstract:'A longitudinal investigation of teachers professional identity.',item_type:'JOUR',url:''};
  const incoming={...before,title:'Teachers professional identity: a three-year longitudinal study',year:2024,abstract:before.abstract+' The incoming record supplies a revised abstract and publication year.'};
  const metaRecord=()=>({record_id:'meta',state:'staged',attention_reason:'metadata',filename:'WOS-demo.ris',row_index:8,article:structuredClone(before),original:{TY:'JOUR',TI:before.title},metadata:{before:structuredClone(before),incoming:structuredClone(incoming),action:null,accepted:null,stale:false}});
  const doneRecord=i=>({record_id:'done'+i,state:'active',attention_reason:'completed',filename:'WOS-demo.ris',row_index:i+1,article:{title:['Teacher agency and professional development','Narrative inquiry in teacher education','Classroom discourse and learner participation'][i%3]},original:{TY:'JOUR'}});
  window.freshRecords=()=>[metaRecord(),...Array.from({length:6},(_,i)=>doneRecord(i))];
  window.recordData=window.freshRecords();
  window.reviewCounts=()=>({attention:recordData.filter(r=>r.attention_reason!=='completed').length,completed:recordData.filter(r=>r.attention_reason==='completed').length,ready:recordData.filter(r=>r.attention_reason==='ready').length,needs_review:recordData.filter(r=>r.attention_reason==='metadata'&&r.metadata?.action==null).length});
  window.summary=()=>({batch_id:'fixture-batch',created_at:'2026-10-29T10:00:00',files:[{filename:'WOS-demo.ris'},{filename:'Scopus-demo.ris'}],raw_source_records:recordData.length,active_records:recordData.filter(r=>r.state==='active').length,pending_records:recordData.filter(r=>r.state==='staged').length,metadata_review_count:recordData.filter(r=>r.metadata&&r.metadata.action==null).length,pending_candidate_groups:0,source_records:recordData.length,can_manage:true,undo:{available:false},policy_version:version});
  window.api=async(url,options={})=>{
   calls.push({url,method:options.method||'GET'});
   if(options.method==='POST'){
    await new Promise(r=>setTimeout(r,40));if(failure)throw Error('Fixture: save failed');
    if(url.includes('/metadata/')){const record=recordData.find(r=>r.record_id===url.split('/').pop());
     record.metadata.action=options.body.action;
     record.metadata.accepted=structuredClone(options.body.action==='accept_incoming'?record.metadata.incoming:record.metadata.before);
     if(options.body.action==='accept_incoming')record.article=structuredClone(record.metadata.incoming);
     version++;return{...summary(),review_updates:{records:[structuredClone(record)],candidates:[],review_counts:reviewCounts()}};}
    throw Error('Fixture: unexpected POST '+url);
   }
   if(url.includes('import-review-summary'))return summary();
   if(url.includes('/import-history'))return{pending_import_records:recordData.filter(r=>r.state==='staged').length,imports:[{result:{imported:8}}]};
   if(url.includes('title-duplicate'))return{groups:[],total:0,undo_available:false,progress:{candidate_groups:8,pending_groups:3}};
   if(url.includes('/candidates'))return{candidates:[],total:0,next_offset:null};
   const u=new URL(url,'http://fixture'),offset=Number(u.searchParams.get('offset')||0),view=u.searchParams.get('view');
   const list=view==='completed'?recordData.filter(r=>r.attention_reason==='completed'):
     view==='all'?recordData:recordData.filter(r=>r.attention_reason!=='completed');
   return{...summary(),records:structuredClone(list.slice(offset,offset+20)),view:view||'attention',view_total:list.length,next_offset:offset+20<list.length?offset+20:null,review_counts:reviewCounts()};
  };
 });
 for(const file of locales)await page.addScriptTag({path:path.join(frontend,file)});
 // The live export-workspace helper prefix, cut exactly where the refined harness
 // cuts it: helper definitions only, none of the full-page bootstrap.
 const ui=fs.readFileSync(path.join(frontend,exportHelpers),'utf8');
 await page.addScriptTag({content:ui.slice(0,ui.indexOf(' UI.tabs='))+'})();'});
 await page.evaluate(()=>{ImportExportWorkspace.reviewHost=document.getElementById('review');ImportExportWorkspace.importPolicy=()=>{};ImportExportWorkspace.showImport=()=>{};});
 for(const file of ['import_review.css','import_export_workspace.css','import_guide.css'])await page.addStyleTag({path:path.join(frontend,file)});
 for(const file of injected)await page.addScriptTag({path:path.join(frontend,file)});
 // Open the live panel on the fixture batch and check its tab bar.
 await page.evaluate(()=>WorkflowImports.open('fixture-batch'));
 await page.waitForSelector('#wf-import-review nav.tabs [role=tab]');
 assert.equal(await page.locator('#wf-import-review nav.tabs [role=tab]').count(),5);
 await page.getByRole('tab',{name:'Information differences'}).click();
 await page.waitForSelector('.wf-metadata-table');
 assert.equal(await page.locator('.wf-metadata-table tbody tr').count(),8);
 // Keep existing metadata: one POST, the diff still shows the kept year.
 await page.getByRole('button',{name:'Keep existing metadata',exact:true}).click();
 await page.waitForFunction(()=>calls.filter(x=>x.method==='POST').length===1);
 await page.waitForFunction(()=>document.querySelector('.status-message')?.textContent==='Saved');
 assert.equal(await page.evaluate(()=>{const row=[...document.querySelectorAll('.wf-metadata-table tbody tr')].find(r=>r.querySelector('th').textContent==='Year');return row&&row.querySelectorAll('td')[0].textContent;}),'2023');
 // The live panel renders both action buttons only while the proposal is
 // pending (pending = action == null, unified_import_workspace.js recordReader),
 // and the Keep click above just decided this record. Reset the fixture data
 // and reopen the batch so Accept runs against a fresh pending proposal.
 await page.evaluate(()=>{recordData=freshRecords();return WorkflowImports.open('fixture-batch');});
 // Accept incoming metadata: the incoming year takes over the record. The api
 // stub delays POST effects by 40ms after recording the call, so wait for the
 // record effect itself - never for the call count.
 await page.getByRole('button',{name:'Accept incoming metadata',exact:true}).click();
 await page.waitForFunction(()=>{const record=recordData.find(r=>r.record_id==='meta');return record&&record.metadata.action==='accept_incoming'&&record.article.year===2024;});
 await page.waitForFunction(()=>{const row=[...document.querySelectorAll('.wf-metadata-table tbody tr')].find(r=>r.querySelector('th').textContent==='Year');return row&&row.querySelectorAll('td')[1].textContent==='2024';});
 assert.equal(await page.evaluate(()=>recordData.find(r=>r.record_id==='meta').article.year),2024);
 // The live onboarding guide (also resolved from the online manifest) tracks progress.
 assert.equal(await page.locator('#rfImportGuide').getAttribute('hidden'),null);
 assert.equal(await page.locator('#rfImportGuide .rf-guide-count').textContent(),'1/4 completed');
 // All ten languages re-render the live tabs from the shared registry.
 for(let language=0;language<10;language++){
  const expected=await page.evaluate(index=>window.localeRows.findLast(row=>row[1]==='Information differences')[index],language);
  await page.evaluate(index=>{localeIndex=index;document.dispatchEvent(new CustomEvent('reviewflow:language-changed'));},language);
  assert.ok((await page.locator('#wf-review-tab-metadata').textContent()).startsWith(expected),'language '+language+': '+await page.locator('#wf-review-tab-metadata').textContent());
 }
 await page.evaluate(()=>{localeIndex=1;document.dispatchEvent(new CustomEvent('reviewflow:language-changed'));});
 assert.deepEqual(errors,[]);
 console.log('PASS live import-review panel via manifest-resolved modules: five tabs, eight-field metadata diff, Keep/Accept effects, guide checklist 1/4, ten languages, no runtime errors');
 await browser.close();
})().catch(error=>{console.error(error);process.exit(1);});
