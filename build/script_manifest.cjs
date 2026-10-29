'use strict';
/* MM7-03: the online module load list, parsed from custom_frontend/index.html.
   build/validate_import_review_static.cjs and build/tests/import_review_browser.cjs
   resolve the modules they check/inject through this helper instead of
   hand-written lists, so a module that index.html never loads can no longer be
   mistaken for production code (the dead duplicate panel that motivated this
   was deleted outright in MM7-04).
   MM9-03: <script src> parsing alone was blind to the three PDF.js modules the
   page loads at runtime - two through `await import('./pdfjs/*.mjs')` and one
   through `GlobalWorkerOptions.workerSrc='./pdfjs/pdf.worker.min.mjs'`. Deleting
   any of them left the gate green while the native PDF viewer silently fell
   back to the built-in iframe viewer (loadPdfLibs catches the import failure;
   the import path itself is not interrupted). The parser now also reads those
   two dynamic-load forms, accepts .mjs specifiers, and asserts every manifest
   entry exists on disk, so a deleted pdfjs module fails the gate loudly. */
const fs=require('fs'),path=require('path');

function loadScriptManifest(root){
  const html=fs.readFileSync(path.join(root,'custom_frontend','index.html'),'utf8');
  const files=[];
  const push=(spec)=>{
    if(!/\.m?js(?:\?|$)/.test(spec))return;      /* JSON/data targets are not modules */
    if(/^([a-z]+:)?\/\//i.test(spec))return;     /* remote modules are not repo modules */
    let rel=spec.split('?')[0].split('#')[0];
    if(rel.startsWith('./'))rel=rel.slice(2);
    if(!rel||rel.startsWith('/')||rel.startsWith('../'))return; /* outside custom_frontend/ */
    const file=path.posix.normalize(rel);       /* keep subdirs: pdfjs/pdf.min.mjs */
    if(!files.includes(file))files.push(file);
  };
  for(const match of html.matchAll(/<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["'][^>]*>/gi))push(match[1]);
  for(const match of html.matchAll(/\bimport\s*\(\s*["']([^"']+)["']\s*\)/g))push(match[1]);
  for(const match of html.matchAll(/\bworkerSrc\s*=\s*["']([^"']+)["']/g))push(match[1]);
  if(!files.length)throw Error('no modules parsed from custom_frontend/index.html');
  for(const file of files)
    if(!fs.existsSync(path.join(root,'custom_frontend',file)))
      throw Error('manifest module is missing on disk: '+file);
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
