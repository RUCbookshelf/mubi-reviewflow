'use strict';
/* R15 真浏览器钉子：暂不确定只在初筛第一轮。
 *
 * 由 build/tests/test_r15_round_maybe_browser.py 在隔离实例（197xx 端口）上
 * 起停；本脚本只看用户可见结果：
 *   1) 第 1 轮：初筛卡片有「2 暂不确定」按钮且可点；
 *   2) 第 2 轮：按钮消失（hidden），work item 的 maybe_allowed=false；
 *   3) 第 2 轮按键盘 2：不产生 POST /decisions，并出现「暂不确定只在第一轮」
 *      说明（不是「只读」那句），决定保持 maybe；
 *   4) 轮次提示可见，且切到英文后同一提示以英文显示（十语言注册在
 *      screening_rounds.js）；
 *   5) 进入全文筛选后 #ftMay 仍在（全文行为不变）。
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
const artifacts = process.env.REVIEWFLOW_R15_ARTIFACTS || 'E:/rf214tmp/r15/artifacts';
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
    // 顶层 const S 是脚本作用域常量，window.S 恒为 undefined；必须用裸名。
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
  // 等引导期的策略刷新/自动重载落定，避免 go('screen') 与 bootstrap 抢时序。
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
  const toastHas = text => page.waitForFunction(
    needle => [...document.querySelectorAll('.toast')].some(node => node.textContent.includes(needle)),
    text, { timeout: 9000 });
  const roundHint = () => page.evaluate(() => {
    const host = document.getElementById('wf-round-controls');
    return host ? { hidden: host.hidden, text: host.textContent } : null;
  });
  const screenReady = async () => {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        await page.waitForFunction(() => {
          const may = document.getElementById('btnMay');
          return !!may && Array.isArray(S.articles) && S.articles.length > 0
            && S.articles[S.scrIdx] && S.articles[S.scrIdx].zotero_key === 'K1';
        }, null, { timeout: 8000 });
        return;
      } catch (error) {
        const debug = await page.evaluate(() => ({
          page: typeof S !== 'undefined' ? S.page : null,
          articles: typeof S !== 'undefined' ? S.articles.map(a => a.zotero_key) : null,
          scrIdx: typeof S !== 'undefined' ? S.scrIdx : null,
          decisions: typeof S !== 'undefined' ? Object.keys(S.decisions) : null,
          body: String(document.getElementById('scrBody')?.textContent || '').slice(0, 300),
        })).catch(e => ({ evaluateError: String(e) }));
        console.log('SCREEN-RETRY', attempt, JSON.stringify(debug));
        if (attempt === 2) throw error;
        // bootstrap 的策略刷新可能与首次 go('screen') 抢版本；像用户点重试一样重载。
        await page.evaluate(() => loadScreen()).catch(() => {});
        await page.waitForTimeout(600);
      }
    }
  };

  // ---- 1. 第 1 轮：「2 暂不确定」按钮存在且可用 ----
  await check('round 1 keeps the Not Sure Now button', async () => {
    // 先把任务策略刷到稳定版本，再进入初筛，避免与引导期刷新抢 policy_version。
    await page.evaluate(async () => { try { await WorkflowControls.refresh('entry', false); } catch (e) { /* 重试由 screenReady 兜底 */ } });
    await page.evaluate(() => go('screen'));
    await screenReady();
    const state = await page.evaluate(() => {
      const button = document.getElementById('btnMay');
      return {
        hidden: button.hidden, disabled: button.disabled, text: button.textContent,
        maybeAllowed: WorkflowControls.activeItem('screen')?.maybe_allowed,
      };
    });
    assert.equal(state.hidden, false, '第 1 轮暂不确定按钮被隐藏');
    assert.equal(state.disabled, false, '第 1 轮暂不确定按钮被禁用');
    assert.equal(state.maybeAllowed, true, '第 1 轮 work item 必须 maybe_allowed=true');
  });

  // ---- 2. 存下第 1 轮 maybe 并开启第 2 轮；按钮消失 ----
  await check('round 2 hides the button and reports maybe_allowed=false', async () => {
    const started = await page.evaluate(async () => {
      for (const key of ['K1', 'K2']) {
        await api(taskApi() + '/decisions', { method: 'POST', body: { zotero_key: key, decision: 'maybe' } });
      }
      const live = await api(taskApi() + '/workflow-settings');
      const round = await api(taskApi() + '/screening-rounds', {
        method: 'POST', body: { action: 'start', expected_policy_version: live.version },
      });
      await WorkflowControls.refresh('reload').catch(() => {});
      await loadScreen();
      return { number: round.screening_round?.number, pending: round.screening_round?.pending_keys || [] };
    });
    assert.equal(started.number, 2, '第一次复筛轮次编号必须是 2：' + JSON.stringify(started));
    assert.deepEqual([...started.pending].sort(), ['K1', 'K2'], '第 2 轮快照必须包含两篇待复核文献');
    await page.waitForFunction(() => {
      const button = document.getElementById('btnMay');
      return !!button && button.hidden === true && S.articles[S.scrIdx]?.zotero_key === 'K1';
    }, null, { timeout: 20000 });
    const state = await page.evaluate(() => ({
      disabled: document.getElementById('btnMay').disabled,
      maybeAllowed: WorkflowControls.activeItem('screen')?.maybe_allowed,
    }));
    assert.equal(state.disabled, true, '第 2 轮按钮即使可见也必须禁用');
    assert.equal(state.maybeAllowed, false, '第 2 轮 work item 必须 maybe_allowed=false');
    const hint = await roundHint();
    assert.ok(hint && hint.hidden === false && hint.text.includes('暂不确定只在第一轮'),
      '第 2 轮必须显示暂不确定关闭的提示：' + JSON.stringify(hint));
  });

  // ---- 3. 键盘 2 不保存，并给出说明提示 ----
  await check('keyboard 2 is blocked with an explanation and no save', async () => {
    decisionPosts = 0;
    await page.evaluate(() => { document.activeElement?.blur(); });
    await page.keyboard.press('2');
    await toastHas('暂不确定只在第一轮');
    await page.waitForTimeout(300);
    assert.equal(decisionPosts, 0, '第 2 轮按 2 不得发出 POST /decisions');
    const decision = await page.evaluate(async () => {
      const data = await api(taskApi() + '/decisions');
      return data.decisions.find(row => row.zotero_key === 'K1')?.decision;
    });
    assert.equal(decision, 'maybe', '被拦后 K1 的决定必须仍是 maybe');
  });

  // ---- 4. 提示经十语言注册；英文界面显示英文 ----
  await check('round hint is localized (English)', async () => {
    await page.evaluate(() => RFLang.set('en'));
    await page.waitForFunction(() => {
      const host = document.getElementById('wf-round-controls');
      return !!host && host.textContent.includes('Not Sure Now is available only in the first round');
    }, null, { timeout: 9000 });
  });

  // ---- 5. 全文筛选的暂不确定按钮仍在（全文行为不变） ----
  await check('full-text keeps its Not Sure Now button', async () => {
    await page.evaluate(async () => {
      const live = await api(taskApi() + '/workflow-settings');
      await api(taskApi() + '/screening-rounds', {
        method: 'POST', body: { action: 'finish', expected_policy_version: live.version },
      });
      await go('fulltext');
    });
    await page.waitForFunction(() => {
      const section = document.querySelector('section[data-p="fulltext"]');
      const button = document.getElementById('ftMay');
      return !!section && !section.hidden && Array.isArray(S.ftQueue) && S.ftQueue.length > 0
        && !!button && button.hidden === false && button.disabled === false;
    }, null, { timeout: 25000 });
  });

  assert.deepEqual(errors, [], '页面不得抛出未捕获脚本错误：' + JSON.stringify(errors));
  console.log(JSON.stringify({ checks, errors, url, task: session.task }));
  await browser.close();
})().catch(error => {
  console.error(String(error && error.stack || error).replace(/Bearer [^\s'"]+/g, 'Bearer [redacted]'));
  process.exit(1);
});
