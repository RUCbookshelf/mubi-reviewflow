/* Task deletion uses a reversible default and a separate exact-ID confirmation. */
(function () {
  'use strict';

  const {text, literal, button, open} = window.RFTaskDialog;
  const prefix = 'tasks.delete_dialog.';
  const dialogId = 'rf-task-delete-dialog';
  document.getElementById('hubGrid')?.before(text('p', 'tasks.trash_retention_hint', null, {class:'hint'}));

  async function openDelete(task) {
    const auth = AUTH_TOKEN;
    const ui = open(dialogId, prefix + 'title');
    if (!ui) return;
    const active = () => ui.active && auth === AUTH_TOKEN;
    const options = el('fieldset', {'aria-labelledby':dialogId + '-title'});
    const trash = el('input', {type:'radio', name:'rf-delete-mode', value:'trash', checked:''});
    const purge = el('input', {type:'radio', name:'rf-delete-mode', value:'purge'});
    options.append(
      el('label', {}, trash, ' ', text('span', prefix + 'option_trash')),
      text('p', prefix + 'option_trash_hint', null, {class:'hint'}),
      el('label', {}, purge, ' ', text('span', prefix + 'option_purge'))
    );

    const input = el('input', {id:'rf-delete-confirm-id', type:'text', class:'input', spellcheck:'false',
      autocapitalize:'off', autocomplete:'off', placeholder:T(prefix + 'purge_confirm_placeholder'),
      'aria-describedby':'rf-delete-purge-warning'});
    const confirmation = el('div', {hidden:''},
      text('p', prefix + 'purge_warning', null, {id:'rf-delete-purge-warning'}),
      text('label', prefix + 'purge_confirm_label', {task_id:task.task_id}, {for:input.id}),
      literal('code', task.task_id, {class:'hint', style:'display:block;margin:4px 0'}),
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
