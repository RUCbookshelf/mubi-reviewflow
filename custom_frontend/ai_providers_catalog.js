/* Shared provider and slot UI for both AI key entry points. */
(function () {
  'use strict';
  let catalogFlight, nextId = 0;
  const kinds = { llm: ['openai_chat', 'openai_responses', 'anthropic'], decision: ['systemone'] };
  function load() {
    if (!catalogFlight) catalogFlight = Promise.all([
      api('/api/ai/providers'),
      api('/api/runtime').catch(() => ({ mode: 'server' }))
    ]).then(([catalog, runtime]) => ({ ...catalog, server: runtime.mode !== 'desktop' }))
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
  function applyChoice(fields, entry, choice) {
    if (entry) {
      fields.provider.value = entry.id;
      fields.baseUrl.value = choice.base_url || '';
      fields.model.value = choice.models?.[0] || '';
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
    const element = el('div', {}, label, formatButton, chooser, list);
    const defaultFormat = kinds[kind][0];
    let currentFormat = kinds[kind].includes(format) ? format : defaultFormat;
    let catalog, selected = '';
    let providerAtChoice = provider.value;
    const draft = el('input', { type:'hidden', value:currentFormat, ...(draftName ? { 'data-rf-name':draftName } : {}) });
    element.append(draft);
    const getFormat = () => kinds[kind].includes(draft.value) ? draft.value : defaultFormat;
    const updateButton = () => { formatButton.textContent = T('选择 API 格式') + ' · ' + getFormat(); };
    const models = values => list.replaceChildren(...(values || []).map(value => el('option', { value })));
    updateButton();
    provider.addEventListener('input', () => {
      if (provider.value !== providerAtChoice) {
        selected = select.value = '';
        draft.value = defaultFormat;
        models([]);
        showChooser(false);
      }
      updateButton();
    });
    function choose(entry) {
      if (entry?.desktop_only && catalog.server) { select.value = selected; return; }
      const choices = formatChoices(catalog, entry, kind);
      const allowed = choices.filter(choice => choice.enabled);
      let picked = allowed.find(choice => choice.format === currentFormat) || allowed[0];
      const confirmChoice = el('button', { type:'button', class:'btn sm', text:T('确认') });
      confirmChoice.disabled = !picked;
      chooser.replaceChildren(el('legend', { text:T('选择 API 格式') }));
      for (const choice of choices) {
        const radio = el('input', { type:'radio', name:id, value:choice.format });
        radio.disabled = !choice.enabled;
        radio.checked = choice === picked;
        radio.addEventListener('change', () => { if (choice.enabled && radio.checked) picked = choice; });
        chooser.append(el('label', { class:'rf-ai-checks' }, radio, el('span', {
          text: choice.format + (choice.enabled ? '' : ' · ' + T('后端暂不支持此格式'))
        })));
      }
      confirmChoice.addEventListener('click', () => {
        if (!picked) return;
        // Reopening the chooser for the same format preserves manual edits.
        if (entry && selected === entry.id && provider.value === entry.id && currentFormat === picked.format) {
          currentFormat = picked.format;
        } else {
          currentFormat = applyChoice({ provider, baseUrl, model }, entry, picked);
        }
        draft.value = currentFormat;
        selected = select.value = entry?.id || '';
        providerAtChoice = provider.value;
        models(entry ? picked.models : []);
        showChooser(false);
        updateButton();
        formatButton.focus();
      });
      const cancel = el('button', { type:'button', class:'btn sm', text:T('取消') });
      cancel.addEventListener('click', () => { showChooser(false); select.value = selected; formatButton.focus(); });
      chooser.append(confirmChoice, cancel);
      showChooser(true);
      chooser.querySelector('input:checked')?.focus();
    }
    select.disabled = formatButton.disabled = true;
    select.append(el('option', { value:'', text:T('自定义…') }));
    formatButton.addEventListener('click', () => { if (catalog) choose(catalog.providers.find(entry => entry.id === selected)); });
    select.addEventListener('change', () => choose(catalog.providers.find(entry => entry.id === select.value)));
    load().then(result => {
      catalog = result;
      currentFormat = getFormat();
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
      const entry = entries.find(item => item.id === provider.value && !(item.desktop_only && catalog.server));
      selected = entry?.id || (provider.value ? '' : '-');
      select.value = selected;
      providerAtChoice = provider.value;
      models(entry?.formats.find(choice => choice.format === currentFormat)?.models);
      select.disabled = formatButton.disabled = false;
    }).catch(() => {
      // Keep manual configuration available even when the directory is down.
      catalog = { providers: [], supported_formats: Object.fromEntries(kinds[kind].map(value => [value, true])) };
      formatButton.disabled = false;
      label.hidden = true;
    });
    return { element, getFormat, draft };
  }
  function appendMeta(host, meta) {
    if (meta?.source === 'env') host.append(el('p', { class:'rf-ai-disclosure', text:T('管理员配置') }));
    if ((meta?.consecutive_failures ?? 0) >= 2) host.append(el('p', {
      class:'rf-ai-disclosure', role:'alert', text:T('最近 {0} 次调用失败，请检查钥匙或额度', meta.consecutive_failures)
    }));
    if (meta?.call_count || meta?.total_tokens || meta?.last_used_at) host.append(el('p', {
      class:'rf-ai-hint',
      text:T('调用 {0} 次 · 累计 {1} tokens', meta.call_count ?? 0, meta.total_tokens ?? 0) +
        (meta.last_used_at ? ' · ' + T('最近使用 {0}', meta.last_used_at) : '')
    }));
  }
  function testButton({ endpoint, kind, status }) {
    const button = el('button', { type:'button', class:'btn sm', text:T('测试连接') });
    button.addEventListener('click', async () => {
      button.disabled = true;
      button.textContent = status.textContent = T('测试中…');
      try {
        const result = await api(endpoint + '/test?kind=' + kind, { method:'POST' });
        status.textContent = T('连接正常 · 延迟 {0} ms', result.latency_ms ?? '—');
      } catch (error) {
        status.textContent = error?.status === 409 ? T('未配置可用的 AI 钥匙，请先在设置页配置。') :
          T('AI 服务调用失败：{0}', error?.message || '');
        if (typeof toast === 'function') toast(status.textContent, 'warn');
      } finally {
        button.disabled = false;
        button.textContent = T('测试连接');
      }
    });
    return button;
  }
  const helpers = { load, mount, slot, appendMeta, testButton, formatChoices, applyChoice };
  if (typeof window !== 'undefined') window.RFAIProviders = helpers;
  if (typeof module !== 'undefined') module.exports = helpers;
})();
