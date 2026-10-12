/* Approved workspace layouts. Move existing controls without replacing handlers,
 * API contracts, coding dimensions, PRISMA counts or publication rendering. */
(function () {
  'use strict';
  if (typeof el !== 'function' || typeof T !== 'function') return;
  const byId = id => document.getElementById(id);
  const fulltext = document.querySelector('.page[data-p="fulltext"]');
  const coding = document.querySelector('.page[data-p="bench"]');
  const prisma = document.querySelector('.page[data-p="prisma"]');
  if (!fulltext || !coding || !prisma) return;
  const textBindings = [];
  function label(key) { const node = el('span', {text:T(key)}); textBindings.push([node,key]); return node; }
  function button(id, key) { return el('button', {id,type:'button',class:'btn b-out'}, label(key)); }

  // Full-text screening: the annotations dock beside the PDF when expanded.
  fulltext.classList.add('rf-fulltext-reader');
  const ftToolbar = byId('ftPrevDoc').parentElement;
  ftToolbar.classList.add('rf-document-toolbar');
  const focus = button('rfFulltextFocus', '专注阅读');
  ftToolbar.append(focus);
  let focused = false;
  function setFocus(value, restore = false) {
    focused = !!value;
    document.body.classList.toggle('rf-document-focus', focused);
    document.body.classList.toggle('rf-screen-focus', focused);
    focus.firstElementChild.textContent = T(focused ? '退出专注' : '专注阅读');
    focus.setAttribute('aria-pressed', String(focused));
    if (focused) window.RFNavigationSheet?.setOpen(false, {immediate:true});
    if (restore && S.page === 'fulltext') focus.focus({preventScroll:true});
  }
  focus.addEventListener('click', () => setFocus(!focused));
  const annotations = byId('ftSide'), annotationsToggle = byId('ftSideToggle');
  annotationsToggle.setAttribute('aria-controls', 'ftSide');
  function syncAnnotations() {
    const open = annotations.classList.contains('open');
    fulltext.querySelector('.ft-layout').classList.toggle('rf-notes-open', open);
    annotationsToggle.setAttribute('aria-expanded', String(open));
  }
  const closeAnnotations = button('rfCloseAnnotations', '关闭');
  annotations.querySelector('h3').after(closeAnnotations);
  closeAnnotations.addEventListener('click', () => { annotationsToggle.click(); annotationsToggle.focus(); });
  new MutationObserver(syncAnnotations).observe(annotations, {attributes:true,attributeFilter:['class']});
  syncAnnotations();

  // Coding: all original fields and save handlers remain inside their original IDs.
  coding.classList.add('rf-coding-reader');
  byId('cbPrev').parentElement.classList.add('rf-document-toolbar');
  const layout = byId('benchLayout'), form = coding.querySelector('.bench-form'), forms = byId('cbForms');
  const formHead = el('header', {class:'rf-coding-header'}, label('数据提取与编码'), byId('cbSaveProg'));
  form.prepend(formHead);
  const saveBar = el('footer', {class:'rf-coding-savebar'}, byId('cbStatus'),
    el('div', {class:'row',style:'margin:0'}, byId('cbSave'), byId('cbNext')));
  form.append(saveBar);
  const grip = el('button', {type:'button',id:'rfCodingDivider',class:'rf-coding-divider',text:'⋮',
    role:'separator','aria-orientation':'vertical',
    'aria-label':T('调整 PDF 与编码表单宽度'), 'aria-valuemin':'28','aria-valuemax':'48','aria-valuenow':'40'});
  byId('benchPdfPane').after(grip);
  let share = 40;
  function setShare(value) {
    share = Math.max(28, Math.min(48, value));
    layout.style.setProperty('--rf-coding-share', share + '%');
    grip.setAttribute('aria-valuenow', String(Math.round(share)));
  }
  grip.addEventListener('pointerdown', event => {
    if (event.button !== 0 || layout.classList.contains('no-pdf')) return;
    grip.setPointerCapture(event.pointerId); grip.focus(); event.preventDefault();
  });
  grip.addEventListener('pointermove', event => {
    if (!grip.hasPointerCapture(event.pointerId)) return;
    const box = layout.getBoundingClientRect();
    if (box.width) setShare((box.right - event.clientX) / box.width * 100);
  });
  function releaseGrip(event) { if (grip.hasPointerCapture(event.pointerId)) grip.releasePointerCapture(event.pointerId); }
  grip.addEventListener('pointerup', releaseGrip); grip.addEventListener('pointercancel', releaseGrip);
  grip.addEventListener('keydown', event => {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault(); setShare(share + (event.key === 'ArrowLeft' ? 2 : -2));
    }
  });
  setShare(40);
  function foldDimensions() {
    for (const card of forms.children) {
      if (!card.matches('.card') || !card.querySelector('.cb-v') || card.querySelector(':scope>details:not(.wf-evidence)')) continue;
      const heading = card.querySelector(':scope>b');
      if (!heading) continue;
      const details = el('details', {}), summary = el('summary', {});
      details.open = true;
      const kind = card.querySelector(':scope>.chip');
      summary.append(heading); if (kind) summary.append(kind);
      details.append(summary, ...card.childNodes); card.append(details);
    }
  }
  new MutationObserver(foldDimensions).observe(forms, {childList:true}); foldDimensions();

  // PRISMA: narrow collapsible inputs and a large preview. Reuse the real engine.
  prisma.classList.add('rf-prisma-workspace');
  const cards = [...prisma.querySelectorAll(':scope>.card')];
  if (cards.length === 6) {
    const toolbar = cards.shift(); toolbar.classList.add('rf-prisma-toolbar');
    toolbar.querySelector('.row').append(byId('prGenerate'));
    const instructions = toolbar.querySelector('.hint');
    if (instructions) {
      const help = el('details', {class:'rf-prisma-instructions'}, el('summary', {}, label('详情')));
      help.append(instructions); toolbar.querySelector('.row').insertBefore(help, byId('prImportCap'));
    }
    const importCaption = byId('prImportCap');
    new MutationObserver(() => { importCaption.title = importCaption.textContent; }).observe(importCaption, {childList:true,characterData:true,subtree:true});
    const inputs = el('div', {class:'rf-prisma-inputs'});
    // Keep moved controls connected so existing ID-based initialization can find them.
    prisma.append(inputs);
    const sectionNames = ['综述设置','① 识别与去重（数据库/注册库）','② 初筛（标题/摘要）','③ 全文复筛（报告检索与评估）','④ 纳入'];
    cards.forEach((card,index) => {
      const heading = card.querySelector(':scope>b');
      const summary = el('summary', {}, heading || label(sectionNames[index]));
      const details = el('details', {class:'rf-prisma-section'}, summary);
      details.open = true;
      details.append(...card.childNodes); card.append(details); inputs.append(card);
    });
    const citation = byId('prCitationStyle');
    const citationLabel = citation.parentElement.querySelector('label');
    const setup = cards[0].querySelector('details');
    setup.append(el('div', {class:'rf-prisma-citation'}, citationLabel, citation));
    const exportToolbar = el('header', {class:'rf-prisma-preview-toolbar'}, label('流程图预览'));
    const exports = el('div', {class:'rf-prisma-export-actions'});
    const extra = el('details', {class:'rf-prisma-extra-exports'}, el('summary', {'aria-label':T('更多导出格式'),text:'···'}));
    const extraBody = el('div', {class:'rf-prisma-export-menu'}, byId('prDotDl'), byId('prJsonDl')); extra.append(extraBody);
    exports.append(byId('prSvgDl'),byId('prPngDl'),byId('prPdfDl'),extra); exportToolbar.append(exports);
    const empty = el('p', {class:'hint rf-prisma-empty'}, label('校验并生成后显示流程图'));
    const preview = el('div', {class:'rf-prisma-preview'}, exportToolbar, byId('prIssues'), empty, byId('prView'));
    const workspace = el('div', {class:'rf-prisma-layout'}, inputs, preview); prisma.append(workspace);
    const originalRow = cards[4].querySelector('details>.row:last-child');
    if (originalRow && !originalRow.children.length) originalRow.remove();
    function syncPrismaResult() {
      const generated = byId('prSvgDl').style.display !== 'none';
      extra.hidden = !generated; if (!generated) extra.open = false;
      empty.hidden = !byId('prView').hidden;
    }
    new MutationObserver(syncPrismaResult).observe(byId('prSvgDl'), {attributes:true,attributeFilter:['style']});
    new MutationObserver(syncPrismaResult).observe(byId('prView'), {attributes:true,attributeFilter:['hidden']});
    syncPrismaResult();
  }

  // Refit only width-based PDF scales; retain a user's explicit numeric zoom.
  let pdfFrame = 0;
  function updatePdfLayout() {
    if (pdfFrame) return;
    pdfFrame = requestAnimationFrame(() => {
      pdfFrame = 0;
      for (const [workspace,stage] of [['fulltext',FtPdf],['bench',CbPdf]]) {
        if (S.page !== workspace) continue;
        const viewer = stage.viewer(); if (!viewer) continue;
        try {
          const scale = viewer.currentScaleValue;
          if (['page-width','page-fit','auto'].includes(scale)) viewer.currentScaleValue = scale;
          viewer.update();
        } catch (_) { /* A document can be loading while its container changes. */ }
      }
    });
  }
  if ('ResizeObserver' in window) {
    const observer = new ResizeObserver(updatePdfLayout);
    observer.observe(byId('pdfStage')); observer.observe(byId('cbPdfStage'));
  }
  function syncTitles() {
    for (const id of ['ftDocTitle','cbTitle']) { const title = byId(id); title.title = title.textContent; }
  }
  for (const id of ['ftDocTitle','cbTitle']) new MutationObserver(syncTitles).observe(byId(id), {childList:true,characterData:true,subtree:true});
  function refreshText() {
    textBindings.forEach(([node,key]) => { node.textContent = T(key); });
    focus.firstElementChild.textContent = T(focused ? '退出专注' : '专注阅读');
    grip.setAttribute('aria-label', T('调整 PDF 与编码表单宽度'));
    prisma.querySelector('.rf-prisma-extra-exports>summary')?.setAttribute('aria-label', T('更多导出格式'));
  }
  document.addEventListener('reviewflow:language-changed', refreshText);
  document.addEventListener('reviewflow:workspace-changed', event => { if (event.detail !== 'fulltext' && focused) setFocus(false); });
  document.addEventListener('reviewflow:task-context-changed', () => { if (focused) setFocus(false); });
  byId('logoutBtn')?.addEventListener('click', () => { if (focused) setFocus(false); });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || S.page !== 'fulltext' || event.defaultPrevented) return;
    if (annotations.classList.contains('open')) { event.preventDefault(); closeAnnotations.click(); }
    else if (focused) { event.preventDefault(); setFocus(false,true); }
  });
  refreshText(); syncTitles();
})();
