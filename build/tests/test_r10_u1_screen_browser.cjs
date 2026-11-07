'use strict';
/* r10-u1 · 初筛/首页/撤销泳道 —— 真实浏览器行为测试（Playwright · 隔离 fixture 页）。
 *
 * 运行：
 *   REVIEWFLOW_PLAYWRIGHT=E:/rf214-playwright/node_modules/playwright \
 *   PLAYWRIGHT_BROWSERS_PATH=C:/Users/16073/AppData/Local/ms-playwright \
 *   node build/tests/test_r10_u1_screen_browser.cjs
 *
 * 覆盖（每条对应批准单一个条目；破坏该条实现即红）：
 *  · F21 焦点在排除理由下拉时按 1/2 不得提交决策；按 3 照常提交；自定义理由框内
 *        Enter = 排除、数字键不触发。被测源码是 index.html 里**逐字抽取**的 keydown 块。
 *  · F08 无理由按 3 时卡片区出现文字说明，2 秒后自动消失。
 *  · F22 「沿用上次理由」默认值 + 可见提示行 + 选项后缀；回看已保存文献（
 *        S.screenEditingKey 非空）时不套用默认值。
 *  · F24（本文件只钉「不带按钮」这一面；can_manage=true 下的动作项取值、
 *        还原与可撤销 toast 在 test_r10_u1_reason_save_browser.cjs 用真实
 *        workflow_controls.js 实测，因为那需要 settings.can_manage 分支）。
 *  · F25 alBadge/alHint 脱离默认隐藏的阅读帮助弹层，改为卡片上方常驻状态行。
 *  · F28 「…」是按钮，点开「全部已决文献」只读浮层。
 *  · F59 重排期间出现「排序更新中…」指示，落定即移除。
 *  · F09 顺序标签旁常显「当前：…」。
 *  · F26 分数文案是「预测纳入概率 …%」并带样本数，不再叫「机器学习相关度」。
 *  · F12 批注面板开合后 PDF 容器短暂获得过渡类。
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.REVIEWFLOW_PLAYWRIGHT || 'playwright');

const root = path.resolve(__dirname, '..', '..');
const frontend = path.join(root, 'custom_frontend');
const html = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');

/* 逐字抽取 index.html 的全局 keydown 块（F21 的被测对象），找不到即红。
   抽取的是从 `document.addEventListener('keydown'` 到其函数体收尾 `}` 的整段，
   末尾补 `);` 还原成可执行语句——被测代码逐字来自产品源文件，不是复写件。 */
function extractKeydownBlock(source) {
  const marker = "document.addEventListener('keydown', e => {\n  if (e.isComposing) return;";
  const start = source.indexOf(marker);
  assert.ok(start >= 0, 'index.html 中找不到 F21 的全局 keydown 块（锚点被改动即红）');
  let depth = 0, i = source.indexOf('{', start);
  for (; i < source.length; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}') { depth--; if (!depth) return source.slice(start, i + 1) + ');'; }
  }
  throw new Error('keydown 块未闭合');
}
const keydownBlock = extractKeydownBlock(html);

const FIXTURE = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#f4f8f7;--surface:#fff;--ink:#172b32;--muted:#60757c;--primary:#2f6f6a;--primary-dark:#1f4e4a;--primary-light:#e3f0ee;--border:#d5e3e1;--danger:#ac573e;--font-body:system-ui}
body{margin:0;background:var(--bg);color:var(--ink);font:16px system-ui}
.view{height:100vh;overflow:auto;padding:16px;box-sizing:border-box}
button,input,select{font:inherit}.row{display:flex;gap:8px;flex-wrap:wrap}
.btn{border:1px solid var(--border);background:var(--surface);border-radius:8px;padding:6px 10px;cursor:pointer}
.input{border:1px solid var(--border);border-radius:8px;padding:6px 8px;background:var(--surface)}
.card{border:1px solid var(--border);border-radius:12px;background:var(--surface);padding:16px}
.chip{border:1px solid var(--border);border-radius:999px;padding:1px 8px;font-size:12px}
.shake{animation:none}[hidden]{display:none!important}
</style></head><body><div id="appMain" class="view">
<section class="page on" data-p="screen">
  <h2>文献初筛</h2><p class="hint">键盘：1 纳入 · 2 待定 · 3 排除</p>
  <div class="al-mode">
    <button id="alModeImport" class="on"><span>导入顺序</span></button>
    <button id="alModeAI"><span>机器学习排序</span></button>
    <span class="chip al-trained" id="alBadge" style="display:none"></span>
    <span class="hint" id="alHint" style="margin:0"></span>
  </div>
  <div class="row scr-status"><span class="chip" id="scrPos">—</span><span class="hint" id="scrHistCap">决策历史：</span><span id="scrHist"></span><span id="scrHistCounts"></span></div>
  <div class="card" id="scrCard"><div id="scrBody"></div></div>
</section>
<div id="pdfWrap" class="pdfstage" style="height:120px"><div class="pdfViewer"><div class="page">page</div></div></div>
<button id="ftSideToggle">✎ 批注</button><aside id="ftSide"></aside>
</div><div id="toasts"></div></body></html>`;

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1280, height: 900}});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.setContent(FIXTURE);

  /* 与主应用同签名的桩：el/T/toast/S/screenDecide/WorkflowControls。 */
  await page.evaluate(() => {
    window.$ = (s, r) => (r || document).querySelector(s);
    window.$$ = (s, r) => Array.from((r || document).querySelectorAll(s));
    window.T = (tpl, ...args) => String(tpl).replace(/\{(\d+)\}/g, (m, d) => args[+d] === undefined ? m : String(args[+d]));
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
    window.toasts = [];
    window.toast = (msg, kind) => { window.toasts.push({msg: String(msg), kind}); };
    window.S = {
      user: {username: 'fixture', user_id: 1}, task: {task_id: 't1'}, screener: 'fixture', page: 'screen',
      articles: [], decisions: {}, scrIdx: 0, screenEditingKey: null,
      al: {mode: 'import', available: false, nLabeled: 0, currentLabels: 0, minLabeled: 5, scores: {}, orderKeys: []}
    };
    window.decisions = [];
    window.screenDecide = d => window.decisions.push(d);
    window.renderScreen = async () => {};
    window.WorkflowControls = {guard: async () => true, canSave: () => true, settings: () => ({})};
    window.RFStoppingGuidance = {close(){}};
  });

  /* ① 注入真实 screen_reader.js（本泳道模块）。 */
  await page.addScriptTag({path: path.join(frontend, 'screen_reader.js')});

  /* ② 注入 index.html 的 keydown 块（F21 的被测代码）。 */
  await page.addScriptTag({content: keydownBlock});

  const check = async (name, fn) => { await fn(); console.log('PASS', name); };
  const pressOn = async (selector, key) => {
    await page.locator(selector).focus();
    await page.evaluate(() => { window.decisions.length = 0; });
    await page.keyboard.press(key);
    await page.waitForTimeout(60);
    return page.evaluate(() => window.decisions.slice());
  };

  /* 渲染一张文献卡：与 renderScreen 同构的决策行 + 理由下拉 + 自定义框。 */
  const renderCard = async ({reasons = [], saved = '', editingKey = null, score = null} = {}) => {
    await page.evaluate(({reasons, saved, editingKey, score}) => {
      const body = document.getElementById('scrBody');
      body.textContent = '';
      S.screenEditingKey = editingKey;
      if (!S.articles.length) S.articles = [{zotero_key: 'k1', title: 'Fixture Article One'}, {zotero_key: 'k2', title: 'Fixture Article Two'}];
      const reason = el('select', {class: 'input', id: 'excReason', style: 'max-width:210px'},
        el('option', {value: '', text: '— 排除理由（必选）—'}),
        ...reasons.map(r => el('option', {value: r, text: r})),
        el('option', {value: '__custom__', text: '其他（自定义）'}));
      reason.value = saved || '';
      const custom = el('input', {class: 'input', id: 'excCustomReason', placeholder: '请输入自定义排除理由', hidden: ''});
      reason.addEventListener('change', () => { custom.hidden = reason.value !== '__custom__'; });
      const row = el('div', {class: 'row scr-decisions', style: 'margin-bottom:0'},
        el('button', {class: 'btn b-inc', id: 'btnInc', text: '1 纳入', onclick: () => screenDecide('include')}),
        el('button', {class: 'btn b-may', id: 'btnMay', text: '2 待定', onclick: () => screenDecide('maybe')}),
        el('button', {class: 'btn b-exc', id: 'btnExc', text: '3 排除', onclick: () => screenDecide('exclude')}),
        reason, custom);
      const nodes = [el('h3', {class: 'scr-title', text: S.articles[0].title})];
      if (score !== null) nodes.push(el('div', {class: 'al-score'},
        el('div', {class: 'bar'}, el('i', {style: 'width:50%'})),
        el('span', {class: 'num', text: T('预测纳入概率 {0}%', (score * 100).toFixed(1))}),
        el('span', {class: 'al-score-basis', text: T('基于 {0} 条已标注', S.al.nLabeled)})));
      nodes.push(row);
      body.append(...nodes);
      window.RFScreenReason?.decorate(reason, custom);
      document.getElementById('scrPos').textContent = '#1 / 2';
    }, {reasons, saved, editingKey, score});
    await page.waitForTimeout(80);
  };

  /* ---------------- F21 ---------------- */
  await renderCard({reasons: ['人群不符', '干预不符']});
  await check('F21 下拉聚焦按 1 不提交（原实现会立即存为纳入）', async () => {
    assert.deepEqual(await pressOn('#excReason', '1'), [], '下拉内按 1 仍在提交决策');
  });
  await check('F21 下拉聚焦按 2 不提交', async () => {
    assert.deepEqual(await pressOn('#excReason', '2'), []);
  });
  await check('F21 下拉聚焦按 3 照常提交排除', async () => {
    assert.deepEqual(await pressOn('#excReason', '3'), ['exclude']);
  });
  await check('F21 下拉聚焦时 1/2 加修饰键仍可提交（可访问性出口）', async () => {
    await page.locator('#excReason').focus();
    await page.evaluate(() => { window.decisions.length = 0; });
    await page.keyboard.press('Control+1');
    await page.waitForTimeout(60);
    assert.deepEqual(await page.evaluate(() => window.decisions.slice()), ['include']);
  });
  await check('F21 自定义理由框内 Enter 提交排除、数字键不提交（原实现两条都无反应）', async () => {
    await renderCard({reasons: ['人群不符']});
    await page.locator('#excReason').selectOption('__custom__');
    await page.locator('#excCustomReason').fill('自定义理由');
    assert.deepEqual(await pressOn('#excCustomReason', '3'), [], '输入框内数字键不应触发决策');
    assert.deepEqual(await pressOn('#excCustomReason', 'Enter'), ['exclude']);
  });
  await check('F21 普通输入控件内仍不触发决策（对照组）', async () => {
    await page.evaluate(() => { const i = el('input', {id: 'plainInput'}); document.body.append(i); });
    assert.deepEqual(await pressOn('#plainInput', '1'), []);
  });

  /* ---------------- F08 ---------------- */
  await check('F08 无理由按 3 出现可见文字说明并在 2 秒后消失', async () => {
    await renderCard({reasons: ['人群不符']});
    await page.locator('#excReason').focus();
    await page.evaluate(() => window.RFScreenReason.missing('select'));
    const text = await page.locator('.scr-reason-error').textContent();
    assert.equal(text, '请先选择排除理由');
    assert.equal(await page.locator('#excReason').evaluate(n => n === document.activeElement), true, '焦点必须留在理由控件');
    await page.waitForTimeout(2400);
    assert.equal(await page.locator('.scr-reason-error').count(), 0, '说明应在 2 秒后自动清除');
  });

  /* ---------------- F22 ---------------- */
  await check('F22 记住上次理由，下一篇带可见提示与选项后缀', async () => {
    await renderCard({reasons: ['人群不符', '干预不符']});
    await page.evaluate(() => window.RFScreenReason.remember('干预不符'));
    await renderCard({reasons: ['人群不符', '干预不符']});
    assert.equal(await page.locator('#excReason').inputValue(), '干预不符', '未沿用上次理由');
    assert.match(await page.locator('.scr-reason-carry').textContent(), /已沿用上次理由：干预不符/);
    const selectedText = await page.locator('#excReason').evaluate(n => n.options[n.selectedIndex].textContent);
    assert.match(selectedText, /（沿用上次）$/, '默认项必须带视觉标记');
  });
  await check('F22 下拉改选后提示行消失（默认值不会被静默沿用）', async () => {
    await page.locator('#excReason').selectOption('人群不符');
    await page.waitForTimeout(60);
    assert.equal(await page.locator('.scr-reason-carry').count(), 0);
    const text = await page.locator('#excReason').evaluate(n => n.options[n.selectedIndex].textContent);
    assert.doesNotMatch(text, /沿用上次/);
  });
  await check('F22 回看已保存文献时不套用默认值（对照组）', async () => {
    await page.evaluate(() => window.RFScreenReason.remember('干预不符'));
    await renderCard({reasons: ['人群不符', '干预不符'], editingKey: 'k2'});
    assert.equal(await page.locator('#excReason').inputValue(), '');
    assert.equal(await page.locator('.scr-reason-carry').count(), 0);
  });
  await check('F22 已在位理由优先于默认值（已保存理由不被覆盖）', async () => {
    await renderCard({reasons: ['人群不符', '干预不符'], saved: '人群不符'});
    assert.equal(await page.locator('#excReason').inputValue(), '人群不符');
  });
  await check('F22 理由多于 8 条时出现过滤输入', async () => {
    await renderCard({reasons: Array.from({length: 9}, (_, i) => `理由${i + 1}`)});
    assert.equal(await page.locator('.scr-reason-filter').count(), 1);
  });

  /* ---------------- F28 ---------------- */
  await check('F28 「…」按钮打开全部已决文献只读浮层，且按决策类型筛选', async () => {
    await page.evaluate(() => {
      S.decisions = {k1: {decision: 'include', screened_at: '2026-10-30T10:00:00'}, k2: {decision: 'exclude', screened_at: '2026-10-31T11:00:00', exclusion_reason: '人群不符'}};
      S.articles = [{zotero_key: 'k1', title: 'Fixture Article One'}, {zotero_key: 'k2', title: 'Fixture Article Two'}];
      document.getElementById('scrCard').dispatchEvent(new Event('noop'));
      window.RFScreenHistory.open();
    });
    await page.waitForSelector('.rf-hist-dialog[open]');
    assert.equal(await page.locator('.rf-hist-row').count(), 2);
    await page.locator('.rf-hist-dialog select').selectOption('exclude');
    assert.equal(await page.locator('.rf-hist-row').count(), 1, '决策类型筛选必须生效');
    assert.match(await page.locator('.rf-hist-row .rf-hist-title').textContent(), /Fixture Article Two/);
    await page.locator('.rf-hist-close').click();
    assert.equal(await page.locator('.rf-hist-dialog').count(), 0);
  });

  /* ---------------- F25 / F09 / F59 / F26 ---------------- */
  await check('F25 状态徽标与标签不足提示在卡片上方常驻，不在阅读帮助弹层内', async () => {
    await page.evaluate(() => {
      const badge = document.getElementById('alBadge');
      badge.style.display = ''; badge.textContent = '已训练于 12 条标签';
      document.getElementById('alHint').textContent = '可用标签不足（3 / 最少 5），暂按导入顺序展示';
    });
    await page.waitForTimeout(80);
    const host = await page.evaluate(() => {
      const status = document.querySelector('.rf-screen-ml-status');
      return {inHelp: !!document.getElementById('rfScreenHelpPanel')?.querySelector('#alBadge'), hidden: status?.hidden,
        beforeCard: status?.nextElementSibling?.id, text: status?.textContent};
    });
    assert.equal(host.inHelp, false, 'alBadge 不得再被收进阅读帮助弹层');
    assert.equal(host.hidden, false);
    assert.equal(host.beforeCard, 'scrCard', '状态行必须在卡片正上方');
    assert.match(host.text, /已训练于 12 条标签/);
  });
  await check('F59 重排指示出现后落定即移除', async () => {
    const during = await page.evaluate(() => {
      const done = window.RFScreenReason.ranking(true);
      const text = document.querySelector('.rf-screen-ranking')?.textContent || '';
      done();
      return {text, after: document.querySelectorAll('.rf-screen-ranking').length};
    });
    assert.match(during.text, /排序更新中…/);
    assert.equal(during.after, 0, '结束函数必须移除指示');
  });
  await check('F09 顺序标签旁常显「当前：…」', async () => {
    assert.match(await page.locator('.rf-screen-order-now').textContent(), /^当前：/);
  });
  await check('F26 分数文案改为「预测纳入概率」并带已标注条数', async () => {
    await renderCard({reasons: ['人群不符'], score: 0.873});
    const num = await page.locator('.al-score .num').textContent();
    assert.match(num, /预测纳入概率 87\.3%/);
    assert.doesNotMatch(num, /机器学习相关度/);
    assert.match(await page.locator('.al-score-basis').textContent(), /基于 \d+ 条已标注/);
  });

  /* ---------------- F12 ---------------- */
  await check('F12 批注面板开合给 PDF 容器加 320ms 过渡类', async () => {
    await page.locator('#ftSideToggle').click();
    assert.equal(await page.locator('.pdfstage').evaluate(n => n.classList.contains('rf-zoom-settle')), true);
    await page.waitForTimeout(420);
    assert.equal(await page.locator('.pdfstage').evaluate(n => n.classList.contains('rf-zoom-settle')), false, '过渡类必须自行移除');
  });

  assert.deepEqual(errors, [], '页面不得有未捕获异常');
  console.log(JSON.stringify({checks: 19, errors}));
  await browser.close();
})().catch(error => {
  console.error(String(error.stack || error));
  process.exit(1);
});
