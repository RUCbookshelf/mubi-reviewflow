/* ReviewFlow · AL 模型预设选择器（规格书 docs/handoff/2026-09-30-al-model-upgrade-spec.md §7.1）。
 *
 * 筛选页「AI 模型」设置区（位于 🧠 AI 开关旁）：
 *   - 下拉列出 GET /api/tasks/{id}/al/profiles 返回的预设（默认「经典模式」=
 *     响应的 default 字段）；选项文案/描述来自后端 profiles.py，经 T() 走字典。
 *   - 切换时必须弹确认框：「切换模型将重置 AI 排序，已有的筛选标签不受影响。」
 *     （§10 不变量 6：模型切换不得静默变更）；取消则回退下拉框、不发请求。
 *   - 确认后 PUT /api/tasks/{id}/al/profile（W11 路由，写 task.json 的 al_profile）。
 *
 * 事件接入（追加式，不改任何既有脚本）：
 *   - reviewflow:task-loaded：规格 §7.1 指定的事件名（主应用将来分发时即生效）；
 *   - reviewflow:task-context-changed：既有事件（pickTask 已分发，detail=task_id），
 *     作为今天的桥接，两路按 task_id 去重，重复触发不重置用户选择；
 *   - reviewflow:language-changed：重填下拉文案（T() 合成句需手动重渲染）。
 *
 * 单点故障：初始化与全部回调自行捕获异常，任何失败只影响本控件，
 * 不波及主应用与 /api/health。网络仅经全局 api() 访问本应用 API。
 */
(function () {
  'use strict';
  try {
    if (typeof document === 'undefined' || typeof T !== 'function' || typeof el !== 'function') return;

    /* 展示顺序：规格 §7.1 的列表顺序（经典/优化/中文优化/SVM）；
       后端将来新增的预设按响应键序追加在末尾。 */
    const PREFERRED_ORDER = ['legacy', 'tuned', 'cjk', 'svm'];
    let version = 0;            // 异步票据：任务切换后丢弃过期响应
    let handledTaskId;          // 已初始化下拉的任务（重复 task-loaded 去重）
    let current = null;         // 会话内经确认生效的预设（null=未设置→显示默认）
    let profiles = {};          // name -> {label, description}
    let defaultName = 'legacy';

    const page = document.querySelector('.page[data-p="screen"]');
    const modeRow = page ? page.querySelector('.al-mode') : null;
    const anchor = modeRow ? modeRow.querySelector('#alModeAI') : null;
    if (!modeRow || !anchor) return;   // 页面结构与预期不符（旧版/降级）：安静退出

    /* ---- 样式（随模块注入，不占用 index.html 既有样式段） ---- */
    const style = document.createElement('style');
    style.textContent = [
      '.al-profile-picker{display:inline-flex;align-items:center;gap:6px;font-size:.82rem;color:var(--muted);flex-wrap:wrap}',
      '.al-profile-picker .input{max-width:190px;font-size:.8rem;padding:4px 8px}',
      'body.fp-screen .al-profile-picker{flex-shrink:0}',
    ].join('\n');
    (document.head || document.documentElement).appendChild(style);

    /* ---- 控件（XSS 安全：全部 el()/textContent 构造） ---- */
    const cap = el('span', { class: 'al-profile-cap', text: T('AI 模型') });
    const select = el('select', { id: 'alProfileSel', class: 'input', disabled: '', 'aria-label': T('AI 模型') });
    const wrap = el('label', { class: 'al-profile-picker', for: 'alProfileSel', title: T('AI 模型预设：切换需确认，已有的筛选标签不受影响') },
      cap, select);
    anchor.after(wrap);
    const homeSelect = el('select', {id:'alHomeProfileSel',class:'input',disabled:'','aria-label':T('AI 模型')});
    const homeCap = el('span', {class:'lbl',text:T('AI 模型')});
    const homeField = el('label', {for:'alHomeProfileSel'}, homeCap, homeSelect);
    document.getElementById('alStrategy').closest('.row').prepend(homeField);
    const selectors = [select, homeSelect];
    const disable = value => selectors.forEach(node => { node.disabled = value; });

    function orderedNames() {
      const names = Object.keys(profiles);
      const head = PREFERRED_ORDER.filter(n => profiles[n]);
      const tail = names.filter(n => !PREFERRED_ORDER.includes(n));
      return head.concat(tail);
    }

    function renderOptions() {
      const selected = current || defaultName;
      for (const select of selectors) {
      select.textContent = '';
      for (const name of orderedNames()) {
        const p = profiles[name] || {};   // 防御：预设项缺失/为 null 时仍可渲染
        select.append(el('option', { value: name, text: T(p.label || name), title: T(p.description || '') }));
      }
      if (!select.options.length) select.append(el('option', { value: '', text: T('暂不可用') }));
      select.value = selected;
      const info = profiles[selected];
      select.title = info ? T(info.description || '') : '';
      }
      S.al.profileLabel = profiles[selected]?.label || '';
      renderALChrome();
    }

    /* ---- 任务载入：拉取预设清单与已保存选择（未设置即默认经典模式） ---- */
    function onTask(detail) {
      const taskId = (typeof S !== 'undefined' && S.task) ? S.task.task_id : (detail || null);
      if (!taskId || (typeof S !== 'undefined' && !S.task)) { version++; current = null; disable(true); handledTaskId = null; return; }
      if (handledTaskId === taskId) return;   // 同一任务重复事件：保留会话内选择
      handledTaskId = taskId;
      current = null;
      S.al.profileLabel = '';
      renderALChrome();
      const ticket = ++version;
      disable(true);
      if (typeof api !== 'function' || typeof taskApi !== 'function') return;
      api(taskApi() + '/al/profiles').then(d => {
        if (ticket !== version || (typeof S !== 'undefined' && S.task && S.task.task_id !== taskId)) return;
        profiles = (d && d.profiles) || {};
        current = (d && d.al_profile) || null;
        defaultName = (d && d.default) || 'legacy';
        if (!profiles[defaultName]) defaultName = orderedNames()[0] || 'legacy';
        renderOptions();
        disable(false);
      }).catch(() => { /* 拉取失败：保持禁用占位，不打扰主流程 */ });
    }

    /* ---- 切换：确认 → PUT；取消 → 回退（§10 不变量 6） ---- */
    for (const select of selectors) select.addEventListener('change', () => {
      try {
        const effective = current || defaultName;
        const next = select.value;
        if (next === effective || !next) return;
        const info = profiles[next] || {};
        const ok = confirm(T('切换模型将重置 AI 排序，已有的筛选标签不受影响。'));
        if (!ok) { select.value = effective; return; }
        const taskId = (typeof S !== 'undefined' && S.task) ? S.task.task_id : null;
        if (!taskId || typeof api !== 'function' || typeof taskApi !== 'function') { select.value = effective; return; }
        const ticket = ++version;
        const active = () => ticket === version && S.task?.task_id === taskId;
        disable(true);
        api(taskApi() + '/al/profile', { method: 'PUT', body: { profile: next } }).then(d => {
          if (!active()) return;
          current = (d && d.al_profile) || next;
          renderOptions();
          if (typeof refreshALRank === 'function') refreshALRank();
          const label = (d && d.label) || info.label || next;
          if (typeof toast === 'function') toast(T('AI 模型已切换：{0}', T(label)), 'inc');
        }).catch(e => {
          if (!active()) return;
          select.value = effective;
          if (typeof toast === 'function') toast(T('保存失败：') + ((e && e.message) || ''), 'warn');
        }).finally(() => { if (active()) disable(false); });
      } catch (e) { /* 单点故障：本控件异常不影响主应用 */ }
    });

    /* ---- 事件接入：规格事件 + 既有桥接事件（按任务去重） ---- */
    document.addEventListener('reviewflow:task-loaded', e => { try { onTask(e && e.detail); } catch (e2) {} });
    document.addEventListener('reviewflow:task-context-changed', e => { try { onTask(e && e.detail); } catch (e2) {} });
    document.addEventListener('reviewflow:language-changed', () => {
      try {
        cap.textContent = T('AI 模型');
        homeCap.textContent = T('AI 模型');
        homeSelect.setAttribute('aria-label', T('AI 模型'));
        wrap.title = T('AI 模型预设：切换需确认，已有的筛选标签不受影响');
        renderOptions();
      } catch (e2) {}
    });

    /* 首次加载时若任务已在（例如脚本晚于 pickTask 注册）：立即初始化一次 */
    if (typeof S !== 'undefined' && S.task) onTask(S.task.task_id);
  } catch (e) { /* 单点故障：模块初始化失败不影响主应用与 /api/health */ }
})();
