'use strict';
/* MM7-03: the online module load list, parsed from custom_frontend/index.html.
   build/validate_import_review_static.cjs and build/tests/import_review_browser.cjs
   resolve the modules they check/inject through this helper instead of
   hand-written lists, so a module that index.html never loads (the dead
   workflow_imports.js) can no longer be mistaken for production code. */
const fs=require('fs'),path=require('path');

function loadScriptManifest(root){
  const html=fs.readFileSync(path.join(root,'custom_frontend','index.html'),'utf8');
  const files=[];
  for(const match of html.matchAll(/<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["'][^>]*>/gi)){
    const src=match[1];
    if(!/\.js(?:\?|$)/.test(src))continue;      /* JSON/data targets are not modules */
    if(/^([a-z]+:)?\/\//i.test(src))continue;   /* remote scripts are not repo modules */
    const file=path.posix.basename(src.split('?')[0]);
    if(!files.includes(file))files.push(file);
  }
  if(!files.length)throw Error('no <script src> modules parsed from custom_frontend/index.html');
  return files;
}

/* The live import-review workspace is whichever module the browser loads to build
   window.WorkflowImports - resolved from content, never from a hardcoded name.
   Exactly one producer must exist; two producers (the MM7-04 dead-module conflict)
   fail loudly here. */
function findWorkspaceModule(root,manifest){
  const list=manifest||loadScriptManifest(root);
  const producers=list.filter(file=>/window\.WorkflowImports\s*=/.test(
    fs.readFileSync(path.join(root,'custom_frontend',file),'utf8')));
  if(producers.length!==1)
    throw Error('expected exactly one online module to define window.WorkflowImports, found: '
      +(producers.length?producers.join(', '):'none'));
  return producers[0];
}

/* Resolve module names a harness still refers to by role through the manifest:
   if a module stops being loaded online, this throws instead of silently
   validating an unloaded file. */
function manifestFiles(manifest,names){
  return names.map(name=>{
    if(!manifest.includes(name))throw Error('module is not loaded online: '+name);
    return name;
  });
}

/* The unique online module that defines the given global (window.X=, the UMD
   root.X= form, or a top-level declaration). null when no module - or more
   than one - defines it, so callers never inject an ambiguous module. */
function manifestModuleDefining(root,manifest,globalName){
  const assignment=new RegExp('(?:window|root|globalThis)\\s*\\.\\s*'+globalName+'\\s*=');
  const declaration=new RegExp('(?:const|let|var|function)\\s+'+globalName+'\\b');
  const definers=manifest.filter(file=>{
    const source=fs.readFileSync(path.join(root,'custom_frontend',file),'utf8');
    return assignment.test(source)||declaration.test(source);
  });
  return definers.length===1?definers[0]:null;
}

/* Online modules whose source matches EVERY given RegExp. This is how harnesses
   discover subjects by content (e.g. the onboarding guide owns #rfImportGuide
   and reads window.WorkflowImports) instead of by hardcoded filename. */
function manifestModulesMatching(root,manifest,patterns){
  return manifest.filter(file=>{
    const source=fs.readFileSync(path.join(root,'custom_frontend',file),'utf8');
    return patterns.every(pattern=>pattern.test(source));
  });
}

module.exports={loadScriptManifest,findWorkspaceModule,manifestFiles,
  manifestModuleDefining,manifestModulesMatching};
