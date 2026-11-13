'use strict';
/* 2.1.5 W1 真浏览器钉子（前端轨完成后启用）：正式两阶段二选一 + 轮次移除。
 *
 * 旧钉（r15）：第 1 轮「2 暂不确定」可用、第 2 轮隐藏并在按 2 时给说明。
 * 新钉（W1）：
 *   1) 初筛卡不再显示「2 暂不确定」（btnMay 不存在或 hidden），
 *      work item maybe_allowed=false；
 *   2) 初筛按键盘 2 不产生 POST /decisions；
 *   3) 复筛卡不再显示「暂不确定」（ftMay 不存在或 hidden）；
 *   4) 轮次条 #wf-round-controls 不存在或 hidden（整块移除）；
 *   5) work-queue 载荷不再含 screening_round / remaining_uncertain。
 *
 * 前端轨按 frontend_contract_2.1.5-w1-binary.md 完成后，由
 * build/tests/test_r15_round_maybe_browser.py 以 REVIEWFLOW_W1_BROWSER=1
 * 显式启用；默认跳过，不冒充通过。
 *
 * 前置：REVIEWFLOW_R15_URL / REVIEWFLOW_R15_SESSION（由 pytest 门控传入）。
 */
const fs = require('node:fs');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.REVIEWFLOW_PLAYWRIGHT || 'playwright');
const url = (process.env.REVIEWFLOW_R15_URL || '').replace(/\/$/, '');
const sessionPath = process.env.REVIEWFLOW_R15_SESSION || '';
if (!url || !sessionPath) throw new Error('REVIEWFLOW_R15_URL / REVIEWFLOW_R15_SESSION 未设置：必须由 pytest 门控运行');
const session = JSON.parse(fs.readFileSync(sessionPath, 'utf8'));
const artifacts = process.env.REVIEWFLOW_R15_ARTIFACTS || (process.env.RF_R9_TMP_ROOT||"E:/rf214tmp")+"/r15/artifacts";
fs.mkdirSync(artifacts, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let decisionPosts = 0;
  page.on('request', request => {
    try {
      const parsed = new URL(request.url());
      if (request.method() === 'POST' && parsed.pathname.endsWith('/decisions')) decisionPosts += 1;
    } catch (e) { /* 非 URL 请求忽略 */ }
  });

  await page.addInitScript(token => sessionStorage.setItem('rf_token', token), session.token);
  page.on('console', message => { if (message.type() === 'error') console.log('CONSOLE', message.text()); });
  page.on('response', response => {
    if (response.status() >= 400) console.log('HTTP', response.status(), response.url());
  });
  await page.goto(url + '/#workspace/home');
  try {
    await page.waitForFunction(id => typeof S !== 'undefined' && S.user && S.task && S.task.task_id === id,
      session.task, { timeout: 25000 });
  } catch (error) {
    const debug = await page.evaluate(() => ({
      token: sessionStorage.getItem('rf_token'),
      user: typeof S !== 'undefined' ? S.user : null,
      task: typeof S !== 'undefined' ? S.task : null,
      tasks: typeof S !== 'undefined' ? S.tasks : [],
      authScreen: document.getElementById('authScreen')?.style.display || null,
      text: String(document.body.textContent || '').slice(0, 300),
    })).catch(e => ({ evaluateError: String(e) }));
    console.log('READY-DEBUG', JSON.stringify(debug));
    throw error;
  }

  let checks = 0;
  await page.waitForFunction(
    () => typeof WorkflowControls !== 'undefined' && !WorkflowControls.refreshing(),
    null, { timeout: 20000 }).catch(() => {});
  const check = async (name, fn) => {
    try { await fn(); checks += 1; console.log('PASS', name); }
    catch (error) {
      await page.screenshot({
        path: artifacts + '/fail-' + name.replace(/[^\w\u4e00-\u9fa5]+/g, '_').slice(0, 40) + '.png',
        fullPage: true,
      });
      throw error;
    }
  };
  const visible = selector => page.evaluate(node => {
    const element = document.querySelector(node);
    return !!element && !element.hidden;
  }, selector);
  const screenReady = async () => {
    await page.waitForFunction(() => {
      return Array.isArray(S.articles) && S.articles.length > 0
        && S.articles[S.scrIdx] && S.articles[S.scrIdx].zotero_key === 'K1';
    }, null, { timeout: 15000 });
  };

  // ---- 1. 初筛：无可暂不确定，work item 明确 false ----
  await check('screen has no Not Sure Now control', async () => {
    await page.evaluate(async () => { try { await WorkflowControls.refresh('entry', false); } catch (e) { /* 兜底 */ } });
    await page.evaluate(() => go('screen'));
    await screenReady();
    const state = await page.evaluate(() => {
      const button = document.getElementById('btnMay');
      return {
        visible: !!button && !button.hidden,
        maybeAllowed: WorkflowControls.activeItem('screen')?.maybe_allowed,
      };
    });
    assert.equal(state.visible, false, '初筛卡不得再显示暂不确定按钮');
    assert.equal(state.maybeAllowed, false, 'work item 必须 maybe_allowed=false');
  });

  // ---- 2. 按键盘 2 不保存 ----
  await check('keyboard 2 does not save a decision', async () => {
    decisionPosts = 0;
    await page.evaluate(() => { document.activeElement?.blur(); });
    await page.keyboard.press('2');
    await page.waitForTimeout(500);
    assert.equal(decisionPosts, 0, '按 2 不得发出 POST /decisions');
  });

  // ---- 3. 复筛：同样没有暂不确定 ----
  await check('fulltext has no Not Sure Now control', async () => {
    await page.evaluate(async () => {
      for (const key of ['K1', 'K2']) {
        await api(taskApi() + '/decisions', { method: 'POST', body: { zotero_key: key, decision: 'include' } });
      }
      await WorkflowControls.refresh('reload').catch(() => {});
      await go('fulltext');
    });
    await page.waitForFunction(() => Array.isArray(S.ftQueue) && S.ftQueue.length > 0,
      null, { timeout: 25000 });
    const state = await page.evaluate(() => {
      const button = document.getElementById('ftMay');
      return {
        visible: !!button && !button.hidden,
        maybeAllowed: WorkflowControls.activeItem('fulltext')?.maybe_allowed,
      };
    });
    assert.equal(state.visible, false, '复筛卡不得再显示暂不确定按钮');
    assert.equal(state.maybeAllowed, false, '复筛 work item 必须 maybe_allowed=false');
  });

  // ---- 4. 轮次条整块移除 ----
  await check('round controls are gone', async () => {
    assert.equal(await visible('#wf-round-controls'), false, '轮次条必须整块移除/隐藏');
  });

  // ---- 5. 队列载荷无轮次字段 ----
  await check('work queue payload has no round fields', async () => {
    const payload = await page.evaluate(async () => {
      const response = await api(taskApi() + '/work-queue/screen');
      return response;
    });
    assert.equal(Object.prototype.hasOwnProperty.call(payload, 'screening_round'), false,
      'queue 不得再返回 screening_round');
    assert.equal(Object.prototype.hasOwnProperty.call(payload, 'remaining_uncertain'), false,
      'queue 不得再返回 remaining_uncertain');
  });

  assert.deepEqual(errors, [], '页面不得抛出未捕获脚本错误：' + JSON.stringify(errors));
  console.log(JSON.stringify({ checks, errors, url, task: session.task }));
  await browser.close();
})().catch(error => {
  console.error(String(error && error.stack || error).replace(/Bearer [^\s'"]+/g, 'Bearer [redacted]'));
  process.exit(1);
});
