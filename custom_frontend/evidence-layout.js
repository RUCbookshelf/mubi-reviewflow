/* Preview overlay only. Original controls, listeners and data remain authoritative.
 * Enabled by the shared body class; restore comments preserve exact DOM positions.
 * Load after workspace_layouts.js. No product functions are wrapped or replaced. */
(function () {
  'use strict';
  if (window.RFEvidenceLayoutPreview) return;
  const toggleClass = 'rf-layout-preview-compact';
  const desktop = matchMedia('(min-width:761px)');
  const moved = new Map();
  const menus = new Map();
  const decorated = new Map();
  const editedText = new Map();
  let enabled = false;
  let scheduled = false;

  function moreLabel() {
    const lang = (document.documentElement.lang || 'zh-CN').toLowerCase();
    const labels = {zh:'更多',en:'More',ja:'その他',ko:'더 보기',de:'Mehr',fr:'Plus',es:'Más',pt:'Mais',ru:'Ещё',ar:'المزيد'};
    return labels[lang.split('-')[0]] || labels.en;
  }

  function textLabel(zh, en) {
    return (document.documentElement.lang || 'zh-CN').toLowerCase().startsWith('zh') ? zh : en;
  }

  const iconPaths = {
    note:'M8 4h12v16H4V8m0-4 5 5m-6 2 8-8 3 3-8 8H3z',
    focus:'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5',
    criteria:'M9 5h12M9 12h12M9 19h12M2 5l2 2 3-4M2 12l2 2 3-4M2 19l2 2 3-4',
    more:'M5 12h.01M12 12h.01M19 12h.01',
    refresh:'M20 8a8 8 0 1 0 0 8M20 3v5h-5',
    document:'M14 2H5v20h14V7zM14 2v5h5M8 12h8M8 16h8',
    link:'M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2m3 6a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2',
    check:'m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6zM8 12l3 3 5-6',
    history:'M3 11a9 9 0 1 1 2 7M3 4v7h7M12 7v5l3 2',
    download:'M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4',
    help:'M9 9a3 3 0 1 1 5 2c-2 1-2 2-2 3m0 3h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
    view:'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12m13 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0'
  };

  function editText(node, value) {
    if (!node || node.data === value) return;
    const record = editedText.get(node) || {original:node.data};
    record.last = value; editedText.set(node, record); node.data = value;
  }

  function decorate(control, kind, label) {
    if (!control) return;
    const text = [...control.childNodes].find(node => node.nodeType === 3 && node.data.trim());
    if (text) editText(text, label || text.data.replace(/^\s*[✎⇱⇩↻]\s*/, ''));
    if (control.querySelector(':scope>.rf-preview-evidence-icon')) return;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'rf-preview-evidence-icon'); svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('focusable', 'false');
    const path = document.createElementNS(svg.namespaceURI, 'path');
    path.setAttribute('d', iconPaths[kind]); svg.append(path);
    // Append, rather than prepend: the existing focus handler updates its first
    // span. CSS order puts this icon first without changing that DOM contract.
    control.append(svg); control.classList.add('rf-preview-evidence-control');
    decorated.set(control, svg);
  }

  function menuFor(key, toolbar) {
    let menu = menus.get(key);
    if (menu?.isConnected) return menu;
    menu = document.createElement('details');
    menu.className = 'rf-preview-evidence-more';
    menu.dataset.previewEvidenceMenu = key;
    const summary = document.createElement('summary');
    summary.className = 'btn b-out';
    const summaryLabel = document.createElement('span');
    summaryLabel.className = 'rf-preview-evidence-label'; summaryLabel.textContent = moreLabel();
    summary.append(summaryLabel);
    const body = document.createElement('div');
    body.className = 'rf-preview-evidence-menu';
    menu.append(summary, body);
    menu.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !menu.open || event.defaultPrevented) return;
      event.preventDefault();
      menu.open = false;
      summary.focus({preventScroll:true});
    });
    body.addEventListener('click', event => {
      if (event.target.closest('button,a')) menu.open = false;
    });
    toolbar.append(menu);
    menus.set(key, menu);
    return menu;
  }

  function groupFor(menu, key, label) {
    let group = menu.querySelector('[data-evidence-group="' + key + '"]');
    if (!group) {
      group = document.createElement('section'); group.dataset.evidenceGroup = key;
      const heading = document.createElement('div'); heading.className = 'rf-preview-evidence-group-label';
      group.append(heading); menu.lastElementChild.append(group);
    }
    if (group.firstElementChild.textContent !== label) group.firstElementChild.textContent = label;
    return group;
  }

  function move(control, destination) {
    if (!control || moved.has(control)) return;
    const anchor = document.createComment('preview restore ' + (control.id || control.className));
    const originalParent = control.parentNode;
    originalParent.insertBefore(anchor, control);
    moved.set(control, {anchor, originalParent});
    destination.append(control);
  }

  function collect(key, pageName, groupKey, label, selectors) {
    const page = document.querySelector('section.page[data-p="' + pageName + '"]');
    const toolbar = page?.querySelector('.rf-document-toolbar');
    if (!toolbar) return;
    for (const selector of selectors) {
      const control = page.querySelector(selector);
      if (!control) continue;
      const menu = menuFor(key, toolbar);
      move(control, groupFor(menu, groupKey, label));
    }
  }

  function enhance() {
    if (!enabled || !desktop.matches) return;
    // Scope selection, refresh/retry, readonly warnings and all save/decision
    // controls deliberately stay in their original, always-visible regions.
    const fulltext = document.querySelector('section.page[data-p="fulltext"]');
    const toolbar = fulltext?.querySelector('.rf-document-toolbar');
    const criteria = fulltext?.querySelector('.rf-criteria-panel');
    // Keep the criteria root intact: its aside, Escape handler and return-focus
    // target still share the original parent. Its empty former dock takes no space.
    if (toolbar && criteria) move(criteria, toolbar);
    collect('fulltext', 'fulltext', 'document', textLabel('当前文献','Current document'), ['#ftReload']);
    collect('fulltext', 'fulltext', 'reports', textLabel('报告工具','Report tools'), ['#mp-full-link', '#rf-retraction-start', '#rf-retraction-history']);
    collect('coding', 'bench', 'export', textLabel('编码导出','Coding export'), ['#cbExport']);
    collect('coding', 'bench', 'help', textLabel('帮助','Help'), ['[data-rf-help-entry="bench"]']);
    for (const [selector,kind,label] of [
      ['#ftSideToggle','note'],['#rfFulltextFocus','focus'],
      ['section[data-p="fulltext"] .rf-criteria-toggle','criteria'],
      ['#ftReload','document',textLabel('重新载入当前 PDF','Reload current PDF')],
      ['#mp-full-link','link'],['#rf-retraction-start','check'],['#rf-retraction-history','history'],
      ['#cbExport','download'],['[data-rf-help-entry="bench"]','help'],['#cbPdfToggle','view'],['#mp-coding-entry','link'],
      ['section[data-p="fulltext"] .wf-queue-tools>button','refresh',textLabel('刷新复筛队列','Refresh screening queue')],
      ['section[data-p="bench"] .wf-queue-tools>button','refresh',textLabel('刷新编码队列','Refresh coding queue')]
    ]) decorate(document.querySelector(selector),kind,label);
    for (const menu of menus.values()) {
      const label = moreLabel();
      const labelNode = menu.firstElementChild.querySelector('.rf-preview-evidence-label');
      if (labelNode.textContent !== label) labelNode.textContent = label;
      decorate(menu.firstElementChild, 'more');
    }
  }

  function restore() {
    for (const [node,record] of editedText) if (node.data === record.last) node.data = record.original;
    editedText.clear();
    for (const [control,icon] of decorated) { icon.remove(); control.classList.remove('rf-preview-evidence-control'); }
    decorated.clear();
    for (const [control, location] of moved) {
      if (location.anchor.parentNode) location.anchor.replaceWith(control);
      else if (location.originalParent.isConnected) location.originalParent.append(control);
    }
    moved.clear();
    for (const menu of menus.values()) menu.remove();
    menus.clear();
  }

  function setEnabled(value) {
    const next = !!value;
    if (next === enabled) { if (next) enhance(); return; }
    enabled = next;
    if (enabled && desktop.matches) enhance(); else restore();
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => { scheduled = false; enhance(); });
  }

  function outsideClick(event) {
    for (const menu of menus.values()) if (menu.open && !menu.contains(event.target)) menu.open = false;
  }

  function onResize() {
    if (!enabled) return;
    // Release preview anchors before the product's mobile media-query handler
    // records its own positions. Desktop composition waits until the next frame.
    if (innerWidth <= 760) restore();
    schedule();
  }

  const treeObserver = new MutationObserver(schedule);
  const classObserver = new MutationObserver(() => setEnabled(document.body.classList.contains(toggleClass)));
  function start() {
    treeObserver.observe(document.getElementById('appMain') || document.body, {childList:true,subtree:true});
    classObserver.observe(document.body, {attributes:true,attributeFilter:['class']});
    document.addEventListener('click', outsideClick);
    window.addEventListener('resize', onResize, {capture:true});
    desktop.addEventListener('change', schedule);
    document.addEventListener('reviewflow:language-changed', schedule);
    setEnabled(document.body.classList.contains(toggleClass));
  }
  window.RFEvidenceLayoutPreview = Object.freeze({setEnabled, refresh:schedule, get enabled() { return enabled; }});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once:true});
  else start();
})();
