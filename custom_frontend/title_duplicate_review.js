/* Manual title-duplicate review: title matches remain candidates until a user decides. */
(function () {
  'use strict';
  const pageSize = 20;
  let offset = 0;
  let loading = false;
  let currentTask = null;
  let undoAvailable = false;
  const undoButton = el('button', {class:'btn b-out sm', id:'titleDupUndo', disabled:'disabled', onclick:undo});
  $('#titleDupExport').before(undoButton);
  function undoLabel(){undoButton.textContent=T('撤销上次核验');undoButton.title=/Mac|iPhone|iPad/.test(navigator.platform)?'Command+Z':'Ctrl+Z';}
  undoLabel();
  document.addEventListener('reviewflow:language-changed',undoLabel);
  async function undo(){
    if(!S.task||loading||!undoAvailable)return;
    const taskId=S.task.task_id, screener=S.screener;
    loading=true;undoButton.disabled=true;
    try{
      const result=await api(taskApi()+'/title-duplicate-undo'+scrQ(),{method:'POST'});
      if(S.task?.task_id!==taskId||S.screener!==screener)return;
      const row=Array.from($('#reportBody').querySelectorAll('tr')).find(r=>r.firstElementChild?.textContent===T('进入筛选（非重复总数）'));
      if(row?.lastElementChild)row.lastElementChild.replaceChildren(el('b',{text:String(result.screening.total)}));
      offset=0;await refreshProgress();await load();toast(T('已撤销上次人工核验'),'inc');
    }catch(error){toast(T('撤销失败：')+error.message,'warn');}
    finally{loading=false;undoButton.disabled=!undoAvailable;}
  }
  document.addEventListener('keydown',event=>{
    const mac=/Mac|iPhone|iPad/.test(navigator.platform);
    if(S.page!=='import'||!S.user||loading||!undoAvailable||event.repeat||event.isComposing||event.altKey||event.shiftKey||event.key.toLowerCase()!=='z'||!(mac?event.metaKey&&!event.ctrlKey:event.ctrlKey&&!event.metaKey))return;
    if(event.target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"]'))return;
    event.preventDefault();undo();
  });

  function label(value) { return value === null || value === undefined || value === '' ? '—' : String(value); }

  function recordCard(member) {
    const source = member.imported_in_batch ? T('本次导入') : T('库内已有');
    const summary = [
      T('作者：{0}', label(member.authors)), T('年份：{0}', label(member.year)),
      T('期刊：{0}', label(member.journal)), T('卷：{0}', label(member.volume)),
      T('期：{0}', label(member.issue)), T('页码：{0}', label(member.pages)),
      T('DOI：{0}', label(member.doi)), T('来源：{0}', label(member.source_file))
    ].join(' · ');
    const details = el('details', { style: 'margin-top:6px' },
      el('summary', { text: T('查看原始记录') }),
      el('pre', { text: JSON.stringify(member.raw || {}, null, 2),
        style: 'white-space:pre-wrap;overflow-wrap:anywhere;max-height:280px;overflow:auto;font-size:.72rem' }));
    const card = el('div', { style: 'border:1px solid var(--border);border-radius:9px;padding:10px;margin:8px 0' },
      el('div', { style: 'display:flex;gap:8px;align-items:flex-start' },
        el('b', { text: member.title || T('（无标题）') }),
        el('span', { class: 'chip', text: source })),
      el('div', { class: 'hint', text: summary, style: 'margin:6px 0 0;overflow-wrap:anywhere' }),
      details);
    return card;
  }

  async function resolve(reviewId, decision, keeperKey) {
    if (!S.task || loading) return;
    const taskId = S.task.task_id;
    loading = true;
    undoButton.disabled = true;
    try {
      const result = await api(taskApi() + '/title-duplicate-reviews/' + encodeURIComponent(reviewId) + scrQ(), {
        method: 'POST', body: { decision, keeper_key: keeperKey || '' }
      });
      const screenRow = Array.from($('#reportBody').querySelectorAll('tr')).find(row =>
        row.firstElementChild?.textContent === T('进入筛选（非重复总数）') ||
        row.firstElementChild?.textContent === T('Entering screening (non-duplicate total)'));
      if (screenRow && screenRow.lastElementChild) {
        screenRow.lastElementChild.textContent = '';
        screenRow.lastElementChild.append(el('b', { text: String(result.screening.total) }));
      }
      await refreshProgress();
      await load();
      toast(decision === 'duplicate' ? T('已确认重复并排除多余记录')
        : decision === 'not_duplicate' ? T('已标记为不同文献，全部保留')
          : T('保留为待核验'), 'inc');
    } catch (error) { toast(T('核验保存失败：') + error.message, 'warn'); }
    finally { loading = false; undoButton.disabled = !undoAvailable; }
    if (S.task?.task_id !== taskId) return;
  }

  async function load(report) {
    const wrap = $('#titleDupWrap');
    if (!wrap || !S.task) return;
    if (currentTask !== S.task.task_id) {
      currentTask = S.task.task_id;
      offset = 0;
      undoAvailable = false; undoButton.disabled = true;
    }
    if (report) {
      const hasReview = Number(report.title_review_groups || 0) > 0;
      if (hasReview) offset = 0;
    }
    try {
      const d = await api(taskApi() + '/title-duplicate-reviews' + scrQ()
        + (S.screener ? '&' : '?') + 'limit=' + pageSize + '&offset=' + offset);
      if (S.task?.task_id !== currentTask) return;
      const p = d.progress || {};
      undoAvailable = !!d.undo_available; undoButton.disabled = !undoAvailable;
      document.dispatchEvent(new CustomEvent('reviewflow:title-review-progress', { detail: { taskId: currentTask, screener: S.screener, progress: p } }));
      wrap.style.display = Number(p.candidate_groups || 0) || Number(p.pending_groups || 0) || undoAvailable ? '' : 'none';
      $('#titleDupSummary').textContent = T('候选组 {0} 组；已确认重复 {1} 组（排除 {2} 条）；判定不同 {3} 组；待核验 {4} 组／{5} 条。待核验条目暂不进入初筛。',
        p.candidate_groups || 0, p.confirmed_duplicate_groups || 0,
        p.confirmed_duplicate_records_excluded || 0, p.not_duplicate_groups || 0,
        p.pending_groups || 0, p.pending_records || 0);
      $('#goScreenBtn').disabled = Number(p.pending_groups || 0) > 0;
      const host = $('#titleDupGroups'); host.textContent = '';
      for (const group of d.groups || []) {
        const members = group.members || [];
        const hasFuzzyMatch = members.some(member => member.match_kind === 'fuzzy');
        const chooser = el('select', { class: 'input', style: 'max-width:100%;margin:8px 0' });
        members.forEach((member, index) => chooser.append(el('option', {
          value: member.zotero_key,
          text: T('保留此记录：{0}（{1}）', member.title || T('（无标题）'),
            member.imported_in_batch ? T('本次导入') : T('库内已有')),
          ...(index === 0 ? { selected: 'selected' } : {})
        })));
        const block = el('div', { class: 'card', style: 'padding:12px;margin:10px 0' },
          el('div', { class: 'hint', text: T(
            hasFuzzyMatch ? '标题和作者相似候选组 · {0} 条记录' : '标题相同候选组 · {0} 条记录',
            members.length), style: 'margin:0 0 5px' }),
          el('h4', { text: members[0]?.title || T('（无标题）'), style: 'margin:4px 0 8px' }),
          chooser,
          ...members.map(member => recordCard(member)),
          el('div', { class: 'row', style: 'flex-wrap:wrap' },
            el('button', { class: 'btn b-exc sm', text: T('确认重复，排除其余记录'),
              onclick: () => resolve(group.review_id, 'duplicate', chooser.value) }),
            el('button', { class: 'btn b-inc sm', text: T('不是重复，全部保留'),
              onclick: () => resolve(group.review_id, 'not_duplicate') }),
            el('button', { class: 'btn b-out sm', text: T('暂不判断'),
              onclick: () => resolve(group.review_id, 'undecided') })));
        host.append(block);
      }
      const first = d.total ? offset + 1 : 0;
      const last = Math.min(offset + pageSize, d.total || 0);
      $('#titleDupPage').textContent = T('{0}–{1} / {2} 组', first, last, d.total || 0);
      $('#titleDupPrev').disabled = offset <= 0;
      $('#titleDupNext').disabled = offset + pageSize >= (d.total || 0);
    } catch (error) {
      wrap.style.display = '';
      $('#titleDupSummary').textContent = T('标题候选加载失败：') + error.message;
    }
  }

  $('#titleDupPrev').addEventListener('click', () => { offset = Math.max(0, offset - pageSize); load(); });
  $('#titleDupNext').addEventListener('click', () => { offset += pageSize; load(); });
  $('#titleDupExport').addEventListener('click', event => {
    if (S.task) download(taskApi() + '/title-duplicate-reviews/export' + scrQ(),
      'title-duplicate-review.csv', event.currentTarget);
  });
  window.TitleDuplicateReview = { load };
})();
