/* Activate the accepted workspace presentation after the authenticated module
   queue. This module never authenticates, selects a task, changes a route, or
   stores a layout preference. Theme/font choices retain their own persistence. */
(() => {
  'use strict';
  if (window.RFWorkspacePresentation) return;
  const main = document.getElementById('appMain');
  if (!main) return;
  const languages = ['zh-CN','en','fr','ru','es','ja','pt','de','sr','ko'];
  const copy = {
    hint: [
      '；也可开启题录关键词高亮，快速定位题名与摘要中的关注词。',
      '; you can also highlight keywords in titles and abstracts to find terms of interest.',
      ' ; vous pouvez aussi surligner les mots-clés dans les titres et résumés pour repérer les termes pertinents.',
      '; подсветка ключевых слов в названиях и аннотациях помогает находить важные термины.',
      '; también puedes resaltar palabras clave en títulos y resúmenes para localizar términos de interés.',
      '。題名と抄録のキーワードを強調表示して、注目する語句を探すこともできます。',
      '; também pode destacar palavras-chave nos títulos e resumos para encontrar termos de interesse.',
      '; außerdem können Sie Schlüsselwörter in Titeln und Abstracts hervorheben, um relevante Begriffe zu finden.',
      '; можете истаћи кључне речи у насловима и сажецима ради лакшег проналажења важних појмова.',
      '. 제목과 초록의 키워드를 강조하여 관심 있는 용어를 빠르게 찾을 수도 있습니다.'
    ],
    action: ['设置关键词高亮','Set up keyword highlighting','Configurer le surlignage','Настроить подсветку','Configurar resaltado','キーワード強調を設定','Configurar destaque','Hervorhebung einrichten','Подеси истицање речи','키워드 강조 설정']
  };
  let tipFrame = 0;
  function text(key) {
    const language = window.RFLang?.get?.() || document.documentElement.lang;
    return copy[key][Math.max(0,languages.indexOf(language))];
  }
  function keywordLaunch() {
    const label = typeof T === 'function' ? T('highlight.title') : '题录关键词高亮';
    return [...main.querySelectorAll('.page[data-p="screen"] button')].find(button =>
      !button.hasAttribute('data-rfpv-keyword-shortcut') && !button.hidden && !button.disabled && button.textContent.trim() === label);
  }
  function syncKeywordTip() {
    tipFrame = 0;
    for (const tip of main.querySelectorAll('.page[data-p="screen"] .rf-screen-ml-tip')) {
      const label = tip.querySelector(':scope > span');
      let hint = tip.querySelector('[data-rfpv-keyword-copy]');
      if (label && !hint) { hint = document.createElement('span'); hint.dataset.rfpvKeywordCopy = ''; label.append(hint); }
      if (hint && hint.textContent !== text('hint')) hint.textContent = text('hint');
      let shortcut = tip.querySelector('[data-rfpv-keyword-shortcut]');
      if (!shortcut) {
        shortcut = document.createElement('button'); shortcut.type = 'button'; shortcut.className = 'btn b-out sm';
        shortcut.dataset.rfpvKeywordShortcut = '';
        shortcut.addEventListener('click',() => keywordLaunch()?.click());
        tip.insertBefore(shortcut,tip.querySelector('.rf-screen-ml-tip-close'));
      }
      if (shortcut.textContent !== text('action')) shortcut.textContent = text('action');
      const disabled = !keywordLaunch(); if (shortcut.disabled !== disabled) shortcut.disabled = disabled;
    }
  }
  function scheduleKeywordTip() { if (!tipFrame) tipFrame = requestAnimationFrame(syncKeywordTip); }
  function activate() {
    // The native authentication owner controls this visibility. On logout,
    // mobile restores its moved controls and closes sheets before login paints.
    const signedOut = main.hidden || main.style.display === 'none';
    document.body.classList.toggle('rf-workspace-presentation-signedout',signedOut);
    if (signedOut) for (const dialog of document.querySelectorAll('.rfa-comment-dialog[open]')) {
      // Use the annotation owner's cancel path, which retains unsaved comments.
      dialog.dispatchEvent(new Event('cancel',{cancelable:true}));
    }
    document.body.classList.add('rf-layout-preview-compact');
    window.RFWorkflowLayoutPreview?.setEnabled(true);
    window.RFEvidenceLayoutPreview?.setEnabled(true);
    document.dispatchEvent(new CustomEvent('reviewflow:layout-preview-mode',{detail:{compact:true}}));
    scheduleKeywordTip();
  }
  const visibility = new MutationObserver(activate);
  visibility.observe(main,{attributes:true,attributeFilter:['style','hidden']});
  const tips = new MutationObserver(scheduleKeywordTip);
  tips.observe(main,{childList:true,subtree:true,attributes:true,attributeFilter:['hidden','disabled']});
  for (const event of ['reviewflow:language-changed','reviewflow:al-settings-loaded']) document.addEventListener(event,scheduleKeywordTip);
  document.addEventListener('reviewflow:workspace-changed',activate);
  window.RFWorkspacePresentation = Object.freeze({refresh:activate,get ready(){return document.body.classList.contains('rf-layout-preview-compact');}});
  activate();
})();
