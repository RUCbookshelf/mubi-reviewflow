/* Blind arbitration keeps the merge-table workflow separate from the reading session. */
(function () {
  'use strict';

  const launch = document.getElementById('mergeSession');
  if (!launch) return;

  document.head.append(el('style', {text:[
    '#rf-arbitration-reader progress{display:block;width:100%;height:6px;margin:12px 0 5px;appearance:none;accent-color:var(--primary);border:0;border-radius:8px;background:var(--primary-light);overflow:hidden}',
    '#rf-arbitration-reader progress::-webkit-progress-bar{background:var(--primary-light)}',
    '#rf-arbitration-reader progress::-webkit-progress-value{background:var(--primary);border-radius:8px}',
    '#rf-arbitration-reader progress::-moz-progress-bar{background:var(--primary);border-radius:8px}',
    '#rf-arbitration-card{overflow-wrap:anywhere}',
    '#rf-arbitration-abstract-toggle{margin-block:4px}',
    '#rf-arbitration-reveal{margin:14px 0 0;padding:12px 0 0;border-top:1px solid var(--border)}',
  ].join('\n')}));

  const node = (tag, source, attrs = {}, ...children) =>
    el(tag, {...attrs, text: source == null ? null : T(source)}, children);
  const literal = (tag, value, attrs = {}) =>
    el(tag, {...attrs, 'data-rf-verbatim':'', text:String(value ?? '')});
  const button = (source, onclick, disabled = false, primary = false) =>
    node('button', source, {
      type:'button', class:'btn ' + (primary ? 'b-inc' : 'b-out'),
      disabled: disabled ? '' : null, onclick
    });

  let view = null;
  const isCurrent = session => view === session && session.taskId === S.task?.task_id
    && session.userId === S.user?.user_id;
  const paramsFor = (stage, key) => new URLSearchParams(
    key ? {stage, zotero_key:key} : {stage});
  const currentItem = session => session.data?.items.find(item => item.zotero_key === session.key);
  const errorText = error => RFServerMessage(error.messageKey, error.params) || error.message;
  const decisionLabel = decision => T(DEC_LABEL[decision] || '未决');
  const available = () => runtimeMode === 'server' && WorkflowControls.settings()?.task_kind === 'team';
  window.RFArbitrationReader = {
    sync() {
      launch.hidden = !available();
      launch.disabled = !S.task || mergeBusy;
    }
  };

  function clearPdf(session) {
    if (session.pdfUrl) URL.revokeObjectURL(session.pdfUrl);
    session.pdfUrl = null;
  }

  function report(session, error) {
    if (isCurrent(session) && error.name !== 'AbortError') session.error = error;
  }

  async function run(session, action) {
    if (!isCurrent(session) || session.busy) return;
    session.busy = true;
    session.error = null;
    render(session);
    try { await action(); }
    catch (error) { report(session, error); }
    finally {
      if (isCurrent(session)) {
        session.busy = false;
        render(session);
        if (session.dialog.open && !session.dialog.contains(document.activeElement)) session.heading.focus();
      }
    }
  }

  function request(session, suffix, options = {}) {
    return api(session.path + suffix, {...options, signal:session.controller.signal});
  }

  async function readReveal(session) {
    const item = currentItem(session);
    if (item?.status !== 'decided') return;
    const reveal = await request(session, '/reveal?' + paramsFor(session.stage, item.zotero_key));
    if (isCurrent(session) && reveal.zotero_key === session.key) session.reveal = reveal;
  }

  async function load(session, key = null) {
    session.reveal = null;
    clearPdf(session);
    const data = await request(session, '?' + paramsFor(session.stage));
    if (!isCurrent(session)) return;
    session.data = data;
    session.key = data.items.find(item => item.zotero_key === key)?.zotero_key ||
      data.items.find(item => item.status === 'pending')?.zotero_key || data.items[0]?.zotero_key || null;
    session.editing = false;
    session.excluding = false;
    session.reason = '';
    await readReveal(session);
  }

  function selectItem(session, key) {
    run(session, async () => {
      session.key = key;
      session.reveal = null;
      session.excluding = false;
      session.editing = false;
      session.reason = '';
      clearPdf(session);
      render(session);
      await readReveal(session);
    });
  }

  function decide(session, decision) {
    const item = currentItem(session);
    if (session.data?.status !== 'open' || !item || (item.status === 'decided' && !session.editing)) return;
    const reason = session.reason.trim();
    if (decision === 'exclude' && !reason) return;
    run(session, async () => {
      const body = {stage:session.stage, zotero_key:item.zotero_key, decision};
      if (decision === 'exclude') body.reason = reason;
      const result = await request(session, '/item', {method:'POST', body});
      if (!isCurrent(session)) return;
      session.data = result;
      session.reveal = result.reveal;
      session.excluding = false;
      session.editing = false;
      session.reason = '';
    });
  }

  function showReason(session) {
    if (session.busy) return;
    session.excluding = true;
    render(session);
    session.dialog.querySelector('#rf-arbitration-reason')?.focus();
  }

  function closeReason(session) {
    session.excluding = false;
    session.reason = '';
    render(session);
    session.dialog.querySelector('#rf-arbitration-include')?.focus();
  }

  /* ---- 常用理由库（与初筛/复筛 F24 同款；stage1→screen、stage2→fulltext 共用同一库） ---- */
  const SAVE_REASON_VALUE = '__save_reason__';
  const libraryStage = stage => stage === 'stage2' ? 'fulltext' : 'screen';

  async function loadReasons(session) {
    try {
      const data = await api(session.taskPath + '/reason-library?stage=' + libraryStage(session.stage),
        {signal:session.controller.signal});
      if (!isCurrent(session)) return;
      session.reasons = data.entries || [];
      session.canManage = data.can_manage === true;
      if (session.excluding) render(session); /* 纯文本退回形态升级为下拉形态 */
    } catch (error) { /* 理由库暂不可用：保持纯文本退回，不阻断裁决。 */ }
  }

  function saveReasonToLibrary(session, text) {
    const reason = String(text || '').trim();
    if (!reason) { toast(T('请先选择或输入要保存的理由'), 'warn'); return; }
    const stage = libraryStage(session.stage), task = session.taskId;
    api(session.taskPath + '/reason-library?stage=' + stage, {method:'POST', body:{text:reason, stage}})
      .then(async created => {
        await loadReasons(session);
        const undoId = created?.id;
        if (!undoId) { toast(T('已存为常用理由，可在「筛选设置 · 可复用排除理由」中管理'), 'inc'); return; }
        undoToast(T('已存为常用理由：{0}', reason), () => run(session, async () => {
          if (task !== S.task?.task_id) throw Error(T('The task changed. Reload the queue.'));
          await api(session.taskPath + '/reason-library/' + encodeURIComponent(undoId) + '?stage=' + stage, {method:'DELETE'});
          await loadReasons(session);
          toast(T('已撤销保存该常用理由'), 'info');
        }));
      })
      .catch(error => toast(T('保存失败：') + errorText(error), 'warn'));
  }

  /* toast 的可撤销版本：与主应用 #toasts 同款样式（workflow_controls.js 同源精简版）。 */
  function undoToast(message, undo) {
    const host = document.getElementById('toasts');
    if (!host) { toast(message, 'inc'); return; }
    const box = el('div', {class:'toast t-inc', role:'status'}, el('span', {text:message}));
    const action = el('button', {type:'button', class:'rf-link', text:T('撤销'), onclick:() => {
      action.disabled = true; box.remove();
      try { Promise.resolve(undo()).catch(error => toast(error.message, 'warn')); }
      catch (error) { toast(error.message, 'warn'); }
    }});
    box.append(action); host.append(box);
    setTimeout(() => box.classList.add('hide'), 6000);
    setTimeout(() => box.remove(), 6360);
  }

  function renderDecisions(session, card) {
    if (session.excluding) {
      const submit = button('3 排除', () => decide(session, 'exclude'), session.busy || !session.reason.trim(), true);
      const hint = node('p', null, {class:'hint', role:'status'});
      const update = () => {
        submit.disabled = session.busy || !session.reason.trim();
        hint.textContent = submit.disabled && !session.busy ? RFServerMessage('team.arbitration_reason_required') : '';
      };
      const label = node('label', '仲裁理由（排除必填）', {for:'rf-arbitration-reason'});
      const row = el('div', {class:'row'}, submit, button('取消', () => closeReason(session), session.busy));
      const submitByKey = event => {
        if (event.isComposing) return;
        if (event.key === 'Enter' && !event.shiftKey) {
          event.preventDefault();
          if (!submit.disabled) decide(session, 'exclude');
        }
      };
      const library = Array.isArray(session.reasons) ? session.reasons : null;
      if (library) {
        /* 与初筛/复筛同款（F24）：常用理由下拉 + 自定义输入 +「＋存为常用」动作项。 */
        const select = el('select', {id:'rf-arbitration-reason', class:'input',
          'aria-label':T('排除理由（必选）'), disabled:session.busy ? '' : null},
          el('option', {value:'', text:T('— 排除理由（必选）—')}));
        for (const entry of library)
          select.append(el('option', {value:entry.text, text:entry.text, translate:'no'}));
        select.append(el('option', {value:'__custom__', text:T('其他（自定义）')}));
        if (session.canManage)
          select.append(el('option', {value:SAVE_REASON_VALUE, text:T('＋ 将此理由存为常用')}));
        const custom = node('textarea', null, {id:'rf-arbitration-custom', class:'input',
          rows:'2', maxlength:'2000', placeholder:T('请输入自定义排除理由'), hidden:''});
        const known = () => library.some(entry => entry.text === session.reason);
        select.value = session.reason ? (known() ? session.reason : '__custom__') : '';
        if (select.value === '__custom__') { custom.value = session.reason; custom.hidden = false; }
        select.addEventListener('change', () => {
          const picked = select.value;
          if (picked === SAVE_REASON_VALUE) {
            /* 动作项不是理由取值：还原先前选择，把当前理由写入常用库。 */
            select.value = session.reason ? (known() ? session.reason : '__custom__') : '';
            if (select.value === '__custom__') custom.hidden = false;
            saveReasonToLibrary(session, session.reason);
            return;
          }
          custom.hidden = picked !== '__custom__';
          if (picked === '__custom__') custom.focus();
          session.reason = picked === '__custom__' ? custom.value : picked;
          update();
        });
        select.addEventListener('keydown', submitByKey);
        custom.addEventListener('input', () => { session.reason = custom.value; update(); });
        custom.addEventListener('keydown', submitByKey);
        card.append(label, select, custom, hint, row);
      } else {
        /* 理由库尚未载入或不可用：退回纯文本输入，不阻断裁决。 */
        const input = node('textarea', null, {id:'rf-arbitration-reason', class:'input',
          rows:'3', required:'', disabled:session.busy ? '' : null});
        input.value = session.reason;
        input.addEventListener('input', () => { session.reason = input.value; update(); });
        input.addEventListener('keydown', submitByKey);
        card.append(label, input, hint, row);
      }
      update();
    } else {
      const include = button('1 纳入', () => decide(session, 'include'), session.busy, true);
      include.id = 'rf-arbitration-include';
      card.append(el('div', {class:'row'}, include, button('3 排除', () => showReason(session), session.busy),
        session.editing ? button('取消', () => {session.editing = false; render(session);}, session.busy) : null),
        node('p', '← / → 切换文献，1 纳入，3 排除', {class:'hint'}));
    }
  }

  function renderReveal(session, item, card) {
    const reveal = session.reveal;
    if (item.status !== 'decided' || reveal?.zotero_key !== item.zotero_key || session.editing) return;
    const panel = el('section', {id:'rf-arbitration-reveal'},
      node('h3', '原筛选决定'),
      literal('p', [decisionLabel(reveal.decision), reveal.reason, reveal.decided_at].filter(Boolean).join(' · ')),
      el('ul', {}, (reveal.screeners || []).map(screener =>
        literal('li', [screener.name, decisionLabel(screener.decision), screener.reason].filter(Boolean).join(' · ')))));
    if (session.data.status === 'open')
      panel.append(button('改判', () => {
        session.editing = true;
        render(session);
        session.dialog.querySelector('#rf-arbitration-include')?.focus();
      }, session.busy));
    card.append(panel);
  }

  function renderItem(session, item) {
    const meta = [item.journal, item.year, item.doi].filter(Boolean).join(' · ');
    const card = el('article', {class:'card', id:'rf-arbitration-card'},
      literal('h3', item.title || T('（无标题）')),
      literal('p', meta, {class:'hint'}));
    if (item.abstract) {
      const abstract = literal('p', item.abstract, {
        style:session.expanded ? 'white-space:pre-wrap' :
          'white-space:pre-wrap;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden'
      });
      const toggle = button(session.expanded ? '收起裁决摘要' : '展开裁决摘要', () => {
        session.expanded = !session.expanded;
        render(session);
        session.dialog.querySelector('#rf-arbitration-abstract-toggle')?.focus();
      });
      toggle.id = 'rf-arbitration-abstract-toggle';
      toggle.classList.add('sm');
      toggle.setAttribute('aria-expanded', String(session.expanded));
      card.append(abstract, toggle);
    }
    if (session.stage === 'stage2') {
      const showPdf = button('打开裁决全文', () => run(session, async () => {
        const blob = await api(session.taskPath + '/fulltext/' + encodeURIComponent(item.zotero_key),
          {blob:true, signal:session.controller.signal});
        if (!isCurrent(session)) return;
        clearPdf(session);
        session.pdfUrl = URL.createObjectURL(new Blob([blob], {type:'application/pdf'}));
      }), session.busy || item.fulltext_available !== true);
      card.append(showPdf);
      if (item.fulltext_available !== true) card.append(node('p', '该文献尚未上传全文。', {class:'hint'}));
      if (session.pdfUrl) card.append(el('iframe', {src:session.pdfUrl, title:T('打开裁决全文'),
        style:'width:100%;height:55vh;border:0;margin-top:8px'}));
    }
    if (session.data.status === 'open' && (item.status === 'pending' || session.editing)) renderDecisions(session, card);
    renderReveal(session, item, card);
    return card;
  }

  function finish(session) {
    run(session, async () => {
      try {
        const data = await request(session, '/finish', {method:'POST', body:{stage:session.stage}});
        if (isCurrent(session)) session.data = data;
      } catch (error) {
        if (error.messageKey !== 'team.arbitration_session_incomplete') throw error;
        await load(session);
        session.error = error;
      }
    });
  }

  function viewRecord(session) {
    run(session, async () => {
      const record = await api(session.taskPath + '/screening-arbitrations/' +
        encodeURIComponent(session.data.snapshot_id), {signal:session.controller.signal});
      if (!isCurrent(session)) return;
      const stage = document.getElementById('mergeStage');
      if (stage.value !== session.stage) {
        stage.value = session.stage;
        stage.dispatchEvent(new Event('change'));
      }
      S.savedArbitration = record;
      const remaining = record.records.filter(row => row.arbitration_status === 'unresolved').length;
      document.getElementById('mergeSaveStatus').textContent =
        T('最近记录 #{0}：{1} 条，{2} 条待仲裁。', record.id, record.records.length, remaining);
      document.getElementById('mergeDownload').hidden = false;
      session.dialog.close();
      document.getElementById('mergeDownload').focus();
    });
  }

  function render(session) {
    session.heading.textContent = T('盲裁决阅读卡');
    session.closeButton.textContent = T('关闭');
    session.body.replaceChildren();
    session.body.setAttribute('aria-busy', String(session.busy));
    const stage = el('select', {class:'input', 'aria-label':T('筛选阶段'),
      disabled:session.busy ? '' : null},
      node('option', '文献初筛', {value:'stage1'}), node('option', '文献复筛', {value:'stage2'}));
    stage.value = session.stage;
    stage.addEventListener('change', () => {
      session.stage = stage.value;
      session.data = null;
      session.reasons = null;
      session.canManage = false;
      session.expanded = false;
      run(session, () => load(session));
      loadReasons(session);
    });
    session.body.append(stage);
    if (session.error) {
      session.body.append(literal('p', errorText(session.error), {role:'alert', class:'mp-error'}),
        button('重新载入裁决会话', () => run(session, () => load(session, session.key))));
    }
    const data = session.data;
    if (!data) {
      if (session.busy) session.body.append(node('p', 'Loading current saved work…', {role:'status'}));
      return;
    }
    const {decided, total, remaining} = data.summary;
    session.body.append(el('progress', {max:Math.max(total, 1), value:decided,
      'aria-label':T('裁决进度 {0} / {1} · 剩余 {2}', decided, total, remaining)}),
      el('p', {class:'hint', role:'status', text:T('裁决进度 {0} / {1} · 剩余 {2}', decided, total, remaining)}));
    const item = currentItem(session);
    session.navigate = null;
    if (item) {
      const index = data.items.indexOf(item);
      session.navigate = offset => {
        const target = data.items[index + offset];
        if (!target) return;
        session.expanded = false;
        selectItem(session, target.zotero_key);
      };
      session.body.append(el('div', {class:'row'},
        button('上一页', () => session.navigate(-1), session.busy || index === 0),
        literal('span', (index + 1) + ' / ' + total),
        button('下一页', () => session.navigate(1), session.busy || index + 1 === total)),
        renderItem(session, item));
    }
    if (data.status === 'completed')
      session.body.append(el('p', {role:'status', text:T('仲裁已归档 · 记录 #{0}', data.snapshot_id)}),
        button('查看或导出仲裁记录', () => viewRecord(session), session.busy));
    else session.body.append(button('完成并归档仲裁', () => finish(session), session.busy || remaining > 0, true));
  }

  launch.addEventListener('click', async () => {
    if (!available() || !S.task || mergeBusy || view || !await WorkflowControls.guard()) return;
    if (!available() || !S.task || view) return;
    const taskPath = taskApi(), controller = new AbortController();
    const heading = el('h2', {id:'rf-arbitration-title', tabindex:'-1'});
    const body = el('div', {class:'mp-dialog-body'});
    const closeButton = button('关闭', () => dialog.close());
    const dialog = el('dialog', {id:'rf-arbitration-reader', class:'mp-dialog', 'aria-labelledby':heading.id},
      el('header', {}, heading, closeButton), body);
    const session = view = {dialog, body, heading, closeButton, controller, taskPath,
      path:taskPath + '/screening-arbitrations/session', taskId:S.task.task_id,
      stage:document.getElementById('mergeStage').value, userId:S.user?.user_id, busy:false,
      reasons:null, canManage:false, navigate:null};
    dialog.addEventListener('close', () => {
      controller.abort();
      clearPdf(session);
      dialog.remove();
      if (view === session) view = null;
    });
    dialog.addEventListener('cancel', event => {
      if (session.excluding) {
        event.preventDefault();
        if (!session.busy) closeReason(session);
      }
    });
    dialog.addEventListener('keydown', event => {
      if (event.defaultPrevented || event.isComposing || event.repeat ||
          event.ctrlKey || event.altKey || event.metaKey || event.shiftKey ||
          event.target.closest('input,textarea,select,[contenteditable]') ||
          session.excluding || session.busy) return;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        /* 摘要展开（expanded）不再禁用键盘：翻页与决策照常（与初筛卡对齐）。 */
        event.preventDefault();
        session.navigate?.(event.key === 'ArrowRight' ? 1 : -1);
        return;
      }
      if (session.data?.status !== 'open') return;
      const item = currentItem(session);
      if (!item || (item.status !== 'pending' && !session.editing)) return;
      if (event.key === '1') { event.preventDefault(); decide(session, 'include'); }
      if (event.key === '3') { event.preventDefault(); showReason(session); }
    });
    document.body.append(dialog);
    dialog.showModal();
    loadReasons(session);
    await run(session, () => load(session));
  });
  document.addEventListener('reviewflow:task-context-changed', () => {
    if (view && !isCurrent(view)) view.dialog.close();
    window.RFArbitrationReader.sync();
  });
  document.addEventListener('reviewflow:language-changed', () => { if (view) render(view); });
  window.RFArbitrationReader.sync();
})();
