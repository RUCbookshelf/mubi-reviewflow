(function () {
  'use strict';

  const anchor = document.getElementById('teamPanel');
  if (!anchor) return;
  const host = el('section', { id: 'rfPilot', class: 'card', hidden: true });
  const gate = el('div', { id: 'rfPilotGate', class: 'card', role: 'status', hidden: true });
  anchor.before(host);
  document.querySelector('.page[data-p="screen"]')?.prepend(gate);
  const style = el('style', { text: [
    '.rf-pilot-dialog{width:min(760px,calc(100vw - 28px));max-height:calc(100dvh - 36px);overflow:auto}',
    '.rf-pilot-card{overflow-wrap:anywhere;margin:10px 0}',
    '.rf-pilot-card details p{white-space:pre-wrap}',
    '.rf-pilot-fields{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));margin:8px 0}',
    '.rf-pilot-fields label{display:grid;gap:4px}',
    '.rf-pilot-pool{display:grid;gap:4px;max-height:250px;overflow:auto;margin:8px 0}',
    '.rf-pilot-pool label{display:flex;align-items:flex-start;gap:8px}',
    '.rf-pilot-borderline{border-inline-start:4px solid var(--primary)}',
  ].join('\n') });
  document.head.append(style);

  const request = (base, suffix, method, body) => api(base + '/pilot' + suffix, { method, body });
  const taskId = () => S.task?.task_id;
  const tr = (tag, key, params, attrs) => el(tag, { ...attrs, text: T(key, params) });
  const literal = (tag, value, attrs) => el(tag, { ...attrs, translate: 'no', text: value ?? '—' });
  const hint = (key, params) => tr('p', key, params, { class: 'hint' });
  const chip = (key, params) => tr('span', key, params, { class: 'chip' });
  const row = (...nodes) => el('div', { class: 'row' }, ...nodes);
  const button = (key, fn, attrs = {}) =>
    tr('button', key, undefined, { type: 'button', class: 'btn b-out sm', onclick: fn, ...attrs });
  const field = (key, input) => el('label', {}, tr('span', key), input);
  const decisionKey = decision => ({ include: '纳入', exclude: '排除', maybe: '暂不确定' }[decision]);
  const verdictKey = verdict => ({ pass: 'pilot.verdict_pass', borderline: 'pilot.verdict_borderline', fail: 'pilot.verdict_fail' }[verdict]);
  const statusKey = status => ({
    draft: 'pilot.status_draft', published: 'pilot.status_published', archived: 'pilot.status_archived',
    in_progress: 'pilot.in_progress', submitted: 'pilot.submitted',
  }[status]);
  const number = (value, digits = 3) => Number.isFinite(value)
    ? new Intl.NumberFormat(RFLang.get(), { maximumFractionDigits: digits }).format(value) : '—';
  const errorText = error => window.RFServerMessage?.(error.messageKey, error.params) || error.message;
  let capability = null;
  let active = null;
  let discovery = 0;
  let settingsBusy = false;
  const practiceResults = new Map();

  function renderHost(message = '') {
    host.replaceChildren();
    if (!capability) {
      host.hidden = !message;
      if (message) host.append(literal('p', message, { class: 'hint', role: 'status' }));
      return;
    }
    const { settings, can_manage: manages } = capability;
    host.hidden = !settings.pilot_enabled && !manages;
    if (host.hidden) return;
    host.append(tr('h3', settings.pilot_enabled ? 'pilot.entry' : 'pilot.activation'));
    if (settings.pilot_enabled) host.append(button('pilot.entry', () => open()));
    if (manages) {
      const options = [
        ['pilot_enabled', 'pilot.enable'],
        ['enforce_pass', 'pilot.enforce'],
      ];
      for (const [key, label] of options) {
        if (key === 'enforce_pass' && !settings.pilot_enabled) continue;
        const input = el('input', { type: 'checkbox', disabled: settingsBusy ? '' : null });
        input.checked = Boolean(settings[key]);
        input.onchange = () => changeSettings(key, input.checked);
        host.append(el('label', { class: 'row' }, input, tr('span', label)));
      }
    }
    if (message) host.append(literal('p', message, { class: 'hint', role: 'status' }));
  }

  async function changeSettings(key, value) {
    if (settingsBusy || !capability?.can_manage || !taskId()) return;
    const ticket = discovery;
    const base = taskApi();
    settingsBusy = true;
    renderHost();
    let message = '';
    try {
      const body = { [key]: value };
      if (key === 'enforce_pass' && value) body.pilot_enabled = true;
      const response = await request(base, '/settings', 'PUT', body);
      if (ticket !== discovery) return;
      capability.settings = response.settings;
      if (!response.settings.pilot_enabled) gate.hidden = true;
    } catch (error) {
      if (ticket === discovery) message = errorText(error);
    } finally {
      if (ticket === discovery) {
        settingsBusy = false;
        renderHost(message);
      }
    }
  }

  async function discover() {
    const ticket = ++discovery;
    if (active?.dialog.open) active.dialog.close();
    capability = null;
    settingsBusy = false;
    practiceResults.clear();
    gate.hidden = true;
    renderHost();
    if (!taskId()) return;
    try {
      const settings = await request(taskApi(), '/settings');
      if (ticket !== discovery) return;
      capability = settings;
      renderHost();
    } catch (error) {
      if (ticket === discovery && error.messageKey === 'coding.collaboration_owner_unset')
        renderHost(errorText(error));
    }
  }

  function makeSession() {
    const task = taskId();
    const base = taskApi();
    const opener = document.activeElement;
    const dialog = el('dialog', { class: 'wf-source-dialog rf-pilot-dialog', 'aria-labelledby': 'rfPilotTitle' });
    const title = tr('h2', 'pilot.entry', undefined, { id: 'rfPilotTitle' });
    const body = el('div');
    const notice = el('p', { class: 'hint', role: 'status' });
    dialog.append(row(title, button('关闭', () => dialog.close())), hint('pilot.training_badge'), notice, body);
    document.body.append(dialog);
    dialog.addEventListener('close', () => {
      dialog.remove();
      if (opener?.isConnected && task === taskId()) opener.focus();
      if (active?.dialog === dialog) active = null;
    });
    dialog.showModal();
    const session = {
      dialog, body, notice, base,
      get live() { return dialog.open && taskId() === task; },
      call: (suffix, method, payload) => request(base, suffix, method, payload),
      async catalogue() {
        if (!session.cataloguePromise) {
          session.cataloguePromise = api(base + '/articles').then(response => response.articles)
            .catch(error => { session.cataloguePromise = null; throw error; });
        }
        return session.cataloguePromise;
      },
      busy: false,
      async run(fn, recover) {
        if (session.busy || !session.live) return;
        session.busy = true;
        body.inert = true;
        notice.textContent = '';
        try { await fn(); }
        catch (error) {
          if (session.live && recover) {
            try { await recover(error); } catch (_) { /* Keep the original diagnostic. */ }
          }
          if (session.live) notice.textContent = errorText(error);
        } finally {
          session.busy = false;
          body.inert = false;
        }
      },
    };
    return session;
  }

  async function open(setId) {
    if (!taskId()) return;
    if (!capability?.settings.pilot_enabled && !setId) {
      if (capability?.can_manage && !host.hidden) {
        if (S.page !== 'home' && !await go('home')) return;
        window.WorkflowControls?.homeTab('tasks');
        host.scrollIntoView({ block: 'nearest' });
        host.querySelector('input')?.focus();
      } else toast(T('pilot.unavailable_entry'), 'info');
      return;
    }
    if (active?.dialog.open) return;
    const session = active = makeSession();
    await session.run(() => loadList(session, setId));
  }

  function intercept(error) {
    if (error?.status !== 403 || error.messageKey !== 'pilot.enforce_blocked') return false;
    const setId = error.params?.set_id;
    const task = taskId();
    gate.replaceChildren(
      literal('p', errorText(error)),
      hint('pilot.gate_pending'),
      button('pilot.open_required', () => open(setId)));
    gate.hidden = false;
    gate.setAttribute('tabindex', '-1');
    gate.focus();
    const status = gate.children[1];
    if (setId && task) {
      request(taskApi(), '/sets/' + encodeURIComponent(setId)).then(({ set }) => {
        if (taskId() !== task || gate.hidden || !status.isConnected) return;
        const key = verdictKey(set.clearance_verdict)
          || (set.attempt_status === 'in_progress' ? 'pilot.in_progress' : 'pilot.not_started');
        status.textContent = T(key);
      }).catch(() => { if (status.isConnected) status.textContent = ''; });
    } else status.textContent = '';
    return true;
  }

  async function loadList(session, focusId) {
    const [listing, history] = await Promise.all([session.call('/sets'), session.call('/my-history')]);
    if (!session.live) return;
    if (history.cleared) gate.hidden = true;
    const { sets, can_manage: manages } = listing;
    const historyRows = history.attempts || [];
    session.body.replaceChildren(
      row(manages ? button('pilot.create', () => session.run(() => wizard(session))) : null,
        button('pilot.history', () => renderHistory(session, historyRows))),
      sets.length ? document.createDocumentFragment() : hint('No records yet.'));
    let focusCard;
    for (const set of sets) {
      const memberAttempts = historyRows.filter(attempt => attempt.set_id === set.id);
      const pending = set.attempt_status === 'in_progress' || memberAttempts.some(attempt => attempt.status === 'in_progress');
      const submitted = memberAttempts.some(attempt => attempt.status === 'submitted');
      const clearance = set.clearance_verdict;
      const title = tr('h3', 'pilot.set_name', { id: set.id });
      const card = el('article', { class: 'card rf-pilot-card' }, title,
        row(chip(statusKey(set.status)), chip('pilot.sample_count', { n: set.item_count }),
          chip('pilot.sampling_' + set.sampling), set.seed == null ? null : chip('pilot.seed_value', { seed: set.seed })),
        thresholds(set.thresholds), set.note ? literal('p', set.note) : null);
      if (manages) {
        card.append(row(
          chip('pilot.gold_progress', { count: set.gold_count, total: set.item_count }),
          chip('pilot.attempt_total', { n: set.attempt_count }),
          set.gold_complete ? chip('pilot.gold_complete') : null));
      } else {
        card.append(row(
          set.attempt_id == null ? chip('pilot.not_started') : chip('pilot.attempt_number', { id: set.attempt_id }),
          set.attempt_status ? chip(statusKey(set.attempt_status)) : null,
          set.verdict ? chip(verdictKey(set.verdict)) : null,
          set.clearance_entered_by_discussion ? chip('pilot.discussion_recorded') : null));
        if (clearance) card.append(hint('pilot.clearance', { verdict: T(verdictKey(clearance)) }));
      }
      card.append(row(
        manages ? button('pilot.gold_badge', () => session.run(() => loadGold(session, set.id))) : null,
        manages ? button('pilot.board', () => session.run(() => loadBoard(session, set.id))) : null,
        (set.status === 'published' || pending) ? button(pending ? 'pilot.resume' : submitted ? 'pilot.practice_start' : 'pilot.start',
          () => session.run(async () => {
            await session.call('/sets/' + set.id + '/attempts', 'POST', {});
            if (session.live) await loadAttempt(session, set.id);
          })) : null,
        set.status !== 'draft' ? button('pilot.first_result', () => session.run(() => loadResult(session, set.id))) : null,
        manages && set.status !== 'archived' ? button('pilot.archive', () => session.run(async () => {
          if (!await RFConfirm(T('pilot.archive_confirm'))) return;
          if (!session.live) return;
          await session.call('/sets/' + set.id + '/archive', 'POST', {});
          if (session.live) await loadList(session);
        })) : null));
      session.body.append(card);
      if (set.id === Number(focusId)) focusCard = card;
    }
    focusCard?.scrollIntoView({ block: 'nearest' });
    focusCard?.querySelector('button')?.focus();
  }

  function view(session, titleKey, params) {
    session.body.replaceChildren(button('pilot.back_to_sets', () => session.run(() => loadList(session))),
      tr('h3', titleKey, params));
  }

  function thresholds(value = {}) {
    return hint('pilot.thresholds', {
      floor: number(value.min_kappa, 8), target: number(value.target_kappa, 8), agreement: number(value.agreement_floor, 8),
    });
  }

  async function wizard(session) {
    const sampling = el('select', { class: 'input' }, ['random', 'curated', 'mixed']
      .map(value => tr('option', 'pilot.sampling_' + value, undefined, { value })));
    const size = el('input', { class: 'input', type: 'number', min: '10', max: '100', value: '50', required: '' });
    const seed = el('input', { class: 'input', type: 'number', min: '0', max: '2147483647', step: '1' });
    const seedField = field('pilot.seed_optional', seed);
    const note = el('textarea', { class: 'input', rows: '2' });
    const thresholdFields = [
      ['min_kappa', 'pilot.kappa_min', '0.61'],
      ['target_kappa', 'pilot.kappa_target', '0.70'],
      ['agreement_floor', 'pilot.agreement_floor', '0.75'],
    ].map(([key, label, value]) => ({ key, label, input: el('input', {
      class: 'input', type: 'number', min: key === 'agreement_floor' ? '0' : '-1', max: '1', step: 'any', value, required: '',
    }) }));
    const filter = el('input', { class: 'input', type: 'search', placeholder: T('pilot.filter_articles'),
      'aria-label': T('pilot.filter_articles') });
    const pool = el('div', { class: 'rf-pilot-pool' });
    const selection = el('p', { class: 'hint', role: 'status' });
    const selected = new Set();
    let catalogue;
    function filterPool() {
      const text = filter.value.trim().toLowerCase();
      const rows = catalogue.filter(article => [article.title, article.authors, article.zotero_key]
        .some(value => String(value || '').toLowerCase().includes(text)));
      selection.textContent = T('pilot.selected_count', { n: selected.size });
      pool.replaceChildren(...rows.map(article => {
        const checkbox = el('input', { type: 'checkbox' });
        checkbox.checked = selected.has(article.zotero_key);
        checkbox.onchange = () => {
          if (checkbox.checked) selected.add(article.zotero_key);
          else selected.delete(article.zotero_key);
          selection.textContent = T('pilot.selected_count', { n: selected.size });
        };
        return el('label', {}, checkbox, literal('span', article.title || article.zotero_key));
      }));
    }
    filter.oninput = filterPool;
    const headings = ['pilot.step_sampling', 'pilot.step_size', 'pilot.step_curated', 'pilot.step_thresholds'];
    const stepHeading = el('h4');
    const panels = [
      el('div', {}, field('pilot.step_sampling', sampling)),
      el('div', { class: 'rf-pilot-fields' }, field('pilot.step_size', size), seedField),
      el('div', {}, filter, selection, pool),
      el('div', { class: 'rf-pilot-fields' }, thresholdFields.map(({ label, input }) => field(label, input)), field('备注', note)),
    ];
    let step = 0;
    const prev = button('pilot.prev', () => move(-1));
    const next = button('pilot.next', () => session.run(async () => {
      if (step === 1 && (!size.reportValidity() || (!seedField.hidden && !seed.reportValidity()))) return;
      if (step === 1 && sampling.value !== 'random' && !catalogue) {
        catalogue = await session.catalogue();
        if (!session.live) return;
        filterPool();
      }
      move(1);
    }));
    const create = button('pilot.create', () => session.run(async () => {
      if (!thresholdFields.every(({ input }) => input.reportValidity())) return;
      const body = {
        size: Number(size.value), sampling: sampling.value, note: note.value,
        thresholds: Object.fromEntries(thresholdFields.map(({ key, input }) => [key, Number(input.value)])),
      };
      if (body.sampling !== 'curated' && seed.value !== '') body.seed = Number(seed.value);
      if (body.sampling !== 'random') body.curated_keys = [...selected];
      const { set } = await session.call('/sets', 'POST', body);
      if (session.live) await loadGold(session, set.id);
    }));
    function move(direction) {
      step += direction;
      if (step === 2 && sampling.value === 'random') step += direction;
      panels.forEach((panel, index) => { panel.hidden = index !== step; });
      seedField.hidden = sampling.value === 'curated';
      stepHeading.textContent = T(headings[step]);
      prev.hidden = step === 0;
      next.hidden = step === 3;
      create.hidden = step !== 3;
    }
    view(session, 'pilot.create');
    session.body.append(stepHeading, ...panels, row(prev, next, create));
    move(0);
  }

  function articleCard(article) {
    return el('article', { class: 'card rf-pilot-card' },
      literal('h4', article.title || article.zotero_key),
      literal('p', [article.authors, article.journal, article.year, article.doi].filter(value => value != null && value !== '').join(' · ')),
      article.abstract ? el('details', {}, tr('summary', '摘要'), literal('p', article.abstract)) : null);
  }

  function answerForm(item, readonly, save) {
    const decision = el('select', { class: 'input', disabled: readonly ? '' : null },
      tr('option', 'pilot.choose_decision', undefined, { value: '' }),
      ['include', 'exclude', 'maybe'].map(value => tr('option', decisionKey(value), undefined, { value })));
    const reason = el('textarea', { class: 'input', rows: '2', disabled: readonly ? '' : null });
    decision.value = item.decision || '';
    reason.value = item.reason || '';
    const submit = button('保存', () => save(decision.value, reason.value));
    submit.disabled = !decision.value;
    decision.onchange = () => { submit.disabled = !decision.value; };
    return el('div', {}, el('div', { class: 'rf-pilot-fields' },
      field('pilot.decision', decision), field('pilot.reason', reason)), readonly ? null : submit);
  }

  function itemNav(index, total, move) {
    const prev = button('pilot.prev', () => move(-1));
    const next = button('pilot.next', () => move(1));
    prev.disabled = index === 0;
    next.disabled = index >= total - 1;
    return row(prev, chip('pilot.item_position', { current: index + 1, total }), next);
  }

  async function loadGold(session, setId, requestedIndex) {
    const path = '/sets/' + setId;
    const data = await session.call(path);
    if (!session.live || !data.can_manage) return;
    const catalogue = await session.catalogue();
    if (!session.live) return;
    const { set, items } = data;
    const byKey = new Map(catalogue.map(article => [article.zotero_key, article]));
    let index = requestedIndex ?? Math.max(0, items.findIndex(item => !item.gold));
    const readonly = set.status !== 'draft';
    const draw = () => {
      view(session, 'pilot.gold_badge');
      session.body.append(thresholds(set.thresholds),
        hint('pilot.gold_progress', { count: set.gold_count, total: set.item_count }));
      if (!readonly) {
        const publish = button('pilot.publish', () => session.run(async () => {
          await session.call(path + '/publish', 'POST', {});
          if (session.live) await loadList(session);
        }, refreshIfLocked));
        publish.disabled = !set.gold_complete;
        session.body.append(publish,
          set.gold_complete ? nullNode() : hint('pilot.gold_incomplete', { missing: set.item_count - set.gold_count }));
      }
      const item = items[index];
      if (!item) return;
      const card = articleCard(byKey.get(item.zotero_key) || item);
      card.prepend(chip('pilot.gold_badge'));
      card.append(answerForm(item.gold || {}, readonly, (decision, reason) => session.run(async () => {
        await session.call(path + '/gold', 'POST', { zotero_key: item.zotero_key, decision, reason });
        if (session.live) await loadGold(session, setId, Math.min(index + 1, items.length - 1));
      }, refreshIfLocked)));
      session.body.append(itemNav(index, items.length, delta => { index += delta; draw(); }), card);
    };
    function refreshIfLocked(error) {
      if (['pilot.gold_locked', 'pilot.set_not_draft'].includes(error.messageKey))
        return loadGold(session, setId, index);
    }
    draw();
  }

  async function loadAttempt(session, setId, requestedIndex) {
    const path = '/sets/' + setId + '/my-attempt';
    const { attempt, set, items, progress } = await session.call(path);
    if (!session.live) return;
    let index = requestedIndex ?? Math.max(0, items.findIndex(item => !item.answer));
    const draw = () => {
      view(session, 'pilot.set_name', { id: setId });
      session.body.append(thresholds(set.thresholds),
        hint('pilot.answered', { answered: progress.answered, total: progress.total }));
      const item = items[index];
      if (item) {
        const card = articleCard(item);
        card.append(answerForm({ decision: item.answer, reason: item.reason }, false, (decision, reason) =>
          session.run(async () => {
            const payload = { zotero_key: item.zotero_key, decision, reason, attempt_id: attempt.id };
            const response = await session.call(path + '/item', 'POST', payload);
            if (!session.live) return;
            item.answer = response.answer.decision;
            item.reason = response.answer.reason;
            Object.assign(progress, response.progress);
            index = Math.min(index + 1, items.length - 1);
            draw();
          })));
        window.RFCriteriaPanel?.mount(card, { training: true });
        session.body.append(itemNav(index, items.length, delta => { index += delta; draw(); }), card);
      }
      session.body.append(row(button('pilot.submit', () => session.run(async () => {
        const { result } = await session.call(path + '/submit', 'POST', {});
        if (!session.live) return;
        if (result.practice) practiceResults.set(result.attempt_id, result);
        renderResult(session, result, new Map(items.map(item => [item.zotero_key, item.title])));
      }, error => {
        if (error.messageKey !== 'pilot.attempt_not_answered') return;
        return loadAttempt(session, setId);
      })), button('pilot.pause', () => session.run(() => loadList(session)))));
    };
    draw();
  }

  function nullNode() { return document.createDocumentFragment(); }

  function metricRow(data = {}) {
    return row(chip('pilot.kappa', { value: number(data.kappa_binary) }),
      chip('pilot.agreement', { value: number(data.agreement) }),
      chip('pilot.pabak', { value: number(data.pabak) }));
  }

  function practiceBadge(attempt) {
    return attempt.status === 'submitted' && attempt.counts_for_clearance === false
      ? chip('pilot.attempt_practice') : null;
  }

  function attemptRow(attempt) {
    if (!attempt) return hint('pilot.not_started');
    return row(chip('pilot.attempt_number', { id: attempt.attempt_id }),
      attempt.status ? chip(statusKey(attempt.status)) : null,
      attempt.verdict ? chip(verdictKey(attempt.verdict)) : null,
      practiceBadge(attempt),
      literal('time', attempt.submitted_at || attempt.started_at));
  }

  function renderHistory(session, attempts) {
    view(session, 'pilot.history');
    if (!attempts.length) session.body.append(hint('No records yet.'));
    for (const attempt of attempts) {
      const practice = practiceResults.get(attempt.attempt_id);
      const card = el('article', { class: 'card rf-pilot-card' },
        tr('h4', 'pilot.set_name', { id: attempt.set_id }),
        chip(statusKey(attempt.set_status)), attemptRow(attempt));
      if (attempt.status === 'submitted') {
        card.append(metricRow(attempt));
        if (attempt.counts_for_clearance) card.append(button('pilot.first_result',
          () => session.run(() => loadResult(session, attempt.set_id))));
        else if (practice) card.append(button('pilot.view_result', () => renderResult(session, practice)));
        else card.append(hint('pilot.practice_detail_unavailable'));
      }
      session.body.append(card);
    }
  }

  async function loadResult(session, setId) {
    const { result } = await session.call('/sets/' + setId + '/my-result');
    if (!session.live) return;
    let titles = new Map();
    try {
      const records = await session.catalogue();
      titles = new Map(records.map(article => [article.zotero_key, article.title]));
    } catch (_) { /* Snapshot keys remain available if the catalogue cannot be read. */ }
    if (session.live) renderResult(session, result, titles);
  }

  function renderResult(session, result, titles = new Map()) {
    view(session, result.practice ? 'pilot.view_result' : 'pilot.first_result');
    session.body.append(row(chip(verdictKey(result.verdict)), practiceBadge(result),
      result.entered_by_discussion ? chip('pilot.discussion_recorded') : null),
      metricRow(result.metrics), hint('pilot.kappa_hint'), thresholds(result.thresholds));
    if (result.discussion_note) session.body.append(literal('p', result.discussion_note));
    if (result.counts_for_clearance && result.verdict === 'borderline' && !result.entered_by_discussion) {
      const note = el('textarea', { class: 'input', rows: '2' });
      session.body.append(field('pilot.discussion_note', note), button('pilot.enter_discussion',
        () => session.run(async () => {
          const response = await session.call('/sets/' + result.set_id + '/my-result/enter-by-discussion',
            'POST', { note: note.value });
          if (!session.live) return;
          gate.hidden = true;
          renderResult(session, response.result, titles);
        })));
    }
    const copies = row();
    for (const lang of ['zh', 'en']) {
      const text = result.methodology?.[lang];
      if (typeof text === 'string') copies.append(button('pilot.methodology_' + lang, () => session.run(async () => {
        await navigator.clipboard.writeText(text);
        if (session.live) session.notice.textContent = T('pilot.copied');
      })));
    }
    session.body.append(copies, hint('pilot.borderline_hint'));
    for (const item of result.items) {
      const details = el('details', { class: 'card rf-pilot-card' + (item.borderline ? ' rf-pilot-borderline' : '') },
        literal('summary', titles.get(item.zotero_key) || item.zotero_key),
        item.borderline ? chip('pilot.boundary_case') : null,
        tr('h4', 'pilot.your_decision'),
        tr('p', decisionKey(item.trainee_decision)), literal('p', item.trainee_reason),
        tr('h4', 'pilot.gold_badge'),
        tr('p', decisionKey(item.gold_decision)), literal('p', item.gold_reason));
      session.body.append(details);
    }
  }

  async function loadBoard(session, setId) {
    const result = await session.call('/sets/' + setId + '/results');
    if (!session.live) return;
    view(session, 'pilot.board');
    session.body.append(hint('pilot.board_summary', result.summary));
    for (const member of result.members) {
      const card = el('article', { class: 'card rf-pilot-card' }, literal('h4', member.username),
        member.cleared ? chip('pilot.cleared') : null,
        member.clearance_verdict ? chip(verdictKey(member.clearance_verdict)) : null,
        tr('h4', 'pilot.first_result'), attemptRow(member.first),
        tr('h4', 'pilot.latest_attempt'), attemptRow(member.latest),
        tr('h4', 'pilot.history'));
      for (const attempt of member.attempts) {
        card.append(attemptRow(attempt));
        if (attempt.status === 'submitted') card.append(metricRow(attempt), thresholds(attempt.thresholds));
      }
      session.body.append(card);
    }
  }

  document.addEventListener('reviewflow:task-context-changed', discover);
  document.addEventListener('reviewflow:language-changed', () => renderHost());
  window.PilotScreening = Object.freeze({ open, intercept });
  if (taskId()) discover();
})();
