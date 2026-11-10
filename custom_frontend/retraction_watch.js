/* W9 user-driven retraction checking, history and article warning badges. */
(function () {
  'use strict';

  const {text, literal, button, context, open} = window.RFTaskDialog;
  const key = name => 'retraction.' + name;
  const t = (name, params) => T(key(name), params || {});
  const n = (tag, name, params, attrs) => text(tag, key(name), params, attrs);
  const b = (name, fn, attrs) => button(key(name), fn, attrs);
  const hint = {class:'hint'};
  const scopes = ['stage2_include', 'stage1_include'];
  const disclosures = [
    'disclosure_not_found_is_not_none', 'disclosure_no_doi_unavailable',
    'disclosure_no_auto_removal', 'disclosure_crossref_coverage'
  ];
  const labels = new Map([
    ['retraction','finding_retraction'], ['partial_retraction','finding_partial_retraction'],
    ['withdrawal','finding_withdrawal'], ['removal','finding_removal'],
    ['expression_of_concern','finding_expression_of_concern'],
    ['correction','finding_correction'], ['erratum','finding_correction'], ['corrigendum','finding_correction']
  ]);
  const state = {context:'', badges:new Map(), loaded:false, badgeTicket:0, permissionTicket:0, canManage:false};
  const ftNotice = document.getElementById('ftScreeningNotice');
  const status = el('span', {...hint, role:'status'});
  const start = b('start_button', () => showPanel(), {id:'rf-retraction-start', hidden:''});
  const history = b('runs_title', () => showPanel(true), {id:'rf-retraction-history'});
  const tools = el('div', {id:'rf-retraction-tools', class:'row', hidden:''}, start, history, status);
  ftNotice?.after(tools);

  document.head.append(el('style', {text:`
    #rf-retraction-tools[hidden],#rf-retraction-start[hidden],.rf-retraction-slot[hidden]{display:none!important}
    #rf-retraction-tools .hint{margin:0}
    .rf-retraction-badge{--retraction-tone:var(--muted);display:inline-flex;max-width:100%;align-items:baseline;
      white-space:normal;overflow-wrap:anywhere;margin-inline:4px;gap:5px;border-radius:999px;padding:2px 8px;
      color:var(--retraction-tone);font-size:.75rem;line-height:1.4;
      border:1px solid color-mix(in srgb,var(--retraction-tone) 24%,var(--border));
      background:color-mix(in srgb,var(--retraction-tone) 8%,var(--surface))}
    .rf-retraction-badge[data-severity="3"]{--retraction-tone:color-mix(in srgb,var(--danger) 78%,var(--ink))}
    .rf-retraction-badge[data-severity="2"]{--retraction-tone:#85552E}
    [data-theme=dark] .rf-retraction-badge[data-severity="2"]{--retraction-tone:#EBBA88}
    a.rf-retraction-badge:hover{text-decoration:underline;text-underline-offset:2px}
    a.rf-retraction-badge:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
    #rf-retraction-dialog .rf-retraction-controls:not([hidden]){display:grid;gap:8px}
    #rf-retraction-dialog .rf-retraction-history{display:grid;gap:6px;padding-inline-start:0;list-style:none}
    #rf-retraction-dialog .rf-retraction-history li{display:flex;flex-wrap:wrap;gap:4px 8px;
      align-items:baseline;margin:0}
    #rf-retraction-dialog .rf-retraction-history li+li{padding-top:6px;border-top:1px solid var(--border)}
  `}));

  function findingType(type) {
    return t(labels.get(type) || 'finding_other', {type});
  }

  function noticeLink(value) {
    if (!value) return null;
    try {
      const url = new URL(value);
      return ['http:','https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch (_) { return null; }
  }

  function renderSlot(slot) {
    const item = state.badges.get(slot.dataset.retractionKey);
    slot.replaceChildren();
    slot.hidden = !item;
    if (!item) return;
    const title = (item.types || [item.type]).map(findingType);
    if (item.source_doi) title.push(t('finding_source', {source_doi:item.source_doi}));
    if (item.source_date) title.push(String(item.source_date));
    if (item.source_title) title.push(String(item.source_title));
    const attrs = {
      class:'rf-retraction-badge', title:title.join('\n'),
      'data-severity':item.severity === 3 ? '3' : item.severity === 2 ? '2' : '1'
    };
    const url = noticeLink(item.url);
    const badge = url ? el('a', {...attrs, href:url, target:'_blank', rel:'noopener noreferrer'}) : el('span', attrs);
    badge.append(el('span', {'aria-hidden':'true', text:'⚠\uFE0E'}), el('span', {text:findingType(item.type)}));
    slot.append(badge);
  }

  function badgeSlot(articleKey) {
    if (!articleKey) return null;
    const slot = el('span', {class:'rf-retraction-slot', 'data-retraction-key':articleKey, hidden:''});
    renderSlot(slot);
    return slot;
  }

  function mountBadge(anchor, articleKey) {
    if (!anchor) return;
    const existing = anchor.nextElementSibling;
    if (existing?.classList.contains('rf-retraction-slot')) existing.remove();
    const slot = badgeSlot(articleKey);
    if (slot) anchor.after(slot);
  }

  function renderBadges() {
    for (const slot of document.querySelectorAll('.rf-retraction-slot')) renderSlot(slot);
  }

  async function refreshBadges(force = false) {
    if (!S.task || !S.user || (!force && state.loaded)) return;
    const stamp = context();
    const ticket = ++state.badgeTicket;
    try {
      const response = await api(taskApi() + '/retraction-badge');
      if (ticket !== state.badgeTicket || stamp !== context()) return;
      state.badges = new Map(Object.entries(response.badges || {}));
      state.loaded = true;
      renderBadges();
    } catch (_) { /* Badge reads must not block screening. */ }
  }

  function renderPermissions(canManage) {
    state.canManage = canManage;
    start.hidden = !canManage;
  }

  async function refreshContext(event) {
    const stamp = context();
    if (stamp !== state.context) {
      state.context = stamp;
      state.loaded = false;
      state.badges.clear();
      renderBadges();
    }
    const ticket = ++state.permissionTicket;
    tools.hidden = !S.task;
    renderPermissions(false);
    status.textContent = '';
    if (!S.task || !S.user) return;
    if (['screen','fulltext','bench'].includes(S.page)) void refreshBadges(event?.type === 'reviewflow:workspace-changed');
    if (S.page !== 'fulltext') return;
    try {
      const data = await api(taskApi() + '/retraction-runs');
      if (ticket !== state.permissionTicket || stamp !== context() || S.page !== 'fulltext') return;
      renderPermissions(data.can_manage === true);
    } catch (error) {
      if (ticket === state.permissionTicket && stamp === context()) status.textContent = error.message;
    }
  }

  function findingsList(details) {
    if (!details.findings?.length) return n('p', 'empty_result', null, hint);
    return el('ul', {class:'rf-task-list'}, details.findings.map(finding => {
      const url = noticeLink(finding.url);
      const source = finding.source_title || finding.source_doi;
      return el('li', {}, literal('b', finding.zotero_key), ' · ',
        el('span', {text:findingType(finding.update_type)}),
        finding.doi ? literal('div', finding.doi) : null,
        finding.source_doi ? n('div', 'finding_source', {source_doi:finding.source_doi}) : null,
        finding.source_date ? literal('div', finding.source_date) : null,
        url ? literal('a', source || url, {href:url,target:'_blank',rel:'noopener noreferrer'}) :
          finding.source_title ? literal('div', finding.source_title) : null);
    }));
  }

  async function showPanel(historyOnly = false) {
    const scopeKey = context();
    if (!S.task || !S.user || (!historyOnly && !state.canManage)) return;
    if (!await WorkflowControls.guard() || scopeKey !== context()) return;
    const base = taskApi();
    const ui = open('rf-retraction-dialog', key('panel_title'), {closableDuringWork:true, taskScoped:true});
    if (!ui) return;
    const active = () => ui.active && scopeKey === context();
    const scopeSelect = el('select', {class:'input', id:'rf-retraction-scope', 'aria-label':t('scope_label')},
      scopes.map(value => n('option', 'scope_' + value, null, {value})));
    const controls = el('div', {class:'rf-retraction-controls', hidden:''}, n('label', 'scope_label', null, {for:scopeSelect.id}), scopeSelect);
    const results = el('div', {class:'rf-task-section', 'aria-live':'polite'});
    const runsBox = el('ul', {class:'rf-task-list rf-retraction-history'});
    const meter = el('progress', {'aria-label':t('panel_title'), value:'0', max:'1', hidden:''});
    const progress = el('p', {...hint, role:'status'});
    const actions = el('div', {class:'row'});
    const begin = b('start_button', () => drive({scope:scopeSelect.value}), {id:'rf-retraction-begin', class:'btn b-inc'});
    const resume = b('resume_button', () => {
      if (continuation) void drive(continuation);
    }, {id:'rf-retraction-resume', hidden:''});
    const next = b('continue_remaining', () => {
      if (remaining) void drive({scope:remaining.scope, offset:remaining.offset});
    }, {id:'rf-retraction-next', hidden:''});
    const reload = button('Retry / refresh', () => void loadRuns(true), {hidden:''});
    const historySection = el('section', {class:'rf-task-section'}, n('h3', 'runs_title'), runsBox);
    actions.append(begin, resume, next);
    controls.append(actions);
    ui.content.append(controls, meter, progress, results, historySection, reload,
      el('div', {class:'rf-task-section'}, disclosures.map(name => n('p', name, null, hint))));

    let continuation = null;
    let remaining = null;
    let canManage = false;
    let activeRunId = null;
    let detailTicket = 0;

    function syncControls() {
      controls.hidden = !canManage;
      scopeSelect.disabled = ui.busy || !!activeRunId || !!continuation;
      begin.disabled = ui.busy || !!activeRunId || !!continuation;
      resume.hidden = !continuation;
      resume.disabled = ui.busy;
      next.hidden = !remaining || !!activeRunId || !!continuation;
      next.disabled = ui.busy;
      reload.disabled = ui.busy;
      for (const entry of runsBox.querySelectorAll('button')) entry.disabled = ui.busy;
    }

    function showProgress(record, fromHistory = false) {
      const done = record.status === 'done';
      const processed = fromHistory ? record.checked_count : record.processed;
      const total = fromHistory ? record.total_count : record.total;
      meter.hidden = false;
      meter.max = Math.max(1, Number(total) || 0);
      meter.value = done && !total ? 1 : Number(processed) || 0;
      progress.replaceChildren(n('span', done ? 'check_done' : 'check_progress', {
        processed,total,found:record.found_count
      }), el('br'), n('span', 'unknown_notice', {count:record.unknown_count}));
      if (record.truncated) progress.append(el('br'), n('span', 'truncated_notice', {remaining:record.remaining_dois}));
      remaining = done && record.truncated && record.next_offset != null
        ? {scope:record.scope, offset:record.next_offset} : null;
      if (remaining) next.textContent = t('continue_remaining', {remaining:record.remaining_dois});
      syncControls();
    }

    async function loadDetail(id, {fromHistory = true} = {}) {
      const ticket = ++detailTicket;
      if (fromHistory) ui.status.textContent = T('taskPackage.working');
      try {
        const details = await api(base + '/retraction-runs/' + encodeURIComponent(id));
        if (!active() || ticket !== detailTicket) return;
        canManage = details.can_manage === true;
        showProgress(details.run, true);
        const actor = canManage && details.run.started_by
          ? n('p', 'started_by', {name:details.run.started_by}, hint) : null;
        results.replaceChildren(...[
          n('h3', 'run_heading', {id,scope:t('scope_' + details.run.scope)}),
          actor, findingsList(details)
        ].filter(Boolean));
        ui.status.textContent = '';
      } catch (error) {
        if (active() && ticket === detailTicket) ui.status.textContent = error.message;
      }
    }

    async function loadRuns(choose = false, preserveStatus = false) {
      if (!preserveStatus) ui.status.textContent = T('taskPackage.working');
      reload.hidden = true;
      try {
        const list = await api(base + '/retraction-runs');
        if (!active()) return false;
        canManage = list.can_manage === true;
        activeRunId = list.active_run_id;
        const activeRun = list.runs?.find(run => run.run_id === activeRunId);
        if (activeRun) scopeSelect.value = activeRun.scope;
        continuation = canManage && activeRunId
          ? {run_id:activeRunId, ...(activeRun ? {scope:activeRun.scope} : {})} : null;
        runsBox.replaceChildren(...(list.runs || []).map(run => el('li', {},
          b('run_entry', () => loadDetail(run.run_id), {class:'btn b-out sm'}),
          literal('span', run.started_at, hint))));
        for (const [index, run] of (list.runs || []).entries()) {
          const rowButton = runsBox.children[index]?.querySelector('button');
          rowButton.textContent = t('run_entry', {id:run.run_id, scope:t('scope_' + run.scope)});
        }
        if (!list.runs?.length) runsBox.append(n('li', 'no_runs', null, hint));
        if (!preserveStatus) ui.status.textContent = continuation ? t('resume_prompt') : '';
        syncControls();
        if (choose && list.runs?.length && (historyOnly || activeRunId)) {
          await loadDetail(activeRunId || list.runs[0].run_id);
          if (active() && continuation) ui.status.textContent = t('resume_prompt');
        }
        return true;
      } catch (error) {
        if (active()) {
          ui.status.textContent = error.message;
          reload.hidden = false;
        }
        return false;
      }
    }

    async function drive(body) {
      if (!active() || ui.busy || !canManage) return;
      const first = body;
      ++detailTicket;
      ui.setBusy(true);
      ui.status.textContent = T('taskPackage.working');
      results.replaceChildren();
      syncControls();
      let nextBody = body;
      let restored = false;
      try {
        while (active()) {
          let result;
          try {
            result = await api(base + '/retraction-check', {method:'POST',body:nextBody});
          } catch (error) {
            if (!active()) return;
            const params = error.params || {};
            if (error.messageKey === key('cursor_stale') && !restored) {
              restored = true;
              if (await loadRuns(false, true) && continuation) {
                nextBody = continuation;
                continue;
              }
            } else if (error.messageKey === key('run_in_progress')) {
              activeRunId = params.run_id;
              continuation = params.cursor ? {cursor:params.cursor,scope:params.scope} : {run_id:params.run_id};
              if (params.scope) scopeSelect.value = params.scope;
            } else if (error.messageKey === key('scope_mismatch')) {
              continuation = {...nextBody,scope:params.run_scope};
              if (params.run_scope) scopeSelect.value = params.run_scope;
            } else if (error.status === 0) {
              if (nextBody === first && !first.run_id && !first.cursor) await loadRuns(false, true);
              else continuation = nextBody;
            } else if (error.messageKey === key('cursor_invalid') || error.messageKey === key('run_not_found')) {
              await loadRuns(false, true);
            }
            if (active()) ui.status.textContent = error.message;
            return;
          }
          if (!active()) return;
          ui.status.textContent = '';
          activeRunId = result.done ? null : result.run_id;
          continuation = result.done ? null : {cursor:result.cursor, scope:result.scope};
          showProgress(result);
          if (result.done) {
            await loadRuns(false, true);
            if (!active()) return;
            await loadDetail(result.run_id, {fromHistory:false});
            if (active()) void refreshBadges(true);
            return;
          }
          nextBody = continuation;
        }
      } finally {
        if (active()) {
          ui.setBusy(false);
          syncControls();
        }
      }
    }

    syncControls();
    await loadRuns(true);
  }

  window.RFRetraction = Object.freeze({badgeSlot, mountBadge});
  document.addEventListener('reviewflow:task-context-changed', refreshContext);
  document.addEventListener('reviewflow:workspace-changed', refreshContext);
  document.addEventListener('reviewflow:language-changed', renderBadges);
  void refreshContext();
})();
