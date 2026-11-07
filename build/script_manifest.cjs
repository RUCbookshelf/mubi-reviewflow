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

/* b08·F1-62 (r9 L7c): an attribute merely NAMED like a loader (data-src) is not
   `src`, and a '>' inside a quoted attribute value does not end the tag. The old
   /<script\b[^>]*\bsrc\s*=/ treated data-src="./ghost.js" as a loaded module (the
   manifest then demanded a file the browser never loads) and stopped the match at
   a quoted '>' (a real loader silently fell out of the manifest). Same scanner
   discipline as build/validate_import_review_static.cjs: read the tag to its
   real '>', then read attributes positionally instead of regexing the raw text. */
function scriptTags(html){
  const tags=[],open=/<script\b/gi;let match;
  while((match=open.exec(html))){
    let index=open.lastIndex,quote=null;
    for(;index<html.length;index++){
      const char=html[index];
      if(quote){if(char===quote)quote=null;}else if(char==='"'||char==="'")quote=char;else if(char==='>')break;
    }
    if(index>=html.length)throw Error('unterminated <script> tag in index.html');
    tags.push(html.slice(open.lastIndex,index));
    open.lastIndex=index+1;
  }
  return tags;
}
function attributes(text){
  const map={};
  for(const match of text.matchAll(/([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g))
    map[match[1].toLowerCase()]=match[2]??match[3]??match[4]??'';
  return map;
}
/* Only the exact `src` attribute is a loader; data-src/lazy-src/anything else
   is not. Values a scanner cannot read as an attribute are ignored rather than
   guessed, so a malformed tag can never invent a module. */
function scriptSrcs(html){
  return scriptTags(html)
    .map(attrs=>attributes(attrs).src)
    .filter(src=>src!==undefined&&src!=='');
}

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
  for(const spec of scriptSrcs(html))push(spec);
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

/* MM9-09: the manifest used to be a one-way street - it could tell which
   modules a harness MAY inject, but a frontend file that index.html never
   loads (mobile_workspace.js/.css) shipped in every package unnoticed because
   the packaging scripts copy the whole directory. This reverse rule fails the
   gate while any dead top-level frontend file exists:
   * every top-level custom_frontend/*.js/.mjs must appear in the online manifest;
   * every top-level custom_frontend/*.css must be referenced by index.html.
   Subdirectories (pdfjs/ internals) are exempt - they are reached through
   the modules that load them.

   Two traps this rule must not fall into (both found in review):
   * a bare `html.includes(file)` is a SUBSTRING test: a dead `concept.css`
     passes merely because index.html links `mobile_concept.css`. CSS
     references are therefore matched as whole quoted specifiers.
   * a dead file must fail even when its name is a suffix of a live one, so
     both checks anchor on a word/quote boundary instead of raw containment.

   Reachability is not only `<script src>`. A Service Worker (2.1.4 W5:
   custom_frontend/sw.js) is never a script element - index.html reaches it
   through `navigator.serviceWorker.register('./sw.js', {scope:'./'})`, so the
   manifest parser (which reads script src / dynamic import / workerSrc)
   legitimately never lists it. Treating that file as dead would force the PWA
   shell to be either deleted or smuggled into the manifest as a lie. The rule
   therefore also accepts top-level JS reachable through a quoted
   `serviceWorker.register(...)` specifier resolved from index.html. This is a
   form-of-reachability exemption, not a filename allowlist: an unregistered
   sw.js (or any other unreferenced JS) still fails. */
const CSS_REFERENCE=(html,file)=>{
  const escaped=file.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  return new RegExp('(?:^|["\'\\s(/,])'+escaped+'(?:["\'\\s)?,#]|$)').test(html);
};
/* Files index.html reaches through a Service Worker registration, as repo-relative
   paths under custom_frontend/ (same normalization as loadScriptManifest). */
const serviceWorkerSpecs=(html)=>{
  const out=[];
  for(const match of html.matchAll(/serviceWorker\s*\.\s*register\s*\(\s*["']([^"']+)["']/g)){
    let rel=match[1].split('?')[0].split('#')[0];
    if(rel.startsWith('./'))rel=rel.slice(2);
    if(!rel||rel.startsWith('/')||rel.startsWith('../'))continue; /* outside custom_frontend/ */
    out.push(path.posix.normalize(rel));
  }
  return out;
};
function assertNoUnloadedFrontendFiles(root,manifest){
  const dir=path.join(root,'custom_frontend');
  const list=manifest||loadScriptManifest(root);
  const html=fs.readFileSync(path.join(dir,'index.html'),'utf8');
  const swFiles=serviceWorkerSpecs(html);
  const problems=[];
  for(const file of fs.readdirSync(dir)){
    if(!/\.(?:js|mjs|css)$/.test(file))continue;
    if(!fs.statSync(path.join(dir,file)).isFile())continue;
    if(file.endsWith('.css')){
      if(!CSS_REFERENCE(html,file))
        problems.push('custom_frontend/'+file+' is not referenced by index.html - dead file (delete it or link it)');
    }else if(!list.includes(file)&&!swFiles.includes(file)){
      problems.push('custom_frontend/'+file+' is not loaded by index.html - dead file (delete it or load it)');
    }
  }
  if(problems.length)
    throw Error('MM9-09 reverse manifest rule: '+problems.join('; '));
}

module.exports={loadScriptManifest,findWorkspaceModule,manifestFiles,
  manifestModuleDefining,manifestModulesMatching,assertNoUnloadedFrontendFiles};
