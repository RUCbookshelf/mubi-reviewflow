(function () {
  'use strict';

  const team = document.getElementById('teamPanel');
  if (!team) return;
  const box = el('section', { id: 'rfSearchStrategy', class: 'card', hidden: true });
  const tr = (tag, key, args, attrs) => el(tag, { ...attrs, text: T(key, args) });
  const text = (tag, value, attrs) => el(tag, { ...attrs, text: value ?? '—', translate: 'no' });
  const row = (...nodes) => el('div', { class: 'row' }, ...nodes);
  const hint = (key, args) => tr('p', key, args, { class: 'hint' });
  const button = (key, fn, attrs) => tr('button', key, undefined,
    { type: 'button', class: 'btn b-out sm', onclick: fn, ...attrs });
  const dbKey = database => ({
    pubmed: 'living_review.source.pubmed', eric: 'living_review.source.eric', embase: 'Embase',
    cochrane: 'Cochrane Library', wos: 'Web of Science', other: '其他',
  })[database] || database;
  const select = (options, selected) => {
    const control = el('select', { class: 'input' }, options.map(([value, key]) =>
      tr('option', key, undefined, { value })));
    if (selected != null) control.value = String(selected);
    return control;
  };
  const field = (key, input) => el('label', { class: 'rf-strategy-field' }, tr('span', key), input);
  const input = attrs => el('input', { class: 'input', ...attrs });
  const textarea = (value = '', attrs) => {
    const control = el('textarea', { class: 'input', rows: '3', ...attrs });
    control.value = value;
    return control;
  };
  const style = el('style', { text: [
    '#rfSearchStrategy .rf-strategy-field{display:grid;gap:4px;min-width:0}',
    '#rfSearchStrategy .rf-strategy-field[hidden]{display:none}',
    '#rfSearchStrategy .rf-strategy-field>span{font-size:.8rem;color:var(--muted)}',
    '#rfSearchStrategy .rf-strategy-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,150px),1fr));gap:10px 12px;margin:8px 0}',
    '#rfSearchStrategy .rf-strategy-card{margin:8px 0;padding:12px;background:var(--bg);min-width:0;overflow-wrap:anywhere}',
    '#rfSearchStrategy .rf-strategy-card:hover{box-shadow:none}',
    '#rfSearchStrategy .rf-strategy-card pre{margin:8px 0;font-size:.82rem;white-space:pre-wrap;overflow-wrap:anywhere}',
    '#rfSearchStrategy .input[type=checkbox]{width:auto;accent-color:var(--primary)}',
    '#rfSearchStrategy .rf-strategy-tabs{gap:4px;padding-bottom:8px;border-bottom:1px solid var(--border)}',
    '#rfSearchStrategy .rf-strategy-tabs>.btn{border-color:transparent;color:var(--muted);min-height:34px;white-space:normal;transition:background-color 140ms ease,color 140ms ease}',
    '#rfSearchStrategy .rf-strategy-tabs>.btn:hover,#rfSearchStrategy .rf-strategy-tabs>.btn[aria-pressed=true]{background:var(--primary-light);color:var(--primary-dark)}',
    '#rfSearchStrategy .rf-strategy-tabs>.btn:focus-visible{outline:2px solid var(--primary);outline-offset:2px}',
    '@media(prefers-reduced-motion:reduce){#rfSearchStrategy .rf-strategy-tabs>.btn{transition:none}}',
  ].join('\n') });
  document.head.append(style);

  const toggle = button('search_strategy.entry', () => {
    if (content.hidden) open();
    else setExpanded(false);
  }, { 'aria-expanded': 'false', 'aria-controls': 'rfSearchStrategyContent' });
  const content = el('div', { id: 'rfSearchStrategyContent', hidden: true });
  const notice = el('p', { class: 'hint', role: 'status' });
  const panel = el('div');
  const controls = el('div', { class: 'row rf-strategy-tabs' });
  const builderTab = button('search_strategy.builder', () => show('builder'), { 'aria-pressed': 'true' });
  const libraryTab = button('search_strategy.library', () => show('library'), { 'aria-pressed': 'false' });
  controls.append(builderTab, libraryTab);
  content.append(controls, notice, panel);
  box.append(toggle, content);

  let state = null;
  function setExpanded(expanded) {
    content.hidden = !expanded;
    toggle.setAttribute('aria-expanded', String(expanded));
  }

  function taskChanged() {
    state = S.task ? {
      taskId: S.task.task_id, base: taskApi() + '/search-strategy', tab: 'builder', busy: false,
      listing: null, timeline: null, generated: null, preview: new Map(),
      controls: null, selected: new Set(),
    } : null;
    notice.textContent = '';
    panel.replaceChildren();
    controls.inert = false;
    panel.inert = false;
    setExpanded(false);
    box.hidden = !state;
    if (runtimeMode === 'server') team.append(box);
    else team.after(box);
  }
  const ask = (s, path, method, body, extra) => api(s.base + path, { method, body, ...extra });

  async function run(action, done) {
    const s = state;
    if (!s || s.busy) return;
    s.busy = true;
    controls.inert = true;
    panel.inert = true;
    notice.textContent = '';
    try {
      const result = await action(s);
      if (state === s && done) await done(s, result);
    } catch (error) {
      if (state === s) notice.textContent = window.RFServerMessage?.(error.messageKey, error.params) || error.message;
    } finally {
      if (state === s) {
        s.busy = false;
        controls.inert = false;
        panel.inert = false;
      }
    }
  }

  async function load(s) {
    const [listing, timeline] = await Promise.all([ask(s, '/strategies'), ask(s, '/snapshots')]);
    if (state !== s) return;
    s.listing = listing;
    s.timeline = timeline;
  }

  async function open() {
    if (S.page !== 'home' && !await go('home')) return;
    if (!state) return;
    window.WorkflowControls?.homeTab('tasks');
    setExpanded(true);
    box.scrollIntoView({ block: 'nearest' });
    if (!state.listing) await run(load, s => show(s.tab));
  }

  function show(tab) {
    const s = state;
    if (!s?.listing || !s.timeline) return;
    s.tab = tab;
    builderTab.setAttribute('aria-pressed', String(tab === 'builder'));
    libraryTab.setAttribute('aria-pressed', String(tab === 'library'));
    notice.textContent = '';
    if (tab === 'builder') renderBuilder(s);
    else renderLibrary(s);
  }

  function externalLink(url, label = 'search_strategy.open_db') {
    if (!url) return null;
    try {
      if (new URL(url).protocol !== 'https:') return null;
      return tr('a', label, undefined, { href: url, target: '_blank', rel: 'noopener noreferrer', class: 'btn b-out sm' });
    } catch (_) { return null; }
  }

  function copy(value) {
    return button('search_strategy.copy_query', () => run(() => navigator.clipboard.writeText(value), () => {
      notice.textContent = T('search_strategy.copied');
    }));
  }

  function warnings(items = []) {
    return items.map(warning => {
      const content = window.RFServerMessage?.(warning.code, warning.params);
      return content ? text('p', content, { class: 'hint' }) : null;
    });
  }

  function conceptForm() {
    const label = input({ type: 'text' });
    const value = input({ type: 'text' });
    const synonyms = input({ type: 'text' });
    const mesh = input({ type: 'text' });
    const operator = select(['AND', 'OR', 'NOT'].map(op => [op, op]));
    const card = el('div', { class: 'card rf-strategy-card' }, el('div', { class: 'rf-strategy-fields' },
      field('search_strategy.concept', label), field('search_strategy.term', value),
      field('search_strategy.synonyms', synonyms), field('search_strategy.mesh', mesh),
      field('search_strategy.connector', operator)));
    return { card, label, value, synonyms, mesh, operator };
  }

  function getBuilder(s) {
    if (s.controls) return s.controls;
    const canvas = el('div');
    const concepts = [];
    const query = textarea();
    const dateFrom = input({ type: 'date' });
    const dateTo = input({ type: 'date' });
    const mode = select([['pico', 'search_strategy.pico'], ['query', 'search_strategy.pubmed_query']]);
    const conceptSection = el('div');
    const queryField = field('search_strategy.pubmed_query', query);
    const results = el('div');
    const builder = el('div', {}, field('search_strategy.input_mode', mode), conceptSection, queryField);
    function add() {
      const concept = conceptForm();
      concepts.push(concept);
      concept.card.append(button('search_strategy.remove_concept', () => {
        concepts.splice(concepts.indexOf(concept), 1);
        concept.card.remove();
      }));
      canvas.append(concept.card);
    }
    function updateMode() {
      conceptSection.hidden = mode.value !== 'pico';
      queryField.hidden = mode.value !== 'query';
    }
    const splitTerms = value => value.split(',').map(part => part.trim()).filter(Boolean);
    function readConcepts() {
      return concepts.map((concept, index) => ({
        key: 'concept_' + (index + 1), label: concept.label.value.trim(), value: concept.value.value.trim(),
        synonyms: splitTerms(concept.synonyms.value), mesh_terms: splitTerms(concept.mesh.value),
        operator: concept.operator.value,
      })).filter(concept => concept.value || concept.synonyms.length || concept.mesh_terms.length);
    }
    function assemble() {
      const expressions = readConcepts().map(concept => ({
        operator: concept.operator,
        query: '(' + [concept.value, ...concept.synonyms].filter(Boolean).concat(
          concept.mesh_terms.map(term => term + '[mh]')).join(' OR ') + ')',
      }));
      query.value = expressions.reduce((combined, entry) => combined ? '(' + combined + ' ' + entry.operator + ' ' + entry.query + ')' : entry.query, '');
      mode.value = 'query';
      updateMode();
      query.focus();
    }
    conceptSection.append(hint('search_strategy.pico_hint'), canvas,
      row(button('search_strategy.add_concept', add), button('search_strategy.assemble', assemble)),
      el('div', { class: 'rf-strategy-fields' },
        field('search_strategy.date_from', dateFrom), field('search_strategy.date_to', dateTo)));
    const translate = button('search_strategy.translate', () => {
      const pico = mode.value === 'pico'
        ? readConcepts().map(({ operator, ...concept }) => concept) : [];
      const payload = mode.value === 'query'
        ? { query_text: query.value.trim() }
        : { pico, date_from: dateFrom.value, date_to: dateTo.value };
      run(current => ask(current, '/translate', 'POST', payload), (current, result) => {
        current.generated = { pico, strategies: result.strategies.filter(entry => entry.query_text) };
        current.preview.clear();
        current.selected = new Set(current.generated.strategies.map(entry => entry.database));
        renderGenerated(current, results);
      });
    }, { class: 'btn b-inc sm' });
    builder.append(row(translate), results);
    mode.onchange = updateMode;
    add();
    updateMode();
    s.controls = { builder, results };
    return s.controls;
  }

  function renderBuilder(s) {
    const { builder, results } = getBuilder(s);
    const timeline = el('div');
    panel.replaceChildren(builder, timeline);
    renderGenerated(s, results);
    renderTimeline(s, timeline);
  }

  function renderGenerated(s, host) {
    host.replaceChildren();
    if (!s.generated) return;
    if (!s.generated.strategies.length) host.append(hint('search_strategy.query_required'));
    for (const entry of s.generated.strategies) {
      const preview = s.preview.get(entry.database);
      const check = input({ type: 'checkbox', 'aria-label': T('search_strategy.select_database', { database: T(dbKey(entry.database)) }) });
      check.checked = s.selected.has(entry.database);
      check.onchange = () => {
        if (check.checked) s.selected.add(entry.database);
        else s.selected.delete(entry.database);
      };
      const canPreview = entry.database === 'pubmed' || entry.database === 'eric';
      const previewButton = button('search_strategy.preview', () => run(
        current => ask(current, '/preview-counts', 'POST', { items: [{ database: entry.database, query_text: entry.query_text }] }),
        (current, result) => {
          current.preview.set(entry.database, result.results[0]);
          renderGenerated(current, host);
        }));
      host.append(el('article', { class: 'card rf-strategy-card' }, row(
        s.listing.can_manage ? check : null, tr('h4', dbKey(entry.database)),
        entry.degraded ? tr('span', 'search_strategy.degraded', undefined, { class: 'chip' }) : null),
        text('pre', entry.query_text), entry.syntax_note ? text('p', entry.syntax_note, { class: 'hint' }) : null,
        warnings(entry.warnings), row(copy(entry.query_text), externalLink(entry.url), canPreview ? previewButton : null),
        preview?.status === 'counted' ? hint('search_strategy.preview_count', { n: preview.count })
          : preview ? hint('search_strategy.preview_' + preview.hint)
            : canPreview ? null : hint('search_strategy.preview_run_in_database')));
    }
    if (!s.listing.can_manage || !s.generated.strategies.length) return;
    const note = textarea('', { rows: '2' });
    const current = input({ type: 'checkbox' });
    const save = createDrafts => run(async active => {
      const generated = active.generated;
      if (createDrafts && !active.selected.size) {
        throw new Error(T('search_strategy.choose_databases'));
      }
      const counts = Object.fromEntries([...active.preview.entries()]
        .filter(([, value]) => value.status === 'counted').map(([key, value]) => [key, value.count]));
      const { snapshot } = await ask(active, '/snapshots', 'POST', {
        pico: generated.pico, strategies: generated.strategies, counts, note: note.value,
        make_current: current.checked,
      });
      if (createDrafts) await ask(active, '/snapshots/' + snapshot.id + '/to-strategy', 'POST', {
        databases: [...active.selected], subscribe: false,
      });
      await load(active);
    }, active => {
      show(createDrafts ? 'library' : 'builder');
      if (active.listing) notice.textContent = T('search_strategy.saved');
    });
    host.append(field('备注', note), el('label', { class: 'row' }, current, tr('span', 'search_strategy.current')),
      row(button('search_strategy.save_snapshot', () => save(false)), button('search_strategy.to_library', () => save(true))));
  }

  function renderTimeline(s, host) {
    const snapshots = s.timeline.snapshots;
    host.append(tr('h3', 'search_strategy.timeline'));
    for (const snapshot of snapshots) {
      const card = el('details', { class: 'card rf-strategy-card rf-plain-disclosure' },
        tr('summary', 'search_strategy.snapshot_label', { id: snapshot.id, date: snapshot.created_at }),
        snapshot.is_current ? hint('search_strategy.current') : null,
        text('p', snapshot.content_sha256),
        snapshot.note ? text('p', snapshot.note) : null);
      for (const strategy of snapshot.strategies) {
        card.append(tr('h4', dbKey(strategy.database)), text('pre', strategy.query_text),
          hint('search_strategy.evidence_count', { n: snapshot.counts[strategy.database] ?? '—', source: snapshot.counts_source
            ? T('search_strategy.hit_' + snapshot.counts_source) : '—' }), copy(strategy.query_text));
      }
      const compare = el('div');
      const compareOptions = snapshots.filter(other => other.id !== snapshot.id);
      if (compareOptions.length) {
        const pick = select([['', 'search_strategy.compare'], ...compareOptions.map(other => [other.id, '#' + other.id])]);
        pick.onchange = () => {
          const other = compareOptions.find(item => item.id === Number(pick.value));
          compare.replaceChildren();
          if (!other) return;
          const first = new Map(snapshot.strategies.map(item => [item.database, item.query_text]));
          const second = new Map(other.strategies.map(item => [item.database, item.query_text]));
          for (const database of new Set([...first.keys(), ...second.keys()])) {
            const before = first.get(database);
            const after = second.get(database);
            compare.append(tr('h4', dbKey(database)), hint(before === after ? 'search_strategy.same' : 'search_strategy.changed'),
              el('div', { class: 'rf-strategy-fields' },
                el('div', {}, text('b', '#' + snapshot.id), text('pre', before)),
                el('div', {}, text('b', '#' + other.id), text('pre', after))));
          }
        };
        card.append(field('search_strategy.compare', pick), compare);
      }
      if (s.listing.can_manage) {
        const path = '/snapshots/' + snapshot.id;
        const checkboxes = snapshot.strategies.map(({ database }) => {
          const checked = input({ type: 'checkbox', checked: '' });
          return { database, checked, label: el('label', {}, checked, tr('span', dbKey(database))) };
        });
        const counts = snapshot.strategies.map(({ database }) => {
          const value = input({ type: 'number', min: '0', step: '1', value: snapshot.counts[database] ?? '' });
          return { database, value, label: field(dbKey(database), value) };
        });
        card.append(
          snapshot.is_current ? null : button('search_strategy.mark_current', () => mutate(path + '/current', 'PUT')),
          el('div', { class: 'rf-strategy-fields' }, counts.map(entry => entry.label)),
          button('search_strategy.manual_counts', () => {
            if (!counts.every(entry => entry.value.reportValidity())) return;
            mutate(path + '/manual-count', 'PUT', { counts: Object.fromEntries(counts.map(({ database, value }) =>
              [database, value.value === '' ? null : Number(value.value)])) });
          }),
          row(checkboxes.map(entry => entry.label), button('search_strategy.to_library', () => {
            const databases = checkboxes.filter(entry => entry.checked.checked).map(entry => entry.database);
            if (!databases.length) { notice.textContent = T('search_strategy.choose_databases'); return; }
            mutate(path + '/to-strategy', 'POST', { databases, subscribe: false }, 'library');
          })));
      }
      host.append(card);
    }
  }

  function mutate(path, method, body, tab) {
    return run(async s => { await ask(s, path, method, body); await load(s); }, s => {
      show(tab || s.tab);
      notice.textContent = T('search_strategy.saved');
      document.dispatchEvent(new Event('reviewflow:search-strategies-changed'));
    });
  }

  function renderLibrary(s) {
    panel.replaceChildren();
    if (!s.listing.can_manage) panel.append(hint('search_strategy.readonly'));
    const actions = row(button('search_strategy.appendix_text', () => download('text')),
      button('search_strategy.appendix_csv', () => download('csv')),
      button('search_strategy.prisma', prefill));
    if (s.listing.can_manage) actions.prepend(button('search_strategy.create', () => editDraft(s)));
    panel.append(actions);
    for (const strategy of s.listing.strategies) {
      const path = '/strategies/' + strategy.id;
      const evidence = strategy.hit_count == null ? null : hint('search_strategy.evidence_count',
        { n: strategy.hit_count, source: T('search_strategy.hit_' + strategy.hit_source) });
      const ops = row(copy(strategy.query_text), externalLink(strategy.url));
      if (s.listing.can_manage) {
        ops.append(button(strategy.subscribe ? 'search_strategy.unsubscribe' : 'search_strategy.subscribe',
          () => mutate(path + '/subscribe', 'PUT', { subscribe: !strategy.subscribe })),
          button('search_strategy.revise', () => mutate(path + '/revise', 'POST', {})));
        if (strategy.status === 'draft') ops.append(button('编辑', () => editDraft(s, strategy)),
          button('search_strategy.finalize', () => finalize(s, strategy)), button('删除', async () => {
            if (await RFConfirm(T('search_strategy.delete_confirm')) && state === s) mutate(path, 'DELETE');
          }));
      }
      const head = row(tr('span', 'search_strategy.status_' + strategy.status, undefined, { class: 'chip' }),
        tr('span', dbKey(strategy.database), undefined, { class: 'chip' }),
        tr('span', 'search_strategy.version', { version: strategy.version, id: strategy.id }, { class: 'chip' }),
        strategy.prev_id == null ? null : tr('span', 'search_strategy.previous', { id: strategy.prev_id }, { class: 'chip' }),
        strategy.subscribe ? tr('span', 'search_strategy.subscribed', undefined, { class: 'chip' }) : null);
      panel.append(el('article', { class: 'card rf-strategy-card' }, head, text('h4', strategy.title),
        text('pre', strategy.query_text), strategy.note ? text('p', strategy.note) : null,
        strategy.executed_at ? hint('search_strategy.executed', { date: strategy.executed_at }) : null, evidence, ops));
    }
    if (!s.listing.strategies.length) panel.append(hint('No records yet.'));
  }

  function editDraft(s, strategy) {
    const title = input({ value: strategy?.title || '', maxlength: '200', required: '' });
    const db = select(['pubmed', 'embase', 'wos', 'cochrane', 'eric', 'other'].map(value => [value, dbKey(value)]), strategy?.database);
    const query = textarea(strategy?.query_text, { required: '', maxlength: '4000' });
    const syntax = textarea(strategy?.syntax_note, { rows: '2' });
    const note = textarea(strategy?.note, { rows: '2' });
    panel.replaceChildren(tr('h3', strategy ? '编辑' : 'search_strategy.create'),
      field('search_strategy.title', title), field('数据库', db), field('检索式', query),
      field('search_strategy.syntax', syntax), field('备注', note),
      row(button('取消', () => renderLibrary(s)), button('保存', () => {
        if (!title.reportValidity() || !query.reportValidity()) return;
        mutate(strategy ? '/strategies/' + strategy.id : '/strategies', strategy ? 'PUT' : 'POST', {
          title: title.value, database: db.value, query_text: query.value, syntax_note: syntax.value, note: note.value,
        });
      })));
  }

  function finalize(s, strategy) {
    const date = input({ type: 'date' });
    const hits = input({ type: 'number', min: '0', step: '1', value: strategy.hit_count ?? '' });
    const source = select([['manual', 'search_strategy.hit_manual'], ['preview', 'search_strategy.hit_preview']]);
    const note = textarea(strategy.note, { rows: '2' });
    panel.replaceChildren(tr('h3', 'search_strategy.finalize'), hint('search_strategy.finalize_hint'),
      field('search_strategy.date', date), field('上次命中数', hits), field('search_strategy.hit_source', source),
      field('备注', note), row(button('取消', () => renderLibrary(s)), button('search_strategy.finalize', () => {
        if (!hits.reportValidity()) return;
        const body = { hit_count: hits.value === '' ? null : Number(hits.value), hit_source: source.value, note: note.value };
        if (date.value) body.executed_at = date.value;
        mutate('/strategies/' + strategy.id + '/finalize', 'POST', body);
      })));
  }

  function download(format) {
    return run(s => ask(s, '/appendix?format=' + format, undefined, undefined, { blob: true }), (s, blob) => {
      const url = URL.createObjectURL(blob);
      const link = el('a', { href: url, download: 'search-strategy-' + s.taskId + (format === 'text' ? '.txt' : '.csv') });
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }

  function prefill() {
    return run(s => ask(s, '/prisma-prefill', 'POST', { only_with_counts: true }), async (s, response) => {
      notice.replaceChildren(...warnings(response.warnings).filter(Boolean));
      if (!response.rows.length) {
        notice.append(hint('search_strategy.no_prefill'));
        return;
      }
      if (!await RFConfirm({ message: T('search_strategy.prefill_confirm', { n: response.rows.length }), danger: false })) return;
      if (state !== s || !await go('prisma') || state !== s) return;
      const host = document.getElementById('prRowsDb');
      if (!host || response.target_field !== 'identified_rows_db') return;
      for (const entry of response.rows) {
        const name = entry.name.trim().toLowerCase();
        const existing = [...host.children].find(item => {
          const values = item._collect?.();
          return values?.source_type === 'database' && values.name.toLowerCase() === name;
        });
        const item = existing || prIdentifiedRow('db', ['database', 'register']);
        const [selectType, inputName, inputCount] = item.children;
        selectType.value = entry.source_type;
        inputName.value = entry.name;
        inputCount.value = String(entry.n);
        item.title = T('search_strategy.evidence_count', { n: entry.n, source: T('search_strategy.hit_' + entry.hit_source) });
        if (!existing) host.append(item);
      }
      prAutoCalc();
      toast(T('search_strategy.review_prisma'), 'info');
      for (const warning of response.warnings || []) {
        const message = window.RFServerMessage?.(warning.code, warning.params);
        if (message) toast(message, 'warn');
      }
    });
  }

  document.addEventListener('reviewflow:task-context-changed', taskChanged);
  window.SearchStrategyManager = Object.freeze({ open, databaseLabel: dbKey });
  taskChanged();
})();
