'use strict';
/* MM7-03/MM7-04: every module this gate checks is resolved from the online load
   list parsed out of custom_frontend/index.html (<script src>), never from a
   hand-written list. The live producer of window.WorkflowImports is
   unified_import_workspace.js; the dead duplicate panel that used to shadow it
   has been deleted outright, and findWorkspaceModule below fails loudly if a
   second producer ever reappears in the load list. */
const fs=require('fs'),path=require('path'),os=require('os'),cp=require('child_process'),vm=require('vm'),assert=require('assert/strict');
const {loadScriptManifest,findWorkspaceModule}=require('./script_manifest.cjs');
const root=path.resolve(__dirname,'..'),frontend=path.join(root,'custom_frontend');
const manifest=loadScriptManifest(root);
assert.ok(manifest.includes('unified_import_workspace.js'),'online load list must include unified_import_workspace.js');
// Syntax-check every module the browser actually loads, not a sample.
for(const file of manifest)cp.execFileSync(process.execPath,['--check',path.join(frontend,file)],{stdio:'pipe'});
const html=fs.readFileSync(path.join(frontend,'index.html'),'utf8'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'reviewflow-inline-'));let inline=0;
try{for(const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)){if(/\bsrc=|application\/json/.test(match[1]))continue;const file=path.join(temp,'inline-'+inline+'.js');fs.writeFileSync(file,match[2]);cp.execFileSync(process.execPath,['--check',file],{stdio:'pipe'});inline++;}}finally{assert.equal(path.dirname(path.resolve(temp)),path.resolve(os.tmpdir()));assert.match(path.basename(temp),/^reviewflow-inline-/);fs.rmSync(temp,{recursive:true,force:true});}
const rows=[],ctx={RFLang:{register:r=>rows.push(...r)},document:{querySelectorAll:()=>[],addEventListener:()=>{}}};ctx.window=ctx;
for(const file of fs.readdirSync(frontend).filter(f=>f.endsWith('_locale.js')))vm.runInNewContext(fs.readFileSync(path.join(frontend,file),'utf8'),ctx);
const english=new Set(rows.map(r=>r[1]));
// The display-string source is the module the browser loads to build window.WorkflowImports.
const workspace=findWorkspaceModule(root,manifest);
const source=fs.readFileSync(path.join(frontend,workspace),'utf8');
const missing=[...new Set([...source.matchAll(/'([^'\n]{8,})'/g)].map(x=>x[1]))].filter(x=>/^[A-Z]/.test(x)&&!english.has(x));
assert.deepEqual(missing,[],'Unregistered display strings in '+workspace);
for(const field of require('../custom_frontend/import_review_model.js').comparison({})){if(!['DOI','URL'].includes(field.label))assert.ok(english.has(field.label),'Unregistered metadata field: '+field.label);}
assert.ok(html.indexOf('import_review_model.js')<html.indexOf('import_guide.js'));
console.log(JSON.stringify({syntax_files:manifest.length,inline_scripts:inline,workspace_module:workspace,new_panel_translation_coverage:'complete'}));
