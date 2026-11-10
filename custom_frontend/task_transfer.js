/* W5 task package transfer and full-text archives. */
(function () {
  'use strict';

  const {text, literal, button, context, open} = window.RFTaskDialog;
  const prefix = 'taskPackage.';
  const dialogId = 'rf-task-transfer-dialog';
  const scopes = ['all', 'stage1_include', 'stage2_include'];
  const fmt = value => new Intl.NumberFormat(RFLang.get()).format(Number(value) || 0);
  const bytes = value => fmt(value) + ' B';
  const t = (key, params) => T(prefix + key, params || {});
  const copy = (tag, key, params, attrs) => text(tag, prefix + key, params, attrs);
  const entry = (key, action, id) => button(prefix + key, action, {id});
  const hint = {class:'hint'};
  const taskPath = id => '/api/tasks/' + encodeURIComponent(id);

  document.head.append(el('style', {text: `
    #rf-task-transfer-exports>.row{margin:0}
    #rf-task-transfer-exports>p:empty{display:none}
    #rf-task-transfer-exports>p{margin:8px 0 0}
    #rf-task-transfer-dialog .rf-task-transfer-field{display:grid;gap:6px}
    #rf-task-transfer-dialog .rf-task-transfer-check{display:flex;align-items:baseline;gap:8px;cursor:pointer}
    #rf-task-transfer-dialog input[type=checkbox]{accent-color:var(--primary)}
    #rf-task-transfer-dialog input[type=file]::file-selector-button{font:inherit;
      margin-inline-end:8px;padding:5px 8px;border:1px solid var(--border);border-radius:6px;
      color:var(--primary-dark);background:var(--primary-light);cursor:pointer}
  `}));

  const exportPage = document.querySelector('.page[data-p=export]');
  const taskPanel = document.getElementById('wf-panel-tasks') || document.querySelector('.page[data-p=home]');
  const taskGrid = document.getElementById('hubGrid');
  const importButton = entry('import.entry', openImport, 'rf-package-import');
  const archiveButton = entry('archive.entry', () => openExport(true), 'rf-archive-export');
  const packageButton = entry('package.entry', () => openExport(false), 'rf-package-export');
  const exportNotice = el('p', {...hint, role:'status'});
  const exportCard = el('div', {class:'card', id:'rf-task-transfer-exports', hidden:''},
    el('div', {class:'row'}, archiveButton, packageButton), exportNotice);
  exportPage?.append(exportCard);
  const importCard = el('div', {class:'row'}, importButton);
  if (taskGrid?.parentElement === taskPanel) taskGrid.before(importCard);
  else taskPanel?.append(importCard);

  function describeError(error) {
    if (error.messageKey === 'coding.collaboration_owner_unset') return t('ownerMigration');
    return error.message;
  }

  let permissionTicket = 0;
  async function refreshPermission() {
    const ticket = ++permissionTicket;
    exportCard.hidden = true;
    archiveButton.hidden = packageButton.hidden = true;
    exportNotice.textContent = '';
    if (!S.task || !S.user || S.page !== 'export') return;
    const scope = context();
    try {
      const policy = await api(taskApi() + '/workflow-settings');
      if (ticket !== permissionTicket || scope !== context() || S.page !== 'export') return;
      archiveButton.hidden = packageButton.hidden = policy.can_manage !== true;
      exportCard.hidden = policy.can_manage !== true;
    } catch (error) {
      if (ticket !== permissionTicket || scope !== context() || S.page !== 'export') return;
      exportNotice.textContent = describeError(error);
      exportCard.hidden = false;
    }
  }

  function memberList(members) {
    return el('ul', {class:'rf-task-list'},
      (members || []).map(member => el('li', {}, literal('b', member.path), ' · ',
        literal('span', bytes(member.size)))));
  }

  function downloadName(disposition, fallback) {
    const extended = disposition?.match(/filename\*\s*=\s*UTF-8''([^;]+)/i);
    let name;
    if (extended) {
      try { name = decodeURIComponent(extended[1].trim()); } catch (_) { /* Use the quoted filename. */ }
    }
    name ||= disposition?.match(/filename\s*=\s*(?:"([^"]*)"|([^;]+))/i)?.slice(1).find(Boolean)?.trim();
    return (name || fallback).replace(/[\\/\x00-\x1f\x7f]/g, '_');
  }

  async function download(response, fallback, active) {
    const blob = await response.blob();
    if (!active()) return;
    const url = URL.createObjectURL(blob);
    const link = el('a', {href:url, download:downloadName(response.headers.get('content-disposition'), fallback)});
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  function archiveSummary(headers) {
    const exported = Number(headers.get('x-archive-exported')) || 0;
    const skipped = Number(headers.get('x-archive-skipped')) || 0;
    const reasons = new URLSearchParams((headers.get('x-archive-skipped-reasons') || '').replace(/,/g, '&'));
    const details = [copy('p', 'archive.summary', {exported:fmt(exported), skipped:fmt(skipped)})];
    if (!exported && !skipped) details.push(copy('p', 'archive.empty'));
    const missing = Number(reasons.get('missing_file'));
    const unreadable = Number(reasons.get('unreadable'));
    if (missing) details.push(copy('p', 'archive.skippedMissing', {n:fmt(missing)}), copy('p', 'fulltext.recovery'));
    if (unreadable) details.push(copy('p', 'archive.skippedUnreadable', {n:fmt(unreadable)}));
    return details;
  }

  function packageSummary(headers) {
    return [copy('p', 'package.downloaded', {
      n:fmt(headers.get('x-package-members')),
      pdfs:fmt(headers.get('x-package-pdfs')),
      skipped:fmt(headers.get('x-package-pdfs-skipped')),
      bytes:bytes(headers.get('x-package-bytes'))
    })];
  }

  async function openExport(archive) {
    const scopeKey = context();
    if (!S.task || !S.user || !await WorkflowControls.guard() || scopeKey !== context()) return;
    const taskId = S.task.task_id;
    const ui = open(dialogId, prefix + (archive ? 'archive.entry' : 'package.entry'), {taskScoped:true});
    if (!ui) return;
    const active = () => ui.active && scopeKey === context();
    const scope = el('select', {id:'rf-archive-scope', class:'input', 'aria-label':t('archive.scope.label')},
      scopes.map(value => copy('option', 'archive.scope.' + value, null, {value})));
    const include = el('input', {type:'checkbox'});
    include.checked = true;
    const preflight = el('div', {class:'rf-task-section'});
    const summary = el('div', {class:'rf-task-section', role:'status'});
    const retryNoPdfs = entry('package.retryWithoutPdfs', () => {
      include.checked = false;
      void loadPreflight(true);
    }, 'rf-package-without-pdfs');
    retryNoPdfs.hidden = true;
    const action = button('导出', runExport, {class:'btn b-inc'});
    action.disabled = true;
    const retryPreflight = button('Retry / refresh', () => loadPreflight());
    retryPreflight.hidden = true;
    ui.actions.prepend(action, retryNoPdfs, retryPreflight);
    ui.content.append(literal('p', S.task.name),
      archive
        ? el('div', {class:'rf-task-transfer-field'}, el('label', {for:scope.id}, t('archive.scope.label')), scope,
          copy('p', 'archive.nameExample', {name:'AB12CD34-王-2022.pdf'}, hint))
        : el('label', {class:'rf-task-transfer-check'}, include, el('span', {text:t('package.includePdfs')})),
      preflight, summary);

    let limits = null;
    let mustExcludePdfs = false;
    function setBusy(value) {
      ui.setBusy(value);
      include.disabled = value;
      scope.disabled = value;
      retryNoPdfs.disabled = value;
      retryPreflight.disabled = value;
      action.disabled = value || !limits || (!archive && (limits.over_limit || mustExcludePdfs))
        || (archive && scope.value === 'all' && limits.archive_export?.over_limit === true);
    }
    scope.addEventListener('change', () => setBusy(false));
    include.addEventListener('change', () => loadPreflight());

    async function loadPreflight(exportAfter = false) {
      if (!active() || ui.busy) return;
      limits = null;
      mustExcludePdfs = false;
      setBusy(true);
      retryNoPdfs.hidden = retryPreflight.hidden = true;
      summary.replaceChildren();
      preflight.replaceChildren();
      ui.status.textContent = t('working');
      try {
        limits = await api(taskPath(taskId) + '/package/preflight?include_pdfs=' + String(include.checked));
        if (!active()) return;
        ui.status.textContent = '';
        if (archive) {
          const estimate = limits.archive_export;
          if (estimate) preflight.append(copy('p', 'package.fulltextBytes', {bytes:bytes(estimate.bytes)}, hint));
          if (estimate?.over_limit) preflight.append(copy('p', 'archive.overLimit', null, hint));
        } else {
          const counts = limits.fulltexts || {};
          preflight.append(copy('h3', 'package.preflight'),
            copy('p', 'package.members', {n:fmt(limits.members?.length)}, hint),
            copy('p', 'package.fulltexts', {n:fmt(counts.count), skipped:fmt(counts.skipped)}, hint),
            copy('p', 'package.fulltextBytes', {bytes:bytes(counts.bytes)}, hint),
            copy('p', 'package.totalBytes', {bytes:bytes(limits.total_bytes)}, hint),
            el('p', {...hint, text:T('筛选员：{0}', (limits.source_task?.screeners || []).join('、') || '—')}),
            memberList(limits.members));
          if (limits.over_limit) preflight.append(copy('p', include.checked ? 'package.tooLarge' : 'package.overLimit', null, hint));
          retryNoPdfs.hidden = limits.suggest_exclude_pdfs !== true;
        }
      } catch (error) {
        if (!active()) return;
        ui.status.textContent = describeError(error);
        retryPreflight.hidden = false;
      } finally {
        if (active()) setBusy(false);
      }
      if (exportAfter && active() && !action.disabled) await runExport();
    }

    async function runExport() {
      if (!active() || action.disabled) return;
      const includePdfs = include.checked;
      const path = archive ? '/fulltext/archive-export' : '/package/export';
      summary.replaceChildren();
      ui.status.textContent = t('working');
      setBusy(true);
      try {
        const response = await api(taskPath(taskId) + path, {
          method:'POST', body:archive ? {scope:scope.value} : {include_pdfs:includePdfs}, response:true
        });
        await download(response, archive ? 'fulltext_archive.zip' : 'task_package.zip', active);
        if (!active()) return;
        ui.status.textContent = '';
        summary.replaceChildren(...(archive ? archiveSummary(response.headers) : packageSummary(response.headers)));
      } catch (error) {
        if (!active()) return;
        ui.status.textContent = describeError(error);
        if (!archive && error.status === 413 && includePdfs) {
          mustExcludePdfs = true;
          retryNoPdfs.hidden = false;
          summary.replaceChildren(copy('p', 'package.tooLarge', null, hint));
        }
      } finally {
        if (active()) setBusy(false);
      }
    }

    await loadPreflight();
  }

  function slugList(mapping) {
    const pairs = Object.entries(mapping || {});
    if (!pairs.length) return null;
    return el('div', {}, copy('h3', 'import.slugMapTitle'),
      el('ul', {class:'rf-task-list'}, pairs.map(([source,target]) => copy('li', 'import.slugMap', {source,target}))));
  }

  async function openImport() {
    const auth = AUTH_TOKEN;
    if (!S.user || !await WorkflowControls.guard() || auth !== AUTH_TOKEN) return;
    const ui = open(dialogId, prefix + 'import.entry');
    if (!ui) return;
    const active = () => ui.active && auth === AUTH_TOKEN;
    const input = el('input', {type:'file', accept:'.zip,application/zip', class:'input',
      'aria-label':t('import.entry')});
    const previewBox = el('div', {class:'rf-task-section'});
    const previewButton = entry('import.preview', preview, 'rf-package-preview');
    const confirmButton = entry('import.confirm', confirm, 'rf-package-confirm');
    confirmButton.className = 'btn b-inc';
    ui.actions.prepend(previewButton, confirmButton);
    ui.content.append(input, previewBox);
    let checked = null;
    let completed = false;

    function setBusy(value) {
      ui.setBusy(value);
      input.disabled = value || completed;
      previewButton.disabled = value || completed || !input.files.length;
      confirmButton.hidden = !checked || completed;
      confirmButton.disabled = value;
    }
    setBusy(false);
    input.addEventListener('change', () => {
      checked = null;
      ui.status.textContent = '';
      previewBox.replaceChildren();
      setBusy(false);
    });

    async function preview() {
      const file = input.files[0];
      if (!active() || !file || ui.busy) return;
      checked = null;
      previewBox.replaceChildren();
      setBusy(true);
      ui.status.textContent = t('working');
      try {
        const form = new FormData();
        form.append('package', file);
        const data = await api('/api/tasks/import-package/preflight', {method:'POST', form});
        if (!active()) return;
        checked = {file, sha256:data.package_sha256};
        const parts = [
          copy('h3', 'import.sourceTask', {name:data.source_task?.display_name || data.source_task?.task_id || '—'}),
          copy('p', 'package.members', {n:fmt(data.members?.length)}, hint),
          copy('p', 'package.totalBytes', {bytes:bytes(data.total_bytes)}, hint),
          copy('p', 'package.fulltexts', {n:fmt(data.fulltexts?.count), skipped:fmt(data.fulltexts?.skipped)}, hint),
          el('p', {...hint, text:T('筛选员：{0}', (data.source_task?.screeners || []).join('、') || '—')}),
          memberList(data.members),
          slugList(data.slug_map)
        ];
        if (data.warnings?.includes('fulltext_files_missing')) parts.push(copy('p', 'import.skippedWarning', null, hint));
        previewBox.replaceChildren(...parts.filter(Boolean));
        ui.status.textContent = '';
      } catch (error) {
        if (active()) ui.status.textContent = describeError(error);
      } finally {
        if (active()) setBusy(false);
      }
    }

    async function confirm() {
      if (!active() || !checked || checked.file !== input.files[0] || ui.busy) return;
      setBusy(true);
      ui.status.textContent = t('working');
      const form = new FormData();
      form.append('package', checked.file);
      form.append('preview_sha256', checked.sha256);
      try {
        const result = await api('/api/tasks/import-package', {method:'POST', form});
        if (!active()) return;
        completed = true;
        ui.status.textContent = '';
        previewBox.replaceChildren(...[
          copy('p', 'import.done', {name:result.display_name}),
          copy('p', 'import.restored', {n:fmt(result.fulltexts_restored), m:fmt(result.fulltexts_skipped)}),
          result.fulltexts_neutralized ? copy('p', 'import.neutralized', {n:fmt(result.fulltexts_neutralized)}, hint) : null,
          slugList(result.member_slug_map)
        ].filter(Boolean));
        await loadTasks();
        if (!active()) return;
        await pickTask(result.task_id, {silent:true});
        if (active()) await go('home', {guarded:true});
        if (active()) document.getElementById('wf-tab-tasks')?.click();
      } catch (error) {
        if (active()) {
          ui.status.textContent = describeError(error);
          if (error.messageKey === 'task_package.package_preview_mismatch') checked = null;
        }
      } finally {
        if (active()) setBusy(false);
      }
    }
  }

  document.addEventListener('reviewflow:task-context-changed', refreshPermission);
  document.addEventListener('reviewflow:workspace-changed', refreshPermission);
  void refreshPermission();
})();
