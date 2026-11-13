/* Preview-only community theme adaptations. No upstream CSS is distributed.
 * Suite and light/dark mode are independent preferences; the product owns mode. */
(function () {
  'use strict';
  if (window.RFThemeSuites) return;
  const root = document.documentElement;
  const storageKey = 'rf_theme_suite';
  const themes = Object.freeze([
    {id: 'default', name: '玉青', nameEn: 'Jade', zh: '原有默认 · 沉静玉青', en: 'Existing default · calm jade'},
    {id: 'minimal', name: '素简', nameEn: 'Quiet', zh: '克制灰阶 · 轻边界', en: 'Quiet neutrals · restrained borders'},
    {id: 'things', name: '澄蓝', nameEn: 'Clarity', zh: '明朗蓝色 · 柔和卡片', en: 'Clear blue · soft cards'},
    {id: 'anuppuccin', name: '柔彩', nameEn: 'Pastel', zh: '奶油与柔紫 · 圆润浮卡', en: 'Cream & mauve · rounded cards'},
    {id: 'blue-topaz', name: '蓝晶', nameEn: 'Azure', zh: '通透蓝调 · 清晰层次', en: 'Luminous blue · layered surfaces'},
    {id: 'nord', name: '霜境', nameEn: 'Frost', zh: '极地雾蓝 · 平整界面', en: 'Arctic blue · flat surfaces'},
    {id: 'obsidianite', name: '墨夜', nameEn: 'Nocturne', zh: '墨紫与青绿 · 微光边界', en: 'Ink violet & teal · luminous edges'},
    {id: 'atom', name: '石墨', nameEn: 'Graphite', zh: '编辑器灰 · 利落线条', en: 'Editor gray · crisp lines'}
  ].map(Object.freeze));
  const known = new Set(themes.map(theme => theme.id));
  const sources = Object.freeze({
    minimal: 'https://github.com/kepano/obsidian-minimal',
    things: 'https://github.com/colineckert/obsidian-things',
    anuppuccin: 'https://github.com/anubisnekhet/AnuPpuccin',
    'blue-topaz': 'https://github.com/pkm-er/Blue-Topaz_Obsidian-css',
    nord: 'https://github.com/insanum/obsidian_nord',
    obsidianite: 'https://github.com/bennyxguo/Obsidian-Obsidianite',
    atom: 'https://github.com/kognise/obsidian-atom'
  });
  let selected = 'default', gallery = null, frame = 0, builtLanguage = '', preferences = null;
  const preferenceMoves = new Map();
  const copy = {
    title: ['主题套系', 'Theme collection'],
    interfaceTitle: ['界面与主题', 'Interface & themes'],
    source: ['主题来源', 'Theme source'],
    intro: ['7 套社区经典风格适配，每套均有浅色与深色。', '7 adaptations of classic community styles, each with light and dark modes.'],
    current: ['当前使用', 'Current theme'],
    browse: ['浏览主题', 'Browse themes'],
    hide: ['收起主题', 'Hide themes'],
    reset: ['恢复默认主题', 'Restore default theme'],
    applied: ['已应用', 'Applied'],
    light: ['浅色', 'Light'],
    dark: ['深色', 'Dark'],
    mode: ['明暗模式', 'Color mode'],
    choose: ['选择主题套系', 'Choose a theme'],
    apply: ['应用', 'Apply'],
    note: ['选择后立即应用，并记住在当前浏览器；明暗模式沿用上方设置。', 'Applied immediately and remembered in this browser; the color mode follows the setting above.']
  };
  function language() {
    try { return typeof RFLang !== 'undefined' ? RFLang.get() : root.lang || 'zh-CN'; }
    catch (_) { return root.lang || 'zh-CN'; }
  }
  function tr(key) {
    const values = copy[key], lang = language();
    if (!values) return key;
    if (lang.startsWith('zh')) return values[0];
    // Reuse the product dictionary where it knows a label, then use English.
    if (typeof T === 'function') { const translated = T(values[0]); if (translated && translated !== values[0]) return translated; }
    return values[1];
  }
  function themeName(theme) { return language().startsWith('zh') ? theme.name : theme.nameEn; }
  function make(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  }
  function label(node, key) { node.dataset.rftText = key; node.textContent = tr(key); return node; }
  function icon(kind) {
    const paths = {
      palette: '<path d="M12 3a9 9 0 1 0 0 18h1a2 2 0 0 0 1-3.73 1.5 1.5 0 0 1 .75-2.77H17a4 4 0 0 0 4-4c0-4.42-4.03-7.5-9-7.5Z"/><circle cx="7.5" cy="10" r=".6"/><circle cx="10.5" cy="6.7" r=".6"/><circle cx="15" cy="7" r=".6"/>',
      reset: '<path d="M3 11a9 9 0 1 1 2.64 7M3 4v7h7"/>',
      check: '<path d="m5 12 4 4L19 6"/>',
      sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
      moon: '<path d="M20 14.4A8.5 8.5 0 0 1 9.6 4 8.5 8.5 0 1 0 20 14.4Z"/>',
      chevron: '<path d="m7 10 5 5 5-5"/>'
    };
    const holder = make('span', 'rft-icon');
    holder.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">' + paths[kind] + '</svg>';
    return holder;
  }
  function control(key, kind, className) {
    const button = make('button', 'rft-action ' + (className || ''));
    button.type = 'button';
    if (kind) button.append(icon(kind));
    button.append(label(make('span'), key));
    return button;
  }
  function miniature(theme, mode) {
    const wrap = make('span', 'rft-mini');
    wrap.dataset.rfPalette = theme.id; wrap.dataset.mode = mode; wrap.setAttribute('aria-hidden', 'true');
    const chrome = make('span', 'rft-mini-chrome');
    chrome.append(make('i'), make('i'), make('i'), label(make('span', 'rft-mini-mode'), mode));
    const workspace = make('span', 'rft-mini-workspace');
    const side = make('span', 'rft-mini-sidebar');
    side.append(make('i', 'rft-mini-brand'), make('i', 'rft-mini-nav on'), make('i', 'rft-mini-nav'), make('i', 'rft-mini-nav'));
    const content = make('span', 'rft-mini-content');
    content.append(make('i', 'rft-mini-heading'), make('i', 'rft-mini-heading short'));
    const paper = make('span', 'rft-mini-paper');
    paper.append(make('i', 'rft-mini-line'), make('i', 'rft-mini-line'), make('i', 'rft-mini-line short'));
    const decisions = make('span', 'rft-mini-decisions');
    decisions.append(make('i'), make('i'));
    paper.append(decisions); content.append(paper); workspace.append(side, content); wrap.append(chrome, workspace);
    return wrap;
  }
  function card(theme) {
    const item = make('article', 'rft-card');
    const button = make('button', 'rft-choice');
    button.type = 'button'; button.dataset.suite = theme.id; button.setAttribute('role', 'radio');
    const previews = make('span', 'rft-preview-pair');
    previews.append(miniature(theme, 'light'), miniature(theme, 'dark'));
    const title = make('span', 'rft-card-title');
    const name = make('strong', '', themeName(theme)); name.dataset.rftName = theme.id; title.append(name);
    const status = make('span', 'rft-card-status'); status.append(icon('check'), label(make('span'), 'applied'));
    title.append(status);
    const description = make('span', 'rft-card-description'); description.dataset.rftDescription = theme.id;
    button.append(previews, title, description);
    button.addEventListener('click', () => selectSuite(theme.id));
    item.append(button);
    if (sources[theme.id]) {
      const source = label(make('a', 'rft-source'), 'source');
      source.dataset.rftSource = theme.id; source.href = sources[theme.id];
      source.target = '_blank'; source.rel = 'noopener noreferrer';
      source.setAttribute('aria-label', themeName(theme) + ' — ' + tr('source'));
      item.append(source);
    }
    return item;
  }
  function mount() {
    const panel = document.querySelector('#wf-panel-appearance');
    if (!panel || gallery?.isConnected) return;
    gallery = make('section', 'rft-suite-library'); gallery.id = 'rfThemeSuites'; gallery.setAttribute('aria-labelledby', 'rfThemeSuitesTitle');
    const header = make('div', 'rft-library-header');
    const heading = make('div', 'rft-library-heading');
    const title = label(make('h3'), 'title'); title.id = 'rfThemeSuitesTitle';
    heading.append(title, label(make('p', 'rft-intro'), 'intro'));
    const reset = control('reset', 'reset'); reset.dataset.rftReset = ''; reset.addEventListener('click', () => selectSuite('default'));
    header.append(heading, reset);
    const toolbar = make('div', 'rft-library-toolbar');
    const current = make('div', 'rft-current'); current.append(icon('palette'), label(make('span'), 'current'), make('strong', 'rft-current-name'));
    const mode = make('div', 'rft-mode'); mode.setAttribute('role', 'group'); mode.dataset.rftLabel = 'mode';
    for (const value of ['light', 'dark']) {
      const button = control(value, value === 'light' ? 'sun' : 'moon'); button.dataset.rftMode = value;
      button.addEventListener('click', () => {
        const original = document.querySelector('#themeSel');
        if (original) { original.value = value; original.dispatchEvent(new Event('change', {bubbles: true})); }
      });
      mode.append(button);
    }
    const browse = control('browse', 'chevron', 'rft-browse'); browse.setAttribute('aria-expanded', 'false'); browse.setAttribute('aria-controls', 'rfThemeSuiteCatalog');
    toolbar.append(current, mode, browse);
    const catalog = make('div', 'rft-catalog'); catalog.id = 'rfThemeSuiteCatalog'; catalog.hidden = true;
    const choices = make('div', 'rft-choices'); choices.setAttribute('role', 'radiogroup'); choices.dataset.rftLabel = 'choose';
    for (const theme of themes) choices.append(card(theme));
    choices.addEventListener('keydown', event => {
      if (!event.target.matches('.rft-choice') || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const buttons = [...choices.querySelectorAll('.rft-choice')], index = buttons.indexOf(event.target);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (['ArrowRight', 'ArrowDown'].includes(event.key) ? 1 : -1) + buttons.length) % buttons.length;
      selectSuite(buttons[next].dataset.suite); buttons[next].focus();
    });
    catalog.append(choices);
    browse.addEventListener('click', () => {
      catalog.hidden = !catalog.hidden; browse.setAttribute('aria-expanded', String(!catalog.hidden));
      const text = browse.querySelector('[data-rft-text]'); label(text, catalog.hidden ? 'browse' : 'hide');
    });
    const notice = label(make('p', 'rft-preference-note'), 'note');
    const live = make('span', 'rft-sr-only'); live.setAttribute('role', 'status'); live.setAttribute('aria-live', 'polite'); live.dataset.rftLive = '';
    const preferenceHost = make('div', 'rft-native-preferences'); preferenceHost.hidden = true;
    const languageField = make('div', 'rft-native-language');
    const modeField = make('div', 'rft-mode-field'); modeField.append(label(make('span', 'rft-field-label'), 'mode'));
    preferenceHost.append(languageField, modeField);
    gallery.append(header, preferenceHost, toolbar, notice, catalog, live);
    panel.append(gallery);
    builtLanguage = ''; syncUI();
  }
  function movePreference(node, target, before = null, fallbackAnchorHost = null) {
    if (!node || node.parentNode === target) return;
    if (!preferenceMoves.has(node)) {
      const anchor = document.createComment('theme preferences origin');
      // The native mobile composer removes its temporary card on desktop.
      // Keep our fallback anchor in the stable pane, not inside that card.
      if (fallbackAnchorHost && node.closest('.rf-mobile-settings-card')) fallbackAnchorHost.insertBefore(anchor, fallbackAnchorHost.querySelector('#localStorageTitle'));
      else node.before(anchor);
      preferenceMoves.set(node, anchor);
    }
    target.insertBefore(node, before);
  }
  function restorePreferences() {
    if (!preferences) return;
    const {card: nativeCard, densityField, nativeModeField, panel} = preferences;
    for (const [node, anchor] of [...preferenceMoves].reverse()) {
      if (anchor.isConnected) anchor.replaceWith(node);
      else if (node.isConnected && gallery?.contains(node)) panel.insertBefore(node, gallery);
    }
    preferenceMoves.clear();
    nativeCard.removeAttribute('data-rft-preferences-owned');
    densityField?.classList.remove('rft-density-field'); nativeModeField?.classList.remove('rft-native-theme-field');
    gallery.classList.remove('rft-integrated'); gallery.querySelector('.rft-native-preferences').hidden = true;
    gallery.querySelector('.rft-library-toolbar').insertBefore(gallery.querySelector('.rft-mode'), gallery.querySelector('.rft-browse'));
    label(gallery.querySelector('#rfThemeSuitesTitle'), 'title');
    panel.append(gallery); preferences = null;
  }
  function composePreferences() {
    if (!gallery?.isConnected) return;
    if (!document.body.classList.contains('rf-layout-preview-compact')) { restorePreferences(); return; }
    const panel = gallery.closest('#wf-panel-appearance'), density = panel?.querySelector('#densitySel'), nativeCard = density?.closest('.card');
    if (!panel || !nativeCard) return;
    const host = gallery.querySelector('.rft-native-preferences'), languageField = host.querySelector('.rft-native-language');
    const languageSelect = panel.querySelector('select:has(option[value="zh-CN"])');
    const nativeModeField = panel.querySelector('#themeSel')?.parentElement;
    // Explicit ownership hand-off: the Home overlay gives up its original
    // move anchors so only this journal restores language/title controls.
    if (!preferences) {
      const handoff = window.RFHomeLayoutPreview?.releaseAppearancePreferences?.() || [];
      for (const {node, anchor} of handoff) preferenceMoves.set(node, anchor);
      preferences = {card: nativeCard, densityField: density.parentElement, nativeModeField, panel};
    }
    if (languageSelect && !languageField.contains(languageSelect)) {
      const languageLabel = languageSelect.previousElementSibling;
      if (languageLabel?.tagName === 'LABEL') movePreference(languageLabel, languageField, null, panel);
      movePreference(languageSelect, languageField, null, panel);
    }
    const heading = nativeCard.previousElementSibling;
    if (heading?.tagName === 'H3' && heading.id !== 'localStorageTitle') movePreference(heading, host);
    movePreference(nativeCard, host, host.querySelector('.rft-mode-field'));
    nativeCard.dataset.rftPreferencesOwned = 'true';
    density.parentElement.classList.add('rft-density-field'); nativeModeField?.classList.add('rft-native-theme-field');
    const mode = gallery.querySelector('.rft-mode'), modeField = host.querySelector('.rft-mode-field');
    if (mode.parentNode !== modeField) modeField.append(mode);
    host.hidden = false; gallery.classList.add('rft-integrated');
    const title = gallery.querySelector('#rfThemeSuitesTitle'); if (title.dataset.rftText !== 'interfaceTitle') label(title, 'interfaceTitle');
    const storageTitle = panel.querySelector(':scope > #localStorageTitle');
    if (storageTitle && gallery.nextElementSibling !== storageTitle) panel.insertBefore(gallery, storageTitle);
  }
  function syncUI() {
    if (!gallery?.isConnected) return;
    const lang = language(), current = themes.find(theme => theme.id === selected), mode = root.dataset.theme === 'dark' ? 'dark' : 'light';
    if (builtLanguage !== lang) {
      for (const node of gallery.querySelectorAll('[data-rft-text]')) {
        node.textContent = tr(node.dataset.rftText);
      }
      for (const node of gallery.querySelectorAll('[data-rft-label]')) node.setAttribute('aria-label', tr(node.dataset.rftLabel));
      for (const node of gallery.querySelectorAll('[data-rft-description]')) {
        const theme = themes.find(item => item.id === node.dataset.rftDescription); node.textContent = lang.startsWith('zh') ? theme.zh : theme.en;
      }
      for (const node of gallery.querySelectorAll('[data-rft-name]')) node.textContent = themeName(themes.find(theme => theme.id === node.dataset.rftName));
      for (const node of gallery.querySelectorAll('[data-rft-source]')) node.setAttribute('aria-label', themeName(themes.find(theme => theme.id === node.dataset.rftSource)) + ' — ' + tr('source'));
      builtLanguage = lang;
    }
    gallery.querySelector('.rft-current-name').textContent = themeName(current);
    gallery.querySelector('[data-rft-reset]').disabled = selected === 'default';
    for (const button of gallery.querySelectorAll('.rft-choice')) {
      const checked = button.dataset.suite === selected;
      button.setAttribute('aria-checked', String(checked)); button.tabIndex = checked ? 0 : -1;
      button.setAttribute('aria-label', themeName(themes.find(theme => theme.id === button.dataset.suite)) + ' — ' + tr(checked ? 'applied' : 'apply'));
    }
    for (const button of gallery.querySelectorAll('[data-rft-mode]')) button.setAttribute('aria-pressed', String(button.dataset.rftMode === mode));
  }
  function selectSuite(id, persist = true) {
    const next = known.has(id) ? id : 'default';
    const changed = next !== selected; selected = next;
    if (next === 'default') root.removeAttribute('data-rf-theme'); else root.dataset.rfTheme = next;
    if (persist) { try { if (next === 'default') localStorage.removeItem(storageKey); else localStorage.setItem(storageKey, next); } catch (_) {} }
    syncUI();
    if (changed && gallery?.isConnected) gallery.querySelector('[data-rft-live]').textContent = tr('applied') + ': ' + themeName(themes.find(theme => theme.id === next));
    if (changed) document.dispatchEvent(new CustomEvent('reviewflow:theme-suite-changed', {detail: {id: next}}));
    return next;
  }
  function schedule() {
    if (frame) return;
    frame = requestAnimationFrame(() => { frame = 0; mount(); composePreferences(); syncUI(); });
  }
  try { const saved = localStorage.getItem(storageKey); selectSuite(known.has(saved) ? saved : 'default', false); } catch (_) { selectSuite('default', false); }
  const observer = new MutationObserver(() => {
    if (!gallery?.isConnected) { schedule(); return; }
    const compact = document.body.classList.contains('rf-layout-preview-compact');
    const languageSelect = gallery.closest('#wf-panel-appearance')?.querySelector('select:has(option[value="zh-CN"])');
    if (compact && (!preferences || !gallery.contains(preferences.card) || languageSelect && !gallery.querySelector('.rft-native-language').contains(languageSelect)) || !compact && preferences) schedule();
  });
  observer.observe(document.body, {childList: true, subtree: true});
  new MutationObserver(schedule).observe(root, {attributes: true, attributeFilter: ['data-theme', 'lang']});
  document.addEventListener('reviewflow:language-changed', schedule);
  document.addEventListener('reviewflow:layout-preview-mode', schedule);
  matchMedia('(max-width:760px)').addEventListener('change', schedule);
  window.addEventListener('storage', event => { if (event.key === storageKey || event.key === null) selectSuite(event.newValue, false); });
  window.RFThemeSuites = Object.freeze({select: selectSuite, refresh: schedule, get selected() { return selected; }, themes});
  mount(); composePreferences();
})();
