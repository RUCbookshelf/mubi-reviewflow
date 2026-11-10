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

  function renderDecisions(session, card) {
    if (session.excluding) {
      const input = node('textarea', null, {id:'rf-arbitration-reason', class:'input',
        rows:'3', required:'', disabled:session.busy ? '' : null});
      input.value = session.reason;
      const submit = button('3 排除', () => decide(session, 'exclude'), session.busy || !session.reason.trim(), true);
      const hint = node('p', null, {class:'hint', role:'status'});
      const update = () => {
        submit.disabled = session.busy || !session.reason.trim();
        hint.textContent = submit.disabled && !session.busy ? RFServerMessage('team.arbitration_reason_required') : '';
      };
      input.addEventListener('input', () => { session.reason = input.value; update(); });
      input.addEventListener('keydown', event => {
        if (event.isComposing) return;
        if (event.key === 'Enter' && !event.shiftKey) {
          event.preventDefault();
          if (!submit.disabled) decide(session, 'exclude');
        }
      });
      update();
      card.append(node('label', '仲裁理由（排除必填）', {for:input.id}), input, hint,
        el('div', {class:'row'}, submit, button('取消', () => closeReason(session), session.busy)));
    } else {
      const include = button('1 纳入', () => decide(session, 'include'), session.busy, true);
      include.id = 'rf-arbitration-include';
      card.append(el('div', {class:'row'}, include, button('3 排除', () => showReason(session), session.busy),
        session.editing ? button('取消', () => {session.editing = false; render(session);}, session.busy) : null));
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
      session.expanded = false;
      run(session, () => load(session));
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
    if (item) {
      const index = data.items.indexOf(item);
      const navigate = offset => {
        session.expanded = false;
        selectItem(session, data.items[index + offset].zotero_key);
      };
      session.body.append(el('div', {class:'row'},
        button('上一页', () => navigate(-1), session.busy || index === 0),
        literal('span', (index + 1) + ' / ' + total),
        button('下一页', () => navigate(1), session.busy || index + 1 === total)),
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
      stage:document.getElementById('mergeStage').value, userId:S.user?.user_id, busy:false};
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
          session.excluding || session.expanded || session.busy || session.data?.status !== 'open') return;
      const item = currentItem(session);
      if (!item || (item.status !== 'pending' && !session.editing)) return;
      if (event.key === '1') { event.preventDefault(); decide(session, 'include'); }
      if (event.key === '3') { event.preventDefault(); showReason(session); }
    });
    document.body.append(dialog);
    dialog.showModal();
    await run(session, () => load(session));
  });
  document.addEventListener('reviewflow:task-context-changed', () => {
    if (view && !isCurrent(view)) view.dialog.close();
    window.RFArbitrationReader.sync();
  });
  document.addEventListener('reviewflow:language-changed', () => { if (view) render(view); });
  window.RFArbitrationReader.sync();
})();
