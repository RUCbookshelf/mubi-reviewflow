'use strict';
/* MM7-03/MM7-04: every module this gate checks is resolved from the online load
   list parsed out of custom_frontend/index.html (<script src>), never from a
   hand-written list. The live producer of window.WorkflowImports is
   unified_import_workspace.js; the dead duplicate panel that used to shadow it
   has been deleted outright, and findWorkspaceModule below fails loudly if a
   second producer ever reappears in the load list. */
const fs=require('fs'),path=require('path'),os=require('os'),cp=require('child_process'),vm=require('vm'),assert=require('assert/strict');
const {loadScriptManifest,findWorkspaceModule,assertNoUnloadedFrontendFiles}=require('./script_manifest.cjs');
const root=path.resolve(__dirname,'..'),frontend=path.join(root,'custom_frontend');
/* F1-64: every child that parses or executes frontend code gets a deadline. A
   top-level infinite loop in a checked file used to hang this gate forever (vm
   had no timeout and execFileSync had none either). Overridable so the
   regression nails can use a short budget. */
const TIMEOUT_MS=Number(process.env.REVIEWFLOW_VALIDATOR_TIMEOUT_MS||20000);
/* F1-62/F1-67: parse <script> tags with a real attribute scanner instead of
   /<script\b([^>]*)>/: a '>' inside a quoted attribute value (data-x="a>b")
   truncated the old match, and an attribute merely NAMED like a loader
   (data-src) was mistaken for src. */
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
/* A block the browser executes as classic/module script. `src` is the exact
   attribute (not data-src); every non-JS type (application/json, ld+json,
   importmap, speculationrules) is data, not code, and must be skipped rather
   than syntax-checked as JavaScript. */
const JS_TYPES=new Set(['','module','text/javascript','application/javascript']);
function isInlineScript(attrs){
  const map=attributes(attrs);
  if('src' in map)return false;
  return JS_TYPES.has((map.type||'').trim().toLowerCase());
}
const manifest=loadScriptManifest(root);
// MM9-09: reverse rule - a frontend file index.html never loads must fail the
// gate instead of silently shipping inside every package (mobile_workspace.*).
assertNoUnloadedFrontendFiles(root,manifest);
assert.ok(manifest.includes('unified_import_workspace.js'),'online load list must include unified_import_workspace.js');
// Syntax-check every module the browser actually loads, not a sample.
for(const file of manifest)cp.execFileSync(process.execPath,['--check',path.join(frontend,file)],{stdio:'pipe',timeout:TIMEOUT_MS});
const html=fs.readFileSync(path.join(frontend,'index.html'),'utf8'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'reviewflow-inline-'));let inline=0;
/* F1-67: the temp-tree containment assertions belong to SETUP, not to the
   finally block. In finally they were tautologies when the try body passed (the
   path is fixed by the two lines above) and, worse, they replaced the real
   syntax error with their own failure when rmSync raised EPERM. Here they can
   only fail before any child runs, and a cleanup failure is reported alongside
   (never instead of) the body's own error. */
assert.equal(path.dirname(path.resolve(temp)),path.resolve(os.tmpdir()),'inline temp dir must live in os.tmpdir()');
assert.match(path.basename(temp),/^reviewflow-inline-/,'inline temp dir must use the reviewflow-inline- prefix');
let failure=null;
try{
  for(const block of scriptBlocks(html)){
    if(!isInlineScript(block.attrs))continue;
    const file=path.join(temp,'inline-'+inline+'.js');fs.writeFileSync(file,block.body);
    cp.execFileSync(process.execPath,['--check',file],{stdio:'pipe',timeout:TIMEOUT_MS});inline++;
  }
}catch(error){failure=error;}
finally{
  try{fs.rmSync(temp,{recursive:true,force:true});}
  catch(cleanup){
    if(failure)console.error('inline temp cleanup failed after '+String(failure.message)+': '+String(cleanup.message));
    else throw cleanup;
  }
}
if(failure)throw failure;
const rows=[],ctx={RFLang:{register:r=>rows.push(...r)},document:{querySelectorAll:()=>[],addEventListener:()=>{}}};ctx.window=ctx;
for(const file of fs.readdirSync(frontend).filter(f=>f.endsWith('_locale.js')))vm.runInNewContext(fs.readFileSync(path.join(frontend,file),'utf8'),ctx,{timeout:TIMEOUT_MS});
const english=new Set(rows.map(r=>r[1]));
/* F1-65: this used to be `html.indexOf(model)<html.indexOf(guide)`. When the
   model's <script src> tag was deleted, indexOf returned the -1 sentinel and
   -1 < <first index> held, so the gate stayed green with exit 0 while the
   browser's metadata model was gone. The presence decision is now taken on the
   parsed online load list (findWorkspaceModule already reads only manifest
   modules), and both required modules are checked by name below - not by a
   substring position that can be -1. */
const workspace=findWorkspaceModule(root,manifest);
const source=fs.readFileSync(path.join(frontend,workspace),'utf8');
/* F1-66: string literals are unescaped before comparison. The old extractor
   matched raw bytes, so `Don\'t` produced a trailing-backslash phantom key and
   the real display string (or a genuinely missing one) was never compared.
   F1-63: the coverage rule is no longer "single-quoted runs of 8+ capitals".
   Every string the workspace uses through a display helper (T/n/l/b/write/
   details) is a user-visible string at any length, and every capital-initial
   literal of 3+ characters ("DOI", "Ctrl Z", "Mac") is a label; each must be
   registered in a *_locale.js pack. A short escape hatch would let exactly the
   strings the inventory named (Authors/Year/Journal/DOI) slide through again. */
const JAVASCRIPT_ESCAPE=/\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|[\s\S])/g;
const ESCAPE_TEXT={n:'\n',r:'\r',t:'\t',b:'\b',f:'\f',v:'\v','0':'\0'};
function unescapeLiteral(value){
  return value.replace(JAVASCRIPT_ESCAPE,(escape,code)=>{
    if(code.length===5&&code[0]==='u')return String.fromCharCode(parseInt(code.slice(1),16));
    if(code.length===3&&code[0]==='x')return String.fromCharCode(parseInt(code.slice(1),16));
    return ESCAPE_TEXT[code]??code;
  });
}
/* Identifiers/keys that are not translatable text: protocol words, platform
   names and key names shown by the browser itself. Kept deliberately tiny and
   semantically closed - a new display string must be registered, not allowed. */
const NOT_DISPLAY_TEXT=new Set(['DOI','URL','POST','GET','PUT','PATCH','DELETE',
  'Mac','Windows','Linux','Ctrl Z','\u2318 Z','ArrowRight','ArrowLeft','ArrowUp','ArrowDown','Home','End']);
const DISPLAY_HELPERS=[[/\bT\('((?:[^'\\\n]|\\.)*)'/g,'T()'],
  [/\bn\('[^']*','((?:[^'\\\n]|\\.)*)'/g,'n()'],
  [/\bl\('[^']*','((?:[^'\\\n]|\\.)*)'/g,'l()'],
  [/\bb\('((?:[^'\\\n]|\\.)*)'/g,'b()'],
  [/\bwrite\('((?:[^'\\\n]|\\.)*)'/g,'write()'],
  [/\.details\('((?:[^'\\\n]|\\.)*)'/g,'details()']];
const unregistered=[];
for(const pattern of DISPLAY_HELPERS)
  for(const match of source.matchAll(pattern)){
    const text=unescapeLiteral(match[1]);
    if(!/[A-Za-z\u00c0-\u024f\u0400-\u04ff\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]/.test(text))continue;
    if(NOT_DISPLAY_TEXT.has(text)||english.has(text))continue;
    unregistered.push(text);
  }
for(const match of source.matchAll(/'((?:[^'\\\n]|\\.)*)'/g)){
  const text=unescapeLiteral(match[1]);
  if(text.length<3||!/^[A-Z]/.test(text)||NOT_DISPLAY_TEXT.has(text)||english.has(text))continue;
  unregistered.push(text);
}
const missing=[...new Set(unregistered)];
assert.deepEqual(missing,[],'Unregistered display strings in '+workspace);
/* F1-65: this used to be `html.indexOf(model)<html.indexOf(guide)`. When the
   model's <script src> tag was deleted, indexOf returned the -1 sentinel and
   -1 < <first index> held, so the gate stayed green with exit 0 while the
   browser's metadata model was gone. Both modules must be present in the
   parsed online load list (not a substring position), and the load order is
   then compared on real positions. */
for(const required of ['import_review_model.js','import_guide.js'])
  assert.ok(manifest.includes(required),'index.html must load '+required);
for(const field of require(path.join(frontend,'import_review_model.js')).comparison({})){if(!['DOI','URL'].includes(field.label))assert.ok(english.has(field.label),'Unregistered metadata field: '+field.label);}
const modelPosition=html.indexOf('import_review_model.js'),guidePosition=html.indexOf('import_guide.js');
assert.ok(modelPosition>=0&&guidePosition>=0&&modelPosition<guidePosition,'import_review_model.js must load before import_guide.js');
console.log(JSON.stringify({syntax_files:manifest.length,inline_scripts:inline,workspace_module:workspace,new_panel_translation_coverage:'complete'}));
