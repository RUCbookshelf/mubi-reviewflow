/* Preview-only full-text highlight comments. All writes target the selected ID. */
(() => {
 'use strict';
 const host=document.querySelector('section.page[data-p="fulltext"]');
 if(!host)return;
 const q=(s,r=host)=>r?.querySelector(s), qa=(s,r=host)=>[...(r?.querySelectorAll(s)||[])];
 const added=new Set(), moves=new Map(), drafts=new Map(), toolbarOffsets=new Map();
 let frame=0, modal=null, popup=null, enabled=false;
 const english=()=>typeof RFLang!=='undefined'&&RFLang.get()!=='zh-CN';
 const label=(zh,en)=>english()?en:zh;
 const button=(zh,en,fn,cls='btn b-out sm')=>{const n=document.createElement('button');n.type='button';n.className=cls;n.textContent=label(zh,en);n.onclick=fn;return n;};
 const node=(tag,cls,text)=>{const n=document.createElement(tag);n.className=cls;if(text)n.textContent=text;return n;};
 function scope(){return {task:S.task?.task_id,user:S.user?.user_id||S.user?.id||S.user?.username,screener:S.screener,key:curFt()?.zotero_key,version:S.taskVersion,path:taskApi(),query:scrQ(),policy:window.WorkflowControls?.activeItem('fulltext')?.policy_version};}
 function same(a){const b=scope();return ['task','user','screener','key','version'].every(k=>a[k]===b[k]);}
 const draftKey=(s,id)=>JSON.stringify([s.user,s.task,s.screener,s.key,id]);
 function remember(){if(!modal)return;const m=modal;if(m.input.value!==m.base)drafts.set(m.key,{note:m.input.value,base:m.base});else drafts.delete(m.key);}
 function closePopup(){popup?.remove();popup=null;}
 function closeModal(){if(!modal)return;remember();const d=modal.dialog;modal=null;d.close();d.remove();}
 async function freshRow(s,id){const data=await api(s.path+'/highlights/'+encodeURIComponent(s.key)+s.query);const row=data.highlights?.find(h=>h.id===id);if(!row)throw Error(label('这条高亮已被删除。','This highlight has been deleted.'));return row;}
 async function openComment(row){
  if(!enabled||!row)return;
  closePopup();closeModal();
  const s=scope();if(!s.key||row.zotero_key&&row.zotero_key!==s.key)return;
  const key=draftKey(s,row.id), saved=drafts.get(key);
  const d=node('dialog','rfa-comment-dialog');d.setAttribute('aria-labelledby','rfa-comment-title');
  const head=node('header','rfa-comment-header');const title=node('h3','',label('高亮批注','Highlight comment'));title.id='rfa-comment-title';
  const close=button('关闭','Close',closeModal);head.append(title,close);
  const body=node('div','rfa-comment-body');
  const meta=node('p','hint',label('第 '+row.page+' 页 · '+(curFt()?.title||''),'Page '+row.page+' · '+(curFt()?.title||'')));
  const quote=node('blockquote','rfa-comment-quote',row.text);
  const caption=node('label','rfa-comment-label',label('针对这段文字写批注','Comment on this passage'));caption.htmlFor='rfa-comment-note';
  const input=node('textarea','ta');input.id='rfa-comment-note';input.maxLength=4000;input.rows=5;input.placeholder=label('例如：这段内容与纳入标准相关；需要核对样本来源。','For example: relevant to the inclusion criteria; check the sample source.');
  const hint=node('p','hint',label('保存后，点击这块高亮即可再次查看或修改批注。留空保存可清除批注，高亮仍保留。','After saving, click this highlight to view or edit its comment. Save an empty comment to clear it while keeping the highlight.'));
  const status=node('p','rfa-comment-status',label('正在读取已保存内容…','Loading saved comment…'));status.setAttribute('role','status');
  const latest=node('blockquote','rfa-latest');latest.hidden=true;
  const foot=node('footer','rfa-comment-footer');
  const refresh=button('查看最新批注','View latest comment',async()=>{
   try {const item=await freshRow(s,row.id);if(modal?.dialog!==d)return;latest.textContent=label('最新已保存内容：\n','Latest saved comment:\n')+(item.note||label('（空）','(empty)'));latest.hidden=false;modal.base=item.note||'';status.textContent=label('你的草稿仍在上方。请合并内容后再保存。','Your draft is still above. Merge any changes before saving.');save.disabled=!same(s);}catch(e){status.textContent=e.message;}
  });refresh.hidden=true;
  const save=button('保存批注','Save comment',async()=>{
   const m=modal;if(!m||m.dialog!==d||m.pending)return;
   if(!same(s)){status.textContent=label('当前文献已切换，草稿已保留。请返回原文献后保存。','The document has changed. Your draft is kept; return to the original document to save.');remember();return;}
   m.pending=true;save.disabled=true;input.disabled=true;refresh.disabled=true;status.textContent=label('正在保存…','Saving…');
   const submitted=input.value;
   try{
    const data=await api(s.path+'/highlights/'+row.id+s.query,{method:'PATCH',body:{zotero_key:s.key,note:submitted,expected_note:m.base,expected_policy_version:s.policy}});
    if(!drafts.has(key)||drafts.get(key).note===submitted)drafts.delete(key);m.base=data.highlight.note;
    if(modal?.dialog===d){input.value=m.base;status.textContent=label('批注已保存。','Comment saved.');refresh.hidden=true;latest.hidden=true;}
    if(same(s))await loadHighlights();
    else if(modal?.dialog===d)status.textContent=label('批注已保存到原文献。','The comment was saved to the original document.');
   }catch(e){
    if(modal?.dialog===d||!drafts.has(key))drafts.set(key,{note:submitted,base:m.base});
    if(modal?.dialog===d){status.textContent=e.message;refresh.hidden=e.status!==409;}
   }finally{m.pending=false;if(modal?.dialog===d){input.disabled=false;refresh.disabled=false;save.disabled=!same(s);}}
  },'btn b-inc');save.disabled=true;
  foot.append(refresh,button('暂存并关闭','Keep draft and close',closeModal),save);
  body.append(meta,quote,caption,input,hint,status,latest);d.append(head,body,foot);document.body.append(d);
  modal={dialog:d,input,key,base:saved?.base??row.note??'',pending:false,loading:true,scope:s};
  input.value=saved?.note??row.note??'';input.disabled=true;input.addEventListener('input',remember);
  d.addEventListener('cancel',e=>{e.preventDefault();closeModal();});
  d.addEventListener('keydown',e=>e.stopPropagation());d.showModal();input.focus();
  try {const item=await freshRow(s,row.id);if(modal?.dialog!==d)return;
   if(!saved){modal.base=item.note||'';input.value=modal.base;}
   save.disabled=!same(s);status.textContent=saved?label('已恢复未保存的草稿。','Unsaved draft restored.'):'';
  }catch(e){if(modal?.dialog===d)status.textContent=e.message;}
  finally{if(modal?.dialog===d){modal.loading=false;input.disabled=modal.pending;input.focus();}}
 }
 function showChoices(rows,x,y){
  closePopup();if(!rows.length)return;
  const box=node('div','rfa-highlight-popup');box.setAttribute('role','group');box.setAttribute('aria-label',label('高亮批注','Highlight comments'));
  if(rows.length>1)box.append(node('p','hint',label('此处有多条高亮，请选择：','Several highlights overlap. Choose one:')));
  rows.forEach(row=>box.append(button((row.note?'查看 / 编辑批注':'添加批注')+(rows.length>1?' · '+row.text.slice(0,40):''),(row.note?'View / edit comment':'Add comment')+(rows.length>1?' · '+row.text.slice(0,40):''),()=>openComment(row))));
  document.body.append(box);popup=box;const rect=box.getBoundingClientRect();box.style.left=Math.max(8,Math.min(x,innerWidth-rect.width-8))+'px';box.style.top=Math.max(8,Math.min(y+8,innerHeight-rect.height-8))+'px';box.querySelector('button')?.focus({preventScroll:true});
 }
 async function clickedHighlight(event){
  if(!enabled||event.target.closest('button,.editToolbar,.rfa-highlight-popup')||getSelection()?.toString().trim())return;
  const native=event.target.closest('.highlightEditor');if(native?.querySelector('.editToolbar')){closePopup();schedule();return;}
  const page=event.target.closest('#pdfViewerEl .page[data-page-number]');if(!page)return;
  const s=scope(),num=Number(page.dataset.pageNumber),box=page.getBoundingClientRect();
  const vp=await FtPdf.viewport1(num);if(!same(s))return;
  const x=(event.clientX-box.left)/box.width*vp.width,y=(event.clientY-box.top)/box.height*vp.height;
  const matches=(S.ftHl||[]).filter(h=>h.page===num&&(h.rects||[]).some(r=>x>=r[0]-2&&x<=r[2]+2&&y>=r[1]-2&&y<=r[3]+2));
  if(matches.length)showChoices(matches,event.clientX,event.clientY);
 }
 function nativeCandidates(editor){
  const page=editor.closest('.page[data-page-number]');if(!page)return [];
  const text=(editor.getAttribute('aria-label')||'').replace(/\s+/g,' ').trim();
  const rows=(S.ftHl||[]).filter(h=>h.page===Number(page.dataset.pageNumber));
  // Never guess between duplicated excerpts. The popup lets users choose.
  return rows.filter(h=>text&&h.text.replace(/\s+/g,' ').trim()===text);
 }
 function track(n){added.add(n);return n;}
 function restoreLayout(){for(const [n,anchor]of [...moves].reverse())if(anchor.isConnected)anchor.replaceWith(n);moves.clear();for(const n of added)n.remove();added.clear();for(const [n,value]of toolbarOffsets)n.style.translate=value;toolbarOffsets.clear();}
 function placeToolbar(toolbar,editor){
  if(!toolbar.getClientRects().length)return;
  if(!toolbarOffsets.has(toolbar))toolbarOffsets.set(toolbar,toolbar.style.translate);
  toolbar.style.translate=toolbarOffsets.get(toolbar);
  const b=toolbar.getBoundingClientRect(),container=q('#pdfContainer').getBoundingClientRect();
  const left=Math.max(8,container.left+4),right=Math.min(innerWidth-8,container.right-4);
  const dx=b.left<left?left-b.left:b.right>right?right-b.right:0;
  const top=Math.max(8,container.top+4);let bottom=Math.min(innerHeight-8,container.bottom-4);
  // The decision panel overlays the lower part of the PDF on small screens.
  const dock=q('.ft-decide');if(dock?.getClientRects().length){const r=dock.getBoundingClientRect();if(r.top>top&&r.top<bottom&&r.right>b.left+dx&&r.left<b.right+dx)bottom=r.top-8;}
  const preferred=Math.min(editor.getBoundingClientRect().top-b.height-8,bottom-b.height);
  const dy=b.bottom>bottom?Math.max(top,preferred)-b.top:b.top<top?top-b.top:0;
  toolbar.style.translate=dx+'px '+dy+'px';
 }
 function compose(){
  frame=0;enabled=document.body.classList.contains('rf-layout-preview-compact');
  if(!enabled){closePopup();closeModal();restoreLayout();return;}
  const side=q('#ftSide'),list=q('#annList');
  if(side&&!q('.rfa-side-help',side)){
   const help=track(node('p','rfa-side-help hint',label('在 PDF 中选中文字并高亮，再点击高亮块添加批注。下方列表可查看、修改批注或跳回原文。','Highlight text in the PDF, then click a highlight to add a comment. Use the list below to edit comments or return to the passage.')));side.querySelector('h3')?.after(help);
   const manual=track(node('details','rfa-manual'));const summary=node('summary','',label('手动录入摘录与批注（可选）','Enter a passage and comment manually (optional)'));manual.append(summary);side.insertBefore(manual,list);
   const fields=[q('#annPage')?.parentElement,q('#annText'),q('#annNote'),q('#annAdd')];
   for(const field of fields){if(!field)continue;const anchor=document.createComment('annotation preview position');field.before(anchor);moves.set(field,anchor);manual.append(field);}
  }
  qa('.ann-item',list).forEach((item,index)=>{
   const row=S.ftHl?.[index];if(!row||q('.rfa-item-actions',item))return;
   const actions=track(node('div','rfa-item-actions'));
   actions.append(button(row.note?'编辑批注':'添加批注',row.note?'Edit comment':'Add comment',()=>openComment(row)),button('定位原文','Go to passage',()=>{FtPdf.viewer().currentPageNumber=row.page;const page=q('#pdfViewerEl .page[data-page-number="'+row.page+'"]');page?.scrollIntoView({block:'start'});closePopup();}));
   item.append(actions);
  });
  for(const editor of qa('#pdfViewerEl .highlightEditor')){
   const toolbar=q('.editToolbar',editor);if(!toolbar)continue;
   let b=q('.rfa-native-comment',toolbar);
   if(!b){b=track(button('添加批注','Add comment',e=>{e.stopPropagation();const rows=nativeCandidates(editor);if(rows.length===1)openComment(rows[0]);else if(rows.length>1)showChoices(rows,e.clientX,e.clientY);},'rfa-native-comment'));const buttons=q('.buttons',toolbar)||toolbar;buttons.insertBefore(b,q('.delete',buttons));}
   const matches=nativeCandidates(editor);b.disabled=!matches.length;b.textContent=matches.length?label(matches[0].note?'查看 / 编辑批注':'添加批注',matches[0].note?'View / edit comment':'Add comment'):label('正在保存高亮…','Saving highlight…');placeToolbar(toolbar,editor);
  }
  if(modal&&!same(modal.scope)){remember();modal.input.disabled=modal.loading||modal.pending;modal.dialog.querySelector('.b-inc').disabled=true;modal.dialog.querySelector('[role="status"]').textContent=label('当前文献已切换，草稿已保留。请返回原文献后保存。','The document has changed. Your draft is kept; return to the original document to save.');}
 }
 function schedule(){if(!frame)frame=requestAnimationFrame(compose);}
 // Only real DOM insertions trigger rebuilding; ignore our own text/status updates.
 const observer=new MutationObserver(records=>{if(records.some(r=>[...r.addedNodes,...r.removedNodes].some(n=>n.nodeType===1&&!n.classList?.contains('rfa-native-comment'))))schedule();});observer.observe(host,{childList:true,subtree:true});
 host.addEventListener('click',event=>{clickedHighlight(event).catch(()=>{});});
 q('#pdfContainer')?.addEventListener('scroll',schedule,{passive:true});window.addEventListener('resize',schedule);
 document.addEventListener('pointerdown',e=>{if(popup&&!popup.contains(e.target)&&!e.target.closest('#pdfViewerEl'))closePopup();});
 document.addEventListener('keydown',e=>{if(e.key==='Escape')closePopup();});
 document.addEventListener('reviewflow:layout-preview-mode',compose);
 document.addEventListener('reviewflow:task-context-changed',()=>{closePopup();schedule();});
 document.addEventListener('reviewflow:language-changed',()=>{restoreLayout();schedule();});
 window.addEventListener('beforeunload',e=>{remember();if(drafts.size){e.preventDefault();e.returnValue='';}});
 window.RFAnnotationLayoutPreview={openComment};
 compose();
})();
