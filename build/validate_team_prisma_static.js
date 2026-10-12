/* Parse the changed JavaScript and inline scripts without running the app. */
const fs=require('fs'),path=require('path'),vm=require('vm'),cp=require('child_process');
const root=path.resolve(__dirname,'..');
const changed=cp.execFileSync('git',['diff','--name-only'],{cwd:root,encoding:'utf8'}).trim().split(/\r?\n/);
const extra=cp.execFileSync('git',['ls-files','--others','--exclude-standard'],{cwd:root,encoding:'utf8'}).trim().split(/\r?\n/);
const required=['custom_frontend/help_locale.js','custom_frontend/workflow_controls.js',
  'custom_frontend/workflow_imports.js','custom_frontend/prisma_task_ui.js',
  'custom_frontend/team_roles.js','custom_frontend/team_prisma_locale.js','build/validate_team_prisma_static.js'];
const files=[...new Set([...required,...changed,...extra])].filter(p=>p.endsWith('.js'));
for(const file of files)new vm.Script(fs.readFileSync(path.join(root,file),'utf8'),{filename:file});
const html=fs.readFileSync(path.join(root,'custom_frontend/index.html'),'utf8');
let inline=0;
for(const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)){
  if(/\bsrc\s*=/.test(match[1])||/type\s*=\s*['"]application\/json/.test(match[1]))continue;
  new vm.Script(match[2],{filename:'index.html:inline-'+(++inline)});
}
let languageRows=0;
vm.runInNewContext(fs.readFileSync(path.join(root,'custom_frontend/team_prisma_locale.js'),'utf8'),{RFLang:{register(rows){
  for(const row of rows){
    if(row.length!==10||row.some(s=>typeof s!=='string'||!s.trim()))throw Error('Invalid ten-language row: '+row[1]);
    const placeholders=s=>[...s.matchAll(/\{(\d+)\}/g)].map(m=>m[1]).sort().join(',');
    if(row.some(s=>placeholders(s)!==placeholders(row[1])))throw Error('Mismatched placeholders: '+row[1]);
    languageRows++;
  }
}}});
const locales=fs.readdirSync(path.join(root,'custom_frontend')).filter(p=>p.endsWith('_locale.js'));
for(const file of locales)new vm.Script(fs.readFileSync(path.join(root,'custom_frontend',file),'utf8'),{filename:file});
console.log(JSON.stringify({status:'passed',changed_js:files.length,inline_scripts:inline,language_pack_files:locales.length,ten_language_rows:languageRows},null,2));
