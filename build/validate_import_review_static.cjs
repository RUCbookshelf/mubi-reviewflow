'use strict';
const fs=require('fs'),path=require('path'),os=require('os'),cp=require('child_process'),vm=require('vm'),assert=require('assert/strict');
const root=path.resolve(__dirname,'..'),frontend=path.join(root,'custom_frontend');
const locales=fs.readdirSync(frontend).filter(f=>f.endsWith('_locale.js'));
const changed=['workflow_imports.js','import_guide.js','import_review_model.js',...locales];
for(const file of changed)cp.execFileSync(process.execPath,['--check',path.join(frontend,file)],{stdio:'pipe'});
const html=fs.readFileSync(path.join(frontend,'index.html'),'utf8'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'reviewflow-inline-'));let inline=0;
try{for(const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)){if(/\bsrc=|application\/json/.test(match[1]))continue;const file=path.join(temp,'inline-'+inline+'.js');fs.writeFileSync(file,match[2]);cp.execFileSync(process.execPath,['--check',file],{stdio:'pipe'});inline++;}}finally{assert.equal(path.dirname(path.resolve(temp)),path.resolve(os.tmpdir()));assert.match(path.basename(temp),/^reviewflow-inline-/);fs.rmSync(temp,{recursive:true,force:true});}
const rows=[],ctx={RFLang:{register:r=>rows.push(...r)},document:{querySelectorAll:()=>[],addEventListener:()=>{}}};ctx.window=ctx;
for(const file of locales)vm.runInNewContext(fs.readFileSync(path.join(frontend,file),'utf8'),ctx);
const english=new Set(rows.map(r=>r[1])),source=fs.readFileSync(path.join(frontend,'workflow_imports.js'),'utf8');
const missing=[...new Set([...source.matchAll(/'([^'\n]{8,})'/g)].map(x=>x[1]))].filter(x=>/^[A-Z]/.test(x)&&!english.has(x));
assert.deepEqual(missing,[],'Unregistered display strings');
for(const field of require('../custom_frontend/import_review_model.js').comparison({})){if(!['DOI','URL'].includes(field.label))assert.ok(english.has(field.label),'Unregistered metadata field: '+field.label);}
assert.ok(html.indexOf('import_review_model.js')<html.indexOf('import_guide.js'));
console.log(JSON.stringify({syntax_files:changed.length,inline_scripts:inline,new_panel_translation_coverage:'complete'}));
