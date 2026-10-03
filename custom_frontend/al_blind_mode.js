/* ReviewFlow · 盲筛模式（规格书 docs/handoff/2026-09-30-al-model-upgrade-spec.md §7.3）。
 *
 * 「盲筛模式」开关（默认开启）：
 *   - 开启：初筛界面不显示 AI 相关度分数与徽标（#scrBody 内 .al-score 分数条
 *     与 .al-tag「AI」徽标，主应用 renderScreen 每次重绘都渲染它们），文献仍按
 *     AI 排序呈现——顺序是模型输出，分数/徽标是其可视化，隐藏后者不改变队列；
 *   - 关闭：分数照常显示，并紧随分数条标注「AI 分数仅供参考，不应影响判断」。
 *     标注经 MutationObserver 在每次重绘后重挂（#scrBody 由 renderScreen 清空
 *     重建，见主应用 index.html renderScreen()），幂等：已在位则只同步文案。
 *   - 筛后审计：本开关只隐藏「初筛进行中」的分数展示，不改动任何数据；分析/
 *     导出页现有功能不受影响（分数数据仍在 /al/rank 响应与 S.al.scores 中）。
 *
 * 开关位置：设置（首页）「AI 排序设置」卡片内、保存按钮行之后。属界面偏好，
 * 仅存本机浏览器 localStorage（与排版方案/主题同一模式：rfpb_density/rfpb_theme），
 * 不写服务端——规格 §6 未定义对应端点，盲筛是个人显示偏好而非任务级设置。
 *
 * 实现方式（零侵入）：不改主应用任何既有行——
 *   - 隐藏：body.al-blind 类 + 注入样式（选择器限 #scrBody，不影响分析页等）；
 *   - 标注：追加式 DOM 注入 + MutationObserver，renderScreen 重绘后自动重挂；
 *   - 事件：reviewflow:language-changed 重填开关文案与标注（T() 合成句需手动
 *     重渲染）；不监听任务事件——盲筛偏好与任务无关，跨任务保持。
 *
 * 单点故障：初始化与全部回调自行捕获异常，任何失败只影响本开关，不波及主
 * 应用与 /api/health。本模块零网络请求。
 */
(function () {
  'use strict';
  try {
    if (typeof document === 'undefined' || typeof T !== 'function' || typeof el !== 'function') return;

    const KEY = 'rf_al_blind';           // 与 rfpb_theme/rfpb_density 同一 localStorage 命名族
    const NOTE = 'AI 分数仅供参考，不应影响判断';

    function readBlind() {
      /* 默认开（规格 §7.3）：仅显式存过 '0' 才关闭；隐私模式等读取失败也回落默认 */
      try { return localStorage.getItem(KEY) !== '0'; } catch (e) { return true; }
    }

    let blind = readBlind();

    /* ---- 样式（随模块注入；选择器限 #scrBody：只隐藏初筛卡片的分数/徽标，
       不影响其他页面或设置区自身）。.al-score{display:flex}/.al-tag{display:
       inline-block} 均自带 display，故隐藏规则需 !important。） ---- */
    const style = document.createElement('style');
    style.textContent = [
      'body.al-blind #scrBody .al-score,body.al-blind #scrBody .al-tag,body.al-blind #scrBody .al-blind-note{display:none !important}',
      '.al-blind-note{font-size:.74rem;color:var(--muted);margin:2px 0 2px}',
      '.al-blind-row{align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:0}',
      '.al-blind-row .lbl{display:flex;align-items:center;gap:8px;cursor:pointer;margin:0;white-space:nowrap}',
      '.al-blind-row input[type=checkbox]{accent-color:var(--primary);width:15px;height:15px;margin:0;cursor:pointer}',
    ].join('\n');
    (document.head || document.documentElement).appendChild(style);

    /* ---- 「仅供参考」标注（盲筛关闭时紧随分数条；幂等，不产生观察者回路） ---- */
    function annotate() {
      const body = document.getElementById('scrBody');
      if (!body) return;
      const score = body.querySelector(':scope > .al-score');
      if (!score) return;                       // 导入顺序 / 模型未就绪：本就无分数
      const text = T(NOTE);
      const note = score.nextElementSibling;
      if (note && note.classList.contains('al-blind-note')) {
        if (note.textContent !== text) note.textContent = text;   // 语言切换后同步
        return;
      }
      score.after(el('div', { class: 'al-blind-note', text }));
    }

    function apply() {
      document.body.classList.toggle('al-blind', blind);
      const body = document.getElementById('scrBody');
      if (body) { for (const n of body.querySelectorAll('.al-blind-note')) n.remove(); }
      if (!blind) annotate();
    }

    /* ---- 设置开关（注入「AI 排序设置」卡片；卡片缺失不致命：隐藏仍生效） ---- */
    const saveBtn = document.getElementById('alSaveSettings');
    const anchorRow = saveBtn ? (saveBtn.closest('.row') || saveBtn.parentElement) : null;
    if (anchorRow) {
      const box = el('input', { type: 'checkbox', id: 'alBlindMode', 'aria-label': T('盲筛模式') });
      const cap = el('label', { class: 'lbl', for: 'alBlindMode' }, box,
        el('span', { text: T('盲筛模式（默认开启）') }));
      const hint = el('span', {
        class: 'hint', style: 'margin:0;flex:1;min-width:220px',
        text: T('开启时初筛界面不显示 AI 相关度分数与徽标（文献仍按 AI 排序呈现）；关闭时显示分数并标注仅供参考。偏好仅存本机浏览器。'),
      });
      anchorRow.after(el('div', { class: 'row al-blind-row' }, cap, hint));
      box.checked = blind;
      box.addEventListener('change', () => {
        try {
          blind = box.checked;
          try { localStorage.setItem(KEY, blind ? '1' : '0'); } catch (e) { /* 隐私模式：仅本会话生效 */ }
          apply();
        } catch (e) { /* 单点故障 */ }
      });
    }

    /* ---- renderScreen 每次重绘（换篇/决策/切语言）后重挂标注 ---- */
    const scrBody = document.getElementById('scrBody');
    if (scrBody && typeof MutationObserver !== 'undefined') {
      new MutationObserver(() => { try { if (!blind) annotate(); } catch (e) { /* 单点故障 */ } })
        .observe(scrBody, { childList: true });   /* .al-score/标注都是 #scrBody 直接子节点 */
    }

    /* ---- 语言切换：重填开关文案（T() 合成句需手动重渲染）与标注 ---- */
    document.addEventListener('reviewflow:language-changed', () => {
      try {
        const box = document.getElementById('alBlindMode');
        if (box) box.setAttribute('aria-label', T('盲筛模式'));
        const label = document.querySelector('.al-blind-row label[for="alBlindMode"] > span');
        if (label) label.textContent = T('盲筛模式（默认开启）');
        const hint = document.querySelector('.al-blind-row .hint');
        if (hint) hint.textContent = T('开启时初筛界面不显示 AI 相关度分数与徽标（文献仍按 AI 排序呈现）；关闭时显示分数并标注仅供参考。偏好仅存本机浏览器。');
        if (!blind) annotate();
      } catch (e) { /* 单点故障 */ }
    });

    apply();   /* 立即生效：后续任何 renderScreen 输出的分数/徽标都会被样式覆盖 */
  } catch (e) { /* 单点故障：模块初始化失败不影响主应用与 /api/health */ }
})();
