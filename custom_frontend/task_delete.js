/* Task deletion uses a reversible default and a separate exact-ID confirmation. */
(function () {
  'use strict';

  const {text, literal, button, open} = window.RFTaskDialog;
  const prefix = 'tasks.delete_dialog.';
  const dialogId = 'rf-task-delete-dialog';
  document.getElementById('hubGrid')?.before(text('p', 'tasks.trash_retention_hint', null, {class:'hint'}));

  document.head.append(el('style', {text: `
    #rf-task-delete-dialog{max-width:480px}
    .rf-task-delete-options{min-inline-size:0;border:0}
    .rf-task-delete-choice{display:grid;grid-template-columns:auto minmax(0,1fr);align-items:start;
      gap:10px;margin-bottom:6px;padding:10px 12px;border:1px solid var(--border);border-radius:9px;
      cursor:pointer;line-height:1.4}
    .rf-task-delete-choice:has(:checked){background:var(--primary-light)}
    .rf-task-delete-choice input{margin-top:3px;accent-color:var(--primary)}
    .rf-task-delete-choice .hint{display:block;margin-top:3px}
    .rf-task-delete-confirmation:not([hidden]){display:grid;gap:8px;padding-top:2px}
    #rf-delete-purge-warning{line-height:1.5;font-weight:600}
  `}));

  async function openDelete(task) {
    const auth = AUTH_TOKEN;
    const ui = open(dialogId, prefix + 'title', {fallbackFocus:document.getElementById('wf-tab-tasks')});
    if (!ui) return;
    const active = () => ui.active && auth === AUTH_TOKEN;
    const options = el('fieldset', {class:'rf-task-delete-options', 'aria-labelledby':dialogId + '-title'});
    const trash = el('input', {type:'radio', name:'rf-delete-mode', value:'trash', checked:''});
    const purge = el('input', {type:'radio', name:'rf-delete-mode', value:'purge'});
    options.append(
      el('label', {class:'rf-task-delete-choice'}, trash, el('span', {},
        text('span', prefix + 'option_trash'), text('span', prefix + 'option_trash_hint', null, {class:'hint'}))),
      el('label', {class:'rf-task-delete-choice'}, purge, text('span', prefix + 'option_purge'))
    );

    const input = el('input', {id:'rf-delete-confirm-id', type:'text', class:'input', spellcheck:'false',
      autocapitalize:'off', autocomplete:'off', placeholder:T(prefix + 'purge_confirm_placeholder'),
      'aria-describedby':'rf-delete-purge-warning'});
    const confirmation = el('div', {class:'rf-task-delete-confirmation', hidden:''},
      el('p', {id:'rf-delete-purge-warning', class:'rf-sensitive-warning'}, rfWarningIcon(),
        text('span', prefix + 'purge_warning')),
      text('label', prefix + 'purge_confirm_label', {task_id:task.task_id}, {for:input.id}),
      input);
    const submit = button('删除', remove, {class:'btn b-exc'});
    ui.actions.append(submit);
    ui.content.append(literal('p', task.name), options, confirmation);
    ui.close.focus();

    function sync() {
      trash.disabled = purge.disabled = ui.busy;
      confirmation.hidden = !purge.checked;
      input.disabled = ui.busy || !purge.checked;
      submit.disabled = ui.busy || (purge.checked && input.value !== task.task_id);
    }
    for (const radio of [trash,purge]) radio.addEventListener('change', () => {
      input.value = '';
      ui.status.textContent = '';
      sync();
      if (purge.checked) input.focus();
    });
    input.addEventListener('input', sync);
    input.addEventListener('paste', event => event.preventDefault());
    input.addEventListener('drop', event => event.preventDefault());
    sync();

    async function refreshHub() {
      await loadTasks();
      if (auth !== AUTH_TOKEN) return;
      renderHub();
      await refreshProgress();
    }

    async function remove() {
      if (!active() || submit.disabled) return;
      if (S.task?.task_id === task.task_id && !await WorkflowControls.guard()) return;
      if (!active() || submit.disabled) return;
      const permanent = purge.checked;
      const query = permanent ? '?' + new URLSearchParams({purge:'true', confirm:input.value}) : '';
      ui.setBusy(true);
      sync();
      ui.status.textContent = T('taskPackage.working');
      let deleted = false;
      try {
        await api('/api/tasks/' + encodeURIComponent(task.task_id) + query, {method:'DELETE'});
        if (!active()) return;
        deleted = true;
        await refreshHub();
        if (!active()) return;
        ui.dialog.close();
        toast(T(permanent ? 'tasks.purged' : 'tasks.trashed'), 'exc');
      } catch (error) {
        if (!active()) return;
        if (deleted) {
          ui.dialog.close();
          toast(T(permanent ? 'tasks.purged' : 'tasks.trashed'), 'exc');
          toast(error.message, 'warn');
        } else {
          ui.status.textContent = error.message;
          if (error.messageKey === 'tasks.purge_partial') {
            try { await refreshHub(); } catch (_) { /* Keep the cleanup details visible. */ }
            if (active()) {
              ui.setBusy(false);
              trash.disabled = purge.disabled = input.disabled = submit.disabled = true;
            }
            return;
          }
        }
      } finally {
        if (active() && ui.busy) {
          ui.setBusy(false);
          sync();
        }
      }
    }
  }

  window.RFTaskDelete = Object.freeze({open:openDelete});
})();
