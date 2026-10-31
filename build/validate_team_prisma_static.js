/* Parse the changed JavaScript and inline scripts without running the app. */
const fs=require('fs'),path=require('path'),vm=require('vm'),cp=require('child_process');
const root=path.resolve(__dirname,'..');
let changed=[],extra=[];
try{
  changed=cp.execFileSync('git',['diff','--name-only'],{cwd:root,encoding:'utf8'}).trim().split(/\r?\n/);
  extra=cp.execFileSync('git',['ls-files','--others','--exclude-standard'],{cwd:root,encoding:'utf8'}).trim().split(/\r?\n/);
}catch(_){
  /* Not a git checkout (e.g. a relocated or extracted Windows build tree, MM7-02 R1):
     fall back to every frontend script so the gate still runs outside git.
     CI and developer checkouts keep the git-diff augmentation unchanged. */
  changed=fs.readdirSync(path.join(root,'custom_frontend')).filter(p=>p.endsWith('.js')).map(p=>'custom_frontend/'+p);
}
/* Only files inside the two trees this gate parses are candidates. Without
   this, an untracked scratch tree in the checkout (a reviewer's .pytest_tmp/,
   an extracted archive) contributed browser-extension bundles and third-party
   ESM files to a CommonJS syntax check, failing the gate for reasons that have
   nothing to do with this repository's frontend. */
const owned=p=>/^(?:custom_frontend|build)\//.test(p);
changed=changed.filter(owned);
extra=extra.filter(owned);
const required=['custom_frontend/help_locale.js','custom_frontend/workflow_controls.js',
  'custom_frontend/prisma_task_ui.js',
  'custom_frontend/team_roles.js','custom_frontend/team_prisma_locale.js','build/validate_team_prisma_static.js'];
/* MM9-09: `git diff --name-only` also lists DELETED files (e.g. a dead module
   removed from the working tree); a deleted path has nothing to parse, and
   trying to read it crashed this gate between the deletion and its commit.
   Only the git-derived lists are existence-filtered: the `required` list is the
   hard contract of this gate and must FAIL when a required file is missing
   (an earlier revision filtered the union, which silently let a deleted
   team_roles.js / prisma_task_ui.js / help_locale.js / workflow_controls.js
   through). The reverse-manifest rule in script_manifest.cjs is what flags
   dead files now. */
const existing=p=>fs.existsSync(path.join(root,p));
const missingRequired=required.filter(p=>!existing(p));
if(missingRequired.length)throw Error('required validator input is missing: '+missingRequired.join(', '));
const files=[...new Set([...required,...changed.filter(existing),...extra.filter(existing)])]
  .filter(p=>p.endsWith('.js'));
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
