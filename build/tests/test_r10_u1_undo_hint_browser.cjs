'use strict';
/* r10-u1 · 初筛/首页/撤销泳道 —— 第二批真实浏览器行为测试
 * （Playwright · fixture 页加载**真实** custom_frontend/workspace_undo.js 与
 *   screen_reader.js，只桩掉网络与主应用状态）。
 *
 * 运行：
 *   REVIEWFLOW_PLAYWRIGHT=E:/rf214-playwright/node_modules/playwright \
 *   PLAYWRIGHT_BROWSERS_PATH=C:/Users/16073/AppData/Local/ms-playwright \
 *   node build/tests/test_r10_u1_undo_hint_browser.cjs
 *
 * 覆盖（破坏该条实现即红；每条都是本条修完才可能绿的破坏点）：
 *  · F25 撤销按钮旁**可见**快捷键提示（此前只有 title 悬停可见）：
 *      - <kbd> 是按钮的可见子节点，且文本随平台出 Ctrl+Z / ⌘Z；
 *      - sync() 反复重绘（忙碌态、语言切换）后 <kbd> 仍在——用 textContent
 *        整体覆盖会把它冲掉，这是本条最容易回归的点；
 *      - ≤760px 视口隐藏（那里没有物理键盘）；
 *      - title 与 aria-keyshortcuts 同步（屏幕阅读器与悬停两条腿都要有）。
 *  · F09（随版缓解）首次进初筛的一次性 ML 提示条：
 *      - 默认出现在卡片状态行上方，文案说明「可以切换为机器学习排序」；
 *      - 点「关闭」写入 localStorage 且立即移除，重载页面不再出现（限频）；
 *      - 点「试试机器学习排序」= 写标记 + 触发 ML 排序按钮（不当成纯关闭）；
 *      - 已存标记时初次渲染就不出现（对照组：不该每次都弹）。
 *  · F22 沿用标记走词典：标记串交给 T() 输出，切换语言后依旧是「词条译文」而
 *      不是硬编码中文；剥离时按 data 属性还原，不依赖当前语言的后缀匹配。
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.REVIEWFLOW_PLAYWRIGHT || 'playwright');

const root = path.resolve(__dirname, '..', '..');
const frontend = path.join(root, 'custom_frontend');

const FIXTURE = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#f4f8f7;--surface:#fff;--ink:#172b32;--muted:#60757c;--primary:#2f6f6a;--primary-dark:#1f4e4a;--primary-light:#e3f0ee;--border:#d5e3e1;--danger:#ac573e;--font-body:system-ui;--shadow:0 8px 22px rgba(0,0,0,.2)}
body{margin:0;background:var(--bg);color:var(--ink);font:16px system-ui}
button,input,select{font:inherit}
.btn{border:1px solid var(--border);background:var(--surface);border-radius:8px;padding:6px 10px;cursor:pointer}
.input{border:1px solid var(--border);border-radius:8px;padding:6px 8px;background:var(--surface)}
.card{border:1px solid var(--border);border-radius:12px;background:var(--surface);padding:16px}
.chip{border:1px solid var(--border);border-radius:999px;padding:1px 8px;font-size:12px}
kbd{background:var(--primary-light);color:var(--primary-dark);border-radius:4px;padding:0 5px;font-size:.75rem}
[hidden]{display:none!important}
</style></head><body><div id="appMain" class="view">
<section class="page on" data-p="screen">
  <h2>文献初筛</h2><p class="hint">键盘：1 纳入 · 2 待定 · 3 排除</p>
  <div class="al-mode">
    <button id="alModeImport" class="on"><span>导入顺序</span></button>
    <button id="alModeAI"><span>机器学习排序</span></button>
    <span class="chip al-trained" id="alBadge" style="display:none"></span>
    <span class="hint" id="alHint" style="margin:0"></span>
  </div>
  <div class="row scr-status"><span class="chip" id="scrPos">#1 / 2</span><span id="scrHist"></span><span id="scrHistCounts"></span></div>
  <div class="card" id="scrCard"><div id="scrBody"><div class="row scr-decisions">
    <select class="input" id="excReason"><option value="">—</option><option value="人群不符">人群不符</option></select>
    <input class="input" id="excCustomReason" hidden>
  </div></div></div>
</section>
<section class="page" data-p="fulltext"><div class="row"><button class="btn b-out" id="ftPrevDoc">‹</button></div></section>
<section class="page" data-p="bench"><div class="row"><button class="btn" id="cbSave">保存</button></div></section>
</div><div id="toasts"></div></body></html>`;

/* 本泳道模块用到的 T() 词条（只列测试要断言的几条；命中即返回译文，否则原样）。 */
const ZH = {
  '撤销上次保存': '撤销上次保存',
  '恢复上次保存前的状态并返回该文献': '恢复上次保存前的状态并返回该文献',
  '（沿用上次）': '（沿用上次）',
  '已沿用上次理由：{0}': '已沿用上次理由：{0}',
};
const EN = {
  '撤销上次保存': 'Undo last save',
  '恢复上次保存前的状态并返回该文献': 'Restore the state before the last save and reopen that article',
  '（沿用上次）': ' (carried over)',
  '已沿用上次理由：{0}': 'Carried over your last reason: {0}',
};

(async () => {
  const browser = await chromium.launch({headless: true});
  const context = await browser.newContext({viewport: {width: 1280, height: 900}});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));

  /* 真实 http 源：localStorage/sessionStorage 在不透明源会抛 Access is denied。 */
  await page.route('http://rf-u1b.local/**', route =>
    route.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: FIXTURE}));
  await page.goto('http://rf-u1b.local/fixture.html', {waitUntil: 'domcontentloaded'});

  /* 主应用契约桩：只桩网络与全局状态，业务实现全部来自产品源文件。 */
  await page.evaluate(() => {
    window.lang = 'zh';
    window.$ = (s, r) => (r || document).querySelector(s);
    window.$$ = (s, r) => Array.from((r || document).querySelectorAll(s));
    /* 词表在下方 addScriptTag 注入（page.evaluate 不能直接传函数对象），
       因此每次调用现取，不在闭包里快照。 */
    window.T = (tpl, ...args) => {
      const dict = (window.lang === 'en' ? window.__EN : window.__ZH) || {};
      const text = dict[String(tpl)] ?? String(tpl);
      return text.replace(/\{(\d+)\}/g, (m, d) => args[+d] === undefined ? m : String(args[+d]));
    };
    window.el = (tag, attrs, ...children) => {
      const node = document.createElement(tag);
      if (attrs) for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined) continue;
        if (k === 'class') node.className = v;
        else if (k === 'text') node.textContent = v;
        else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
        else node.setAttribute(k, v);
      }
      for (const c of children.flat(Infinity)) if (c !== null && c !== undefined) node.append(c);
      return node;
    };
    window.toast = msg => (window.toasts ||= []).push(String(msg));
    window.apiPendingWrites = 0;
    window.api = async () => ({latest: null});
    window.taskApi = () => '/api/tasks/t1';
    window.scrQ = () => '';
    window.curFt = () => null;
    window.refreshProgress = async () => {};
    window.renderScreen = async () => {};
    window.S = {
      user: {username: 'fixture', user_id: 1}, task: {task_id: 't1', name: 'T'}, screener: 'fixture',
      page: 'screen', articles: [{zotero_key: 'k1', title: 'A'}, {zotero_key: 'k2', title: 'B'}],
      decisions: {}, scrIdx: 0, screenEditingKey: null, screenLoadedTask: 't1', fulltextLoadedTask: 't1',
      al: {mode: 'import', available: false, nLabeled: 0, currentLabels: 0, minLabeled: 5, scores: {}, orderKeys: []},
    };
    window.WorkflowControls = {settings: () => ({}), guard: async () => true, reopen: async () => true, order: () => 'import'};
    window.WorkflowEvidence = {syncBusy: () => {}};
    window.RFStoppingGuidance = {close() {}};
    window.__tipClicks = 0;
    document.getElementById('alModeAI').addEventListener('click', () => { window.__tipClicks++; });
  });
  /* 词典在页面上下文里注入（page.evaluate 不能直接传函数）。 */
  await page.addScriptTag({content: `window.__ZH=${JSON.stringify(ZH)};window.__EN=${JSON.stringify(EN)};`});
  await page.addScriptTag({path: path.join(frontend, 'workspace_undo.js')});
  await page.waitForFunction(() => window.WorkspaceUndo);

  const check = async (name, fn) => { await fn(); console.log('PASS', name); };

  /* ---------------- F25：可见快捷键提示 ---------------- */
  await check('F25 撤销按钮内有可见 <kbd> 快捷键（不再只藏在 title 里）', async () => {
    const state = await page.evaluate(() => {
      const b = document.querySelector('[data-undo-stage="screen"]');
      const kbd = b.querySelector('kbd');
      return {text: b.textContent, hasKbd: !!kbd, kbdText: kbd?.textContent,
        visible: kbd ? getComputedStyle(kbd).display !== 'none' : false,
        title: b.getAttribute('title'), keyshortcuts: b.getAttribute('aria-keyshortcuts')};
    });
    assert.equal(state.hasKbd, true, 'F25：撤销按钮内必须有 <kbd> 快捷键提示（此前仅 title 悬停可见）');
    assert.match(state.kbdText, /^(Ctrl\+Z|⌘Z)$/, 'F25：提示必须写出实际快捷键，实为 ' + state.kbdText);
    assert.equal(state.visible, true, 'F25：桌面视口下提示必须可见');
    assert.match(state.text, /撤销上次保存/, 'F25：按钮文案不得被快捷键提示挤掉');
    assert.match(state.title, /Ctrl\+Z|⌘Z/, 'F25：title 里仍应保留快捷键（悬停路径）');
    assert.match(state.keyshortcuts, /^(Control|Meta)\+Z$/, 'F25：必须给屏幕阅读器 aria-keyshortcuts');
  });
  await check('F25 sync() 重绘后 <kbd> 仍在（textContent 整体覆盖会把它冲掉）', async () => {
    const state = await page.evaluate(() => {
      const b = document.querySelector('[data-undo-stage="screen"]');
      b.textContent = 'stale';            // 模拟被别的路径清空
      window.WorkspaceUndo.sync();        // 产品侧重绘
      return {hasKbd: !!b.querySelector('kbd'), text: b.textContent};
    });
    assert.equal(state.hasKbd, true, 'F25：sync() 必须重建 <kbd>，不能只写纯文本');
    assert.match(state.text, /撤销上次保存/);
  });
  await check('F25 语言切换后提示与文案一起重绘（不残留旧语言）', async () => {
    const state = await page.evaluate(() => {
      window.lang = 'en';
      document.dispatchEvent(new Event('reviewflow:language-changed'));
      const b = document.querySelector('[data-undo-stage="screen"]');
      return {text: b.textContent, kbd: b.querySelector('kbd')?.textContent};
    });
    assert.match(state.text, /Undo last save/, 'F25：语言切换必须重绘按钮文案');
    assert.match(state.kbd, /^(Ctrl\+Z|⌘Z)$/, 'F25：快捷键提示与语言无关，重绘后仍在');
    await page.evaluate(() => { window.lang = 'zh'; document.dispatchEvent(new Event('reviewflow:language-changed')); });
  });
  await check('F25 窄屏（≤760px）隐藏快捷键提示（那里没有物理键盘）', async () => {
    /* 断言的正是产品样式表里的媒体查询，因此注入真实 screen_reader.css。 */
    await page.addStyleTag({path: path.join(frontend, 'screen_reader.css')});
    await page.setViewportSize({width: 390, height: 780});
    await page.waitForTimeout(120);
    const narrow = await page.locator('[data-undo-stage="screen"] kbd').first()
      .evaluate(n => getComputedStyle(n).display);
    await page.setViewportSize({width: 1280, height: 900});
    await page.waitForTimeout(120);
    const wide = await page.locator('[data-undo-stage="screen"] kbd').first()
      .evaluate(n => getComputedStyle(n).display);
    assert.equal(narrow, 'none', 'F25：窄屏必须隐藏 <kbd>（screen_reader.css 的媒体查询）');
    assert.notEqual(wide, 'none', 'F25：宽屏必须恢复显示（否则提示等于没做）');
  });

  /* ---------------- F09：一次性 ML 提示条 ---------------- */
  await check('F09 首次进初筛出现可关闭提示条，文案说明可切 ML 排序', async () => {
    await page.addScriptTag({path: path.join(frontend, 'screen_reader.js')});
    await page.waitForSelector('.rf-screen-ml-tip', {timeout: 4000});
    const tip = page.locator('.rf-screen-ml-tip');
    assert.match(await tip.innerText(), /可以切换为机器学习排序/);
    const relation = await page.evaluate(() => {
      const tip = document.querySelector('.rf-screen-ml-tip');
      return {parent: tip.parentElement.className, beforeCard: tip.nextElementSibling?.className || '',
        statusAfter: !!tip.nextElementSibling?.classList.contains('rf-screen-ml-status')};
    });
    assert.equal(relation.statusAfter, true, 'F09：提示条必须紧贴卡片上方的状态行，不进弹层');
  });
  await check('F09 点「试试机器学习排序」既记限频又真的切到 ML 排序', async () => {
    const before = await page.evaluate(() => window.__tipClicks);
    await page.locator('.rf-screen-ml-tip .btn').click();
    await page.waitForTimeout(80);
    const state = await page.evaluate(() => ({
      clicks: window.__tipClicks,
      stored: localStorage.getItem('rf_screen_ml_tip'),
      gone: document.querySelectorAll('.rf-screen-ml-tip').length,
    }));
    assert.equal(state.clicks, before + 1, 'F09：「试试」必须真的触发 ML 排序按钮，而不是纯关闭');
    assert.equal(state.stored, '1', 'F09：必须写入限频标记');
    assert.equal(state.gone, 0, 'F09：点完必须移除提示条');
  });
  await check('F09 已存标记时初次渲染不再出现（限频生效，对照组）', async () => {
    const page2 = await context.newPage();
    const errs2 = [];
    page2.on('pageerror', e => errs2.push(e.message));
    await page2.route('http://rf-u1b.local/**', route =>
      route.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: FIXTURE}));
    await page2.goto('http://rf-u1b.local/fixture.html', {waitUntil: 'domcontentloaded'});
    await page2.evaluate(() => localStorage.setItem('rf_screen_ml_tip', '1'));
    await page2.evaluate(() => {
      window.$ = (s, r) => (r || document).querySelector(s);
      window.T = tpl => String(tpl);
      window.el = (tag, attrs, ...children) => {
        const node = document.createElement(tag);
        if (attrs) for (const [k, v] of Object.entries(attrs)) {
          if (v === null || v === undefined) continue;
          if (k === 'class') node.className = v;
          else if (k === 'text') node.textContent = v;
          else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
          else node.setAttribute(k, v);
        }
        for (const c of children.flat(Infinity)) if (c !== null && c !== undefined) node.append(c);
        return node;
      };
      window.S = {page: 'screen', articles: [], decisions: {}, scrIdx: 0, task: {task_id: 't1'}, al: {mode: 'import'}};
    });
    await page2.addScriptTag({path: path.join(frontend, 'screen_reader.js')});
    await page2.waitForTimeout(200);
    assert.equal(await page2.locator('.rf-screen-ml-tip').count(), 0,
      'F09：限频标记存在时不得再弹提示条');
    assert.deepEqual(errs2, []);
    await page2.close();
  });

  /* ---------------- F22：沿用标记走词典 ---------------- */
  await check('F22 沿用标记经 T() 输出：切到英文后是词典译文而非硬编码中文', async () => {
    const state = await page.evaluate(async () => {
      const select = document.getElementById('excReason');
      window.RFScreenReason.remember('人群不符');
      window.lang = 'en';
      window.RFScreenReason.decorate(select, document.getElementById('excCustomReason'));
      const carried = [...select.options].find(o => o.value === '人群不符');
      const text = carried.textContent;
      const withMark = text;
      // 改选后必须能精确剥离（按 data 属性还原，不依赖当前语言后缀）
      select.value = '';
      select.dispatchEvent(new Event('change', {bubbles: true}));
      const stripped = [...select.options].find(o => o.value === '人群不符').textContent;
      return {withMark, stripped, note: document.querySelector('.scr-reason-carry')?.textContent || '', lang: 'en'};
    });
    assert.match(state.withMark, /\(carried over\)$/, 'F22：英文界面下标记必须是词典译文，实为 ' + state.withMark);
    assert.doesNotMatch(state.withMark, /沿用上次/, 'F22：不得把中文标记硬拼在英文选项文案里');
    assert.equal(state.stripped, '人群不符', 'F22：剥离后必须精确还原原始文案（不留残留标记）');
  });

  assert.deepEqual(errors, [], '页面不得有未捕获异常');
  console.log(JSON.stringify({checks: 8, errors}));
  await browser.close();
})().catch(error => {
  console.error(String(error.stack || error));
  process.exit(1);
});
