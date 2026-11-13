/* Preview study-design assistance. Installed before the lazy product module.
 * Detection is an explicit local preference, independent of layout comparison.
 * No exclusion-reason prefill: this feature only displays advisory labels. */
(function () {
  'use strict';
  const page = document.querySelector('section.page[data-p="screen"]');
  if (!page || window.RFDesignDetectionPreview) return;
  const preference = 'rf_screen_design_detection';
  let active = false, generation = 0, frame = 0, cache = null, flight = null, control = null;
  try { active = localStorage.getItem(preference) === 'true'; } catch (_) {}
  const languages = ['zh-CN','en','fr','ru','es','ja','pt','de','sr','ko'];
  const words = {
    label:['研究设计识别','Study-design detection','Identification du design','Определение дизайна','Detección del diseño','研究デザインの識別','Identificação do desenho','Studiendesign erkennen','Препознавање дизајна','연구 설계 식별'],
    on:['已开启','On','Activé','Вкл.','Activado','オン','Ativado','Ein','Укљ.','켜짐'],
    off:['已关闭','Off','Désactivé','Выкл.','Desactivado','オフ','Desativado','Aus','Искљ.','꺼짐'],
    reading:['阅读辅助','Reading aids','Aides à la lecture','Помощь при чтении','Ayudas de lectura','閲覧補助','Apoio à leitura','Lesehilfen','Помоћ при читању','읽기 지원']
  };
  const designNames = {qualitative:'sd.label.qualitative',mixed_methods:'sd.label.mixed_methods',discourse:'sd.label.discourse',survey:'sd.label.survey',rct:'随机对照试验',cohort:'队列研究',case_control:'病例对照',cross_sectional:'横断面研究',diagnostic:'诊断准确性',meta_analysis:'Meta 分析',systematic_review:'系统综述'};
  const families = {qualitative:'qual',mixed_methods:'mixed',discourse:'discourse',survey:'quan',rct:'quan',cohort:'quan',case_control:'quan',cross_sectional:'quan',diagnostic:'quan',meta_analysis:'review',systematic_review:'review'};
  const names = {qual:['质 (Qual-)','Qual-'],quan:['量 (Quan-)','Quan-'],review:['综 (Review)','Review'],mixed:['混 (Mixed)','Mixed'],discourse:['语 (Discourse)','Discourse']};
  const lang = () => typeof RFLang === 'undefined' ? 'zh-CN' : RFLang.get();
  const text = key => words[key][Math.max(0,languages.indexOf(lang()))];
  const translate = (...args) => typeof T === 'function' ? T(...args) : args[0];
  const q = selector => page.querySelector(selector);
  function current() {
    try {
      if (!S.task || !S.user || S.page !== 'screen') return null;
      const article = S.articles?.[S.scrIdx];
      if (!article?.zotero_key || typeof taskApi !== 'function' || typeof scrQ !== 'function') return null;
      const url = taskApi() + '/study-designs' + scrQ();
      return {key:String(article.zotero_key), url, scope:[S.user.user_id,S.task.task_id,S.taskVersion,S.screener,url].join('|')};
    } catch (_) { return null; }
  }
  function schedule() { if (!frame) frame = requestAnimationFrame(sync); }
  // The original module assigns a frozen API after lazy loading. Intercept the
  // assignment before first rendering, rather than replacing global fetch or
  // letting its asynchronous prefill run and trying to undo a user's selection.
  const adapter = Object.freeze({badge() { schedule(); return null; }, prefill() { return false; }});
  Object.defineProperty(window, 'StudyDesignBadge', {configurable:true, get:() => adapter, set:() => {}});
  function invalidate() { generation++; cache = null; flight = null; schedule(); }
  function setActive(value) {
    active = !!value;
    try { localStorage.setItem(preference,String(active)); } catch (_) {}
    invalidate(); sync();
  }
  function ensureControl() {
    const host = q('.rf-screen-tools');
    if (!host) return;
    if (!control) {
      control = document.createElement('button'); control.type = 'button';
      control.id = 'rfScreenDesignDetection'; control.className = 'btn b-out sm rfl-design-toggle';
      const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
      svg.setAttribute('viewBox','0 0 24 24'); svg.setAttribute('aria-hidden','true'); svg.setAttribute('focusable','false');
      const path = document.createElementNS(svg.namespaceURI,'path');
      path.setAttribute('d','M9 3h6M10 3v6l-6 10a1 1 0 0 0 1 2h14a1 1 0 0 0 1-2L14 9V3M7 16h10'); svg.append(path);
      const label = document.createElement('span'), state = document.createElement('span');
      label.className = 'rfl-design-toggle-label'; state.className = 'rfl-design-toggle-state';
      control.append(svg,label,state); control.addEventListener('click',() => setActive(!active));
    }
    if (!control.isConnected) host.append(control);
    const label = control.querySelector('.rfl-design-toggle-label'), state = control.querySelector('.rfl-design-toggle-state');
    if (label.textContent !== text('label')) label.textContent = text('label');
    if (state.textContent !== text(active?'on':'off')) state.textContent = text(active?'on':'off');
    control.setAttribute('aria-pressed',String(active));
    const mobile = innerWidth <= 760, sheet = q(':scope>.rf-mobile-sheet');
    if (mobile && sheet) {
      let reading = sheet.querySelector('.rfl-design-mobile-reading');
      if (!reading) {
        reading = document.createElement('section'); reading.className = 'rfl-design-mobile-reading';
        const heading = document.createElement('h3'); reading.append(heading); sheet.append(reading);
      }
      if (reading.firstChild.textContent !== text('reading')) reading.firstChild.textContent = text('reading');
      if (control.parentNode !== reading) reading.append(control);
    } else if (!mobile && control.closest('.rfl-design-mobile-reading')) {
      const reading = q('.rfl-reading>.rfl-group-body'); (reading || host).append(control);
    }
  }
  function render(item) {
    const meta = q('#scrBody .scr-meta');
    const family = item?.recognized === true && families[item.label];
    let badge = q('.rfl-design-badge');
    if (!active || !meta || !family) { badge?.remove(); return; }
    if (!badge) { badge = document.createElement('span'); badge.className = 'rfl-design-badge'; meta.append(badge); }
    if (badge.parentNode !== meta) meta.append(badge);
    const label = names[family][lang()==='zh-CN'?0:1];
    if (badge.textContent !== label) badge.textContent = label;
    badge.dataset.design = family;
    badge.title = translate('本地关键词规则判定为「{0}」（命中：{1}）。仅供参考，请人工核对。',translate(designNames[item.label]),item.matched_keyword || '—');
    badge.setAttribute('aria-label',translate('研究设计（本地规则）') + ': ' + translate(designNames[item.label]));
  }
  function sync() {
    frame = 0; observer.disconnect();
    try {
      ensureControl();
      const context = current();
      if (!active || !context || !q('#scrBody .scr-meta')) { render(null); return; }
      if (cache?.scope === context.scope) { render(cache.items?.[context.key]); return; }
      render(null);
      if (flight?.scope === context.scope) return;
      const request = {scope:context.scope, generation}; flight = request;
      Promise.resolve(api(context.url)).then(data => {
        if (!active || generation !== request.generation || current()?.scope !== request.scope) return;
        cache = {scope:request.scope,items:data?.available !== false && data?.truncated !== true && data?.items && typeof data.items === 'object' ? data.items : null};
      }).catch(() => {
        if (active && generation === request.generation && current()?.scope === request.scope) cache = {scope:request.scope,items:null};
      }).finally(() => { if (flight === request) flight = null; schedule(); });
    } finally { observer.observe(page,{childList:true,subtree:true}); }
  }
  const observer = new MutationObserver(schedule);
  for (const event of ['reviewflow:import-completed','reviewflow:workflow-changed','reviewflow:task-context-changed']) document.addEventListener(event,invalidate);
  for (const event of ['reviewflow:language-changed','reviewflow:workspace-changed','reviewflow:workspace-modules-ready','reviewflow:layout-preview-mode']) document.addEventListener(event,schedule);
  window.addEventListener('resize',schedule);
  window.addEventListener('storage',event => { if (event.key === preference) { active = event.newValue === 'true'; invalidate(); } });
  window.RFDesignDetectionPreview = Object.freeze({get active() { return active; }});
  sync();
})();

/* Preview only. Real screen + pilot DOM, original controls and handlers.
 * Load after the real app assets; opt in with RFWorkflowLayoutPreview.setEnabled(true).
 * No app state, storage, network, decision handler or document content is modified. */
(function () {
  'use strict';
  if (window.RFWorkflowLayoutPreview) return;
  const desktop = matchMedia('(min-width:761px)');
  const screen = () => document.querySelector('.page[data-p="screen"]');
  const pilots = new Map();
  const q = (root, selector) => root?.querySelector(selector);
  const qa = (root, selector) => [...(root?.querySelectorAll(selector) || [])];
  let enabled = false, frame = 0, screenJournal = null, reasonEntry = null, disposed = false;
  const translate = value => typeof window.T === 'function' ? window.T(value) : value;

  function journal() {
    const moves = new Map(), additions = [], classes = [], props = [], attrs = new Map();
    return {
      moves,
      add(tag, className, parent, before) {
        const node = document.createElement(tag);
        node.className = className;
        parent.insertBefore(node, before || null);
        additions.push(node);
        return node;
      },
      insert(node, parent, before) {
        parent.insertBefore(node, before || null); additions.push(node); return node;
      },
      attribute(node, name, value) {
        if (!node || node.getAttribute(name) === value) return;
        if (!attrs.has(node)) attrs.set(node, new Map());
        if (!attrs.get(node).has(name)) attrs.get(node).set(name, node.getAttribute(name));
        if (value === null) node.removeAttribute(name); else node.setAttribute(name, value);
      },
      move(node, parent, before) {
        if (!node || !parent || node === parent || node.contains(parent)) return;
        if (node.parentNode === parent && (!before || node.nextSibling === before)) return;
        if (!moves.has(node)) {
          const marker = document.createComment('workflow-preview-original-position');
          node.before(marker);
          moves.set(node, marker);
        }
        parent.insertBefore(node, before || null);
      },
      mark(node, name) {
        if (node && !node.classList.contains(name)) { node.classList.add(name); classes.push([node, name]); }
      },
      property(node, name, value) {
        if (!node || node[name] === value) return;
        props.push([node, name, node[name]]); node[name] = value;
      },
      restore() {
        for (const [node, marker] of [...moves].reverse()) {
          if (marker.parentNode) marker.replaceWith(node);
        }
        for (const [node, name, value] of props.reverse()) node[name] = value;
        for (const [node, values] of attrs) for (const [name, value] of values) {
          if (value === null) node.removeAttribute(name); else node.setAttribute(name, value);
        }
        for (const [node, name] of classes) node.classList.remove(name);
        for (const node of additions.reverse()) node.remove();
        moves.clear();
      }
    };
  }

  function group(j, parent, label, className = '', before) {
    const details = j.add('details', 'rfl-group ' + className, parent, before);
    const summary = j.add('summary', '', details);
    summary.textContent = translate(label);
    const body = j.add('div', 'rfl-group-body', details);
    details.addEventListener('toggle', () => {
      if (!details.open) return;
      const owner = details.closest('.rfl-screen-commandbar,.rfl-pilot-body') || parent;
      for (const sibling of qa(owner, '.rfl-group[open]')) if (sibling !== details && !sibling.contains(details)) sibling.open = false;
      placeGroup(details);
    });
    // A real input/button inside a disclosure must not send 1/3/F to screening.
    details.addEventListener('keydown', event => {
      if (event.key === 'Escape' && details.open) {
        details.open = false; summary.focus({preventScroll:true}); event.preventDefault();
        event.stopPropagation();
      }
      if (details.open && event.target !== summary) event.stopPropagation();
    });
    return {details, summary, body};
  }

  function placeGroup(details) {
    const body = q(details, '.rfl-group-body');
    if (!body || !details.open) return;
    body.style.left = ''; body.style.right = '';
    if (details.classList.contains('rfl-reason-find') && !desktop.matches) return;
    const box = body.getBoundingClientRect(), anchor = details.getBoundingClientRect();
    const left = Math.max(12, Math.min(box.left, innerWidth - 12 - box.width));
    // Position, not transform: descendant product panels retain their fixed viewport coordinates.
    body.style.left = (left - anchor.left) + 'px'; body.style.right = 'auto';
  }

  function decorateMenu(j, body) {
    const paths = {
      focus:'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5',
      keyboard:'M3 6h18v12H3zM6 9h.01M10 9h.01M14 9h.01M18 9h.01M6 12h.01M10 12h.01M14 12h.01M18 12h.01M7 15h10',
      book:'M12 5v15M3 4c4-1 6 0 9 1 3-1 5-2 9-1v15c-4-1-6 0-9 1-3-1-5-2-9-1z',
      criteria:'M9 5h12M9 12h12M9 19h12M3 5l1 1 2-2M3 12l1 1 2-2M3 19l1 1 2-2',
      marker:'m14 3 7 7-10 10-7-7zM2 22h10M7 16l4 4',
      download:'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M12 11v7m-3-3 3 3 3-3',
      relations:'M12 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM5 21a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM19 21a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM12 7v5M5 17v-5h14v5',
      reminder:'M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM12 13v4l3 2',
      training:'M9 4H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-3M9 2h6v4H9zM8 13l3 3 5-6'
    };
    for (const control of qa(body, 'button,a.rf-help-entry')) {
      if (control.closest('.rf-criteria-view,.rf-screen-help,.al-stop-panel')) continue;
      j.mark(control, 'rfl-menu-control');
      // Existing product icons (including the ML companion) keep their exact
      // nodes, paths and rendering. The order controls also keep text-only items.
      if (q(control, 'svg:not(.rfl-menu-icon)') || control.closest('.rf-screen-order')) continue;
      if (q(control, ':scope > .rfl-menu-icon')) continue;
      const kind = control.id === 'rfScreenFocus' ? 'focus' : control.id === 'rfScreenHelp' ? 'keyboard'
        : control.matches('a.rf-help-entry') ? 'book' : control.matches('.rf-criteria-toggle') ? 'criteria'
        : control.matches('[data-rfl-batch-pdf]') ? 'download'
        : control.matches('.mp-link-summary[data-mp-summary="screen"]') ? 'relations'
        : control.matches('.rf-daily-trigger') ? 'reminder' : control.id === 'rfPilotEntry' ? 'training'
        : /关键词|highlight/i.test(control.textContent) ? 'marker' : null;
      if (!kind) continue;
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('class', 'rfl-menu-icon'); svg.setAttribute('viewBox', '0 0 24 24');
      svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('focusable', 'false');
      const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', paths[kind]); svg.append(path);
      j.insert(svg, control, control.firstChild);
    }
  }

  function reasonCompose() {
    const page = screen(), input = q(page, '.scr-reason-tools .scr-reason-filter') || q(page, '.scr-reason-filter');
    const select = q(page, '#excReason'), row = q(page, '.scr-decisions');
    if (reasonEntry && (reasonEntry.input !== input || reasonEntry.select !== select || !reasonEntry.details.isConnected)) {
      reasonEntry.j.restore(); reasonEntry = null;
    }
    if (!input || !select || !row || reasonEntry) return;
    const j = journal(), originalTools = input.closest('.scr-reason-tools');
    const menu = group(j, row, '查找理由', 'rfl-reason-find', select.nextSibling);
    j.move(input, menu.body); j.mark(originalTools, 'rfl-empty-reason-tools');
    j.attribute(input, 'placeholder', translate('搜索排除理由'));
    j.attribute(input, 'aria-label', translate('搜索排除理由'));
    const hint = j.add('p', 'rfl-reason-hint', menu.body);
    hint.textContent = translate('仅筛选排除理由选项，不记录筛选决定。');
    reasonEntry = {j, input, select, details:menu.details};
  }

  function screenCompose() {
    const page = screen();
    if (!page || !desktop.matches) {
      screenJournal?.restore(); screenJournal = null;
      return;
    }
    const mode = q(page, '.al-mode'), status = q(page, '.scr-status'), order = q(page, '.rf-screen-order');
    if (!mode || !status || !order || !q(page, '#scrCard')) return;
    if (!screenJournal) {
      const j = screenJournal = journal();
      j.mark(page, 'rfl-screen');
      const bar = j.add('div', 'rfl-screen-commandbar', page, mode);
      j.move(mode, bar); j.move(status, bar);
      const nav = j.add('div', 'rfl-doc-navigation', bar, mode);
      for (const id of ['scrPos', 'scrBrowsePrev', 'scrBrowseNext']) j.move(q(page, '#' + id), nav);
      const orderGroup = group(j, mode, 'Screening order', 'rfl-order');
      const orderNow = q(order, '.rf-screen-order-now');
      j.move(order, orderGroup.body);
      // syncOrderNow retains its original node reference; the current order stays visible.
      j.move(orderNow, orderGroup.summary);
      const reading = group(j, bar, '阅读辅助', 'rfl-reading');
      const documents = group(j, bar, '文献工具', 'rfl-documents');
      const progress = group(j, bar, '训练与提醒', 'rfl-progress');
      const history = group(j, bar, '决策历史', 'rfl-history');
      j.move(q(page, '#scrHistCounts'), history.summary);
      j.move(q(page, '#scrHistCap'), history.body);
      j.move(q(page, '#scrHist'), history.body);
      j.refs = {bar, nav, mode, status, orderGroup, reading, documents, progress, history};
    }
    const j = screenJournal, r = j.refs;
    if (!r.bar.isConnected) { j.restore(); screenJournal = null; return; }
    j.move(q(page, '.al-profile-picker'), r.orderGroup.body);
    const toolHost = q(page, '.rf-screen-tools');
    j.mark(toolHost, 'rfl-original-tools');
    for (const control of qa(toolHost, ':scope > button')) {
      if (control.hasAttribute('data-undo-stage')) j.move(control, r.nav);
      else if (control.id === 'rfScreenFocus' || control.id === 'rfScreenHelp' || control.id === 'rfScreenDesignDetection') j.move(control, r.reading.body);
      else {
        // The original reader's only unkeyed tool is its PDF batch launcher.
        // Mark the node before moving it; never identify it by translated text.
        if (!control.id && !control.hasAttribute('data-mp-summary')) j.attribute(control, 'data-rfl-batch-pdf', '');
        j.move(control, r.documents.body);
      }
    }
    j.move(q(page, '.rf-criteria-panel'), r.reading.body);
    j.move(q(page, '.rf-daily-trigger'), r.progress.body);
    j.move(q(page, '#rfPilotEntry'), r.progress.body);
    // The remaining direct status button is the real keyword-highlighting launcher.
    for (const button of qa(r.status, ':scope > button')) {
      if (!['scrBrowsePrev','scrBrowseNext','rfPilotEntry'].includes(button.id) && !button.hasAttribute('data-undo-stage'))
        j.move(button, r.reading.body);
    }
    // Keep stop guidance visible, including its existing attention indicator and count.
    j.move(q(page, '.rf-stop-anchor'), r.bar);
    // Late-mounted report entry still appears under the real .rf-screen-tools host.
    j.move(q(page, '#mp-screen-link'), r.documents.body);
    const helpLink = q(page, '[data-rf-help-entry="screen"]');
    j.move(helpLink, r.reading.body);
    j.attribute(helpLink, 'style', null);
    j.property(q(helpLink, '[data-rf-help-entry-label]'), 'textContent', translate('使用文档'));
    const shortcut = q(page, '#rfScreenHelp');
    j.property(q(shortcut, 'span'), 'textContent', translate('阅读快捷键'));
    j.attribute(shortcut, 'aria-label', translate('阅读快捷键'));
    for (const key of ['reading','documents','progress','history','orderGroup']) decorateMenu(j, r[key].body);
  }

  function pilotCompose(dialog) {
    if (pilots.has(dialog)) {
      const previous = pilots.get(dialog);
      if (previous.card.isConnected) return;
      previous.j.restore(); pilots.delete(dialog);
    }
    const card = qa(dialog, '.rf-pilot-card').find(node => q(node, 'select option[value="include"]'));
    const body = card?.parentElement;
    const answer = qa(card, ':scope > div').find(node => q(node, ':scope > .rf-pilot-fields select'));
    if (!card || !body || !answer) return; // Set management and creation keep their original layout.
    const j = journal(); pilots.set(dialog, {j, card});
    j.mark(dialog, 'rfl-pilot'); j.mark(body, 'rfl-pilot-body');
    j.mark(card, 'rfl-pilot-reading'); j.mark(answer, 'rfl-pilot-answer');
    const original = [...body.children];
    const toolbar = j.add('div', 'rfl-pilot-toolbar', body, body.firstChild);
    const heading = original.find(node => node.tagName === 'H3');
    const back = original.find(node => node.tagName === 'BUTTON');
    const nav = original.find(node => node.matches('.row') && q(node, '.chip'));
    j.move(back, toolbar); j.move(heading, toolbar); j.move(nav, toolbar);
    const notices = original.filter(node => node.matches('p.hint'));
    const info = group(j, toolbar, '训练信息', 'rfl-pilot-info');
    // Threshold details are low-frequency; answered/gold progress remains in the toolbar.
    if (notices[0]) j.move(notices[0], info.body);
    for (const notice of notices.slice(1)) j.move(notice, toolbar);
    const criteria = q(card, '.rf-criteria-panel');
    j.move(criteria, toolbar);
    const actions = j.add('div', 'rfl-pilot-actions', body);
    j.move(answer, actions);
    const finalRow = original.find(node => node !== nav && node.matches('.row') && (node.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_PRECEDING));
    if (finalRow) {
      const submit = group(j, actions, '整套训练', 'rfl-pilot-submit');
      j.move(finalRow, submit.body);
    }
    // Gold-standard publication is still the original separate action, not a save shortcut.
    for (const button of original.filter(node => node.tagName === 'BUTTON' && node !== back)) j.move(button, info.body);
    const abstract = q(card, ':scope > details.rf-plain-disclosure');
    j.property(abstract, 'open', true);
  }

  function observe() {
    if (!enabled || disposed) return;
    const page = screen(); if (page) observer.observe(page, {childList:true,subtree:true});
    observer.observe(document.body, {childList:true});
    for (const dialog of qa(document, 'dialog.rf-pilot-dialog')) observer.observe(dialog, {childList:true,subtree:true});
  }
  function compose() {
    frame = 0; if (!enabled || disposed) return;
    observer.disconnect();
    const focused = document.activeElement;
    const scroll = [...qa(document, '#scrBody .absbox, dialog.rf-pilot-dialog, .rfl-pilot-reading')].map(node => [node,node.scrollTop]);
    try {
      screenCompose();
      reasonCompose();
      for (const [dialog, entry] of pilots) if (!dialog.isConnected) { entry.j.restore(); pilots.delete(dialog); }
      for (const dialog of qa(document, 'dialog.rf-pilot-dialog')) pilotCompose(dialog);
      for (const details of qa(document, '.rfl-group[open]')) placeGroup(details);
    } finally {
      if (focused?.isConnected && focused !== document.activeElement) focused.focus({preventScroll:true});
      for (const [node, top] of scroll) if (node.isConnected) node.scrollTop = top;
      observe();
    }
  }
  function schedule() { if (enabled && !frame && !disposed) frame = requestAnimationFrame(compose); }
  // Resize runs before media-query change handlers. Restore before the product's
  // mobile composer records its own anchors, so two presentation layers never
  // capture each other's temporary wrappers.
  function onResize() {
    if (!enabled) return;
    if (innerWidth <= 760 && screenJournal) {
      observer.disconnect(); screenJournal.restore(); screenJournal = null; observe();
    }
    schedule();
  }
  const observer = new MutationObserver(schedule);
  function setEnabled(value) {
    if (disposed || enabled === Boolean(value)) return;
    const focused = document.activeElement;
    enabled = Boolean(value);
    document.documentElement.classList.toggle('rf-workflow-layout-preview', enabled);
    if (enabled) compose();
    else {
      observer.disconnect(); cancelAnimationFrame(frame); frame = 0;
      screenJournal?.restore(); screenJournal = null;
      reasonEntry?.j.restore(); reasonEntry = null;
      for (const entry of pilots.values()) entry.j.restore();
      pilots.clear();
    }
    if (focused?.isConnected && focused !== document.activeElement) focused.focus({preventScroll:true});
  }
  const events = ['reviewflow:workspace-changed','reviewflow:task-context-changed','reviewflow:language-changed','reviewflow:workspace-modules-ready'];
  for (const event of events) document.addEventListener(event,schedule);
  desktop.addEventListener('change',schedule);
  window.addEventListener('resize',onResize,{capture:true});
  window.RFWorkflowLayoutPreview = Object.freeze({
    setEnabled,
    refresh:schedule,
    get enabled() { return enabled; },
    destroy() {
      setEnabled(false); disposed = true;
      for (const event of events) document.removeEventListener(event,schedule);
      desktop.removeEventListener('change',schedule);
      window.removeEventListener('resize',onResize,{capture:true});
    }
  });
})();
