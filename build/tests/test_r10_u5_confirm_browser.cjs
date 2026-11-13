'use strict';
/* r10-U5 · F52 应用内确认对话框 —— 真浏览器行为钉子（Playwright · 隔离实例）。
 *
 * 运行：
 *   REVIEWFLOW_PLAYWRIGHT=E:/rf214-playwright/node_modules/playwright \
 *   PLAYWRIGHT_BROWSERS_PATH=C:/Users/16073/AppData/Local/ms-playwright \
 *   node build/tests/test_r10_u5_confirm_browser.cjs
 *
 * 被测对象是**真实的 custom_frontend/index.html**（整页外链脚本全量装载，只桩网络
 * API）。这样 RFConfirm 用的是产品里的 el()/T()/词典与真实 <style>，不是手写复写件；
 * 也顺带证明这些改动没有把整页装机搞崩（任何 pageerror 即红）。
 *
 * 断言（每条都是「破坏该实现即红」）：
 *  1. 初始焦点落在「取消」——安全默认，误按 Enter 不会执行危险动作；
 *  2. 危险动作是红色主按钮（.b-exc，实际计算背景色 = --danger）；
 *  3. Tab 在对话框内循环（末项 Tab → 首项；首项 Shift+Tab → 末项），不外泄；
 *  4. Escape 取消：Promise 解析为 false，对话框从 DOM 移除；
 *  5. 关闭后焦点回到打开它的控件；
 *  6. 点主按钮：Promise 解析为 true；
 *  7. 原生 confirm 不再参与（页面内 confirm 被打桩，调用即失败）；
 *  8. 文案来自十语言词典：切 en 后标题/按钮为英文（不是浏览器本地化）。
 *  9. Enter 保留安全默认与取消语义，显式输入框聚焦时可提交文字。
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chromium} = require(process.env.REVIEWFLOW_PLAYWRIGHT || 'playwright');

const root = path.resolve(__dirname, '..', '..');
const frontend = path.join(root, 'custom_frontend');
const index = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');

/* 锚点自检：被测组件必须在 index.html 里（改动/删除锚点即红）。 */
for (const marker of ['const RF_CONFIRM_FOCUSABLE =', 'function RFConfirm(options)',
                      "opener.focus()", 'dialog.showModal();']) {
  assert.ok(index.includes(marker), `index.html 缺少 F52 锚点：${marker}`);
}

const ORIGIN = 'http://r10u5.test';

(async () => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'r10u5-confirm-'));
  const context = await chromium.launchPersistentContext(profile, {
    headless: true,
    viewport: {width: 1100, height: 800},
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
    /* 后端请求一律桩成空对象：本钉子只关心前端组件行为。 */
    await page.route('**/api/**', route => route.fulfill({status: 200, contentType: 'application/json', body: '{}'}));

    await page.goto(ORIGIN + '/index.html', {waitUntil: 'load'});
    await page.waitForTimeout(600);
    assert.deepEqual(errors, [], '整页装载不得抛未捕获脚本错误：' + JSON.stringify(errors));

    const ready = await page.evaluate(() => typeof window.RFConfirm === 'function');
    assert.equal(ready, true, 'F52：window.RFConfirm 必须存在（组件未装载）');

    /* 原生 confirm 调用计数（F52 的核心主张：不再依赖浏览器本地化按钮） */
    await page.evaluate(() => {
      window.__nativeConfirmCalls = 0;
      window.confirm = () => { window.__nativeConfirmCalls += 1; return true; };
    });

    /* 真实 Enter：危险确认默认取消，只有显式输入框能直接提交文字。 */
    const renameOptions = {message: 'Rename probe', input: true, value: '  replacement  ', danger: false};
    const keyboardCases = [
      {label: '危险确认', options: {message: 'Delete probe?'}, focus: '.rf-confirm-actions button:first-child', expected: false},
      {label: '改名输入框', options: renameOptions, focus: 'input', expected: 'replacement'},
      {label: '改名取消', options: renameOptions, tab: true, focus: '.rf-confirm-actions button:first-child', expected: false},
    ];
    for (const {label, options, tab, focus, expected} of keyboardCases) {
      await page.evaluate(opts => {
        const opener = document.createElement('button');
        opener.id = 'r10u5-keyboard-opener';
        document.body.append(opener);
        opener.focus();
        window.__r10u5KeyboardResult = window.RFConfirm(opts);
      }, options);
      const dialog = page.locator('#rf-confirm-dialog');
      assert.equal(await dialog.locator('input').count(), options.input ? 1 : 0,
        label + '：只有显式要求输入时才显示输入框');
      if (tab) await page.keyboard.press('Tab');
      assert.equal(await dialog.locator(focus).evaluate(node => document.activeElement === node), true,
        label + '：Enter 前焦点应落在预期控件');
      await page.keyboard.press('Enter');
      await dialog.waitFor({state: 'detached', timeout: 5000});
      const outcome = await page.evaluate(async () => {
        const opener = document.getElementById('r10u5-keyboard-opener');
        const result = await window.__r10u5KeyboardResult;
        const focusBack = document.activeElement === opener;
        opener.remove();
        delete window.__r10u5KeyboardResult;
        return {result, focusBack};
      });
      assert.deepEqual(outcome, {result: expected, focusBack: true},
        label + '：Enter 应返回预期值并归还焦点');
    }

    /* --- 1/2/6：安全默认焦点、危险色、确认返回 true（+ 焦点归还） --- */
    const confirmed = await page.evaluate(async () => {
      const opener = document.createElement('button');
      opener.id = 'r10u5-opener';
      opener.textContent = 'open';
      document.body.append(opener);
      opener.focus();
      const pending = window.RFConfirm({message: 'Delete probe?', confirmLabel: 'Delete'});
      const dialog = document.getElementById('rf-confirm-dialog');
      if (!dialog) return {error: 'no dialog'};
      const buttons = [...dialog.querySelectorAll('button')];
      const primary = buttons[buttons.length - 1];
      const info = {
        open: dialog.open,
        focusId: document.activeElement === buttons[0] ? 'cancel'
          : document.activeElement === primary ? 'confirm' : 'other:' + document.activeElement.tagName,
        confirmClass: primary.className,
        confirmBg: getComputedStyle(primary).backgroundColor,
        labelledBy: dialog.getAttribute('aria-labelledby'),
        describedBy: dialog.getAttribute('aria-describedby'),
        role: dialog.tagName.toLowerCase(),
      };
      primary.click();
      info.result = await pending;
      info.stillInDom = !!document.getElementById('rf-confirm-dialog');
      info.focusBack = document.activeElement === opener;
      opener.remove();
      return info;
    });
    assert.equal(confirmed.error, undefined, 'F52：RFConfirm 未渲染对话框：' + confirmed.error);
    assert.equal(confirmed.open, true, 'F52：对话框必须以 showModal 打开');
    assert.equal(confirmed.focusId, 'cancel',
      'F52：初始焦点必须落在「取消」（安全默认），实际：' + confirmed.focusId);
    assert.match(confirmed.confirmClass, /b-exc/,
      'F52：危险动作必须是红色主按钮，实际 class=' + confirmed.confirmClass);
    assert.equal(confirmed.confirmBg, 'rgb(176, 86, 63)',
      'F52：危险主按钮实际背景必须等于 --danger(#B0563F)，实际 ' + confirmed.confirmBg);
    assert.equal(confirmed.labelledBy, 'rf-confirm-title');
    assert.equal(confirmed.describedBy, 'rf-confirm-message');
    assert.equal(confirmed.role, 'dialog');
    assert.equal(confirmed.result, true, 'F52：点主按钮必须 resolve(true)');
    assert.equal(confirmed.stillInDom, false, 'F52：结束后对话框必须从 DOM 移除');
    assert.equal(confirmed.focusBack, true, 'F52：关闭后焦点必须回到打开它的控件');

    /* --- 3：Tab 在对话框内循环 --- */
    const trap = await page.evaluate(async () => {
      const opener = document.createElement('button');
      document.body.append(opener);
      opener.focus();
      const pending = window.RFConfirm({message: 'Cycle test'});
      const dialog = document.getElementById('rf-confirm-dialog');
      const buttons = [...dialog.querySelectorAll('button')];
      const first = buttons[0], last = buttons[buttons.length - 1];
      const press = (target, shift) => {
        target.focus();
        const event = new KeyboardEvent('keydown',
          {key: 'Tab', shiftKey: !!shift, bubbles: true, cancelable: true});
        target.dispatchEvent(event);
        return document.activeElement;
      };
      const forward = press(last, false);
      const backward = press(first, true);
      const info = {
        forwardWrapped: forward === first,
        backwardWrapped: backward === last,
        forwardTarget: forward === first ? 'first' : (forward === last ? 'last' : 'outside'),
        backwardTarget: backward === last ? 'last' : (backward === first ? 'first' : 'outside'),
      };
      buttons[0].click();
      info.result = await pending;
      opener.remove();
      return info;
    });
    assert.equal(trap.forwardWrapped, true,
      'F52：末项按 Tab 必须回到首项（焦点陷阱），实际停在 ' + trap.forwardTarget);
    assert.equal(trap.backwardWrapped, true,
      'F52：首项按 Shift+Tab 必须回到末项，实际停在 ' + trap.backwardTarget);
    assert.equal(trap.result, false);

    /* --- 4/5：Escape 取消 + 焦点归还 --- */
    const escaped = await page.evaluate(async () => {
      const opener = document.createElement('button');
      document.body.append(opener);
      opener.focus();
      const pending = window.RFConfirm({message: 'Escape test'});
      const dialog = document.getElementById('rf-confirm-dialog');
      const cancelled = new Event('cancel', {bubbles: false, cancelable: true});
      dialog.dispatchEvent(cancelled);
      const result = await pending;
      const info = {
        result,
        removed: !document.getElementById('rf-confirm-dialog'),
        focusBack: document.activeElement === opener,
        defaultPrevented: cancelled.defaultPrevented,
      };
      opener.remove();
      return info;
    });
    assert.equal(escaped.result, false, 'F52：Esc 必须 resolve(false)，不得确认');
    assert.equal(escaped.removed, true, 'F52：Esc 后对话框必须移除');
    assert.equal(escaped.focusBack, true, 'F52：Esc 后焦点必须归还打开它的控件');
    assert.equal(escaped.defaultPrevented, true,
      'F52：cancel 事件必须 preventDefault（由组件自己收尾，不让原生 close 抢先）');

    /* --- 7：原生 confirm 全程零调用 --- */
    const natives = await page.evaluate(() => window.__nativeConfirmCalls);
    assert.equal(natives, 0, 'F52：不得调用原生 confirm（按钮文字会随浏览器语言）');

    /* --- 8：英文界面文案来自词典 --- */
    const english = await page.evaluate(async () => {
      if (window.RFLang && typeof window.RFLang.set === 'function') window.RFLang.set('en');
      await new Promise(r => setTimeout(r, 120));
      const opener = document.createElement('button');
      document.body.append(opener);
      opener.focus();
      const pending = window.RFConfirm({message: 'Plain message'});
      const dialog = document.getElementById('rf-confirm-dialog');
      const buttons = [...dialog.querySelectorAll('button')];
      const info = {
        title: dialog.querySelector('#rf-confirm-title')?.textContent,
        cancel: buttons[0].textContent,
        confirm: buttons[buttons.length - 1].textContent,
      };
      buttons[0].click();
      await pending;
      opener.remove();
      if (window.RFLang && typeof window.RFLang.set === 'function') window.RFLang.set('zh-CN');
      return info;
    });
    assert.equal(english.title, 'Please confirm',
      'F52：英文界面的对话框标题必须来自词典，实际 ' + JSON.stringify(english.title));
    assert.equal(english.cancel, 'Cancel', 'F52：取消按钮英文槽，实际 ' + JSON.stringify(english.cancel));
    assert.equal(english.confirm, 'Confirm', 'F52：确认按钮英文槽，实际 ' + JSON.stringify(english.confirm));

    assert.deepEqual(errors, [], 'F52：整个流程不得抛未捕获脚本错误：' + JSON.stringify(errors));
  } finally {
    await context.close();
    try { fs.rmSync(profile, {recursive: true, force: true}); } catch (e) {}
  }
  console.log('R10-U5 CONFIRM OK');
})().catch(error => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
