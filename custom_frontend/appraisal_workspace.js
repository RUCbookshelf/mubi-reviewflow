/* ReviewFlow 2.1.5 · task-scoped appraisal and GRADE input. */
(function () {
  'use strict';

  const coding = document.getElementById('cbForms');
  if (!coding) return;
  const node = (tag, text, attrs = {}, ...children) => el(tag, { ...attrs, text }, ...children);
  const raw = (tag, text, attrs) => node(tag, String(text ?? ''), { ...attrs, translate: 'no' });
  const tr = (tag, key, attrs) => node(tag, T(key), attrs);
  const button = (key, onclick, primary = false) =>
    tr('button', key, { type: 'button', class: primary ? 'btn b-inc' : 'btn b-out', onclick });
  const scope = () => [S.task?.task_id, S.taskVersion, S.user?.user_id, S.screener].join('|');
  const resultMessage = (data, fallback) => RFServerMessage(data?.message_key, data?.params) || T(fallback);
  const query = params => '?' + new URLSearchParams(params).toString();
  const field = (key, control) => node('label', null, { class: 'rf-assessment-field' }, tr('span', key), control);
  const statusLine = () => node('p', '', { class: 'hint', role: 'status' });
  const sharedClose = '关闭';
  const retry = '重试';
  const unavailable = 'appraisal.unavailable';
  const gradeUnavailable = 'appraisal.grade_input_unavailable';
  let permissionRequest;
  async function canAssess() {
    if (!S.task || !S.user) return false;
    const identity = scope();
    const allowed = WorkflowControls.settings()?.can_manage === true;
    if (allowed) return true;
    if (runtimeMode !== 'server') return false;
    if (!permissionRequest || permissionRequest.scope !== identity) {
      const promise = api(taskApi() + '/team-progress').then(data => {
        const me = data.members?.find(member => member.user_id === S.user?.user_id);
        return data.can_manage === true || !!me?.roles?.includes('reconciler');
      }).catch(() => false);
      permissionRequest = { scope: identity, promise };
    }
    return await permissionRequest.promise && identity === scope();
  }

  function select(options, value = '') {
    const control = node('select', null, { class: 'input' },
      options.map(([code, label, literal]) =>
        literal ? raw('option', label, { value: code }) : tr('option', label, { value: code })));
    control.value = value;
    return control;
  }

  function toolsSelect(tools, value, emptyLabel = 'appraisal.select_tool') {
    const control = select([['', emptyLabel]], '');
    const families = new Map();
    for (const tool of tools) {
      if (!families.has(tool.family)) {
        const group = node('optgroup', null, { label: tool.family, translate: 'no' });
        families.set(tool.family, group);
        control.append(group);
      }
      const name = [tool.title, tool.version, T('appraisal.item_count', { n: tool.item_count })].filter(Boolean).join(' · ');
      families.get(tool.family).append(raw('option', name, { value: tool.id }));
    }
    control.value = value;
    return control;
  }

  let dialogSerial = 0;
  function popup(title) {
    const heading = tr('h2', title, { id: 'rf-assessment-dialog-title-' + ++dialogSerial });
    const body = node('div', null, { class: 'rf-assessment-body' });
    const dialog = node('dialog', null, { class: 'rf-assessment-dialog', 'aria-labelledby': heading.id },
      node('header', null, { class: 'row' }, heading, button(sharedClose, () => dialog.close())), body);
    const remove = () => dialog.remove();
    dialog.addEventListener('close', remove, { once: true });
    document.body.append(dialog);
    dialog.showModal();
    return { dialog, body };
  }

  function grid(headers, rows) {
    const head = node('thead', null, {}, node('tr', null, {}, headers.map(v => raw('th', v, { scope: 'col' }))));
    const body = node('tbody');
    for (const row of rows) {
      body.append(node('tr', null, {}, row.map(v => v instanceof Node ? node('td', null, {}, v) : raw('td', v))));
    }
    return node('div', null, { class: 'rf-assessment-table' }, node('table', null, {}, head, body));
  }

  async function fileResponse(path, options, fallback, valid) {
    const response = await api(path, { ...options, response: true });
    const file = await response.blob();
    if (!valid()) return;
    const header = response.headers.get('Content-Disposition') || '';
    const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(header)?.[1];
    const ascii = /filename="?([^";]+)"?/i.exec(header)?.[1];
    let name = ascii || fallback;
    if (utf8) {
      try { name = decodeURIComponent(utf8); } catch (_) { /* Use ASCII fallback. */ }
    }
    const url = URL.createObjectURL(file);
    const link = el('a', { href: url, download: name });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  function officialLink(url, label) {
    try {
      const parsed = new URL(url);
      if (parsed.protocol === 'https:' || parsed.protocol === 'http:')
        return raw('a', label, { href: parsed.href, target: '_blank', rel: 'noopener noreferrer' });
    } catch (_) { /* Invalid official link is displayed as text. */ }
    return raw('span', label);
  }

  function details(key, ...children) {
    return node('details', null, {}, tr('summary', key), ...children);
  }

  const codingTab = button('数据提取与编码', () => chooseTab(false));
  const appraisalTab = button('appraisal.title', () => chooseTab(true));
  const tabBar = node('div', null, { class: 'row rf-assessment-tabs', 'aria-label': T('appraisal.title') }, codingTab, appraisalTab);
  const host = node('section', null, { id: 'rfAppraisal', 'aria-label': T('appraisal.title') });
  coding.before(tabBar);
  coding.after(host);
  let selectedTab = false;
  let article = null;
  let catalog = null;
  let recommendation = null;
  let record = null;
  let toolId = '';
  let fieldError = null;
  let ticket = 0;
  let saving = false;
  let canTeam = false;
  let activeScope = scope();
  let drafts = new Map();
  let feedback = '';
  const queryTool = id => query({ tool: id });
  const endpoint = () => taskApi() + '/appraisal';
  const recordPath = () => endpoint() + '/' + encodeURIComponent(article.zotero_key);
  const draftKey = () => JSON.stringify([article?.zotero_key, toolId]);
  const active = () => selectedTab && !!article && activeScope === scope();
  const valid = (request, identity) => request === ticket && identity === scope() && active();

  function showTab() {
    host.hidden = !selectedTab;
    tabBar.hidden = !article;
    coding.hidden = selectedTab;
    coding.parentElement.classList.toggle('rf-appraisal-open', selectedTab);
    document.getElementById('cbSave').hidden = selectedTab;
    codingTab.setAttribute('aria-pressed', String(!selectedTab));
    appraisalTab.setAttribute('aria-pressed', String(selectedTab));
  }

  function chooseTab(open) {
    if (saving || (open && !article)) return;
    selectedTab = open;
    showTab();
    if (open && !catalog) load();
    else if (open && toolId && !record) loadTool(toolId);
    else if (open) render();
  }

  function setArticle(current, context) {
    const identity = scope();
    const next = context === 'personal' && current?.zotero_key ? current : null;
    if (identity === activeScope && article?.zotero_key === next?.zotero_key) return;
    if (identity !== activeScope) {
      activeScope = identity;
      drafts = new Map();
      selectedTab = false;
    }
    article = next;
    catalog = recommendation = record = fieldError = null;
    toolId = feedback = '';
    saving = canTeam = false;
    ++ticket;
    host.replaceChildren();
    if (!article) selectedTab = false;
    showTab();
    if (active()) load();
  }

  function failed(error, action) {
    feedback = error?.message || T(unavailable);
    if (!catalog || !recommendation) {
      host.replaceChildren(raw('p', feedback, { role: 'alert' }), button(retry, action));
    } else render(action);
  }

  async function load() {
    if (!active()) return;
    const request = ++ticket;
    const identity = scope();
    host.replaceChildren(tr('p', 'appraisal.loading', { class: 'hint', role: 'status' }));
    catalog = recommendation = record = fieldError = null;
    const key = article.zotero_key;
    try {
      const [tools, rec, capability] = await Promise.all([
        api(endpoint() + '/tools'),
        api(endpoint() + '/recommendation' + query({ article_key: key })),
        canAssess()
      ]);
      if (!valid(request, identity)) return;
      if (!tools.available || !rec.available) {
        const problem = !tools.available ? tools : rec;
        throw Error(resultMessage(problem, unavailable));
      }
      catalog = tools;
      recommendation = rec;
      canTeam = capability;
      const retained = tools.tools.some(tool => tool.id === toolId);
      const initial = retained ? toolId : rec.primary?.tool_id || '';
      feedback = '';
      await loadTool(initial);
    } catch (error) {
      if (valid(request, identity)) failed(error, load);
    }
  }

  async function loadTool(id, preserveMessage = '') {
    if (!active() || saving) return;
    const request = ++ticket;
    const identity = scope();
    toolId = id;
    record = null;
    fieldError = null;
    feedback = id ? T('appraisal.loading') : '';
    render();
    if (!id) return;
    try {
      const data = await api(recordPath() + queryTool(id));
      if (!valid(request, identity)) return;
      record = data;
      if (!drafts.get(draftKey())?.dirty) drafts.delete(draftKey());
      feedback = data.available ? preserveMessage : resultMessage(data, unavailable);
      render(data.available ? null : () => loadTool(id));
    } catch (error) {
      if (valid(request, identity)) failed(error, () => loadTool(id));
    }
  }

  function citation(tool, copy) {
    const box = node('div', null, { class: 'rf-appraisal-citation' });
    const license = tool.license;
    box.append(tr('b', 'appraisal.citation'), raw('p', tool.citation));
    if (license) {
      box.append(officialLink(license.url, license.name), raw('p', license.notice));
      if (license.no_derivatives || license.id === 'CC-BY-NC-SA-4.0')
        box.append(node('p', T('appraisal.copyright', { license: license.name })));
    }
    if (tool.id === 'mmat') box.append(officialLink(tool.url, T('appraisal.mmat_link')));
    if (copy && tool.bibtex) {
      const copied = statusLine();
      box.append(button('appraisal.copy_citation', async () => {
        try {
          await navigator.clipboard.writeText(tool.bibtex);
          copied.textContent = T('appraisal.copied');
        } catch (_) {
          const output = raw('textarea', tool.bibtex, { class: 'input', readonly: '', 'aria-label': T('appraisal.citation') });
          box.append(output);
          output.select();
        }
      }), copied);
    }
    return box;
  }

  function recommendationCards() {
    const box = node('div', null, { class: 'rf-assessment-stack' });
    const cards = [recommendation.primary, ...(recommendation.alternatives || [])].filter(Boolean);
    const designLabels = {
      meta_analysis: 'Meta 分析', systematic_review: '系统综述', rct: '随机对照试验',
      cohort: '队列研究', case_control: '病例对照', cross_sectional: '横断面研究',
      qualitative: 'sd.label.qualitative', mixed_methods: 'sd.label.mixed_methods',
      survey: 'sd.label.survey', discourse: 'sd.label.discourse',
      diagnostic: '诊断准确性', prevalence: 'appraisal.design.prevalence',
      economic_evaluation: 'appraisal.design.economic_evaluation', prediction_rule: 'appraisal.design.prediction_rule'
    };
    const classification = recommendation.design?.appraisal_label;
    const design = Object.hasOwn(designLabels, classification) ? T(designLabels[classification]) : '';
    if (!cards.length) box.append(node('p', resultMessage(recommendation, 'appraisal.recommend.unrecognized')));
    for (const card of cards) {
      const metadata = catalog.tools.find(tool => tool.id === card.tool_id);
      const reason = RFServerMessage(card.reason_key, { design }) ||
        T(design ? 'appraisal.recommend.design' : 'appraisal.recommend.alternative', { design });
      box.append(node('article', null, { class: 'card' },
        raw('b', card.title), raw('span', card.version, { class: 'chip' }), node('p', reason),
        citation({ ...metadata, ...card }, false),
        button('appraisal.select_tool', () => loadTool(card.tool_id))));
    }
    return box;
  }

  function linkedTools() {
    const body = node('div', null, { class: 'rf-assessment-stack' });
    for (const tool of catalog.link_only || []) {
      body.append(node('article', null, {}, officialLink(tool.url, tool.title), raw('span', tool.provider, { class: 'chip' }),
        raw('p', tool.license_note), node('p', resultMessage({ message_key: tool.reason_key }, 'appraisal.link_only.permission_required'))));
    }
    return details('appraisal.link_only.permission_required', body);
  }

  function overrideFrom(overall) {
    if (overall?.manual_judgement) return { ...overall.manual_judgement };
    if (overall?.manual_confidence) return { ...overall.manual_confidence };
    return null;
  }

  function editRecord() {
    const key = draftKey();
    if (!drafts.has(key)) {
      drafts.set(key, {
        variant: record.variant || record.tool.default_variant || '',
        responses: { ...record.responses },
        comments: { ...record.comments },
        note: record.note || '',
        override: overrideFrom(record.overall),
        dirty: false
      });
    }
    return drafts.get(key);
  }

  function itemGroups(tool, variant) {
    if (tool.domains?.length) return tool.domains.map(domain => ({
      title: domain.title, items: domain.variants?.[variant]?.items || domain.items || []
    }));
    const items = tool.items || [];
    if (tool.sections?.length) {
      const remaining = new Set(items);
      const groups = tool.sections.map(section => {
        const subset = items.filter(item => item.section === section.id);
        subset.forEach(item => remaining.delete(item));
        return { title: section.title, items: subset };
      });
      if (remaining.size) groups.push({ items: [...remaining] });
      return groups;
    }
    return [{ items }];
  }

  const riskClass = code => {
    if (['high', 'some_concerns', 'low'].includes(code)) return 'rf-appraisal-bias rf-appraisal-bias-' + code;
    return 'chip';
  };
  const optionLabel = (tool, value) => {
    const english = tool.response_option_labels?.[value];
    const alias = 'appraisal.response.' + value;
    return english && RFLang.has(alias) ? value + ' · ' + T(alias) : english ? value + ' · ' + english : value;
  };

  function itemRow(tool, item, draft, readOnly, critical) {
    const itemOptions = item.response_options || tool.response_options || [];
    const options = itemOptions.map(value => [value, optionLabel(tool, value), true]);
    const answer = select([['', 'appraisal.unanswered'], ...options], draft.responses[item.id] ?? '');
    const comment = node('textarea', null, { class: 'input', maxlength: 20000, rows: 2, translate: 'no' });
    comment.value = draft.comments[item.id] || '';
    const prompt = raw('p', item.text);
    const issue = fieldError && (fieldError.item_id === item.id || fieldError.item_ids?.includes(item.id));
    answer.disabled = comment.disabled = readOnly;
    if (issue) answer.setAttribute('aria-invalid', 'true');
    answer.addEventListener('change', () => {
      draft.dirty = true;
      if (answer.value) draft.responses[item.id] = answer.value;
      else delete draft.responses[item.id];
      updateProgress();
    });
    comment.addEventListener('input', () => {
      draft.dirty = true;
      if (comment.value.trim()) draft.comments[item.id] = comment.value;
      else delete draft.comments[item.id];
    });
    const row = node('div', null, { class: 'rf-appraisal-item', 'data-item': item.id },
      raw('b', item.number), prompt);
    if (critical) row.append(tr('span', 'appraisal.critical', { class: 'chip' }));
    if (item.reverse_direction) row.append(tr('p', 'appraisal.reverse_axis', { class: 'hint' }));
    row.append(field('appraisal.answer', answer), field('appraisal.comment', comment));
    return row;
  }

  function rules(tool) {
    function flatten(data) {
      if (typeof data === 'string') return [data];
      return data && typeof data === 'object' ? Object.values(data).flatMap(flatten) : [];
    }
    const all = flatten([tool.overall_algorithm_text, tool.box1_text, tool.box2_text, tool.official_overall_text,
      tool.rating_down_guidance, tool.aggregation_notes,
      tool.domains?.map(domain => [domain.title, domain.algorithm_text, Object.values(domain.variants || {}).map(variant => variant.algorithm_text)])]);
    return all.length ? details('appraisal.rules', ...all.map(line => raw('p', line))) : null;
  }

  function summary(overall, tool, manualAction) {
    if (!overall?.complete) return null;
    const box = node('section', null, { class: 'card rf-appraisal-result' }, tr('h3', 'appraisal.result'));
    if (overall.mode === 'domains') {
      for (const [id, result] of Object.entries(overall.domains || {})) {
        const title = tool.domains?.find(domain => domain.id === id)?.title;
        const row = node('div', null, { class: 'row' }, raw('b', title || id), raw('span', result.label || result.judgement, { class: riskClass(result.judgement) }));
        for (const part of ['part_1', 'part_2']) {
          const label = result[part] && tool.judgement_labels?.[result[part]];
          if (label) row.append(node('span', T('appraisal.rob_part', { n: part.at(-1), value: label }), { class: riskClass(result[part]) }));
        }
        box.append(row);
      }
      box.append(raw('strong', overall.judgement_label, { class: riskClass(overall.judgement) }));
      if (overall.manual_upgrade_recommended) box.append(tr('p', 'appraisal.rob2.multiple_some_concerns'), button('appraisal.upgrade', () => manualAction('high')));
    }
    if (overall.mode === 'critical_domains') {
      box.append(raw('strong', overall.confidence_label));
      for (const [list, label] of [[overall.critical_flaws, 'appraisal.critical_flaws'], [overall.noncritical_weaknesses, 'appraisal.weaknesses']]) {
        if (list?.length) box.append(tr('h4', label), node('ul', null, {}, list.map(item => raw('li', [item.item, item.answer].join(' · ')))));
      }
      if (overall.manual_downgrade_recommended)
        box.append(tr('p', 'appraisal.amstar2.multiple_weaknesses'), button('appraisal.downgrade', () => manualAction('low')));
    }
    if (overall.mode === 'downgrade') {
      box.append(raw('strong', overall.confidence_label), node('p', T('appraisal.cerqual_levels', { n: overall.downgrade_levels })));
      for (const item of Object.values(overall.components || {}))
        box.append(raw('p', [item.component, item.concern].filter(Boolean).join(' · ')));
      for (const note of overall.notes || []) box.append(node('p', RFServerMessage(note) || note));
    }
    if (overall.mode === 'no_score') {
      box.append(grid([T('appraisal.item'), T('appraisal.answer')], (overall.items || []).map(item => [item.number, item.response])));
      const counts = Object.entries(overall.answer_distribution || {}).map(([answer, count]) => answer + ' · ' + count);
      if (counts.length) box.append(raw('p', counts.join(' | ')));
      if (overall.overall_judgement) box.append(tr('p', 'appraisal.rating.' + overall.overall_judgement));
      if (tool.id === 'mmat') box.append(tr('p', 'appraisal.mmat_no_score'));
    }
    const manual = overrideFrom(overall);
    if (manual) box.append(tr('span', 'appraisal.manual', { class: 'chip' }), raw('p', manual.reason));
    return box;
  }

  function updateProgress() {
    const status = host.querySelector('[data-appraisal-progress]');
    if (!record || !status) return;
    const draft = editRecord();
    const items = itemGroups(record.tool, draft.variant).flatMap(group => group.items);
    const answered = items.filter(item => Object.hasOwn(draft.responses, item.id)).length;
    status.textContent = T('appraisal.progress', { n: answered, total: items.length });
    const summary = host.querySelector('.rf-appraisal-result');
    if (summary) summary.hidden = draft.dirty;
  }

  function editor() {
    if (!record?.tool) return null;
    const tool = record.tool;
    const draft = editRecord();
    const readOnly = !record.available || saving;
    const form = node('fieldset', null, { class: 'rf-appraisal-editor' });
    form.disabled = readOnly;
    if (tool.instruction) form.append(tr('b', 'appraisal.cerqual_finding'), raw('p', tool.instruction));
    const variants = tool.domains?.find(domain => domain.variants)?.variants;
    if (variants) {
      const picker = select(Object.entries(variants).map(([id, variant]) => [id, variant.label, true]), draft.variant);
      picker.addEventListener('change', () => {
        const variantDomain = tool.domains.find(domain => domain.variants);
        for (const item of variantDomain.variants[draft.variant]?.items || []) {
          delete draft.responses[item.id];
          delete draft.comments[item.id];
        }
        draft.variant = picker.value;
        draft.override = null;
        draft.dirty = true;
        feedback = T('appraisal.variant_reset');
        render();
      });
      form.append(field('appraisal.variant', picker), tr('p', 'appraisal.same_variant', { class: 'hint' }));
    }
    form.append(node('p', '', { 'data-appraisal-progress': '', role: 'status' }));
    const critical = new Set(record.overall?.critical_items || []);
    for (const group of itemGroups(tool, draft.variant)) {
      if (!group.items.length) continue;
      const groupBox = node('fieldset', null, { class: 'rf-appraisal-items' });
      if (group.title) groupBox.append(raw('legend', group.title));
      for (const item of group.items) groupBox.append(itemRow(tool, item, draft, readOnly, critical.has(item.id)));
      form.append(groupBox);
    }
    const ruleBlock = rules(tool);
    if (ruleBlock) form.append(ruleBlock);
    const note = node('textarea', null, { class: 'input', maxlength: 20000, rows: 3, translate: 'no' });
    note.value = draft.note;
    note.addEventListener('input', () => { draft.note = note.value; draft.dirty = true; });
    form.append(field('appraisal.note', note));
    const manualChoices = tool.overall_judgement_options || [];
    let manualAction = () => {};
    if (manualChoices.length) {
      const confidence = ['amstar2', 'cerqual'].includes(tool.id);
      const code = draft.override?.[confidence ? 'confidence' : 'judgement'] || '';
      const options = manualChoices.map(value => [value,
        tool.judgement_labels?.[value] || (RFLang.has('appraisal.rating.' + value) ? T('appraisal.rating.' + value) : value), true]);
      const choice = select([['', 'appraisal.no_manual'], ...options], code);
      const reason = node('textarea', null, { class: 'input', maxlength: 2000, translate: 'no' });
      reason.value = draft.override?.reason || '';
      reason.disabled = !code;
      const update = () => {
        const selected = choice.value;
        reason.disabled = !selected;
        draft.override = selected ? { [confidence ? 'confidence' : 'judgement']: selected, reason: reason.value } : null;
        draft.dirty = true;
        updateProgress();
      };
      choice.addEventListener('change', update);
      reason.addEventListener('input', update);
      const overrides = node('div', null, { class: 'card' },
        field('appraisal.manual', choice), field('appraisal.reason_required', reason));
      if (tool.id === 'axis') overrides.append(tr('p', 'appraisal.axis_subjective', { class: 'hint' }));
      form.append(overrides);
      manualAction = value => {
        choice.value = value;
        update();
        reason.focus();
      };
    }
    form.append(button('appraisal.save', save, true));
    const output = draft.dirty ? null : summary(record.overall, tool, manualAction);
    return node('div', null, {}, citation(tool, true), form, output);
  }

  async function save() {
    if (!active() || !record?.available || saving) return;
    const draft = editRecord();
    if (draft.override && !draft.override.reason.trim()) {
      feedback = T('appraisal.overall_invalid');
      render();
      return;
    }
    const identity = scope(), request = ticket, key = draftKey();
    const payload = {
      tool: toolId, responses: draft.responses, comments: draft.comments, note: draft.note,
      ...(draft.variant ? { variant: draft.variant } : {}),
      ...(draft.override ? { overall: draft.override } : {})
    };
    saving = true;
    feedback = T('appraisal.saving');
    render();
    try {
      const data = await api(recordPath(), { method: 'PUT', body: payload });
      if (!valid(request, identity)) return;
      if (!data.available) throw Error(resultMessage(data, unavailable));
      drafts.delete(key);
      record = data;
      fieldError = null;
      feedback = T('appraisal.saved');
    } catch (error) {
      if (!valid(request, identity)) return;
      fieldError = error.params || null;
      feedback = error.message || T(unavailable);
      if (error.messageKey === 'appraisal.variant_invalid') draft.variant = record.tool.default_variant || '';
      if (error.messageKey === 'appraisal.option_invalid' && fieldError?.item_id)
        delete draft.responses[fieldError.item_id];
      if (error.messageKey === 'appraisal.tool_unknown') {
        saving = false;
        const message = feedback;
        await load();
        feedback = message;
      }
      if (error.messageKey === 'appraisal.item_unknown') {
        saving = false;
        const message = feedback;
        const problem = fieldError;
        await loadTool(toolId, message);
        if (identity !== scope() || key !== draftKey()) return;
        fieldError = problem;
      }
    } finally {
      if (valid(request, identity)) saving = false;
      if (identity === scope() && key === draftKey() && active()) render();
    }
  }

  const kappa = (value, status) => value == null ? T('appraisal.kappa.' + (status || 'unavailable')) : String(value);
  const rate = value => value == null ? '' : new Intl.NumberFormat(document.documentElement.lang, { style: 'percent', maximumFractionDigits: 2 }).format(value);
  const labels = keys => keys.map(key => T('appraisal.' + key));
  function agreementResult(data) {
    const box = node('section');
    if (data.insufficient_raters) box.append(tr('p', 'appraisal.two_raters'));
    box.append(node('p', T('appraisal.mean_kappa', { value: kappa(data.mean_cohen_kappa) })));
    box.append(tr('h3', 'appraisal.raters'),
      grid(labels(['rater', 'articles', 'answered']), (data.raters || []).map(row =>
        [row.rater_id, row.articles_rated, row.items_answered])),
      tr('h3', 'appraisal.pairs'),
      grid(labels(['rater_a', 'rater_b', 'articles', 'compared', 'agreement_rate', 'kappa']), (data.pairwise || []).map(row =>
        [row.rater_a, row.rater_b, row.n_articles_shared, row.n_paired_cells, rate(row.agreement_rate), kappa(row.cohen_kappa, row.kappa_status)])),
      tr('h3', 'appraisal.items'),
      grid(labels(['item', 'compared', 'disagreements', 'agreement_rate', 'kappa']), (data.items || []).map(row =>
        [row.number, row.n_paired_cells, row.n_disagreements, rate(row.agreement_rate), kappa(row.cohen_kappa, row.kappa_status)])),
      tr('h3', 'appraisal.disagreements'),
      grid(labels(['article', 'item', 'answer']), (data.disagreements || []).map(row => [
        row.article_key, row.number,
        node('ul', null, {}, Object.entries(row.responses || {}).map(([rater, value]) => raw('li', rater + ' · ' + value)))
      ])));
    return box;
  }

  function gradeResult(data, keys) {
    const ready = new Map((data.grade_ready || []).map(row => [row.article_key, row.risk_of_bias]));
    const rows = (data.studies || []).filter(row => !keys || keys.has(row.article_key)).map(row => [
      row.article_key, row.title, ready.has(row.article_key) ? T('appraisal.rating.' + ready.get(row.article_key)) : '',
      T('appraisal.basis.' + row.basis)
    ]);
    return node('section', null, {}, tr('p', 'appraisal.grade_hint'), grid(labels(['article', 'article_title', 'risk', 'basis']), rows));
  }

  function openGrade(combo, initialDesign, prefill) {
    const identity = scope();
    const path = taskApi();
    const { dialog, body } = popup('appraisal.grade_input');
    const status = statusLine();
    body.append(status);
    const valid = () => identity === scope() && dialog.open;
    async function load() {
      status.textContent = T('appraisal.loading');
      try {
        if (!await canAssess() || !valid()) {
          status.textContent = T('appraisal.task_forbidden');
          return;
        }
        const registry = await api(path + '/appraisal/tools');
        if (!valid()) return;
        if (!registry.available) throw Error(resultMessage(registry, unavailable));
        const picker = toolsSelect(registry.tools, '');
        const results = node('div');
        const inspect = button('appraisal.grade_input', async () => {
          if (!picker.value || !valid()) return;
          const tool = picker.value;
          picker.disabled = inspect.disabled = true;
          status.textContent = T('appraisal.loading');
          try {
            const source = await api(path + '/appraisal/grade-input' + queryTool(tool));
            if (!valid()) return;
            if (!source.available) throw Error(resultMessage(source, gradeUnavailable));
            const sameOutcome = row => row.comparison === combo.comparison && row.outcome === combo.outcome && row.timepoint === combo.timepoint;
            const keys = new Set(anEffects.filter(sameOutcome).map(effect => effect.source_key).filter(Boolean));
            const preview = await api(path + '/tables/sof?detail=1', {
              method: 'POST', body: { outcomes: [combo.outcome], initial_design: initialDesign, rob_tool: tool }
            });
            if (!valid()) return;
            results.replaceChildren(gradeResult(source, keys));
            for (const row of (preview.rows || []).filter(sameOutcome)) {
              const suggestion = row.detail?.grade?.suggestions?.rob;
              if (!suggestion?.signal?.available) continue;
              const apply = button('appraisal.use_prefill', () => {
                if (!valid()) return;
                prefill(suggestion.level, suggestion.signal);
                dialog.close();
              }, true);
              results.append(node('div', null, { class: 'row' }, raw('b', row.measure),
                tr('span', 'tables.level.' + suggestion.level), apply));
            }
            status.textContent = '';
          } catch (error) {
            if (valid()) status.textContent = error.message || T(gradeUnavailable);
          } finally {
            picker.disabled = false;
            inspect.disabled = !picker.value;
          }
        });
        inspect.disabled = true;
        picker.addEventListener('change', () => {
          results.replaceChildren();
          inspect.disabled = !picker.value;
        });
        body.replaceChildren(field('appraisal.tool', picker), inspect, status, results);
        status.textContent = '';
      } catch (error) {
        if (!valid()) return;
        status.textContent = error.message || T(gradeUnavailable);
        body.replaceChildren(status, button(retry, load));
      }
    }
    load();
  }

  function teamActions() {
    if (!canTeam || !toolId) return null;
    const id = toolId;
    const path = endpoint();
    const identity = scope();
    const box = node('section', null, { class: 'card' }, tr('h3', 'appraisal.team'));
    const body = node('div');
    const status = statusLine();
    const controls = node('div', null, { class: 'row' });
    let busy = false;
    const valid = () => scope() === identity && box.isConnected && id === toolId;
    async function read(kind, output) {
      if (busy || !valid()) return;
      busy = true;
      status.textContent = T('appraisal.loading');
      for (const control of controls.children) control.disabled = true;
      try {
        if (kind === 'export') {
          await fileResponse(path + '/export' + queryTool(id), {}, 'appraisal-' + S.task.task_id + '-' + id + '.csv', valid);
        } else {
          const data = await api(path + '/' + kind + queryTool(id));
          if (!valid()) return;
          if (!data.available) throw Error(resultMessage(data, unavailable));
          body.replaceChildren(output(data));
        }
        if (valid()) status.textContent = '';
      } catch (error) {
        if (valid()) status.textContent = error.message || T(unavailable);
      } finally {
        busy = false;
        for (const control of controls.children) control.disabled = false;
      }
    }
    if (runtimeMode === 'server')
      controls.append(button('appraisal.agreement', () => read('agreement', agreementResult)));
    controls.append(button('appraisal.export', () => read('export')),
      button('appraisal.grade_input', () => read('grade-input', gradeResult)));
    box.append(controls, status, body);
    return box;
  }

  function render(retryAction = null) {
    if (!active() || !catalog || !recommendation) return;
    const chooser = toolsSelect(catalog.tools, toolId);
    chooser.disabled = saving;
    chooser.addEventListener('change', () => loadTool(chooser.value));
    const messages = node('div', null, { role: 'status', 'aria-live': 'polite' }, raw('p', feedback, { class: 'hint' }));
    if (fieldError?.item_ids?.length) messages.append(raw('p', fieldError.item_ids.join(' · ')));
    if (retryAction) messages.append(button(retry, retryAction));
    const selected = catalog.tools.find(tool => tool.id === toolId);
    const form = editor();
    host.replaceChildren(raw('h2', article.title || article.zotero_key), field('appraisal.tool', chooser), messages,
      ...(form ? [form] : selected ? [citation(selected, true)] : []),
      recommendationCards(), linkedTools());
    const team = teamActions();
    if (team) host.append(team);
    updateProgress();
  }

  const style = document.createElement('style');
  style.textContent = `
    #rfAppraisal{flex:1;min-height:0;overflow:auto;padding:.6rem;overflow-wrap:anywhere}
    #rfAppraisal[hidden],.rf-assessment-tabs[hidden],#cbForms[hidden],#cbSave[hidden],.rf-appraisal-result[hidden]{display:none!important}
    .rf-appraisal-open #rfExtractPanel{display:none!important}
    .rf-assessment-tabs{margin:0;padding:.25rem .5rem;gap:.35rem}
    .rf-assessment-tabs button[aria-pressed=true]{color:var(--primary);border-color:var(--primary);font-weight:650}
    .rf-assessment-field{display:grid;gap:.3rem;margin:.5rem 0;min-width:0}
    .rf-assessment-field .input{width:100%;max-width:100%;min-width:0}
    .rf-assessment-table{overflow:auto;max-width:100%}
    .rf-assessment-table table{min-width:32rem}
    .rf-assessment-stack{display:grid;gap:.6rem}
    .rf-appraisal-editor,.rf-appraisal-items{min-width:0;margin:.6rem 0;padding:.65rem;border:1px solid var(--border);border-radius:var(--radius,8px)}
    .rf-appraisal-item{padding:.65rem 0;border-bottom:1px solid var(--border)}
    .rf-appraisal-item:last-child{border-bottom:0}
    .rf-appraisal-item [aria-invalid=true]{border:2px solid #ac3535}
    .rf-appraisal-citation{margin:.6rem 0;padding:.65rem;border:1px solid var(--border);border-radius:var(--radius,8px)}
    .rf-appraisal-citation p{margin:.35rem 0}
    .rf-appraisal-bias{padding:.15rem .4rem;border-radius:.35rem;display:inline-block}
    .rf-appraisal-bias-low{background:#e3f4ee;color:#225649}
    .rf-appraisal-bias-some_concerns{background:#faedcd;color:#654b12}
    .rf-appraisal-bias-high{background:#fde5df;color:#862e23}
    .rf-assessment-dialog{width:min(54rem,calc(100% - 1rem));max-height:calc(100dvh - 1rem);padding:1rem;border:1px solid var(--border);border-radius:12px;background:var(--panel,var(--page,#fff));color:var(--text);overflow:auto;overflow-wrap:anywhere}
    .rf-assessment-dialog::backdrop{background:rgba(10,15,18,.58)}
    .rf-assessment-dialog header{justify-content:space-between;margin-top:0}
    .rf-assessment-dialog fieldset{min-width:0;border:1px solid var(--border);padding:.65rem;margin:.7rem 0;border-radius:8px}
    .rf-assessment-dialog details{margin:.5rem 0}
    .rf-assessment-dialog select,.rf-assessment-dialog textarea{max-width:100%}
    .rf-tables-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,15rem),1fr));gap:.6rem}
    #rfTablesEntry{margin-top:.85rem;padding:.85rem}
    .rf-tables-cards button{width:100%}
    .rf-tables-choice{display:flex;align-items:baseline;gap:.55rem;padding:.2rem 0}
    .rf-tables-audit{margin:.6rem 0}
  `;
  document.head.append(style);
  showTab();
  document.addEventListener('reviewflow:analysis-task-changed', () => {
    permissionRequest = null;
    setArticle(null, 'personal');
    for (const dialog of document.querySelectorAll('dialog.rf-assessment-dialog[open]')) dialog.close();
  });
  document.addEventListener('reviewflow:workflow-changed', () => { permissionRequest = null; });
  document.addEventListener('reviewflow:language-changed', () => {
    appraisalTab.textContent = T('appraisal.title');
    codingTab.textContent = T('数据提取与编码');
    render();
  });

  window.AppraisalWorkspace = Object.freeze({
    setArticle, canAssess, openGrade, fileResponse, popup, grid, select, toolsSelect, node, raw, tr, button, field, statusLine
  });
})();
