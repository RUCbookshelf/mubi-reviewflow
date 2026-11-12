/* Shared provider and slot UI for both AI key entry points. */
(function () {
  'use strict';
  let catalogFlight, nextId = 0;
  const wizards = new WeakMap();
  const formatNames = { openai_chat:'聊天补全', openai_responses:'响应接口', anthropic:'消息接口', systemone:'决策接口' };
  const formatLabel = value => T(formatNames[value] || value) + ' (' + value + ')';
  const kinds = { llm: ['openai_chat', 'openai_responses', 'anthropic'], decision: ['systemone'] };
  function load() {
    if (!catalogFlight) catalogFlight = Promise.all([
      api('/api/ai/providers'),
      api('/api/runtime').catch(() => ({ mode: 'unknown' }))
    ]).then(([catalog, runtime]) => ({ ...catalog, server: runtime.mode !== 'desktop', runtimeUnknown:runtime.mode === 'unknown' }))
      .catch(error => { catalogFlight = null; throw error; });
    return catalogFlight;
  }
  function slot(view, kind) {
    return view?.[kind] || (view?.configured && (view.kind || 'llm') === kind ? view : null);
  }
  function formatChoices(catalog, entry, kind) {
    const choices = entry ? entry.formats : kinds[kind].map(format => ({ format, models: [] }));
    return choices.map(choice => ({ ...choice,
      enabled: kinds[kind].includes(choice.format) && catalog.supported_formats?.[choice.format] === true
    }));
  }
  function applyChoice(fields, entry, choice, previous) {
    if (entry) {
      fields.provider.value = entry.id;
      if (!previous || previous.provider !== entry.id || !fields.baseUrl.value || fields.baseUrl.value === previous.baseUrl) fields.baseUrl.value = choice.base_url || '';
      if (!previous || previous.provider !== entry.id || !fields.model.value || fields.model.value === previous.model) fields.model.value = choice.models?.[0] || '';
    }
    return choice.format;
  }
  function mount({ kind, provider, baseUrl, model, format, draftName }) {
    const id = 'rf-ai-models-' + ++nextId;
    const select = el('select', { class: 'input', 'aria-label': T('厂商') });
    const label = el('label', { class: 'lbl' }, el('span', { text: T('厂商') }), select);
    const list = el('datalist', { id });
    model.setAttribute('list', id);
    const formatButton = el('button', { type:'button', class:'btn sm' });
    const chooser = el('fieldset', { class:'rf-ai-checks' });
    const showChooser = visible => {
      chooser.hidden = !visible;
      chooser.setAttribute('style', visible ? '' : 'display:none');
    };
    showChooser(false);
    const element = el('div', { 'data-rf-wizard':draftName || id }, label, formatButton, chooser, list);
    const defaultFormat = kinds[kind][0];
    let currentFormat = kinds[kind].includes(format) ? format : defaultFormat;
    let catalog, selected = '', applied = null, pendingFormat = '', restored = null, dirty = false;
    let providerAtChoice = provider.value;
    const draft = el('input', { type:'hidden', value:currentFormat, ...(draftName ? { 'data-rf-name':draftName } : {}) });
    element.append(draft);
    const directoryStatus = el('div', { class:'hint' });
    element.append(directoryStatus);
    const rawFormat = () => kinds[kind].includes(draft.value) ? draft.value : defaultFormat;
    const getFormat = () => {
      if (selected && selected !== '-' && provider.value !== selected) throw new Error(T('请先确认所选厂商的 API 格式。'));
      return rawFormat();
    };
    const updateButton = () => { formatButton.textContent = T('选择 API 格式') + ' · ' + formatLabel(rawFormat()); };
    const syncProviderField = () => {
      const preset = !!selected && selected !== '-';
      provider.readOnly = preset;
      const row = provider.closest?.('label');
      if (row) row.hidden = preset;
    };
    const models = values => list.replaceChildren(...(values || []).map(value => el('option', { value })));
    updateButton();
    provider.addEventListener('input', () => {
      dirty = true;
      if (provider.value !== providerAtChoice) {
        selected = select.value = '';
        // A manually named endpoint may still use Anthropic or Responses.
        // Never silently reset its selected protocol to chat completions.
        applied = null;
        models([]);
        showChooser(false);
      }
      updateButton();
      syncProviderField();
    });
    function apply(entry, picked) {
      currentFormat = applyChoice({ provider, baseUrl, model }, entry, picked, applied);
      draft.value = currentFormat;
      selected = select.value = entry?.id || '';
      providerAtChoice = provider.value;
      applied = entry ? { provider:entry.id, baseUrl:picked.base_url || '', model:picked.models?.[0] || '' } : null;
      pendingFormat = '';
      models(entry ? picked.models : []);
      showChooser(false);
      updateButton();
      syncProviderField();
    }
    function choose(entry, explicit = false) {
      if (entry?.desktop_only && catalog.server) { select.value = selected; return; }
      const choices = formatChoices(catalog, entry, kind);
      const allowed = choices.filter(choice => choice.enabled);
      let picked = allowed.find(choice => choice.format === (pendingFormat || rawFormat())) || allowed[0];
      if (entry && allowed.length === 1 && !explicit) { apply(entry, picked); return; }
      pendingFormat = picked?.format || '';
      const confirmChoice = el('button', { type:'button', class:'btn sm', text:T('确认') });
      confirmChoice.disabled = !picked;
      chooser.replaceChildren(el('legend', { text:T('选择 API 格式') }));
      if (!entry) chooser.append(el('p', { class:'hint', text:T('自定义服务商：请选择实际使用的 API 格式，地址与模型需手动填写。') }));
      for (const choice of choices) {
        const radio = el('input', { type:'radio', name:id, value:choice.format });
        radio.disabled = !choice.enabled;
        radio.checked = choice === picked;
        radio.addEventListener('change', () => { if (choice.enabled && radio.checked) { picked = choice; pendingFormat = choice.format; } });
        chooser.append(el('label', { class:'rf-ai-checks' }, radio, el('span', {
          text: formatLabel(choice.format) + (choice.enabled ? '' : ' · ' + T('后端暂不支持此格式'))
        })));
      }
      confirmChoice.addEventListener('click', () => {
        if (!picked) return;
        apply(entry, picked);
        formatButton.focus();
      });
      const cancel = el('button', { type:'button', class:'btn sm', text:T('取消') });
      cancel.addEventListener('click', () => { showChooser(false); select.value = selected; pendingFormat = ''; formatButton.focus(); });
      chooser.append(confirmChoice, cancel);
      showChooser(true);
      chooser.querySelector('input:checked')?.focus();
    }
    select.disabled = formatButton.disabled = true;
    select.append(el('option', { value:'', text:T('自定义…') }));
    formatButton.addEventListener('click', () => { dirty = true; if (catalog) choose(catalog.providers.find(entry => entry.id === selected), true); });
    select.addEventListener('change', () => { dirty = true; selected = select.value; pendingFormat = ''; syncProviderField(); choose(catalog.providers.find(entry => entry.id === selected)); });
    const restore = state => {
      if (!state) return;
      restored = state;
      dirty = !!state.dirty;
      draft.value = state.format;
      if (!catalog) return;
      const entry = catalog.providers.find(item => item.id === state.selected);
      selected = select.value = entry ? entry.id : state.selected === '-' ? '-' : '';
      applied = state.applied;
      pendingFormat = state.pendingFormat;
      providerAtChoice = provider.value;
      models(entry?.formats.find(choice => choice.format === rawFormat())?.models);
      syncProviderField(); updateButton();
      if (state.open) choose(entry, true); else showChooser(false);
    };
    wizards.set(element, { snapshot:() => ({ selected, format:rawFormat(), applied, pendingFormat, open:!chooser.hidden, dirty }), restore });
    const retryDirectory = () => {
      restored = wizards.get(element).snapshot();
      catalogFlight = null;
      select.disabled = formatButton.disabled = true;
      directoryStatus.replaceChildren();
      initialize();
    };
    const showDirectoryStatus = text => {
      directoryStatus.setAttribute('role', 'status');
      const retry = el('button', { type:'button', class:'btn sm', text:T('重试') });
      retry.addEventListener('click', retryDirectory);
      directoryStatus.replaceChildren(el('span', { text:T(text) }), retry);
    };
    function initialize() { load().then(result => {
      catalog = result;
      label.hidden = false;
      currentFormat = rawFormat();
      updateButton();
      const entries = catalog.providers.filter(entry => entry.kinds.includes(kind));
      select.replaceChildren(el('option', { value:'-', text:T('请选择厂商'), disabled:true }));
      for (const entry of entries) {
        const disabled = !!(entry.desktop_only && catalog.server);
        const option = el('option', { value:entry.id, text:T(entry.name_key) + (disabled ? ' · ' + T('仅桌面版') : '') });
        option.disabled = disabled;
        select.append(option);
      }
      select.append(el('option', { value:'', text:T('自定义…') }));
      const normalize = value => String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
      const entry = entries.find(item => [item.id, T(item.name_key)].some(value => normalize(value) === normalize(provider.value)) && !(item.desktop_only && catalog.server));
      selected = entry?.id || (provider.value ? '' : '-');
      select.value = selected;
      if (entry) provider.value = entry.id;
      providerAtChoice = provider.value;
      const initialChoice = entry?.formats.find(choice => choice.format === currentFormat);
      models(initialChoice?.models);
      applied = entry ? { provider:entry.id, baseUrl:initialChoice?.base_url || '', model:initialChoice?.models?.[0] || '' } : null;
      select.disabled = formatButton.disabled = false;
      syncProviderField();
      if (restored) restore(restored);
      if (catalog.runtimeUnknown) showDirectoryStatus('运行模式暂不可用，桌面专属厂商暂不可选。');
    }).catch(() => {
      // Keep manual configuration available even when the directory is down.
      catalog = { providers: [], supported_formats: Object.fromEntries(kinds[kind].map(value => [value, true])) };
      formatButton.disabled = false;
      label.hidden = true;
      selected = '';
      syncProviderField();
      showDirectoryStatus('厂商目录暂不可用，可手动填写配置。');
      if (restored) restore(restored);
    }); }
    initialize();
    return { element, getFormat, draft, markClean:() => { dirty = false; } };
  }
  function snapshotWizards(host) {
    const result = {};
    for (const element of host?.querySelectorAll('[data-rf-wizard]') || []) {
      const wizard = wizards.get(element);
      if (wizard) result[element.getAttribute('data-rf-wizard')] = wizard.snapshot();
    }
    return result;
  }
  function restoreWizards(host, snapshots) {
    if (!snapshots) return;
    for (const element of host?.querySelectorAll('[data-rf-wizard]') || []) wizards.get(element)?.restore(snapshots[element.getAttribute('data-rf-wizard')]);
  }
  function appendMeta(host, meta) {
    if (meta?.source === 'env') host.append(el('p', { class:'rf-ai-disclosure', text:T('管理员配置') }));
    if ((meta?.consecutive_failures ?? 0) >= 2) host.append(el('p', {
      class:'rf-ai-disclosure', role:'alert', text:T('最近 {0} 次调用失败，请检查 API Key 或额度', meta.consecutive_failures)
    }));
    if (meta?.call_count || meta?.total_tokens || meta?.last_used_at) host.append(el('p', {
      class:'rf-ai-hint',
      text:T('调用 {0} 次 · 累计 {1} tokens', meta.call_count ?? 0, meta.total_tokens ?? 0) +
        (meta.last_used_at ? ' · ' + T('最近使用 {0}', meta.last_used_at) : '')
    }));
  }
  function testButton({ endpoint, kind, status }) {
    const button = el('button', { type:'button', class:'btn sm', text:T('测试已保存配置'), title:T('仅测试服务器中已保存的配置；未保存的输入不会参与测试。') });
    button.addEventListener('click', async () => {
      button.disabled = true;
      button.textContent = status.textContent = T('测试中…');
      try {
        const result = await api(endpoint + '/test?kind=' + kind, { method:'POST' });
        status.textContent = T('已保存配置连接正常 · 延迟 {0} ms', result.latency_ms ?? '—');
      } catch (error) {
        status.textContent = error?.status === 409 ? T('未配置可用的 API Key，请先在设置页配置。') :
          T('AI 服务调用失败：{0}', error?.message || '');
        if (typeof toast === 'function') toast(status.textContent, 'warn');
      } finally {
        button.disabled = false;
        button.textContent = T('测试已保存配置');
      }
    });
    return button;
  }
  const helpers = { load, mount, slot, appendMeta, testButton, formatChoices, applyChoice, snapshotWizards, restoreWizards };
  if (typeof window !== 'undefined') window.RFAIProviders = helpers;
  if (typeof module !== 'undefined') module.exports = helpers;
})();
