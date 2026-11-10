/* Accessible task dialogs shared by transfer, deletion and retraction history. */
(function () {
  'use strict';

  const style = el('style', {text: `
    .rf-task-dialog{width:min(680px,calc(100% - 32px));max-height:calc(100dvh - 32px);
      margin:auto;overflow:auto;overscroll-behavior:contain;padding:clamp(16px,4vw,24px);
      border:1px solid var(--border);border-radius:16px;background:var(--surface);color:var(--ink);
      box-shadow:var(--shadow)}
    .rf-task-dialog::backdrop{background:rgb(20 34 36 / 35%)}
    .rf-task-dialog h2{margin:0 0 14px;color:var(--primary-dark);font-size:1.12rem}
    .rf-task-dialog h3{font-size:.94rem;line-height:1.4;overflow-wrap:anywhere}
    .rf-task-dialog .rf-task-content{display:grid;gap:12px;min-width:0}
    .rf-task-dialog .row{gap:8px;flex-wrap:wrap;margin:0}
    .rf-task-dialog .input{width:100%;min-width:0}
    .rf-task-dialog :is(p,li,dd,td,code,label){overflow-wrap:anywhere}
    .rf-task-dialog .rf-task-list{max-height:13rem;overflow:auto;padding-inline-start:1.25rem}
    .rf-task-dialog .rf-task-list li+li{margin-top:4px}
    .rf-task-dialog .rf-task-section:not([hidden]):not(:empty){display:grid;gap:6px;min-width:0;
      padding:12px;border:1px solid var(--border);border-radius:10px;
      background:color-mix(in srgb,var(--bg) 62%,var(--surface))}
    .rf-task-dialog .rf-task-section:empty{display:none}
    .rf-task-dialog .hint{margin:0;line-height:1.55}
    .rf-task-dialog [role=status]:empty{display:none}
    .rf-task-dialog [role=status]{white-space:pre-line}
    .rf-task-dialog>p[role=status]{margin-top:12px}
    .rf-task-dialog .rf-dialog-actions{justify-content:flex-end;margin-top:16px;
      padding-top:14px;border-top:1px solid var(--border)}
    .rf-task-dialog :is(.btn,.input){max-width:100%;overflow-wrap:anywhere}
    .rf-task-dialog progress:not([hidden]){display:block;width:100%;accent-color:var(--primary)}
    @media (max-width:640px){.rf-task-dialog .btn{min-height:40px}}
    @media (prefers-reduced-motion:no-preference){
      .rf-task-dialog[open]{animation:rf-task-dialog-in 140ms ease-out}
      @keyframes rf-task-dialog-in{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
    }
    @media (prefers-reduced-motion:reduce){.rf-task-dialog .btn{transition:none}.rf-task-dialog .btn:active{transform:none}}
  `});
  document.head.append(style);

  const text = (tag, key, params, attrs = {}) => el(tag, {...attrs, text:T(key, params || {})});
  const literal = (tag, value, attrs = {}) => el(tag, {...attrs, translate:'no', text:String(value ?? '')});
  const button = (key, onclick, attrs = {}) => text('button', key, null, {class:'btn b-out', type:'button', onclick, ...attrs});
  const context = () => [S.user?.user_id, S.task?.task_id].join('|');

  function open(id, title, {closableDuringWork = false, taskScoped = false, fallbackFocus = null} = {}) {
    if (document.getElementById(id)) return null;
    const trigger = document.activeElement;
    const auth = AUTH_TOKEN;
    const stamp = context();
    const heading = text('h2', title, null, {id:id + '-title'});
    const content = el('div', {class:'rf-task-content'});
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
      if (trigger?.isConnected && trigger !== document.body) trigger.focus();
      if (document.activeElement !== trigger || trigger === document.body) fallbackFocus?.focus();
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
