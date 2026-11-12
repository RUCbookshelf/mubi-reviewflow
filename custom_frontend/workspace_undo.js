/* Restore one saved operation, then reopen its article in the current workspace. */
(function () {
  'use strict';
  const stages = {screen:'screen', fulltext:'fulltext', bench:'coding'};
  const latest = {screen:null, fulltext:null, coding:null}, tickets = {screen:0, fulltext:0, coding:0};
  const buttons = [];
  let context = '', busy = false, saving = 0;
  const scope = () => [S.task?.task_id, S.screener, S.user?.user_id].join('|');
  const mac = /Mac|iPhone|iPad/.test(navigator.platform);
  function addButton(stage, host, before) {
    if (!host) return;
    const b = el('button', {type:'button', class:'btn b-out sm', disabled:'', onclick:()=>undo(stage)});
    b.dataset.undoStage = stage;
    host.insertBefore(b, before || null); buttons.push(b);
  }
  const screenTools = document.querySelector('.rf-screen-tools');
  addButton('screen', screenTools || $('#scrPos').parentElement, screenTools?.firstElementChild);
  const focusStrip = document.querySelector('.rf-focus-strip');
  addButton('screen', focusStrip, focusStrip?.lastElementChild);
  addButton('fulltext', $('#ftPrevDoc')?.parentElement, $('#ftPrevDoc')?.nextSibling);
  addButton('coding', $('#cbSave')?.parentElement, $('#cbSave'));
  /* F25（r10-u1）：批准单「Ctrl+Z 在撤销按钮旁给可见快捷键提示（现仅 title
     悬停可见）」。快捷键此前只出现在 title 里——触屏与从不悬停的用户完全看不到。
     提示作为按钮子节点，随按钮一起被移动端布局（mobile_concept.js 按
     [data-undo-stage] 搬迁）带走，不会脱队；≤760px 与粗指针设备由
     screen_reader.css 隐藏（那里没有物理键盘）。
     注意 sync() 必须只重写文本节点，不能用 textContent 覆盖——那会把 <kbd> 冲掉。 */
  const shortcut = mac ? '⌘Z' : 'Ctrl+Z';
  function paint(b, label) {
    b.replaceChildren(label, el('kbd', {class:'rf-undo-kbd', 'aria-hidden':'true', text:shortcut}));
  }
  function sync() {
    for (const b of buttons) {
      paint(b, T(busy ? '正在撤销…' : '撤销上次保存'));
      b.title = T('恢复上次保存前的状态并返回该文献') + ' · ' + shortcut;
      b.setAttribute('aria-keyshortcuts', mac ? 'Meta+Z' : 'Control+Z');
      b.disabled = !S.task || !S.user || !latest[b.dataset.undoStage] || busy || saving > 0 ||
        window.WorkflowControls?.settings()?.stages?.[b.dataset.undoStage]?.mode === 'paused' ||
        (b.dataset.undoStage === 'screen' && S.screenLoadedTask !== S.task?.task_id) ||
        (b.dataset.undoStage === 'fulltext' && S.fulltextLoadedTask !== S.task?.task_id);
    }
    window.WorkflowEvidence?.syncBusy();
  }
  function reset() {
    if (context === scope()) return;
    context = scope(); S.ftUndoSaved = null;
    for (const stage of Object.keys(latest)) { latest[stage] = null; tickets[stage]++; }
    sync();
  }
  async function refresh(stage = stages[S.page]) {
    reset(); sync();
    if (!stage || !S.task || !S.user) return;
    const current = scope(), ticket = ++tickets[stage];
    try {
      const result = await api(taskApi() + '/undo/' + stage + scrQ());
      if (current !== scope() || ticket !== tickets[stage]) return;
      latest[stage] = result.latest;
    } catch (_) {
      if (current === scope() && ticket === tickets[stage]) latest[stage] = null;
    }
    sync();
  }
  function restoreReason(select, custom, reason) {
    if (!select) return;
    const known = Array.from(select.options).some(o => o.value === reason);
    select.value = known ? reason : (reason ? '__custom__' : '');
    if (custom) { custom.value = known ? '' : reason; custom.hidden = select.value !== '__custom__'; }
  }
  async function reopen(stage, result) {
    return WorkflowControls.reopen(stage, result.zotero_key, result.access_context || 'personal', null, {guarded:true});
  }
  async function undo(stage, linkedEntryId = null) {
    if (busy || saving || apiPendingWrites || !S.task) return;
    const requestedScope = scope();
    if (window.WorkflowControls && !await WorkflowControls.guard()) return;
    if (requestedScope !== scope()) return;
    await refresh(stage);
    if (busy || saving || requestedScope !== scope()) return;
    if (!latest[stage] || (linkedEntryId != null && String(latest[stage].entry_id) !== String(linkedEntryId))) { toast(T('撤销历史已变化，请重新载入后再试。'),'warn'); return; }
    const entry = latest[stage], current = scope(), page = S.page;
    let committed = false;
    busy = true; sync();
    // Supersede a status request already in flight so it cannot restore an old entry ID.
    tickets[stage]++;
    if (stage === 'screen') { screenRequestVersion++; alRankVersion++; $('#scrCard').classList.remove('out'); }
    try {
      const result = await api(taskApi() + '/undo/' + stage + scrQ(), {method:'POST', body:{entry_id:entry.entry_id,expected_policy_version:window.WorkflowControls?.version()}});
      if (current !== scope()) return;
      committed = true;
      tickets[stage]++; latest[stage] = result.latest;
      if (S.page === page) await reopen(stage, result, current);
      if (current !== scope()) return;
      await refreshProgress();
      toast(T('已恢复上次保存前的状态'), 'inc');
    } catch (error) {
      if (current === scope()) {
        /* F62（r10-u1）：后端英文错误串此前被原样拼进中文界面（撤销边界等
           十语言词条其实早已注册却没接上）。两处拼接点统一过 T()——词典以
           中文源串与英文原串**双键**注册（见 interface_locale.js 的撤销错误
           注册块），因此任一界面语言都能命中译文；未注册的技术串原样透传。
           注意 error.message 在 errOf 已过一层 T()，重复过 T() 对已译文是
           幂等（字典里没有以译文为键的行）。 */
        if (committed) {
          await refreshProgress();
          toast(T('已恢复上次保存前的状态') + ' · ' + T(error.message), 'warn');
        } else {
          toast(T('撤销失败：') + T(error.message), 'warn');
          if (error.status === 409) { WorkflowControls.error(error); await refresh(stage); }
        }
      }
    } finally { busy = false; reset(); sync(); }
  }
  window.WorkspaceUndo = {
    isBusy:()=>busy || saving > 0,
    beginSave:()=>{saving++; sync();},
    endSave:stage=>{saving = Math.max(0, saving - 1); refresh(stage);},
    sync, refresh, restoreReason,
    undoEntry:(stage,entryId)=>undo(stage,entryId),
    reopenCurrent:(stage,key,access,field)=>WorkflowControls.reopen(stage,key,access,field)
  };
  document.addEventListener('reviewflow:workspace-changed', ()=>refresh());
  document.addEventListener('reviewflow:analysis-task-changed', ()=>{reset();refresh();});
  document.addEventListener('reviewflow:task-context-changed', reset);
  document.addEventListener('reviewflow:language-changed', sync);
  document.addEventListener('keydown', event=>{
    if(event.defaultPrevented || window.RFModalActive?.()) return;
    const stage = stages[S.page];
    if (!stage || !S.user || event.repeat || event.isComposing || event.altKey || event.shiftKey ||
        (event.code !== 'KeyZ' && (typeof event.key !== 'string' || event.key.toLowerCase() !== 'z')) || !(mac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey)) return;
    if (event.target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"]')) return;
    if (buttons.find(b=>b.dataset.undoStage===stage)?.disabled || apiPendingWrites) return;
    event.preventDefault(); undo(stage);
  });
  reset(); refresh();
})();
