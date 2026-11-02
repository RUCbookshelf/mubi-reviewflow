'use strict';
/* r10-U5 · F23/F47 可见文案 —— 真浏览器渲染钉子（Playwright · 隔离实例）。
 *
 * 运行：
 *   REVIEWFLOW_PLAYWRIGHT=E:/rf214-playwright/node_modules/playwright \
 *   PLAYWRIGHT_BROWSERS_PATH=C:/Users/16073/AppData/Local/ms-playwright \
 *   node build/tests/test_r10_u5_copy_browser.cjs
 *
 * 为什么不只是静态钉：静态钉只能证明「源串 + 词典条目都对」，证明不了
 * MutationObserver 真的把文本换掉了（标签包裹、translate="no"、属性翻译都会
 * 让源串正确但屏幕仍旧）。本钉子装真实 index.html（外链脚本全量），只桩网络。
 *
 * 断言（每条破坏即红）：
 *  F23-1 中文界面：初筛/复筛的决策按钮显示「2 暂不确定」，不含「待定」；
 *  F23-2 中文界面：决策历史计数行显示「暂不确定」，不含「待定」；
 *  F23-3 英文界面：同一按钮显示「2 Not Sure Now」；
 *  F47-1 中文界面：邀请下拉的两个 option 是「筛选与编码员（Coder）」「裁决员（Adjudicator）」；
 *  F47-2 英文界面：同样的 option 显示 Coder / Adjudicator（译名的英文槽）；
 *  F47-3 两语言下都不出现裸 Reconciler（内部授权标识不上屏）。
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chromium} = require(process.env.REVIEWFLOW_PLAYWRIGHT || 'playwright');

const root = path.resolve(__dirname, '..', '..');
const frontend = path.join(root, 'custom_frontend');
const index = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
const ORIGIN = 'http://r10u5copy.test';

/* 页面里必须真有的锚点（改 DOM 结构即红，避免断言悄悄空转）。
   #btnMay 是 renderScreen() 里按队列动态建的，未登录任务时不存在；
   这里断言的是**静态**存在于 index.html 的 #ftMay 与角色下拉。 */
for (const id of ['ftMay', 'teamInviteRole']) {
  assert.ok(index.includes(`id="${id}"`), `index.html 缺少静态锚点 #${id}（F23/F47 断言会空转）`);
}
assert.ok(index.includes("id: 'btnMay'"), 'index.html 缺少 renderScreen 的动态 #btnMay 锚点');

(async () => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'r10u5-copy-'));
  const context = await chromium.launchPersistentContext(profile, {
    headless: true,
    viewport: {width: 1280, height: 900},
    args: ['--no-first-run', '--no-default-browser-check'],
  });
  try {
    const page = context.pages()[0] || await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));

    await page.route(ORIGIN + '/**', route => {
      const url = new URL(route.request().url());
      const name = decodeURIComponent(url.pathname.replace(/^\/+/, '')) || 'index.html';
      if (name === 'index.html') {
        return route.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: index});
      }
      const file = path.join(frontend, name);
      if (file.startsWith(frontend) && fs.existsSync(file) && fs.statSync(file).isFile()) {
        const type = file.endsWith('.css') ? 'text/css'
          : file.endsWith('.json') ? 'application/json' : 'text/javascript';
        return route.fulfill({status: 200, contentType: type + '; charset=utf-8',
                              body: fs.readFileSync(file)});
      }
      return route.fulfill({status: 404, contentType: 'application/json', body: '{}'});
    });
    await page.route('**/api/**', route => route.fulfill({status: 200, contentType: 'application/json', body: '{}'}));

    await page.goto(ORIGIN + '/index.html', {waitUntil: 'load'});
    await page.waitForTimeout(700);
    assert.deepEqual(errors, [], '整页装载不得抛未捕获脚本错误：' + JSON.stringify(errors));

    /* 语言切换后等 MutationObserver 走完一轮 */
    async function setLanguage(code) {
      await page.evaluate(lang => {
        if (window.RFLang && typeof window.RFLang.set === 'function') window.RFLang.set(lang);
      }, code);
      await page.waitForTimeout(220);
    }

    async function snapshot() {
      return page.evaluate(() => ({
        ftMay: document.getElementById('ftMay')?.textContent?.trim() ?? null,
        /* renderScreen 的动态按钮：用产品自身的 T() 复算，证明词典+引擎对这两个
           源串给出的就是屏幕文案（DOM 里没有任务时不渲染它）。 */
        btnMayRendered: window.T ? window.T('2 待定') : null,
        histCountsRendered: window.T
          ? window.T('(纳入：{0} | 待定：{1} | 排除：{2})', 3, 2, 1) : null,
        roleOptions: [...document.querySelectorAll('#teamInviteRole option')]
          .map(option => option.textContent.trim()),
        pageText: document.body.innerText,
      }));
    }

    /* ---- 中文界面 ---- */
    await setLanguage('zh-CN');
    const zh = await snapshot();
    assert.equal(zh.ftMay, '2 暂不确定',
      'F23：复筛决策按钮必须显示「2 暂不确定」，实际 ' + JSON.stringify(zh.ftMay));
    assert.equal(zh.btnMayRendered, '2 暂不确定',
      'F23：初筛决策按钮的源串经词典必须渲染为「2 暂不确定」，实际 ' + JSON.stringify(zh.btnMayRendered));
    assert.equal(zh.histCountsRendered, '(纳入：3 | 暂不确定：2 | 排除：1)',
      'F23：决策历史计数行必须显示「暂不确定」，实际 ' + JSON.stringify(zh.histCountsRendered));
    assert.ok(!zh.pageText.includes('待定'),
      'F23：中文界面不得再出现旧词「待定」（出现位置：' +
      JSON.stringify(zh.pageText.split('\n').filter(line => line.includes('待定')).slice(0, 4)) + '）');
    assert.deepEqual(zh.roleOptions, ['筛选与编码员（Coder）', '裁决员（Adjudicator）'],
      'F47：中文界面的角色下拉必须是「译名（英文原词）」，实际 ' + JSON.stringify(zh.roleOptions));
    assert.ok(!zh.pageText.includes('Reconciler'),
      'F47：内部授权标识 Reconciler 不得上屏');

    /* ---- 英文界面 ---- */
    await setLanguage('en');
    const en = await snapshot();
    assert.equal(en.ftMay, '2 Not Sure Now',
      'F23：英文界面复筛按钮必须是「2 Not Sure Now」，实际 ' + JSON.stringify(en.ftMay));
    assert.equal(en.btnMayRendered, '2 Not Sure Now',
      'F23：英文界面初筛按钮源串必须是「2 Not Sure Now」，实际 ' + JSON.stringify(en.btnMayRendered));
    assert.equal(en.histCountsRendered, '(Included: 3 | Not Sure Now: 2 | Excluded: 1)',
      'F23：英文界面计数行必须用 Not Sure Now，实际 ' + JSON.stringify(en.histCountsRendered));
    assert.deepEqual(en.roleOptions, ['Coder', 'Adjudicator'],
      'F47：英文界面的角色下拉必须是原词，实际 ' + JSON.stringify(en.roleOptions));
    assert.ok(!en.pageText.includes('Reconciler'),
      'F47：内部授权标识 Reconciler 不得上屏');

    /* 收尾：切回中文，确认往返不叠加翻译（translateTree 的 canonical 源串） */
    await setLanguage('zh-CN');
    const roundTrip = await snapshot();
    assert.equal(roundTrip.ftMay, '2 暂不确定',
      'F23：语言往返后按钮必须仍显示「2 暂不确定」，实际 ' + JSON.stringify(roundTrip.ftMay));

    assert.deepEqual(errors, [], '整个流程不得抛未捕获脚本错误：' + JSON.stringify(errors));
  } finally {
    await context.close();
    try { fs.rmSync(profile, {recursive: true, force: true}); } catch (e) {}
  }
  console.log('R10-U5 COPY OK');
})().catch(error => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
