/* W6 cluster ranking settings use the existing strategy and seed fields. */
(function () {
  'use strict';

  const strategy = document.getElementById('alStrategy');
  if (!strategy) return;
  const seed = document.getElementById('alSeed');
  const originalSeedTitle = RFLang.source(seed.title) || seed.title;
  const card = strategy.closest('.card');
  const heading = card?.previousElementSibling;
  const option = el('option', {value:'cluster', text:T('al.strategy.cluster'), title:T('al.strategy.cluster.hint')});
  strategy.append(option);
  const disclosure = el('p', {id:'rf-al-cluster-disclosure', class:'hint', hidden:''});
  document.getElementById('alSettingsInfo')?.parentElement.after(disclosure);
  strategy.setAttribute('aria-describedby', disclosure.id);

  function render() {
    if (heading?.tagName === 'H3') heading.textContent = T('al.settings.section_title');
    option.textContent = T('al.strategy.cluster');
    option.title = T('al.strategy.cluster.hint');
    disclosure.textContent = T('al.cluster.disclosure');
    const cluster = strategy.value === 'cluster';
    disclosure.hidden = !cluster;
    seed.title = T(cluster ? 'al.cluster.seedHint' : originalSeedTitle);
    window.RFALProfiles?.syncStrategy();
  }

  function renderModeLabel() {
    const button = document.getElementById('alModeAI');
    const cluster = S.al.strategy === 'cluster';
    button.querySelector('span').textContent = T(cluster ? 'al.strategy.cluster' : 'Machine Learning');
    button.title = T(cluster ? 'al.strategy.cluster.hint' :
      '机器学习排序：基于你的筛选决策，按当前策略排列待筛文献');
    window.RFALProfiles?.syncStrategy();
  }

  strategy.addEventListener('input', render);
  strategy.addEventListener('change', render);
  let settingsTask = null;
  for (const event of ['reviewflow:al-settings-loaded', 'reviewflow:al-settings-saved'])
    document.addEventListener(event, () => {
      settingsTask = S.task?.task_id;
      render();
      renderModeLabel();
    });
  document.addEventListener('reviewflow:language-changed', () => { render(); renderModeLabel(); });
  document.addEventListener('reviewflow:task-context-changed', () => { settingsTask = null; });
  document.addEventListener('reviewflow:workspace-changed', event => {
    if (event.detail === 'screen' && S.task && settingsTask !== S.task.task_id && !WorkflowControls.settingsDirty())
      void loadALSettingsUI();
  });
  window.RFTopicCluster = Object.freeze({renderModeLabel});
  render();
  renderModeLabel();
})();
