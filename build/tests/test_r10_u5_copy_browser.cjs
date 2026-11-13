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
 *  F23：二元决策历史的计数只显示纳入与排除，旧记录的状态仍显示规范译名。
 *  F47：邀请下拉中英文显示对应角色名，内部授权标识不出现在界面。
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
    await page.evaluate(() => window.RFAppModules.ready());

    // Read actual decision history DOM after rendering synthetic saved records.
    async function snapshot(language) {
      return page.evaluate(code => {
        RFLang.set(code);
        S.articles = [
          {zotero_key:'included', title:'Fixture included'},
          {zotero_key:'excluded', title:'Fixture excluded'},
          {zotero_key:'legacy', title:'Fixture legacy'},
        ];
        S.decisions = {
          included:{decision:'include'}, excluded:{decision:'exclude'}, legacy:{decision:'maybe'},
        };
        renderScreenHist();
        return {
          counts: document.getElementById('scrHistCounts').textContent.trim(),
          labels: [...document.querySelectorAll('#scrHist button')]
            .map(button => button.getAttribute('aria-label')),
          roleOptions: [...document.querySelectorAll('#teamInviteRole option')]
            .map(option => option.textContent.trim()),
          pageText: document.body.innerText,
        };
      }, language);
    }

    const zh = await snapshot('zh-CN');
    assert.equal(zh.counts, '(纳入：1 · 排除：1)', 'F23：决策历史应显示二元计数');
    assert.deepEqual(zh.labels, [
      'Fixture included · 纳入', 'Fixture excluded · 排除', 'Fixture legacy · 暂不确定',
    ]);
    assert.deepEqual(zh.roleOptions, ['筛选与编码员（Coder）', '裁决员（Adjudicator）']);
    assert.ok(!zh.pageText.includes('待定'), 'F23：中文界面仍有旧词「待定」');
    assert.ok(!zh.pageText.includes('Reconciler'), 'F47：内部授权标识不得上屏');

    const en = await snapshot('en');
    assert.equal(en.counts, '(Included: 1 · Excluded: 1)');
    assert.deepEqual(en.labels, [
      'Fixture included · Included', 'Fixture excluded · Excluded', 'Fixture legacy · Not Sure Now',
    ]);
    assert.deepEqual(en.roleOptions, ['Coder', 'Adjudicator']);
    assert.ok(!en.pageText.includes('Reconciler'), 'F47：内部授权标识不得上屏');

    assert.deepEqual(await snapshot('zh-CN'), zh, 'F23/F47：语言往返应恢复中文文案');
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
