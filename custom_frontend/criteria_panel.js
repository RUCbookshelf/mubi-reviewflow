/* Task-wide read-only criteria reference. Protocol Designer owns every write. */
(function () {
  'use strict';
  const style = el('style', {text: [
    '.rf-criteria-toggle{margin:0;white-space:normal}',
    '.rf-criteria-toggle[aria-expanded=true]{background:var(--primary-light);color:var(--primary-dark)}',
    '.rf-criteria-view{position:fixed;z-index:40;inset:76px 12px auto auto;width:min(340px,calc(100vw - 24px));max-height:calc(100dvh - 96px);margin:0;padding:0;overflow:auto;overscroll-behavior:contain;overflow-wrap:anywhere;box-shadow:var(--shadow)}',
    '.rf-criteria-heading{position:sticky;inset-block-start:0;display:flex;align-items:center;justify-content:space-between;gap:8px;padding:10px 16px;border-bottom:1px solid var(--border);background:var(--surface)}',
    '.rf-criteria-heading h3{margin:0}',
    '.rf-criteria-content{padding:12px 16px 16px;white-space:pre-wrap}',
    '.rf-criteria-content h4{margin:12px 0 5px}.rf-criteria-content h4:first-child{margin-top:0}',
    '.rf-criteria-content ul{margin:0;padding-inline-start:18px}.rf-criteria-content dd{margin-inline-start:12px}.rf-criteria-content :is(li,dd){margin-block-end:5px}',
    '@media(max-width:760px){#appMain .rf-mobile-screen-actions:has(.rf-criteria-panel:not([hidden])){flex-wrap:wrap}.rf-criteria-view{inset:auto 0 0;width:100%;max-height:72dvh;border-radius:16px 16px 0 0;z-index:85}.rf-criteria-content{padding-bottom:max(16px,env(safe-area-inset-bottom))}}',
    '@media(prefers-reduced-motion:no-preference){.rf-criteria-view:not([hidden]){animation:rf-criteria-enter 140ms ease-out}@keyframes rf-criteria-enter{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}}',
  ].join('\n')});
  document.head.append(style);
  let nextId = 0;
  let cached = null;
  const mounted = new WeakMap();
  const screenPanels = [];

  function scope() {
    return S.task?.task_id && S.user ? S.user.user_id + '/' + S.task.task_id : null;
  }
  function prefsKey(surface) {
    return 'rf.criteria_panel.' + S.user?.username + '.' + surface;
  }
  function preference(surface, value) {
    try {
      if (value !== undefined) localStorage.setItem(prefsKey(surface), value ? 'open' : 'closed');
      return localStorage.getItem(prefsKey(surface)) === 'open';
    } catch (_) { return false; }
  }
  function criteria(taskId) {
    const key = scope();
    if (cached?.key === key) return cached.promise;
    const base = '/api/tasks/' + encodeURIComponent(taskId);
    const record = {key, promise: Promise.all([
      api(base + '/criteria-panel'),
      api(base + '/workflow-settings').catch(() => null),
    ]).catch(error => {
      if (cached === record) cached = null;
      throw error;
    })};
    cached = record;
    return record.promise;
  }
  function errorMessage(error) {
    switch (error.status) {
      case 401: return T('criteria_panel.login');
      case 403: return T('criteria_panel.task_forbidden');
      case 404: return T('criteria_panel.task_missing');
      case 409: return error.messageKey === 'coding.collaboration_owner_unset'
        ? T('coding.collaboration_owner_unset') : T('criteria_panel.task_busy');
      case 503: return T('criteria_panel.owner_unavailable');
      default: return T('criteria_panel.unavailable');
    }
  }
  function verbatim(tag, text) { return el(tag, {text, translate: 'no'}); }
  function items(label, values, empty) {
    return [el('h4', {text: T(label)}), values.length
      ? el('ul', {}, values.map(value => verbatim('li', value)))
      : el('p', {class: 'hint', text: T(empty)})];
  }

  function mount(container, options = {}) {
    if (!container) return null;
    if (mounted.has(container)) return mounted.get(container).root;
    const surface = options.training ? 'training' : options.surface || 'screen';
    const id = 'rf-criteria-view-' + (++nextId);
    const button = el('button', {type: 'button', class: 'btn b-out rf-criteria-toggle', 'aria-controls': id});
    const close = el('button', {type: 'button', class: 'btn b-out sm'});
    const title = el('h3', {id: id + '-heading'});
    const content = el('div', {class: 'rf-criteria-content', 'aria-live': 'polite'});
    const view = el('aside', {id, class: 'card rf-criteria-view', 'aria-labelledby': title.id, hidden: ''},
      el('div', {class: 'rf-criteria-heading'}, title, close), content);
    const root = el('div', {class: 'rf-criteria-panel'}, button, view);
    let request = 0;
    function syncLabels() {
      button.textContent = title.textContent = T('criteria_panel.title');
      close.textContent = T('关闭');
      button.setAttribute('aria-expanded', String(!view.hidden));
      button.setAttribute('aria-label', T(view.hidden ? 'criteria_panel.expand' : 'criteria_panel.collapse'));
      close.setAttribute('aria-label', T('criteria_panel.collapse'));
    }
    async function load() {
      const taskId = S.task?.task_id;
      const key = scope();
      if (!taskId || !key || view.hidden) return;
      const ticket = ++request;
      const active = () => root.isConnected && !view.hidden && ticket === request && scope() === key;
      content.replaceChildren(el('p', {class: 'hint', text: T('Loading current saved work…')}));
      try {
        const [data, settings] = await criteria(taskId);
        if (!active()) return;
        content.replaceChildren();
        if (options.training) content.append(el('p', {class: 'hint', text: T('criteria_panel.training_hint')}));
        if (!data.has_protocol) content.append(el('p', {class: 'hint', text: T('criteria_panel.guide')}));
        else {
          content.append(...items('criteria_panel.inclusion', data.inclusion, 'criteria_panel.empty_inclusion'),
            ...items('criteria_panel.exclusion', data.exclusion, 'criteria_panel.empty_exclusion'),
            el('h4', {text: T('criteria_panel.key_terms')}));
          content.append(data.key_terms.length
            ? el('dl', {}, data.key_terms.flatMap(term => [verbatim('dt', term.term),
              term.definition ? verbatim('dd', term.definition) : el('dd', {text: T('criteria_panel.term_undefined')})]))
            : el('p', {class: 'hint', text: T('criteria_panel.empty_key_terms')}));
        }
        if (settings?.can_manage === true || !data.has_protocol) content.append(el('button', {
          type: 'button', class: 'btn b-out',
          text: T(settings?.can_manage === true ? 'criteria_panel.edit' : 'criteria_panel.view_protocol'),
          onclick: () => window.RFProtocolDesigner?.open(taskId),
        }));
      } catch (error) {
        if (!active()) return;
        content.replaceChildren(el('p', {class: 'hint', text: errorMessage(error)}));
        if (error.messageKey !== 'coding.collaboration_owner_unset' &&
            ![401, 403, 404].includes(error.status)) {
          content.append(el('button', {type: 'button', class: 'btn b-out', text: T('Retry / refresh'), onclick: load}));
        }
      }
    }
    function setOpen(open, remember = false) {
      view.hidden = !open;
      if (remember) preference(surface, open);
      syncLabels();
      if (open) load();
      else { request++; content.replaceChildren(); }
    }
    button.addEventListener('click', () => setOpen(view.hidden, true));
    close.addEventListener('click', () => { setOpen(false, true); button.focus(); });
    root.addEventListener('keydown', event => {
      event.stopPropagation();
      if (!view.hidden && event.key === 'Escape') close.click();
    });
    container.append(root);
    const controller = {root, sync() {
      root.hidden = !scope();
      setOpen(!root.hidden && S.page === surface && preference(surface));
    }};
    mounted.set(container, controller);
    if (options.training) setOpen(!!scope() && preference(surface));
    else { screenPanels.push(controller); controller.sync(); }
    return root;
  }

  window.RFCriteriaPanel = Object.freeze({mount});
  mount(document.querySelector('.page[data-p="screen"] .scr-status'));
  const fulltextNotice = document.getElementById('ftScreeningNotice');
  if (fulltextNotice) {
    const dock = el('div', {});
    fulltextNotice.before(dock);
    mount(dock, {surface: 'fulltext'});
  }
  document.addEventListener('reviewflow:workspace-changed', () => screenPanels.forEach(panel => panel.sync()));
  document.addEventListener('reviewflow:task-context-changed', () => {
    cached = null;
    screenPanels.forEach(panel => panel.sync());
  });
  document.addEventListener('reviewflow:protocol-saved', () => { cached = null; });
})();
