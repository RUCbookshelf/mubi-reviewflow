/* Parse the changed JavaScript and inline scripts without running the app. */
const fs=require('fs'),path=require('path'),vm=require('vm'),cp=require('child_process');
const root=path.resolve(__dirname,'..');
/* F1-64: no child of this gate may run without a deadline (a top-level infinite
   loop in a checked file used to hang the build forever; the git queries below
   are children too). */
const TIMEOUT_MS=Number(process.env.REVIEWFLOW_VALIDATOR_TIMEOUT_MS||20000);
let changed=[],extra=[];
try{
  changed=cp.execFileSync('git',['diff','--name-only'],{cwd:root,encoding:'utf8',timeout:TIMEOUT_MS}).trim().split(/\r?\n/);
  extra=cp.execFileSync('git',['ls-files','--others','--exclude-standard'],{cwd:root,encoding:'utf8',timeout:TIMEOUT_MS}).trim().split(/\r?\n/);
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
   dead files now. This is also the F1-77 guard: the intersection of "changed
   per git" with "still on disk" is empty for a deletion, so removing an
   ordinary module cannot raise a bare ENOENT from here (probed on bfd07ae:
   deleting title_duplicate_review.js exits 0 with no ENOENT). */
const existing=p=>fs.existsSync(path.join(root,p));
const missingRequired=required.filter(p=>!existing(p));
if(missingRequired.length)throw Error('required validator input is missing: '+missingRequired.join(', '));
const files=[...new Set([...required,...changed.filter(existing),...extra.filter(existing)])]
  .filter(p=>p.endsWith('.js'));
for(const file of files)new vm.Script(fs.readFileSync(path.join(root,file),'utf8'),{filename:file});
/* F1-78/F1-81: <script> blocks are located with a quote-aware attribute
   scanner. The old /<script\b([^>]*)>/ truncated at a '>' inside a quoted
   attribute (data-x="a>b"), skipped a tag merely because an attribute merely
   NAMED data-src existed, and case-sensitively accepted only
   type="application/json" as data - so ld+json/importmap blocks reached
   new vm.Script and crashed the gate, while data-x="a>b" was cut in half. */
function scriptBlocks(html){
  const blocks=[],open=/<script\b/gi;let match;
  while((match=open.exec(html))){
    let index=open.lastIndex,quote=null;
    for(;index<html.length;index++){
      const char=html[index];
      if(quote){if(char===quote)quote=null;}else if(char==='"'||char==="'")quote=char;else if(char==='>')break;
    }
    if(index>=html.length)throw Error('unterminated <script> tag in index.html');
    const end=html.toLowerCase().indexOf('</script>',index+1);
    if(end<0)throw Error('unterminated <script> block in index.html');
    blocks.push({attrs:html.slice(open.lastIndex,index),body:html.slice(index+1,end)});
    open.lastIndex=end+9;
  }
  return blocks;
}
function attributes(text){
  const map={};
  for(const match of text.matchAll(/([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g))
    map[match[1].toLowerCase()]=match[2]??match[3]??match[4]??'';
  return map;
}
/* Only blocks the browser executes as classic/module script. `src` is the exact
   attribute (never data-src) and every non-JS type - application/json,
   application/ld+json, importmap, speculationrules - is DATA, not code. */
const JS_TYPES=new Set(['','module','text/javascript','application/javascript']);
const isInlineScript=attrs=>{
  const map=attributes(attrs);
  if('src' in map)return false;
  return JS_TYPES.has((map.type||'').trim().toLowerCase());
};
const html=fs.readFileSync(path.join(root,'custom_frontend/index.html'),'utf8');
let inline=0;
for(const block of scriptBlocks(html)){
  if(!isInlineScript(block.attrs))continue;
  new vm.Script(block.body,{filename:'index.html:inline-'+(++inline)});
}
/* F1-75/F1-82: this block used to print status:'passed' unconditionally. When
   the register call was deleted from team_prisma_locale.js the stub never ran,
   languageRows stayed 0 and the gate still exited 0 - the localization contract
   was not checked at all. Fail-closed now: the locale pack must actually call
   register (a source that mentions RFLang.register but never invokes it is a
   dead registration) and the row count must be non-zero. */
const registrations=[];
const registerStub=file=>function(rows){
  registrations.push({file,rows});
  for(const row of rows){
    if(!Array.isArray(row)||row.length!==10||row.some(s=>typeof s!=='string'||!s.trim()))throw Error('Invalid ten-language row: '+(row&&row[1]));
    const placeholders=s=>[...String(s).matchAll(/\{(\d+)\}/g)].map(m=>m[1]).sort((a,b)=>a-b).join(',');
    const primary=placeholders(row[1]);
    if(row.some(s=>placeholders(s)!==primary))throw Error('Mismatched placeholders: '+row[1]);
    /* F1-79: aliases carry historical/cached wording and were never validated.
       Each alias group is one slot per language; every slot must be a non-blank
       string whose placeholders match that language's primary string, so an
       alias row cannot silently disagree with the row it aliases. */
    if(row.aliases!==undefined){
      if(!Array.isArray(row.aliases)||row.aliases.length===0||row.aliases.length%10!==0)
        throw Error('Invalid alias group for row: '+row[1]);
      row.aliases.forEach((alias,index)=>{
        if(typeof alias!=='string'||!alias.trim())throw Error('Blank alias in row: '+row[1]);
        const slot=index%10;
        if(placeholders(alias)!==placeholders(row[slot]))
          throw Error('Mismatched alias placeholders: '+row[slot]+' / '+alias);
      });
    }
  }
};
const teamPrisma=fs.readFileSync(path.join(root,'custom_frontend/team_prisma_locale.js'),'utf8');
vm.runInNewContext(teamPrisma,{RFLang:{register:registerStub('team_prisma_locale.js')}},{timeout:TIMEOUT_MS});
const locales=fs.readdirSync(path.join(root,'custom_frontend')).filter(p=>p.endsWith('_locale.js'));
for(const file of locales)new vm.Script(fs.readFileSync(path.join(root,'custom_frontend',file),'utf8'),{filename:file});
if(/\bRFLang\s*\.\s*register\s*\(/.test(teamPrisma)&&registrations.length===0)
  throw Error('dead RFLang.register call in team_prisma_locale.js (the call never executes)');
const languageRows=registrations.reduce((total,entry)=>total+entry.rows.length,0);
if(!languageRows)throw Error('team_prisma_locale.js registered no ten-language rows');
console.log(JSON.stringify({status:'passed',changed_js:files.length,inline_scripts:inline,language_pack_files:locales.length,ten_language_rows:languageRows},null,2));
