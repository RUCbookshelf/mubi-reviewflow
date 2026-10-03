/* Presentation and accessibility only. Keeps existing IDs, handlers and machine values. */
(function () {
 'use strict';
 const page=document.querySelector('.page[data-p="analysis"]');if(!page)return;
 const byId=id=>document.getElementById(id);
 const card=byId('anJumpQual'),raw=byId('anQualResult');
 const heading=card.querySelector(':scope>b'),intro=card.querySelector(':scope>p');
 const labelFor=(id,text)=>{const field=byId(id);const label=el('label',{class:'rf-qual-field',for:id},el('span',{text:T(text)}),field);return label;};
 const section=(title,fields,buttonId)=>{
  const node=el('section',{class:'rf-qual-section'},el('h4',{text:T(title)}));
  const grid=el('div',{class:'rf-qual-fields'});fields.forEach(([id,label])=>grid.append(labelFor(id,label)));node.append(grid);
  if(buttonId)node.append(el('div',{class:'rf-qual-actions'},byId(buttonId)));return node;
 };
 const capture=section('记录研究发现',[
  ['anQualStudy','定性研究'],['anQualSource','来源报告'],['anQualCredibility','研究发现的可信度'],
  ['anQualFinding','研究发现'],['anQualQuote','来源原文引述'],['anQualLocator','页码或段落']],'anQualSaveFinding');
 const categories=section('归类支持性发现',[
  ['anQualCategory','类别名称'],['anQualFindingSelect','纳入该类别的研究发现']],'anQualSaveCategory');
 const synthesis=section('形成综合发现',[
  ['anQualSynthesis','综合研究发现'],['anQualCategorySelect','纳入综合的类别']],'anQualSaveSynthesis');
 const multipleHint=el('p',{id:'anQualSelectionHint',class:'hint',text:T('选择多项时可使用 Ctrl 或 Command 键。')});
 const records=el('section',{class:'rf-qual-records'},el('h4',{text:T('记录')}));
 const list=el('div',{id:'anQualRecordList'});records.append(list);
 raw.setAttribute('translate','no');raw.removeAttribute('aria-live');
 const technical=el('details',{class:'an-support-details rf-raw-output'},el('summary',{text:T('原始数据（保留字段名）')}),raw);
 card.replaceChildren(heading,intro,capture,categories,synthesis,multipleHint,records,technical);
 ['anQualFindingSelect','anQualCategorySelect'].forEach(id=>byId(id).setAttribute('aria-describedby',multipleHint.id));

 let lastRaw=null,parsed=null;
 function renderRecords(){
  const input=raw.textContent;
  if(input!==lastRaw){lastRaw=input;try{parsed=input?JSON.parse(input):null;}catch{parsed=null;}}
  list.replaceChildren();
  const groups=[['研究发现',parsed?.findings],['类别名称',parsed?.categories],['综合研究发现',parsed?.syntheses]];
  if(!groups.some(([,items])=>items?.length)){list.append(el('p',{class:'hint',text:T('尚无记录。')}));return;}
  for(const [label,items] of groups){if(!Array.isArray(items)||!items.length)continue;
   const group=el('section',{class:'rf-record-group'},el('h5',{text:T(label)+' · '+items.length}));
   for(const item of items){
    const row=el('article',{class:'rf-qual-record'});
    row.append(el('p',{translate:'no',text:item.finding??item.label??'—'}));
    if(item.illustration)row.append(el('blockquote',{translate:'no',text:item.illustration}));
    const meta=el('div',{class:'rf-record-meta'});
    if(item.credibility)meta.append(el('span',{text:T(item.credibility)}));
    const source=[item.study_id,item.source_key,item.locator].filter(v=>v!==null&&v!==undefined&&v!=='');
    if(source.length)meta.append(el('span',{translate:'no',text:source.join(' · ')}));
    const ids=item.finding_ids||item.category_ids;
    if(Array.isArray(ids)&&ids.length)meta.append(el('span',{translate:'no',text:ids.join(', ')}));
    row.append(meta);group.append(row);
   }list.append(group);
  }
 }
 new MutationObserver(renderRecords).observe(raw,{childList:true,characterData:true,subtree:true});renderRecords();
 document.addEventListener('reviewflow:language-changed',renderRecords);
 // Saved source/assessment JSON is an auditable record, not untranslated help prose.
 for(const id of ['anRobList','anDtaResult']){
  const record=byId(id);if(!record||record.closest('details'))continue;
  const details=el('details',{class:'an-support-details rf-raw-output'},el('summary',{text:T('原始数据（保留字段名）')}));
  record.before(details);record.setAttribute('translate','no');details.append(record);
 }
 // Keep meaningful field names available when a placeholder disappears after typing.
 function enhance(root){
  const elements=root.nodeType===1?[root,...root.querySelectorAll('input,textarea,table,pre')]:[];
  for(const node of elements){
   if(node.matches?.('input:not([type="hidden"]),textarea')&&!node.hasAttribute('aria-label')&&!node.labels?.length&&node.getAttribute('placeholder'))node.setAttribute('aria-label',node.getAttribute('placeholder'));
   if(node.tagName==='PRE'&&/^[\s]*[\[{]/.test(node.textContent))node.setAttribute('translate','no');
   if(node.tagName==='TABLE'&&!node.parentElement?.classList.contains('rf-table-scroll')&&!node.closest('.an-interval-comparison')){
    const wrap=el('div',{class:'rf-table-scroll',tabindex:'0','aria-label':T('表格可横向滚动')});node.before(wrap);wrap.append(node);
   }
  }
 }
 enhance(page);
 let pending=new Set(),scheduled=false;
 const observer=new MutationObserver(records=>{
  for(const r of records)for(const node of r.addedNodes)if(node.nodeType===1)pending.add(node);
  if(scheduled||!pending.size)return;scheduled=true;
  queueMicrotask(()=>{scheduled=false;const nodes=[...pending];pending.clear();for(const n of nodes)if(n.isConnected&&page.contains(n))enhance(n);});
 });
 observer.observe(page,{childList:true,subtree:true});
})();
