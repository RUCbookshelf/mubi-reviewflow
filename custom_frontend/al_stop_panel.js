/* ReviewFlow · 停止建议面板（召回证书 UI；规格书 2026-09-30 §7.2，API=W11 §6.2/§6.3 路由）。
 *
 * 筛选页底部「停止建议」面板：
 *   - 常显两行（数字全部来自只读端点 GET /api/tasks/{id}/al/stop-certificate/check）：
 *       「已筛 1,234 / 5,678 条（21.8%）」「已发现 23 篇相关文献」+ 进度条；
 *   - 到达 look 点（50/70/85/95%）且本点未完成过抽验：建议随机抽验——
 *     「从剩余 {pool} 条中随机抽取 {m} 条进行筛选」（m=ceil(池×φ)，φ=0.30），
 *     给 [开始抽验] 按钮：随机选 m 条**置顶队列**（§10 不变量 3：抽样永远是
 *     用户手动操作，本模块只调整展示顺序，不代做任何决策）；
 *   - 抽验完成（抽满 m 条判读）：按证书语义判定（与 coscreen/al/
 *     stop_certificate.py 的 should_stop 同口径）——量足且 0 条新相关 →
 *     「证书通过 ✅ …」；发现新相关 → 「证书未通过 ❌ …请继续筛选」；
 *   - streak 降级为信息条：「已连续排除 N 条 · 仅进度提示」（§5.2）。
 *
 * 事件接入（追加式）：
 *   - reviewflow:screening-loaded：规格 §7.2 事件名（将来分发即生效）；
 *   - reviewflow:workspace-changed(detail='screen')：既有桥接——进入初筛页后
 *     轮询 S.screenLoadedTask（loadScreen 完成标志），就绪即激活（最多 ~15s）；
 *   - reviewflow:task-loaded / task-context-changed：任务切换 → 复位面板状态；
 *   - #scrBody childList 变化（每次 renderScreen 重绘）→ 400ms 节流本地刷新 +
 *     ≥2s 节流的网络刷新（每次决策后进度推进）。
 * AI 模式兼容：refreshALRank→applyAIOrder 每次决策后重建队列，会冲掉置顶的
 * 抽验条目；本模块以追加方式包装 window.applyAIOrder（不改签名/返回值），
 * 在原函数返回后把尚未判读的抽验条目重新置顶。
 * 单点故障：初始化与全部回调自行捕获异常；仅经全局 api() 访问本应用 API。
 */
(function () {
  'use strict';
  try {
    if (typeof document === 'undefined' || typeof T !== 'function' || typeof el !== 'function') return;

    const page = document.querySelector('.page[data-p="screen"]');
    const card = page ? document.getElementById('scrCard') : null;
    if (!page || !card) return;   // 结构不符：安静退出

    /* ---- 样式（随模块注入） ---- */
    const style = document.createElement('style');
    style.textContent = [
      '.al-stop-panel{margin-top:10px;padding:10px 14px;font-size:.85rem}',
      '.al-stop-panel h3{margin:0 0 2px;font-size:.9rem}',
      '.al-stop-panel .pg{margin:6px 0 2px}',
      '.al-stop-look,.al-stop-result{margin-top:8px}',
      '.al-stop-look p,.al-stop-result p{margin:0 0 6px}',
      '.al-stop-verdict{font-weight:600}',
      '.al-stop-verdict.pass{color:var(--primary-dark)}',
      '.al-stop-verdict.fail{color:var(--danger)}',
      'body.fp-screen .al-stop-panel{flex-shrink:0;margin-top:8px}',
    ].join('\n');
    (document.head || document.documentElement).appendChild(style);

    /* ---- 面板骨架（hidden 直到当前任务的队列载入完成） ---- */
    const title = el('h3', { text: T('停止建议') });
    const barLine = el('i');
    const statusLine = el('p', { class: 'hint', style: 'margin:2px 0 0' });
    const foundLine = el('p', { class: 'hint', style: 'margin:2px 0 0' });
    const nextLine = el('p', { class: 'hint', style: 'margin:2px 0 0;flex-shrink:0' });
    const lookBox = el('div', { class: 'al-stop-look', hidden: '' });
    const resultBox = el('div', { class: 'al-stop-result', hidden: '' });
    const streakLine = el('p', { class: 'hint', style: 'margin:6px 0 0;flex-shrink:0' });
    const startBtn = el('button', { class: 'btn b-out', type: 'button', text: T('开始抽验') });
    const panel = el('div', { class: 'card al-stop-panel', id: 'alStopPanel', hidden: '' },
      title,
      el('div', { class: 'pg' }, el('div', { class: 'bar' }, barLine)),
      statusLine, foundLine, lookBox, resultBox, streakLine, nextLine);
    card.after(panel);

    /* ---- 状态 ---- */
    let version = 0;        // 网络票据
    let trackedTask = null; // 当前面板所属任务
    let lastFetchAt = 0;    // 网络节流（决策重绘频繁，≥2s 一次）
    let data = null;        // 最近一次 check 响应
    let spot = null;        // 进行中的抽验 {taskId, look, m, kT, keys[]}
    let doneLook = -1;      // 已完成抽验的最高 look 点（更高 look 才再次建议）
    let result = null;      // 已出具的判定 {look, pass, newRelevant, kT, m}

    function safe(fn) { return function () { try { fn.apply(this, arguments); } catch (e) { /* 单点故障 */ } }; }
    function fmt(n) { return String(Math.max(0, n | 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ','); }
    function taskId() { return (typeof S !== 'undefined' && S.task) ? S.task.task_id : null; }

    /* ---- 任务切换：复位 ---- */
    function onTask() {
      const id = taskId();
      if (id === trackedTask) return;
      trackedTask = id;
      version++;
      data = null; spot = null; result = null; doneLook = -1;
      panel.hidden = true;
      if (id && (typeof S !== 'undefined' && S.page === 'screen')) waitForScreen();
    }

    /* ---- 桥接：等待 loadScreen 完成（S.screenLoadedTask 对齐当前任务） ---- */
    function waitForScreen() {
      let tries = 0;
      (function poll() {
        try {   /* setTimeout 回调脱离 safe() 包裹，自行捕获（冒烟测试断言零未捕获异常） */
          const id = taskId();
          if (id && (typeof S !== 'undefined' && S.screenLoadedTask === id)) { onScreeningReady(); return; }
          if (++tries > 60) return;                 // ~15s：无任务/加载失败则放弃
          setTimeout(poll, 250);
        } catch (e) { /* 单点故障 */ }
      })();
    }

    function onScreeningReady() {
      const id = taskId();
      if (!id) { panel.hidden = true; return; }
      trackedTask = id;
      panel.hidden = false;
      refresh(true);
    }

    /* ---- 只读检查（comparison/outcome/timepoint 为评审问题上下文，初筛层无此
       维度，按 W11 契约传空串原样回显；screener 与主应用同口径） ---- */
    function refresh(force) {
      const id = taskId();
      if (!id || typeof api !== 'function' || typeof taskApi !== 'function') { panel.hidden = true; return; }
      if (typeof S !== 'undefined' && S.screenLoadedTask !== id) { renderPanel(); return; }
      const now = Date.now();
      if (!force && now - lastFetchAt < 2000) { renderPanel(); return; }
      lastFetchAt = now;
      const ticket = ++version;
      let q = '?comparison=&outcome=&timepoint=';
      if (typeof S !== 'undefined' && S.screener) q += '&screener=' + encodeURIComponent(S.screener);
      api(taskApi() + '/al/stop-certificate/check' + q).then(d => {
        if (ticket !== version || taskId() !== id) return;
        data = d || null;
        renderPanel();
      }).catch(() => { /* 检查失败：保留上次内容，不打扰筛选 */ });
    }

    /* ---- 抽验条目状态（判读数 / 新发现相关数，来自本地 S.decisions） ---- */
    function spotCounts() {
      if (!spot || typeof S === 'undefined' || !S.decisions) return null;
      let judged = 0, newRelevant = 0;
      for (const key of spot.keys) {
        const d = S.decisions[key];
        if (d) { judged++; if (d.decision === 'include') newRelevant++; }
      }
      return { judged, newRelevant };
    }

    function finalizeSpot(counts) {
      result = { look: spot.look, pass: counts.newRelevant === 0, newRelevant: counts.newRelevant, kT: spot.kT, m: spot.m };
      doneLook = Math.max(doneLook, spot.look);
      spot = null;
    }

    /* ---- 渲染（纯本地：data + spot + result + S.al.streak）。
       整体自行捕获：本函数会被 setTimeout / MutationObserver / promise 回调
       调用（均脱离 safe() 包裹），任何渲染异常只影响本面板。 ---- */
    function renderPanel() {
      try {
        renderPanelBody();
      } catch (e) { /* 单点故障 */ }
    }

    function renderPanelBody() {
      if (!data) return;
      const total = data.total_records | 0;
      const screened = data.screened_count | 0;
      const found = data.found_relevant | 0;
      const frac = Number(data.progress_fraction) || 0;
      title.textContent = T('停止建议');
      statusLine.textContent = T('已筛 {0} / {1} 条（{2}%）', fmt(screened), fmt(total), (frac * 100).toFixed(1));
      foundLine.textContent = T('已发现 {0} 篇相关文献', fmt(found));
      barLine.style.width = (total > 0 ? Math.min(100, screened / total * 100) : 0).toFixed(1) + '%';

      const look = (data.look_fraction === null || data.look_fraction === undefined) ? null : Number(data.look_fraction);
      const atLook = !!(data.at_look_point && look !== null);
      let m = atLook ? (data.required_sample_size | 0) : 0;

      /* 抽验进行中 → 完成即判定（幂等：finalize 后 spot=null） */
      const counts = spotCounts();
      if (spot && counts && counts.judged >= spot.m) finalizeSpot(counts);

      /* 建议区：进行中显示进度；到达新 look 点且未在此点抽验过显示建议 */
      if (spot && counts) {
        lookBox.hidden = false;
        startBtn.hidden = true;
        lookBox.replaceChildren(
          el('p', { text: T('抽验进行中：已判读 {0} / {1} 条，其中新发现相关 {2} 条', String(counts.judged), String(spot.m), String(counts.newRelevant)) }),
          el('p', { class: 'hint', style: 'margin:0', text: T('抽验条目已置于队列顶部，请按正常流程逐条判读。') }));
      } else if (atLook && m > 0 && doneLook < look) {
        lookBox.hidden = false;
        startBtn.hidden = false;
        startBtn.textContent = T('开始抽验');
        lookBox.replaceChildren(
          el('p', { text: T('建议进行随机抽验：从剩余 {0} 条中随机抽取 {1} 条进行筛选', fmt(data.pool_size), fmt(m)) }),
          startBtn);
      } else lookBox.hidden = true;

      /* 结果区：通过 / 未通过（文案=规格 §7.2 示例） */
      if (result) {
        resultBox.hidden = false;
        const verdict = result.pass
          ? el('p', { class: 'al-stop-verdict pass', text: T('证书通过 ✅ 以 ≥95% 置信度，总召回 ≥95%（剩余相关 ≤ {0} 条）', String(result.kT)) })
          : el('p', { class: 'al-stop-verdict fail', text: T('证书未通过 ❌ 抽验中发现 {0} 条新的相关文献，请继续筛选', String(result.newRelevant)) });
        resultBox.replaceChildren(verdict);
      } else resultBox.hidden = true;

      /* streak 降级为进度提示（§5.2：不再作为停止依据） */
      const streak = (typeof S !== 'undefined' && S.al && S.al.streak) ? S.al.streak : 0;
      streakLine.textContent = streak > 0 ? T('已连续排除 {0} 条 · 仅进度提示', String(streak)) : '';

      const next = (data.next_look_fraction === null || data.next_look_fraction === undefined) ? null : Number(data.next_look_fraction);
      nextLine.textContent = (!atLook && next !== null) ? T('下一检查点：已筛 {0}%', String(Math.round(next * 100))) : '';
    }

    /* ---- [开始抽验]：随机选 m 条置顶队列（抽样=用户手动，§10 不变量 3） ---- */
    function shuffle(arr) {
      for (let i = arr.length - 1; i > 0; i--) {
        let r;
        try {
          const buf = new Uint32Array(1);
          (window.crypto || crypto).getRandomValues(buf);
          r = buf[0] / 4294967296;
        } catch (e) { r = Math.random(); }
        const j = Math.floor(r * (i + 1));
        const t = arr[i]; arr[i] = arr[j]; arr[j] = t;
      }
      return arr;
    }

    startBtn.addEventListener('click', safe(() => {
      if (spot || !data || !data.at_look_point) return;
      const id = taskId();
      if (!id || typeof S === 'undefined' || S.screenLoadedTask !== id || !Array.isArray(S.articles)) return;
      let m = data.required_sample_size | 0;
      const undecided = S.articles.filter(a => a && a.zotero_key && !(S.decisions[a.zotero_key]));
      if (m <= 0 || !undecided.length) return;
      if (m > undecided.length) m = undecided.length;   // 竞态兜底：以实际未判条目为准
      const picked = shuffle(undecided.slice()).slice(0, m);
      const pickedSet = new Set(picked.map(a => a.zotero_key));
      S.articles = picked.concat(S.articles.filter(a => !pickedSet.has(a.zotero_key)));
      spot = { taskId: id, look: Number(data.look_fraction), m, kT: data.k_t | 0, keys: picked.map(a => a.zotero_key) };
      S.scrIdx = (typeof nextUndecided === 'function') ? nextUndecided() : 0;
      if (typeof renderScreen === 'function') renderScreen();
      if (typeof toast === 'function') toast(T('已随机抽取 {0} 条置于队列顶部，请逐条判读完成抽验', String(m)), 'inc');
      renderPanel();
    }));

    /* ---- AI 模式兼容：包装 applyAIOrder，重排后把未判读的抽验条目重新置顶 ---- */
    if (typeof window.applyAIOrder === 'function') {
      const original = window.applyAIOrder;
      window.applyAIOrder = function () {
        const out = original.apply(this, arguments);
        try { promoteSpot(); } catch (e) { /* 单点故障 */ }
        return out;
      };
    }
    function promoteSpot() {
      if (!spot || typeof S === 'undefined' || !Array.isArray(S.articles)) return;
      const pending = spot.keys.filter(k => !S.decisions[k]);
      if (!pending.length) return;
      const pendingSet = new Set(pending);
      const byKey = new Map(S.articles.map(a => [a.zotero_key, a]));
      const front = pending.map(k => byKey.get(k)).filter(Boolean);
      if (!front.length) return;
      const rest = S.articles.filter(a => !pendingSet.has(a.zotero_key));
      const changed = front.some((a, i) => S.articles[i] !== a);
      S.articles = front.concat(rest);
      const idx = (typeof nextUndecided === 'function') ? nextUndecided() : 0;
      if (changed || idx !== S.scrIdx) {
        S.scrIdx = idx;
        if (typeof renderScreen === 'function') renderScreen();
      }
    }

    /* ---- 决策重绘钩子：#scrBody 每次重绘（renderScreen 清空重建）→ 节流刷新 ---- */
    const scrBody = document.getElementById('scrBody');
    if (scrBody && typeof MutationObserver !== 'undefined') {
      let timer = 0;
      new MutationObserver(() => {
        try {   /* 观察器/定时器回调脱离 safe() 包裹，自行捕获 */
          clearTimeout(timer);
          timer = setTimeout(() => {
            try { renderPanel(); refresh(false); } catch (e) { /* 单点故障 */ }
          }, 400);
        } catch (e) { /* 单点故障 */ }
      }).observe(scrBody, { childList: true });
    }

    /* ---- 事件接入：规格事件 + 既有桥接 ---- */
    document.addEventListener('reviewflow:screening-loaded', safe(onScreeningReady));
    document.addEventListener('reviewflow:workspace-changed', safe(e => { if (e && e.detail === 'screen') waitForScreen(); }));
    document.addEventListener('reviewflow:task-loaded', safe(onTask));
    document.addEventListener('reviewflow:task-context-changed', safe(onTask));
    document.addEventListener('reviewflow:language-changed', safe(renderPanel));

    /* 首次加载时任务已就绪且停在初筛页（hash 直达）：立即激活 */
    if (taskId() && (typeof S !== 'undefined' && S.page === 'screen')) onTask();
  } catch (e) { /* 单点故障：模块初始化失败不影响主应用与 /api/health */ }
})();
