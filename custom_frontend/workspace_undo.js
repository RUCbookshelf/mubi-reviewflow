/* Restore one saved operation, then reopen its article in the current workspace. */
(function () {
  'use strict';
  const stages = {screen:'screen', fulltext:'fulltext', bench:'coding'};
  const latest = {screen:null, fulltext:null, coding:null}, tickets = {screen:0, fulltext:0, coding:0};
  const buttons = [];
  let context = '', busy = false, saving = 0, codingBaseline = null;
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
  function sync() {
    for (const b of buttons) {
      b.textContent = T(busy ? '正在撤销…' : '撤销上次保存');
      b.title = T('恢复上次保存前的状态并返回该文献') + ' · ' + (mac ? 'Command+Z' : 'Ctrl+Z');
      b.disabled = !S.task || !S.user || !latest[b.dataset.undoStage] || busy || saving > 0 ||
        (b.dataset.undoStage === 'screen' && S.screenLoadedTask !== S.task?.task_id) ||
        (b.dataset.undoStage === 'fulltext' && S.fulltextLoadedTask !== S.task?.task_id);
    }
  }
  function reset() {
    if (context === scope()) return;
    context = scope(); codingBaseline = null; S.ftUndoSaved = null;
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
  function codingSignature() {
    return JSON.stringify($$('#cbForms .cb-v').map(c => [c.dataset.dim, c.value, c.type === 'checkbox' ? c.checked : null]));
  }
  function restoreReason(select, custom, reason) {
    if (!select) return;
    const known = Array.from(select.options).some(o => o.value === reason);
    select.value = known ? reason : (reason ? '__custom__' : '');
    if (custom) { custom.value = known ? '' : reason; custom.hidden = select.value !== '__custom__'; }
  }
  async function reopen(stage, result, current) {
    const key = result.zotero_key, restored = result.decision;
    if (stage === 'screen') {
      if (restored) S.decisions[key] = {decision:restored.decision, reason:restored.exclusion_reason, notes:restored.notes, tags:restored.tags};
      else delete S.decisions[key];
      const index = S.articles.findIndex(a => a.zotero_key === key);
      if (index < 0) throw new Error(T('该文献当前不在队列中，请重新载入页面。'));
      S.screenEditingKey = key; S.scrIdx = index; renderScreen();
      if (S.al.mode === 'ai') {
        await refreshALRank();
        if (current !== scope()) return;
        S.scrIdx = S.articles.findIndex(a => a.zotero_key === key); renderScreen();
      }
      restoreReason($('#excReason'), $('#excCustomReason'), restored?.exclusion_reason || '');
    } else if (stage === 'fulltext') {
      await loadFulltext();
      if (current !== scope()) return;
      const index = S.ftQueue.findIndex(a => a.zotero_key === key);
      if (index < 0) throw new Error(T('该文献当前不在队列中，请重新载入页面。'));
      S.ftUndoSaved = {key, decision:restored};
      S.ftIdx = index; renderFtDoc(); await openFtPdf();
      if (current === scope()) restoreReason($('#ftReason'), $('#ftCustomReason'), restored?.exclusion_reason || '');
    } else {
      const queue = await api(taskApi() + '/coding/queue' + scrQ());
      if (current !== scope()) return;
      S.cbQueue = queue.queue;
      const index = S.cbQueue.findIndex(a => a.zotero_key === key);
      if (index < 0) throw new Error(T('该文献当前不在队列中，请重新载入页面。'));
      S.cbIdx = index; renderBench();
    }
  }
  async function undo(stage) {
    if (busy || saving || apiPendingWrites || !S.task || !latest[stage]) return;
    if (stage === 'coding' && S.codingFormReady && codingBaseline !== null && codingBaseline !== codingSignature() &&
        !confirm(T('当前表单有未保存的修改。撤销会放弃这些修改并恢复上次保存前的状态，是否继续？'))) return;
    const entry = latest[stage], current = scope(), page = S.page;
    let committed = false;
    busy = true; sync();
    // Supersede a status request already in flight so it cannot restore an old entry ID.
    tickets[stage]++;
    if (stage === 'screen') { screenRequestVersion++; alRankVersion++; $('#scrCard').classList.remove('out'); }
    try {
      const result = await api(taskApi() + '/undo/' + stage + scrQ(), {method:'POST', body:{entry_id:entry.entry_id}});
      if (current !== scope()) return;
      committed = true;
      tickets[stage]++; latest[stage] = result.latest;
      if (S.page === page) await reopen(stage, result, current);
      if (current !== scope()) return;
      await refreshProgress();
      toast(T('已恢复上次保存前的状态'), 'inc');
    } catch (error) {
      if (current === scope()) {
        if (committed) {
          await refreshProgress();
          toast(T('已恢复上次保存前的状态') + ' · ' + error.message, 'warn');
        } else {
          toast(T('撤销失败：') + error.message, 'warn');
          if (error.status === 409) await refresh(stage);
        }
      }
    } finally { busy = false; reset(); sync(); }
  }
  window.WorkspaceUndo = {
    isBusy:()=>busy || saving > 0,
    beginSave:()=>{saving++; sync();},
    endSave:stage=>{saving = Math.max(0, saving - 1); refresh(stage);},
    sync, refresh
  };
  document.addEventListener('reviewflow:workspace-changed', ()=>refresh());
  document.addEventListener('reviewflow:analysis-task-changed', ()=>{reset();refresh();});
  document.addEventListener('reviewflow:task-context-changed', reset);
  document.addEventListener('reviewflow:language-changed', sync);
  document.addEventListener('reviewflow:coding-form-loaded', ()=>{codingBaseline=codingSignature();});
  document.addEventListener('reviewflow:coding-form-saved', event=>{
    if (S.cbQueue[S.cbIdx]?.zotero_key === event.detail) codingBaseline=codingSignature();
  });
  document.addEventListener('keydown', event=>{
    const stage = stages[S.page];
    if (!stage || !S.user || event.repeat || event.isComposing || event.altKey || event.shiftKey ||
        (event.code !== 'KeyZ' && event.key.toLowerCase() !== 'z') || !(mac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey)) return;
    if (event.target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"]')) return;
    if (buttons.find(b=>b.dataset.undoStage===stage)?.disabled || apiPendingWrites) return;
    event.preventDefault(); undo(stage);
  });
  reset(); refresh();
})();
