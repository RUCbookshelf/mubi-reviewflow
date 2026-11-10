/* Personal keyword sets and server-computed Unicode ranges. */
(function () {
  'use strict';
  const anchor = document.querySelector('.page[data-p="screen"] .scr-status');
  if (!anchor) return;
  const colors = ['#f5dfa1', '#cce6db', '#f7d8d2', '#dcd8f9', '#c8e8fa', '#f4d1e7'];
  document.head.append(el('style', {text: [
    '#rfKeywordSettings{width:min(680px,calc(100% - 24px));margin:auto}',
    '#rfKeywordSettings>header{justify-content:space-between;margin:0 0 8px}',
    '#rfKeywordSettings>header h3{margin:0}',
    '#rfKeywordSettings .rf-keyword-set{display:grid;gap:8px;padding-block:12px;border-bottom:1px solid var(--border)}',
    '#rfKeywordSettings .rf-keyword-set>.btn{justify-self:start}',
    '.rf-keyword-hit{background:var(--rf-keyword-color);color:#263536;border-radius:3px}',
    '@supports(background:color-mix(in srgb,white,black)){[data-theme=dark] .rf-keyword-hit{background:color-mix(in srgb,var(--surface) 65%,var(--rf-keyword-color));color:var(--ink)}}',
    '.rf-keyword-legend{margin:6px 0;gap:5px;overflow-wrap:anywhere}.rf-keyword-legend .rf-keyword-hit{padding-inline:4px}',
  ].join('\n')}));
  const launch = el('button', {type: 'button', class: 'btn b-out', text: T('highlight.title'), hidden: ''});
  anchor.append(launch);
  const heading = el('h3', {id: 'rfKeywordSettingsHeading', text: T('highlight.title')});
  const main = el('div', {});
  const message = el('p', {class: 'hint', role: 'status'});
  const close = el('button', {type: 'button', class: 'btn b-out', text: T('关闭')});
  const panel = el('dialog', {id: 'rfKeywordSettings', class: 'wf-source-dialog', 'aria-labelledby': heading.id},
    el('header', {class: 'row'}, heading, close), message, main);
  document.body.append(panel);
  close.addEventListener('click', () => panel.close());
  panel.addEventListener('keydown', event => event.stopPropagation());

  let current = null;
  let saved = null;
  let loading = null;
  let saving = false;
  let context = 0;
  let revision = 0;

  function endpoint() { return taskApi() + '/highlight'; }
  function updateScope() {
    context++;
    revision++;
    launch.hidden = !S.task;
    saved = loading = current = null;
    saving = false;
    if (panel.open) panel.close();
    main.replaceChildren();
    message.textContent = '';
  }
  function getSettings(refresh = false) {
    if (!refresh && saved) return Promise.resolve(saved);
    if (loading) return loading;
    const ticket = context;
    loading = api(endpoint() + '/sets').then(data => {
      if (ticket === context) saved = data;
      return data;
    }).finally(() => {
      if (ticket === context) loading = null;
    });
    return loading;
  }
  function tintedMark(termId, text) {
    const color = colors[termId % colors.length];
    return el('mark', {class: 'rf-keyword-hit', text, 'data-term-id': termId, translate: 'no',
      style: '--rf-keyword-color:' + color});
  }
  function markedText(response) {
    const fragment = document.createDocumentFragment();
    if (response.range_count === 0) {
      fragment.append(document.createTextNode(response.text));
      return fragment;
    }
    const chars = Array.from(response.text);
    let cursor = 0;
    for (const range of response.ranges) {
      fragment.append(document.createTextNode(chars.slice(cursor, range.start).join('')),
        tintedMark(range.term_id, chars.slice(range.start, range.end).join('')));
      cursor = range.end;
    }
    fragment.append(document.createTextNode(chars.slice(cursor).join('')));
    return fragment;
  }
  function restore(surface) {
    for (const {node, plain, label} of surface.fields) {
      node.replaceChildren();
      if (label) node.append(el('span', {text: T(label)}));
      node.append(el('span', {text: plain, translate: 'no'}));
    }
  }
  async function compute(surface) {
    if (!surface || !S.task) return;
    const ticket = ++revision;
    const active = () => ticket === revision && surface.legend.isConnected;
    restore(surface);
    surface.legend.replaceChildren();
    try {
      const settings = await getSettings();
      if (!active()) return;
      if (!settings.available) {
        surface.legend.textContent = T('highlight.storage_unavailable');
        return;
      }
      const activeSet = settings.sets.find(set => set.set_id === settings.active_set_id);
      if (!activeSet?.terms.length) {
        surface.legend.textContent = T('highlight.inactive');
        return;
      }
      const results = await Promise.all(surface.fields.map(async field => {
        if (field.text) return api(endpoint() + '/compute', {method: 'POST', body: {text: field.text}});
        return null;
      }));
      if (!active()) return;
      const first = results.find(Boolean);
      if (results.some(result => result?.settings_available === false)) {
        surface.legend.textContent = T('highlight.storage_unavailable');
        return;
      }
      if (!first?.terms.length) {
        surface.legend.textContent = T('highlight.inactive');
        return;
      }
      results.forEach((result, index) => {
        if (!result) return;
        const {node, label} = surface.fields[index];
        const value = el('span', {translate: 'no'}, markedText(result));
        node.replaceChildren();
        if (label) node.append(el('span', {text: T(label)}));
        node.append(value);
      });
      surface.legend.append(el('b', {text: T('highlight.legend')}));
      for (const item of first.terms) surface.legend.append(tintedMark(item.term_id, item.term));
    } catch (error) {
      if (active()) surface.legend.textContent = error.message || T('highlight.storage_unavailable');
    }
  }

  function show(article, nodes) {
    if (!article || !nodes.abstract || !nodes.title) return;
    nodes.abstract.parentElement.querySelector('.rf-keyword-legend')?.remove();
    const legend = el('div', {class: 'row hint rf-keyword-legend', role: 'status'});
    nodes.abstract.after(legend);
    const fields = [
      {node: nodes.title, plain: nodes.title.textContent, text: article.title || ''},
      {node: nodes.abstract, plain: article.abstract || nodes.abstract.textContent, text: article.abstract || '',
        label: article.abstract ? '摘要：' : null},
    ];
    current = {fields, legend};
    compute(current);
  }

  function editor(data) {
    main.replaceChildren();
    if (data.available !== true) {
      message.textContent = T('highlight.storage_unavailable');
      return;
    }
    const rows = [];
    const setList = el('div', {});
    const activeSet = el('select', {class: 'input', 'aria-label': T('highlight.activate')},
      el('option', {value: '', text: T('highlight.inactive')}));
    const add = el('button', {type: 'button', class: 'btn b-out', text: T('highlight.add_set')});
    const save = el('button', {type: 'submit', class: 'btn b-inc', text: T('highlight.save_sets')});
    const fields = el('fieldset', {style: 'border:0;padding:0;margin:0;min-width:0'},
      setList, el('div', {class: 'row'}, add, el('label', {}, T('highlight.activate'), activeSet), save));
    const form = el('form', {}, fields);
    function addRow(set = {}) {
      const name = el('input', {class: 'input', required: '', maxlength: data.limits.max_set_name_length,
        value: set.name || '', 'aria-label': T('highlight.set_name'), placeholder: T('highlight.set_name')});
      const terms = el('textarea', {class: 'ta', rows: 3, 'aria-label': T('highlight.terms'), placeholder: T('highlight.terms')});
      terms.value = (set.terms || []).join('\n');
      const remove = el('button', {type: 'button', class: 'btn b-out', text: T('highlight.remove_set')});
      const option = set.set_id ? el('option', {value: set.set_id, translate: 'no', text: set.name}) : null;
      const row = el('section', {class: 'rf-keyword-set'}, name, terms,
        set.set_id ? el('small', {class: 'hint', text: set.set_id, translate: 'no'}) : null,
        typeof set.term_count === 'number' ? el('p', {class: 'hint', text: T('highlight.term_count', set.term_count)}) : null,
        remove);
      const record = {set, name, terms};
      if (option) {
        activeSet.append(option);
        name.addEventListener('input', () => { option.textContent = name.value; });
      }
      rows.push(record);
      add.disabled = rows.length >= data.limits.max_sets;
      setList.append(row);
      remove.addEventListener('click', () => {
        rows.splice(rows.indexOf(record), 1);
        row.remove();
        option?.remove();
        add.disabled = false;
      });
      return name;
    }
    data.sets.forEach(addRow);
    activeSet.value = data.active_set_id || '';
    add.addEventListener('click', () => { addRow().focus(); });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (saving) return;
      const ticket = context;
      const sets = rows.map(({set, name, terms}) => ({
        ...(set.set_id ? {set_id: set.set_id} : {}),
        name: name.value,
        terms: terms.value.split(/\r?\n/).filter(line => line.trim() !== ''),
      }));
      fields.disabled = true;
      saving = true;
      message.textContent = '';
      try {
        const result = await api(endpoint() + '/sets', {method: 'PUT', body: {sets, active_set_id: activeSet.value || null}});
        if (ticket !== context) return;
        saved = result;
        if (panel.open) editor(result);
        compute(current);
      } catch (error) {
        if (ticket === context) message.textContent = error.message;
      } finally {
        fields.disabled = false;
        if (ticket === context) saving = false;
      }
    });
    main.append(form);
  }

  async function open() {
    if (!S.task || panel.open || saving) return;
    const ticket = context;
    main.replaceChildren();
    message.textContent = T('Loading current saved work…');
    panel.showModal();
    try {
      const data = await getSettings(true);
      if (ticket !== context || !panel.open) return;
      message.textContent = '';
      editor(data);
    } catch (error) {
      if (ticket !== context || !panel.open) return;
      message.textContent = error.message;
      main.append(el('button', {type: 'button', class: 'btn b-out', text: T('Retry / refresh'),
        onclick: () => { panel.close(); open(); }}));
    }
  }
  launch.addEventListener('click', open);
  document.addEventListener('reviewflow:task-context-changed', updateScope);
  updateScope();
  window.RFKeywordHighlight = Object.freeze({show});
})();
