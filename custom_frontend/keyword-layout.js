/* Preview only: reorganize the product keyword editor; its original form owns all writes. */
(function () {
  'use strict';
  if (window.RFKeywordLayoutPreview) return;
  const q = (root, selector) => root?.querySelector(selector);
  const qa = (root, selector) => [...(root?.querySelectorAll(selector) || [])];
  const commonTerms = ['研究','research','方法','method','结果','results','分析','analysis','数据','data','结论','conclusion','样本','sample','参与者','participants'];
  const offered = new WeakSet();
  const suppressedDefaults = new Set();
  const baselines = new WeakMap();
  // Source-matched limits for this preview: coscreen/highlight.py MAX_TERMS_PER_SET/MAX_TERM_LENGTH.
  const previewLimits = {terms:500,termLength:200};
  let enabled = false, frame = 0, entry = null, remembered = null;

  function scopeKey() {
    return typeof S === 'undefined' ? 'preview' : String(S.user?.username || S.user?.name || '') + '\u0000' + String(S.task?.task_id || '');
  }
  function makeJournal() {
    const moves = new Map(), added = [], properties = new Map(), classes = [], events = [];
    return {
      add(tag, className, parent, text) {
        const node = document.createElement(tag); node.className = className;
        if (text !== undefined) node.textContent = text;
        parent.append(node); added.push(node); return node;
      },
      move(node, parent, before) {
        if (!node || !parent || node.parentNode === parent && !before) return;
        if (!moves.has(node)) { const marker = document.createComment('keyword-preview-position'); node.before(marker); moves.set(node, marker); }
        parent.insertBefore(node, before || null);
      },
      mark(node, name) {
        if (!node || node.classList.contains(name)) return;
        node.classList.add(name); classes.push([node, name]);
      },
      property(node, name, value) {
        if (!node || node[name] === value) return;
        if (!properties.has(node)) properties.set(node, new Map());
        if (!properties.get(node).has(name)) properties.get(node).set(name, node[name]);
        node[name] = value;
      },
      listen(node, event, listener, options) { node.addEventListener(event, listener, options); events.push([node,event,listener,options]); },
      restore() {
        for (const [node,event,listener,options] of events) node.removeEventListener(event,listener,options);
        for (const [node,marker] of [...moves].reverse()) if (marker.parentNode) marker.replaceWith(node);
        for (const [node,values] of properties) for (const [name,value] of values) node[name] = value;
        for (const [node,name] of classes) node.classList.remove(name);
        for (const node of added.reverse()) node.remove();
      }
    };
  }
  function termsOf(input) { return input.value.split(/\r?\n/).map(value => value.trim()).filter(Boolean); }
  function recordId(row) { return q(row, ':scope > small')?.textContent.trim() || ''; }
  function captureBaseline(form) {
    if (!form || baselines.has(form)) return;
    const active = q(form,'select'), rows = qa(form,'.rf-keyword-set');
    if (!active || rows.some(row=>!q(row,'input') || !q(row,'textarea'))) return;
    baselines.set(form,{active:active.value,rows:new Map(rows.map(row=>[row,{name:q(row,'input').value,terms:q(row,'textarea').value}]))});
  }
  function selectedKey(record) { return record ? {id:recordId(record.row),name:record.name.value,index:[...entry.records.keys()].indexOf(record.row),scope:entry.scope} : null; }
  function remember() { if (entry) remembered = selectedKey(entry.records.get(entry.selected)); }
  function setText(node, value) { if (node.textContent !== value) node.textContent = value; }

  function selectRow(row, focus = false) {
    if (!entry || !entry.records.has(row)) return;
    entry.selected = row;
    for (const [candidate,record] of entry.records) {
      const active = candidate === row;
      entry.j.property(candidate, 'hidden', !active);
      record.select.setAttribute('aria-pressed', String(active));
      record.item.classList.toggle('rfkl-selected', active);
    }
    remember(); updateState();
    if (focus) entry.records.get(row).name.focus({preventScroll:true});
  }

  function updateState() {
    if (!entry) return;
    const e = entry, current = e.records.get(e.selected), busy = e.fields.disabled;
    let dirty = e.records.size !== e.initialSize || e.active.value !== e.savedActive;
    for (const record of e.records.values()) {
      const id = recordId(record.row), name = record.name.value.trim() || '未命名词组';
      const count = termsOf(record.terms).length;
      setText(record.label, name); setText(record.count, count + ' 个词');
      const status = id && id === e.savedActive ? '已激活' : id && id === e.active.value && id !== e.savedActive ? '待激活' : !id ? '未保存' : '';
      setText(record.badge, status); record.badge.hidden = !status;
      record.item.classList.toggle('rfkl-active', !!id && id === e.savedActive);
      record.select.setAttribute('aria-label', name + '，' + count + ' 个词' + (status ? '，' + status : ''));
      if (record.name.value !== record.initialName || record.terms.value !== record.initialTerms || !id) dirty = true;
    }
    const id = current && recordId(current.row);
    e.dirty = dirty;
    setText(e.activate, !current ? '激活词组' : !id ? '保存后可激活' : id === e.savedActive ? '已激活' : '激活词组');
    e.activate.disabled = busy || dirty || !id || id === e.savedActive;
    e.activate.title = dirty ? '请先保存修改，再激活' : '';
    e.stop.disabled = busy || dirty || !e.savedActive && !e.active.value;
    e.stop.title = dirty ? '请先保存修改，再停用' : '';
    e.common.hidden = !current || current.name.value.trim().toLowerCase() !== 'default';
    e.common.disabled = busy;
    setText(e.note, e.feedback || (dirty ? '有未保存的更改 · 保存将保存全部词组。请先保存修改，再激活。' : '选择词组仅切换编辑对象；激活后才用于题名与摘要高亮。'));
    e.note.classList.toggle('rfkl-dirty', dirty);
    e.empty.hidden = e.records.size !== 0;
    setText(e.total, e.records.size + ' 个词组');
  }

  function addCommon() {
    const record = entry?.records.get(entry.selected);
    if (!record) return;
    const values = termsOf(record.terms);
    // Only the preset ASCII words fold case. Other existing Unicode terms stay unchanged.
    const key = term => /^[\x00-\x7F]*$/.test(term) ? term.toLowerCase() : term;
    const seen = new Set(values.map(key));
    const additions = commonTerms.filter(term => !seen.has(key(term)));
    if (additions.length) {
      record.terms.value = record.terms.value.replace(/\s*$/, '') + (values.length ? '\n' : '') + additions.join('\n');
      record.terms.dispatchEvent(new Event('input',{bubbles:true}));
    }
    entry.feedback = additions.length ? '已添加 ' + additions.length + ' 个常用词到草稿，保存后生效。' : '常用词均已存在，没有重复添加。';
    updateState();
  }

  function submitActivation(value) {
    if (!entry || entry.fields.disabled || entry.dirty) return;
    entry.feedback = '';
    entry.active.value = value;
    entry.active.dispatchEvent(new Event('change',{bubbles:true}));
    // requestSubmit retains native required checks and the original PUT/context/busy logic.
    entry.form.requestSubmit(entry.save);
    updateState();
  }

  function reconcileRows() {
    if (!entry) return;
    const e = entry, rows = qa(e.setList, ':scope > .rf-keyword-set');
    const previous = [...e.records.keys()], previousIndex = previous.indexOf(e.selected);
    for (const [row,record] of e.records) if (!rows.includes(row)) { record.item.remove(); e.records.delete(row); }
    let addedRow = null;
    for (const row of rows) {
      if (e.records.has(row)) continue;
      const name = q(row, ':scope > input'), terms = q(row, ':scope > textarea'), remove = q(row, ':scope > button');
      if (!name || !terms || !remove) continue;
      const item = e.j.add('div','rfkl-list-item',e.list);
      const select = e.j.add('button','rfkl-select',item); select.type = 'button';
      const label = e.j.add('span','rfkl-list-name',select);
      const metadata = e.j.add('span','rfkl-list-meta',select);
      const count = e.j.add('span','',metadata), badge = e.j.add('span','rfkl-badge',metadata);
      e.j.move(remove,item); e.j.mark(remove,'rfkl-remove');
      e.j.property(remove,'textContent','删除');
      const nameLabel = e.j.add('span','rfkl-field-label',row,'词组名称'); row.insertBefore(nameLabel,name);
      const termLabel = e.j.add('span','rfkl-field-label',row,'关键词'); row.insertBefore(termLabel,terms);
      const hint = e.j.add('p','rfkl-terms-hint',row,'每行一个词或短语，支持中文和英文。激活后将在题名与摘要中高亮显示。');
      e.j.mark(row,'rfkl-editor-row');
      const baseline = e.baseline.rows.get(row);
      e.records.set(row,{row,name,terms,remove,item,select,label,count,badge,initialName:baseline?.name ?? name.value,initialTerms:baseline?.terms ?? terms.value});
      e.j.listen(select,'click',()=>{e.feedback='';selectRow(row);});
      e.j.listen(remove,'click',()=>{if(name.value.trim().toLowerCase()==='default')suppressedDefaults.add(e.scope);e.feedback='';},true);
      addedRow = row;
    }
    // A user-created row has already been focused by the original add handler.
    const focusedRow = document.activeElement?.closest('.rf-keyword-set');
    let selected = rows.includes(e.selected) ? e.selected : null;
    if (focusedRow && e.records.has(focusedRow)) selected = focusedRow;
    if (!selected && remembered?.scope === e.scope) selected = rows.find(row => remembered.id && recordId(row) === remembered.id)
      || rows.find(row => q(row, ':scope > input')?.value === remembered.name);
    if (!selected) selected = rows[Math.max(0,Math.min(previousIndex,rows.length-1))] || addedRow;
    if (selected) selectRow(selected); else { e.selected = null; updateState(); }
  }

  function mount(panel, form) {
    const fields = q(form, 'fieldset'), setList = fields?.firstElementChild;
    const footer = q(fields, ':scope > .row'), add = q(footer, 'button[type=button]'), save = q(footer, 'button[type=submit]'), active = q(footer, 'select');
    const header = q(panel, ':scope > header'), heading = q(header,'h3'), close = q(header,'button');
    if (!fields || !setList || !footer || !add || !save || !active || !header || !heading || !close) return;
    const j = makeJournal(), scope = scopeKey();
    captureBaseline(form);
    const baseline = baselines.get(form);
    const e = entry = {j,panel,form,fields,setList,footer,add,save,active,scope,baseline,records:new Map(),selected:null,savedActive:baseline.active,initialSize:baseline.rows.size};
    j.mark(panel,'rfkl-dialog'); j.mark(form,'rfkl-form'); j.mark(form.parentElement,'rfkl-main'); j.mark(fields,'rfkl-fields');
    j.mark(header,'rfkl-original-header'); j.mark(footer,'rfkl-original-actions');
    const top = j.add('div','rfkl-top',fields); fields.prepend(top);
    j.move(heading,top);
    const actions = j.add('div','rfkl-actions',top);
    j.move(add,actions); j.move(save,actions); j.property(add,'textContent','新建词组'); j.property(save,'textContent','保存全部');
    e.activate = j.add('button','btn b-out rfkl-activate',actions,'激活词组'); e.activate.type='button';
    j.move(close,actions);
    const workspace = j.add('div','rfkl-workspace',fields);
    const aside = j.add('aside','rfkl-sidebar',workspace); aside.setAttribute('aria-label','关键词词组');
    const caption = j.add('div','rfkl-list-caption',aside);
    j.add('strong','',caption,'词组'); e.total = j.add('span','',caption);
    e.list = j.add('div','rfkl-list',aside);
    e.stop = j.add('button','rfkl-stop',aside,'停用高亮'); e.stop.type='button';
    const right = j.add('div','rfkl-editor',workspace);
    j.move(setList,right); j.mark(setList,'rfkl-set-list');
    e.empty = j.add('p','rfkl-empty',right,'还没有词组。点击“新建词组”开始。');
    const editorActions = j.add('div','rfkl-editor-actions',right);
    e.common = j.add('button','btn b-out sm',editorActions,'添加常用词'); e.common.type='button';
    e.note = j.add('p','rfkl-note',fields);
    j.listen(form,'input',()=>{e.feedback='';updateState();}); j.listen(active,'change',updateState);
    j.listen(add,'click',()=>{e.feedback='';},true);
    j.listen(e.common,'click',addCommon);
    j.listen(e.activate,'click',()=>{const row=entry?.selected,id=row&&recordId(row);if(id)submitActivation(id);});
    j.listen(e.stop,'click',()=>submitActivation(''));
    j.listen(form,'submit',event=>{
      e.feedback='';
      for (const record of e.records.values()) {
        const values = termsOf(record.terms);
        const tooLong = values.findIndex(value=>Array.from(value).length>previewLimits.termLength);
        const emptyName = !record.name.value.trim();
        if (!emptyName && values.length<=previewLimits.terms && tooLong<0) continue;
        event.preventDefault(); event.stopImmediatePropagation(); selectRow(record.row); (emptyName?record.name:record.terms).focus({preventScroll:true});
        e.feedback=emptyName ? '请填写词组名称，再保存。' : values.length>previewLimits.terms ? '此词组最多可保存 500 个关键词，请减少后重试。' : '第 '+(tooLong+1)+' 个关键词超过 200 字符，请缩短后重试。';
        updateState();
        return;
      }
    },true);
    let invalidFocused = false;
    j.listen(form,'invalid',event=>{
      const row = event.target.closest('.rf-keyword-set');
      if (!row) return;
      event.preventDefault();
      if (invalidFocused) return;
      invalidFocused = true; selectRow(row); event.target.focus({preventScroll:true});
      e.feedback='请填写词组名称，再保存。'; updateState();
      setTimeout(()=>{invalidFocused=false;},0);
    },true);
    if (!offered.has(form)) {
      offered.add(form);
      if (!suppressedDefaults.has(scope) && !qa(setList,'.rf-keyword-set > input').some(input=>input.value.trim().toLowerCase()==='default') && !add.disabled) {
        add.click();
        const row = setList.lastElementChild, name = q(row,'input'), terms = q(row,'textarea');
        if (name && terms) { name.value='default'; terms.value=commonTerms.join('\n'); name.dispatchEvent(new Event('input',{bubbles:true})); }
      }
    }
    reconcileRows();
  }

  function observe() { if(enabled)observer.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','open']}); }
  function compose() {
    frame=0;if(!enabled)return;observer.disconnect();
    try {
      const panel = document.getElementById('rfKeywordSettings'), form = q(panel,'form');
      if (entry && entry.form !== form) { remember(); entry.j.restore(); entry=null; }
      if (panel?.open && form && !entry) mount(panel,form);
      if (entry) reconcileRows();
    } finally { observe(); }
  }
  function schedule() { if(enabled&&!frame)frame=requestAnimationFrame(compose); }
  const observer = new MutationObserver(schedule);
  // Read-only from first appearance, even in Original layout: enabling the preview
  // later must never recategorize edits already made in the real form as saved.
  const baselineObserver = new MutationObserver(()=>captureBaseline(q(document.getElementById('rfKeywordSettings'),'form')));
  baselineObserver.observe(document.getElementById('rfKeywordSettings') || document.body,{childList:true,subtree:true});
  captureBaseline(q(document.getElementById('rfKeywordSettings'),'form'));
  // The original layout can also explicitly remove default; remember intent without
  // changing, preventing or replacing the product delete listener.
  document.addEventListener('click',event=>{
    const remove = event.target.closest?.('#rfKeywordSettings .rf-keyword-set > button');
    if (remove && q(remove.parentElement,':scope > input')?.value.trim().toLowerCase()==='default') suppressedDefaults.add(scopeKey());
  },true);
  function setEnabled(value) {
    if(enabled===Boolean(value))return;
    enabled=Boolean(value);
    document.documentElement.classList.toggle('rf-keyword-layout-preview',enabled);
    if(enabled)compose();
    else {observer.disconnect();cancelAnimationFrame(frame);frame=0;remember();entry?.j.restore();entry=null;}
  }
  document.addEventListener('reviewflow:layout-preview-mode',event=>setEnabled(event.detail?.compact));
  document.addEventListener('reviewflow:task-context-changed',()=>{remembered=null;schedule();});
  window.RFKeywordLayoutPreview=Object.freeze({setEnabled,refresh:schedule,get enabled(){return enabled;}});
  setEnabled(document.body.classList.contains('rf-layout-preview-compact'));
})();
