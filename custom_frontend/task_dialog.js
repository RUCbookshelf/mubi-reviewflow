/* Accessible task dialogs shared by transfer, deletion and retraction history. */
(function () {
  'use strict';

  const style = el('style', {text: `
    .rf-task-dialog{width:min(680px,calc(100% - 32px));max-height:calc(100dvh - 32px);
      overflow:auto;padding:20px;border:1px solid var(--border);border-radius:14px;
      background:var(--surface);color:var(--ink);box-shadow:0 12px 48px var(--shadow)}
    .rf-task-dialog::backdrop{background:rgba(0,0,0,.45)}
    .rf-task-dialog h2{margin:0 0 12px;font-size:1.12rem}
    .rf-task-dialog .row{gap:8px;flex-wrap:wrap}
    .rf-task-dialog .input{width:100%;min-width:0}
    .rf-task-dialog :is(p,li,dd,td,code){overflow-wrap:anywhere}
    .rf-task-dialog .rf-task-list{max-height:13rem;overflow:auto;padding-inline-start:24px}
    .rf-task-dialog [role=status]:empty{display:none}
    .rf-task-dialog [role=status]{white-space:pre-line}
    .rf-task-dialog .rf-dialog-actions{justify-content:flex-end;margin-top:16px;margin-bottom:0}
    .rf-task-dialog progress{display:block;width:100%;margin:8px 0}
    .rf-task-dialog fieldset{border:1px solid var(--border);border-radius:8px;margin:0;padding:10px}
    .rf-task-dialog fieldset>label{display:block}
  `});
  document.head.append(style);

  const text = (tag, key, params, attrs = {}) => el(tag, {...attrs, text:T(key, params || {})});
  const literal = (tag, value, attrs = {}) => el(tag, {...attrs, translate:'no', text:String(value ?? '')});
  const button = (key, onclick, attrs = {}) => text('button', key, null, {class:'btn b-out', type:'button', onclick, ...attrs});
  const context = () => [S.user?.user_id, S.task?.task_id].join('|');

  function open(id, title, {closableDuringWork = false, taskScoped = false} = {}) {
    if (document.getElementById(id)) return null;
    const trigger = document.activeElement;
    const auth = AUTH_TOKEN;
    const stamp = context();
    const heading = text('h2', title, null, {id:id + '-title'});
    const content = el('div');
    const status = el('p', {class:'hint', role:'status', 'aria-live':'polite'});
    const actions = el('div', {class:'row rf-dialog-actions'});
    const dialog = el('dialog', {class:'rf-task-dialog', id, 'aria-labelledby':heading.id},
      heading, content, status, actions);
    const close = button('关闭', () => dialog.close());
    let busy = false;
    actions.append(close);
    dialog.addEventListener('cancel', event => {
      if (busy && !closableDuringWork) event.preventDefault();
    });
    const invalidate = () => {
      if (AUTH_TOKEN !== auth || (taskScoped && stamp !== context())) dialog.close();
    };
    document.addEventListener('reviewflow:task-context-changed', invalidate);
    dialog.addEventListener('close', () => {
      document.removeEventListener('reviewflow:task-context-changed', invalidate);
      dialog.remove();
      if (trigger?.isConnected) trigger.focus();
    }, {once:true});
    document.body.append(dialog);
    dialog.showModal();
    return {
      dialog, content, status, actions, close,
      setBusy(value) {
        busy = value;
        close.disabled = value && !closableDuringWork;
      },
      get busy() { return busy; },
      get active() { return dialog.open && dialog.isConnected; }
    };
  }

  window.RFTaskDialog = Object.freeze({text, literal, button, context, open});
})();
