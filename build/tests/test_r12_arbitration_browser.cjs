'use strict';
/*
 * r12 真实浏览器验收（裁决员筛选开关 + 仲裁页「从服务器加载全员决策」）。
 *
 * 真实后端 + 真实 Chromium；账号/任务/文献用 API 准备（同 r12 服务端测试的种子口径），
 * 所有**被验收的动作**都走页面点击：邀请成员、成员卡开关、初筛/复筛决策、暂停阶段、
 * 服务器加载、逐条冲突仲裁、保存、最近记录、导出、CSV 上传。
 *
 * 运行（Git Bash 内联前缀）：
 *   REVIEWFLOW_PLAYWRIGHT=E:/rf214-playwright/node_modules/playwright \
 *   PLAYWRIGHT_BROWSERS_PATH=C:/Users/16073/AppData/Local/ms-playwright \
 *   REVIEWFLOW_R12_URL=http://127.0.0.1:19711 \
 *   REVIEWFLOW_R12_DESKTOP_URL=http://127.0.0.1:19712 \
 *   node build/tests/test_r12_arbitration_browser.cjs
 *
 * 门控文件 build/tests/test_r12_browser_acceptance.py 负责起停两个隔离实例（server /
 * desktop）并在未配置 Playwright 时如实 skip。
 *
 * 覆盖（与验收单 1~7 对齐）：
 *  1) 负责人邀请两名筛选员与一名裁决员；裁决员成员卡出现「允许参与筛选」开关且默认关；
 *  2) 裁决员初筛/复筛页看到引导说明（非裸 403/空白），真实写入被 403 结构化拒绝；
 *  3) 两名筛选员做出含冲突决定；独立筛选未暂停时点「从服务器加载全员决策」→ 防泄盲引导，
 *     页面与响应均不含成员决定内容；
 *  4) 暂停阶段后一键加载：κ + 逐条冲突表 → 处理冲突 → 保存 → 最近记录可取回 → 导出可用；
 *     刷新后状态正确；stage2 同路径；
 *  5) 开关打开 → 裁决员可筛选并保存；关闭 → 恢复引导且已保存决定不丢；
 *  6) server 模式 CSV 上传回退可合并并保存；desktop 模式服务器入口隐藏/禁用、CSV 不变；
 *  7) en 下新文案已翻译；390px 视口仲裁页按钮行不溢出。
 *
 * 注意：Playwright 1.64 的 page.waitForFunction 不 await 异步判定函数，本文件的
 * waitFor() 用显式轮询（page.evaluate 逐次 await），避免异步条件静默假绿。
 */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.REVIEWFLOW_PLAYWRIGHT || 'playwright');

const SERVER = (process.env.REVIEWFLOW_R12_URL || '').replace(/\/$/, '');
const DESKTOP = (process.env.REVIEWFLOW_R12_DESKTOP_URL || '').replace(/\/$/, '');
const ARTIFACTS = process.env.REVIEWFLOW_R12_ARTIFACTS || 'E:/rf214tmp/r14/arbit/artifacts';
const TMP = process.env.REVIEWFLOW_R12_TMP || path.join(process.env.TEMP || '.', 'rf_r12_browser');
/* 每个进程随机生成本次运行的临时账号口令；不落任何可用凭据字面量。 */
const PASSWORD = process.env.REVIEWFLOW_R12_PASSWORD || crypto.randomBytes(18).toString('base64url');

if (!SERVER) {
  console.error('缺少 REVIEWFLOW_R12_URL（server 模式隔离实例地址）');
  process.exit(2);
}
fs.mkdirSync(ARTIFACTS, { recursive: true });
fs.mkdirSync(TMP, { recursive: true });

const GUIDANCE_ZH = '你尚未获得参与本任务筛选的权限，请联系任务负责人开启「允许参与筛选」后再试。';
const TOGGLE_OFF_ZH = '允许参与筛选（已关闭）';
const TOGGLE_ON_ZH = '允许参与筛选（已开启）';

const RIS = `TY  - JOUR
TI  - Study Alpha
AU  - Ada
DO  - 10.9999/r12b.1
PY  - 2020
ER  -

TY  - JOUR
TI  - Study Beta
AU  - Bob
DO  - 10.9999/r12b.2
PY  - 2021
ER  -

TY  - JOUR
TI  - Study Gamma
AU  - Cal
DO  - 10.9999/r12b.3
PY  - 2022
ER  -
`;

const failures = [];
let checks = 0;

async function check(name, fn) {
  try {
    await fn();
    checks += 1;
    console.log('PASS', name);
  } catch (error) {
    failures.push({ name, message: (error && error.stack) || String(error) });
    console.log('FAIL', name);
    console.log(String((error && error.stack) || error).split('\n').slice(0, 12).join('\n'));
  }
}

function cjk(text) {
  return /[\u3400-\u9fff\uf900-\ufaff]/.test(String(text || ''));
}

/* ---------- HTTP 准备（账号 / 任务 / 文献 / 导出的决策 CSV） ---------- */

async function http(base, route, { method = 'GET', token, body } = {}) {
  const headers = {};
  if (token) headers.Authorization = 'Bearer ' + token;
  let payload;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
  const response = await fetch(base + route, { method, headers, body: payload });
  const text = await response.text();
  let data;
  try { data = JSON.parse(text); } catch { data = text; }
  return { status: response.status, data, text };
}

async function requireOk(base, route, options) {
  const result = await http(base, route, options);
  if (result.status < 200 || result.status >= 300) {
    throw new Error(`${options?.method || 'GET'} ${route} -> ${result.status}: ${result.text.slice(0, 400)}`);
  }
  return result.data;
}

async function registerOrLogin(base, username) {
  const registered = await http(base, '/api/auth/register', {
    method: 'POST', body: { username, password: PASSWORD },
  });
  if (registered.status >= 200 && registered.status < 300) return registered.data;
  const logged = await http(base, '/api/auth/login', {
    method: 'POST', body: { username, password: PASSWORD },
  });
  if (logged.status !== 200) throw new Error(`无法注册/登录 ${username}: ${logged.status} ${logged.text.slice(0, 300)}`);
  return logged.data;
}

async function prepareInstance(base, { owner, taskName }) {
  const account = await registerOrLogin(base, owner);
  const token = account.token;
  const tasks = await requireOk(base, '/api/tasks?include_archived=true', { token });
  let task = (tasks.tasks || []).find(item => item.name === taskName);
  if (!task) task = await requireOk(base, '/api/tasks', { method: 'POST', token, body: { name: taskName } });
  const taskId = task.task_id;
  let articles = await requireOk(base, `/api/tasks/${taskId}/articles`, { token });
  if (!articles.articles || !articles.articles.length) {
    const form = new FormData();
    form.append('file', new Blob([RIS], { type: 'text/plain' }), 'r12b.ris');
    await requireOk(base, `/api/tasks/${taskId}/import`, { method: 'POST', token, body: form });
    const deadline = Date.now() + 20000;
    while (Date.now() < deadline) {
      articles = await requireOk(base, `/api/tasks/${taskId}/articles`, { token });
      if ((articles.articles || []).length >= 3) break;
      await new Promise(resolve => setTimeout(resolve, 200));
    }
  }
  const keys = (articles.articles || []).map(item => item.zotero_key).sort();
  if (keys.length < 3) throw new Error(`文献准备不足：${JSON.stringify(articles).slice(0, 300)}`);
  return { token, taskId, keys, userId: account.user && account.user.id };
}

/* ---------- 浏览器辅助 ---------- */

async function waitFor(page, predicate, label, { timeout = 20000, interval = 150, arg } = {}) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    try {
      last = await page.evaluate(predicate, arg);
      if (last) return last;
    } catch (error) { last = 'evaluate: ' + error.message; }
    await page.waitForTimeout(interval);
  }
  throw new Error(`等待超时（${label}）：${JSON.stringify(last)}`);
}

async function openAs(browser, base, token, hash, { viewport = { width: 1440, height: 1000 }, language = null, taskId = null } = {}) {
  const expectedTask = taskId || TASK_ID;
  const context = await browser.newContext({ viewport, acceptDownloads: true });
  const page = await context.newPage();
  const pageErrors = [];
  const net = [];
  page.r12Net = net;
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('response', async response => {
    const pathname = new URL(response.url()).pathname;
    if (!/(workflow-settings|work-queue|progress)$/.test(pathname)) return;
    let version = null;
    try { version = (JSON.parse(await response.text()) || {}).version ?? null; } catch (_) { /* binary/HTML */ }
    net.push({ pathname, status: response.status(), version });
  });
  if (language) await page.addInitScript(code => localStorage.setItem('rf_lang', code), language);
  await page.addInitScript(value => sessionStorage.setItem('rf_token', value), token);
  await page.goto(base + '/#' + hash);
  try {
    await page.waitForFunction(id => typeof S !== 'undefined' && S.task && S.task.task_id === id, expectedTask, { timeout: 25000 });
  } catch (error) {
    const diagnostic = await page.evaluate(() => ({
      screener: typeof S !== 'undefined' ? S.screener : null,
      page: typeof S !== 'undefined' ? S.page : null,
      settingsVersion: window.WorkflowControls?.settings()?.version ?? null,
      body: (document.querySelector('#scrBody') || {}).innerText?.slice(0, 200) || '',
    })).catch(() => null);
    throw new Error(`页面未就绪（${hash}）：${error.message}\n诊断：${JSON.stringify(diagnostic)}\n网络：${JSON.stringify(net.slice(-8))}`);
  }
  await page.waitForTimeout(1200);
  page.r12Errors = pageErrors;
  return page;
}

async function nav(page, destination) {
  await page.evaluate(target => go(target), destination);
  await page.waitForTimeout(500);
}

async function goHomeTab(page, tab) {
  await page.locator(`#wf-tab-${tab}`).click();
  await page.waitForTimeout(400);
}

async function inviteMember(page, username, role) {
  await page.locator('#teamInviteName').fill(username);
  await page.locator('#teamInviteRole').selectOption(role);
  const [response] = await Promise.all([
    page.waitForResponse(item => item.url().includes('/collaboration/invite')
      && item.request().method() === 'POST', { timeout: 15000 }),
    page.locator('#teamInviteBtn').click(),
  ]);
  assert.ok([200, 201].includes(response.status()),
    `邀请 ${username} 失败：${response.status()} ${(await response.text()).slice(0, 200)}`);
  await waitFor(page, name => Array.from(document.querySelectorAll('#teamList .team-member'))
    .some(row => row.textContent.includes(name)), `成员 ${username} 出现在团队面板`, { timeout: 15000, arg: username });
}

async function screeningToggleState(page, username) {
  const row = page.locator('#teamList .team-member', { hasText: username });
  const trigger = row.locator('.team-more');
  if (!(await row.locator('.team-member-menu').isVisible())) {
    await trigger.click();
    await page.waitForTimeout(250);
  }
  const toggle = row.locator('.team-screening-toggle');
  const count = await toggle.count();
  if (!count) return { present: false };
  return {
    present: true,
    checked: await toggle.getAttribute('aria-checked'),
    label: (await toggle.innerText()).trim(),
    disabled: await toggle.isDisabled(),
  };
}

async function clickToggle(page, username) {
  const row = page.locator('#teamList .team-member', { hasText: username });
  await row.locator('.team-more').click();
  await page.waitForTimeout(250);
  await row.locator('.team-screening-toggle').click();
  await page.waitForTimeout(1800);
}

async function setStageModes(page, modes) {
  await nav(page, 'home');
  await goHomeTab(page, 'tasks');
  await waitFor(page, () => document.querySelectorAll('#wf-policy select[aria-label]').length >= 3,
    '工作流阶段下拉出现', { timeout: 20000 });
  const selects = page.locator('#wf-policy select[aria-label]');
  for (let index = 0; index < modes.length; index += 1) {
    await selects.nth(index).selectOption(modes[index]);
  }
  await page.getByRole('button', { name: '保存当前任务的流程设置' }).click();
  await page.waitForTimeout(1500);
}

async function stageModesFromApi(base, token, taskId) {
  const settings = await requireOk(base, `/api/tasks/${taskId}/workflow-settings`, { token });
  return ['screen', 'fulltext', 'coding'].map(stage => settings.stages[stage].mode);
}

async function screenDecide(page, kind, reason) {
  if (kind === 'exclude') {
    await page.locator('#excReason').selectOption(reason || '人群不符');
  }
  await page.locator(kind === 'include' ? '#btnInc' : '#btnExc').click();
  await page.waitForTimeout(650);
}

async function fulltextDecide(page, kind, reason) {
  if (kind === 'exclude') {
    await page.locator('#ftReason').selectOption(reason || '人群不符');
  }
  await page.locator(kind === 'include' ? '#ftInc' : '#ftExc').click();
  await page.waitForTimeout(650);
}

/* 按「与筛选计划一致的当前文献」做决定，避免依赖队列顺序。 */
async function decideScreenPlan(page, plan) {
  const expected = Object.keys(plan).length;
  await waitFor(page, count => typeof S !== 'undefined' && Array.isArray(S.articles)
    && S.articles.length >= count && document.querySelector('#btnInc'), '初筛队列就绪',
    { timeout: 25000, arg: expected });
  const seen = new Set();
  for (let guard = 0; guard < 12; guard += 1) {
    const current = await page.evaluate(() => {
      const article = S.articles && S.articles[S.scrIdx];
      return article ? article.zotero_key : null;
    });
    if (!current) break;
    if (!(current in plan)) break;
    if (seen.has(current)) break;
    seen.add(current);
    await screenDecide(page, plan[current]);
  }
  assert.deepEqual([...seen].sort(), Object.keys(plan).sort(), '初筛计划中的每篇都必须经页面点击完成');
}

async function decideFulltextPlan(page, plan) {
  await waitFor(page, () => typeof S !== 'undefined' && Array.isArray(S.ftQueue) && S.ftQueue.length
    && document.querySelector('#ftInc'), '复筛队列就绪', { timeout: 25000 });
  const queue = await page.evaluate(() => (S.ftQueue || []).map(item => item.zotero_key));
  for (const key of queue) {
    if (!(key in plan)) continue;
    await page.evaluate(target => {
      const index = (S.ftQueue || []).findIndex(item => item.zotero_key === target);
      if (index >= 0) S.ftIdx = index;
      renderFtDoc();
    }, key);
    await page.waitForTimeout(300);
    await fulltextDecide(page, plan[key]);
  }
}

async function resolveAllConflicts(page) {
  const selects = page.locator('#confBody select');
  const count = await selects.count();
  for (let index = 0; index < count; index += 1) {
    await selects.nth(index).selectOption('include');
  }
  return count;
}

async function serverLoad(page) {
  const responses = [];
  const listener = async response => {
    if (response.url().includes('/screening-arbitrations/server-merge')) {
      responses.push({ status: response.status(), body: await response.text().catch(() => '') });
    }
  };
  page.on('response', listener);
  await page.locator('#mergeServerLoad').click();
  /* 等待按钮自己结束忙碌态并把结果写进状态行（比固定 sleep 稳）。 */
  await page.waitForTimeout(150);
  await waitFor(page, () => typeof mergeBusy !== 'undefined' && mergeBusy === false
    && (document.querySelector('#mergeSaveStatus') || {}).textContent.trim().length > 0,
    '服务器加载结束', { timeout: 20000 });
  page.off('response', listener);
  return { status: await page.locator('#mergeSaveStatus').innerText(), responses };
}

/* ---------- 主流程 ---------- */

let TASK_ID = '';

(async () => {
  const server = await prepareInstance(SERVER, { owner: 'r12bowner', taskName: 'r12-browser-acceptance' });
  TASK_ID = server.taskId;
  const s1 = await registerOrLogin(SERVER, 'r12bs1');
  const s2 = await registerOrLogin(SERVER, 'r12bs2');
  const adj = await registerOrLogin(SERVER, 'r12badj');
  const keys = server.keys;              // 排好序的三篇
  const K0 = keys[0], K1 = keys[1], K2 = keys[2];

  const browser = await chromium.launch({ headless: true });
  let adjPage = null;
  try {
    const ownerPage = await openAs(browser, SERVER, server.token, 'workspace/home');

    /* ===== 1. 负责人邀请 + 成员卡开关默认关 ===== */
    await check('1 负责人经页面邀请两名筛选员与一名裁决员', async () => {
      await goHomeTab(ownerPage, 'tasks');
      await waitFor(ownerPage, () => {
        const panel = document.getElementById('teamPanel');
        return panel && !panel.hidden;
      }, '团队面板可见');
      await inviteMember(ownerPage, 'r12bs1', 'coder');
      await inviteMember(ownerPage, 'r12bs2', 'coder');
      await inviteMember(ownerPage, 'r12badj', 'reconciler');
      const rows = await ownerPage.locator('#teamList .team-member').count();
      assert.equal(rows, 4, '负责人 + 两名筛选员 + 一名裁决员');
    });

    await check('1 裁决员成员卡显示「允许参与筛选」开关且默认关', async () => {
      await goHomeTab(ownerPage, 'tasks');
      const state = await screeningToggleState(ownerPage, 'r12badj');
      assert.equal(state.present, true, '裁决员成员卡必须出现筛选参与开关');
      assert.equal(state.checked, 'false', '新授权的裁决员默认关');
      assert.equal(state.disabled, false, '负责人可操作开关');
      assert.equal(state.label, TOGGLE_OFF_ZH);
    });

    /* ===== 2. 裁决员初筛/复筛页引导 + 结构化 403 ===== */
    await check('2 裁决员初筛页显示引导说明（非裸 403/空白）', async () => {
      adjPage = await openAs(browser, SERVER, adj.token, 'workspace/screen');
      await waitFor(adjPage, () => document.querySelector('#scrBody') && S.articles && S.articles.length >= 3,
        '裁决员初筛队列就绪', { timeout: 25000 });
      const body = (await adjPage.locator('#scrBody').innerText()).trim();
      assert.ok(body.length > 20, '初筛页不能是空白：' + JSON.stringify(body));
      assert.equal(await adjPage.evaluate(() => S.screeningEnabled), false);
      assert.ok(body.includes(GUIDANCE_ZH), '初筛页必须显示「允许参与筛选」引导说明：' + body.slice(0, 200));
      assert.ok(!/^\s*403\s*$/.test(body) && !/Forbidden/i.test(body), '不得是裸 403');
      await adjPage.screenshot({ path: path.join(ARTIFACTS, 'r12-screen-guidance.png'), fullPage: false });
    });

    await check('2 裁决员初筛页真实写入被 403 结构化拒绝', async () => {
      const posts = [];
      const listener = async response => {
        const request = response.request();
        if (request.method() === 'POST' && /\/api\/tasks\/[^/]+\/decisions$/.test(new URL(response.url()).pathname)) {
          posts.push({ status: response.status(), body: await response.text().catch(() => '') });
        }
      };
      adjPage.on('response', listener);
      await adjPage.locator('#btnInc').click();
      await adjPage.waitForTimeout(1200);
      adjPage.off('response', listener);
      assert.equal(posts.length, 1, '点击纳入必须触发一次真实写入');
      assert.equal(posts[0].status, 403, '被关闭的裁决员写入必须是 403：' + posts[0].body.slice(0, 300));
      const detail = JSON.parse(posts[0].body).detail;
      assert.equal(detail.error_code, 'screening_not_enabled');
      assert.equal(detail.message_key, 'team.screening_not_enabled');
      assert.equal(detail.message, GUIDANCE_ZH);
      assert.ok(!posts[0].body.includes(K0), '拒绝响应不得泄露文献键');
      const toastText = (await adjPage.locator('.toast').allInnerTexts()).join(' | ');
      assert.ok(toastText.includes(GUIDANCE_ZH), '页面须以引导文案提示，而不是裸露 403：' + toastText);
    });

    await check('2 裁决员复筛页显示引导说明（非裸 403/空白）', async () => {
      await nav(adjPage, 'fulltext');
      await waitFor(adjPage, () => {
        const node = document.querySelector('#ftScreeningNotice');
        return node && !node.hidden && node.textContent.trim().length > 10;
      }, '复筛页引导说明可见', { timeout: 20000 });
      const notice = (await adjPage.locator('#ftScreeningNotice').innerText()).trim();
      assert.equal(notice, GUIDANCE_ZH);
      const pageText = await adjPage.locator('section.page[data-p="fulltext"]').innerText();
      assert.ok(!/^\s*403\s*$/.test(pageText) && !/Forbidden/i.test(pageText), '复筛页不得是裸 403');
      await adjPage.screenshot({ path: path.join(ARTIFACTS, 'r12-fulltext-guidance.png'), fullPage: false });
    });

    /* ===== 3. 两名筛选员做出含冲突的决定；未暂停时防泄盲 ===== */
    await check('3 两名筛选员经页面点击做出含冲突的初筛与复筛决定', async () => {
      const s1Page = await openAs(browser, SERVER, s1.token, 'workspace/screen');
      const s2Page = await openAs(browser, SERVER, s2.token, 'workspace/screen');
      await decideScreenPlan(s1Page, { [K0]: 'include', [K1]: 'include', [K2]: 'include' });
      await decideScreenPlan(s2Page, { [K0]: 'exclude', [K1]: 'include', [K2]: 'include' });
      const counts = {
        s1: await s1Page.evaluate(() => Object.keys(S.decisions).sort()),
        s2: await s2Page.evaluate(() => Object.keys(S.decisions).sort()),
      };
      assert.deepEqual(counts.s1, [K0, K1, K2].sort());
      assert.deepEqual(counts.s2, [K0, K1, K2].sort());
      /* 独立筛选包含两个阶段：复筛决定也要在「暂停」前完成，否则暂停后
         复筛按钮只读，stage2 的仲裁路径就无从验证。 */
      await nav(s1Page, 'fulltext');
      await decideFulltextPlan(s1Page, { [K0]: 'include', [K1]: 'exclude', [K2]: 'include' });
      await nav(s2Page, 'fulltext');
      await decideFulltextPlan(s2Page, { [K1]: 'include', [K2]: 'exclude' });
      const ftCounts = {
        s1: await s1Page.evaluate(() => Object.keys(S.ftDec).sort()),
        s2: await s2Page.evaluate(() => Object.keys(S.ftDec).sort()),
      };
      assert.deepEqual(ftCounts.s1, [K0, K1, K2].sort());
      assert.deepEqual(ftCounts.s2, [K1, K2].sort());
      await s1Page.context().close();
      await s2Page.context().close();
    });

    await check('3 独立筛选未暂停：服务器加载显示防泄盲引导且不含成员决定', async () => {
      await nav(adjPage, 'merge');
      await adjPage.locator('#mergeStage').selectOption('stage1');
      await adjPage.waitForTimeout(300);
      const { status, responses } = await serverLoad(adjPage);
      assert.ok(status.includes('双盲保护'), '必须显示防泄盲引导：' + status);
      assert.equal(responses.length, 1);
      assert.equal(responses[0].status, 409);
      for (const key of keys) {
        assert.ok(!responses[0].body.includes(key), '409 响应不得携带成员判定（文献键）');
        assert.ok(!(await adjPage.locator('body').innerText()).includes(key), '页面不得出现未解锁的文献键');
      }
      assert.ok(!/"decision_/.test(responses[0].body));
      assert.ok(!/"include"|"exclude"/.test(responses[0].body), '409 响应不得含判定值');
      const table = await adjPage.locator('#mergeCard').evaluate(node => node.style.display);
      assert.equal(table, 'none', '防泄盲时不得显示冲突表');
      await adjPage.screenshot({ path: path.join(ARTIFACTS, 'r12-double-blind.png'), fullPage: false });
    });

    /* ===== 4. 暂停后一键加载 / 仲裁 / 保存 / 最近 / 导出 / 刷新 / stage2 ===== */
    await check('4 负责人经页面暂停全部筛选阶段', async () => {
      await setStageModes(ownerPage, ['paused', 'paused', 'paused']);
      const modes = await stageModesFromApi(SERVER, server.token, TASK_ID);
      assert.deepEqual(modes, ['paused', 'paused', 'paused']);
    });

    let stage1RecordId = null;
    await check('4 暂停后一键加载显示 κ 与逐条冲突表', async () => {
      await nav(adjPage, 'merge');
      await adjPage.locator('#mergeStage').selectOption('stage1');
      await adjPage.waitForTimeout(300);
      const { status, responses } = await serverLoad(adjPage);
      assert.equal(responses.length, 1);
      assert.equal(responses[0].status, 200, '暂停后应可加载：' + responses[0].body.slice(0, 200));
      assert.ok(status.includes('已从服务器加载 2 位筛选员的决策'), status);
      const metrics = await adjPage.locator('#mergeMetrics').innerText();
      assert.ok(/κ/.test(metrics), '必须显示 κ：' + metrics);
      const body = await adjPage.locator('#confBody').innerText();
      assert.ok(body.includes(K0), '冲突表必须逐条列出文献键');
      assert.equal(await adjPage.locator('#confBody select').count(), 1, '两名筛选员在三篇上应恰有一条冲突');
      await adjPage.screenshot({ path: path.join(ARTIFACTS, 'r12-merge-loaded.png'), fullPage: true });
    });

    await check('4 处理冲突并保存仲裁记录', async () => {
      await adjPage.locator('#confBody select').first().selectOption('exclude');
      await adjPage.locator('#confBody input').first().fill('证据不足，按排除处理');
      await adjPage.locator('#mergeSave').click();
      await waitFor(adjPage, () => document.querySelector('#mergeSaveStatus').textContent.includes('已保存'),
        '保存成功提示', { timeout: 15000 });
      const status = await adjPage.locator('#mergeSaveStatus').innerText();
      assert.match(status, /仲裁记录 #\d+ 已保存；0 条仍待仲裁/);
      stage1RecordId = Number((status.match(/#(\d+)/) || [])[1]);
      assert.ok(stage1RecordId > 0);
    });

    await check('4 「最近的仲裁记录」能取回且导出可用', async () => {
      await adjPage.locator('#mergeLatest').click();
      await waitFor(adjPage, () => document.querySelector('#mergeSaveStatus').textContent.includes('最近记录'),
        '最近记录提示', { timeout: 15000 });
      const status = await adjPage.locator('#mergeSaveStatus').innerText();
      assert.match(status, new RegExp(`最近记录 #${stage1RecordId}：`));
      assert.equal(await adjPage.locator('#mergeDownload').evaluate(node => node.hidden), false);
      const [download] = await Promise.all([
        adjPage.waitForEvent('download', { timeout: 10000 }),
        adjPage.locator('#mergeDownload').click(),
      ]);
      const file = await download.path();
      const payload = JSON.parse(fs.readFileSync(file, 'utf8'));
      assert.equal(payload.id, stage1RecordId);
      assert.equal(payload.stage, 'stage1');
      assert.ok(Array.isArray(payload.records) && payload.records.length === 3);
    });

    await check('4 刷新后仲裁页状态正确（加载/最近记录仍可用）', async () => {
      await adjPage.reload();
      await adjPage.waitForFunction(id => typeof S !== 'undefined' && S.task && S.task.task_id === id, TASK_ID, { timeout: 20000 });
      await adjPage.waitForTimeout(1200);
      await nav(adjPage, 'merge');
      const { status } = await serverLoad(adjPage);
      assert.ok(status.includes('已从服务器加载 2 位筛选员的决策'), status);
      assert.ok(/κ/.test(await adjPage.locator('#mergeMetrics').innerText()));
      await adjPage.locator('#mergeLatest').click();
      await waitFor(adjPage, () => document.querySelector('#mergeSaveStatus').textContent.includes('最近记录'),
        '刷新后最近记录提示');
      assert.match(await adjPage.locator('#mergeSaveStatus').innerText(), new RegExp(`最近记录 #${stage1RecordId}：`));
    });

    await check('4 复筛（stage2）走同一路径：加载/冲突/保存/最近记录', async () => {
      await nav(adjPage, 'merge');
      await adjPage.locator('#mergeStage').selectOption('stage2');
      await adjPage.waitForTimeout(400);
      const { status } = await serverLoad(adjPage);
      assert.ok(/已从服务器加载/.test(status), status);
      assert.ok(/κ/.test(await adjPage.locator('#mergeMetrics').innerText()), 'stage2 必须显示 κ');
      const conflicts = await adjPage.locator('#confBody select').count();
      assert.equal(conflicts, 2, 'stage2 应有两处冲突');
      await resolveAllConflicts(adjPage);
      await adjPage.locator('#mergeSave').click();
      await waitFor(adjPage, () => document.querySelector('#mergeSaveStatus').textContent.includes('已保存'), 'stage2 保存提示');
      const saved = await adjPage.locator('#mergeSaveStatus').innerText();
      /* stage2：K1/K2 两处冲突已仲裁；K0 只有 s1 复筛（单人已筛，既非 agree
         也不可选仲裁）沿用产品既有口径计入「待仲裁」，故剩 1 条。 */
      assert.match(saved, /仲裁记录 #\d+ 已保存；1 条仍待仲裁/);
      await adjPage.locator('#mergeLatest').click();
      await waitFor(adjPage, () => document.querySelector('#mergeSaveStatus').textContent.includes('最近记录'), 'stage2 最近记录');
      assert.match(await adjPage.locator('#mergeSaveStatus').innerText(), /最近记录 #\d+：/);
      assert.equal(await adjPage.locator('#mergeDownload').evaluate(node => node.hidden), false);
    });

    /* ===== 5. 开关打开 → 可筛选；关闭 → 引导恢复且决定不丢 ===== */
    await check('5 负责人打开开关后裁决员能筛选并保存', async () => {
      await setStageModes(ownerPage, ['all', 'all', 'all']);
      await goHomeTab(ownerPage, 'tasks');
      await clickToggle(ownerPage, 'r12badj');
      const state = await screeningToggleState(ownerPage, 'r12badj');
      assert.equal(state.checked, 'true');
      assert.equal(state.label, TOGGLE_ON_ZH);

      await nav(adjPage, 'screen');
      await waitFor(adjPage, () => S.screeningEnabled === true, '裁决员筛选权恢复', { timeout: 20000 });
      const noticeGone = await waitFor(adjPage, () => {
        const text = (document.querySelector('#scrBody') || {}).textContent || '';
        return !text.includes('允许参与筛选');
      }, '初筛引导消失');
      assert.equal(noticeGone, true);
      const posts = [];
      const listener = async response => {
        const request = response.request();
        if (request.method() === 'POST' && /\/api\/tasks\/[^/]+\/decisions$/.test(new URL(response.url()).pathname)) {
          posts.push({ status: response.status(), body: await response.text().catch(() => '') });
        }
      };
      adjPage.on('response', listener);
      await adjPage.locator('#btnInc').click();
      await adjPage.waitForTimeout(1200);
      adjPage.off('response', listener);
      assert.equal(posts.length, 1);
      assert.equal(posts[0].status, 200, '开关打开后必须可写：' + posts[0].body.slice(0, 200));
      assert.equal(JSON.parse(posts[0].body).saved, true);
    });

    await check('5 关闭开关后引导恢复且已保存决定不丢', async () => {
      await setStageModes(ownerPage, ['all', 'all', 'all']).catch(() => {});
      await goHomeTab(ownerPage, 'tasks');
      await clickToggle(ownerPage, 'r12badj');
      const state = await screeningToggleState(ownerPage, 'r12badj');
      assert.equal(state.checked, 'false');

      await adjPage.reload();
      await adjPage.waitForFunction(id => typeof S !== 'undefined' && S.task && S.task.task_id === id, TASK_ID, { timeout: 20000 });
      await adjPage.waitForTimeout(1600);
      await waitFor(adjPage, expected => {
        const text = (document.querySelector('#scrBody') || {}).textContent || '';
        return text.includes(expected);
      }, '关闭后初筛页恢复引导', { timeout: 20000, arg: GUIDANCE_ZH });
      const body = (await adjPage.locator('#scrBody').innerText()).trim();
      assert.ok(body.includes(GUIDANCE_ZH), '关闭后初筛页必须恢复引导：' + body.slice(0, 200));
      const decisions = await requireOk(SERVER, `/api/tasks/${TASK_ID}/decisions`, { token: adj.token });
      assert.equal(decisions.total, 1, '已保存的裁决员决定不得因关闭开关而丢失');
      const kept = await adjPage.evaluate(() => Object.keys(S.decisions || {}).length);
      assert.equal(kept, 1, '页面重新载入后应仍能看到已保存决定');
    });

    /* ===== 6. CSV 上传回退（server）与 desktop 模式 ===== */
    await check('6 服务器模式 CSV 上传仍可合并并保存', async () => {
      const csvFiles = [];
      for (const [name, account] of [['r12bs1', s1], ['r12bs2', s2]]) {
        const response = await fetch(SERVER + `/api/tasks/${TASK_ID}/export/decisions`, {
          headers: { Authorization: 'Bearer ' + account.token },
        });
        assert.equal(response.status, 200, '导出决策 CSV 失败：' + name);
        const file = path.join(TMP, `${name}.csv`);
        fs.writeFileSync(file, Buffer.from(await response.arrayBuffer()));
        csvFiles.push(file);
      }
      await nav(adjPage, 'merge');
      await adjPage.locator('#mergeStage').selectOption('stage1');
      await adjPage.waitForTimeout(300);
      await adjPage.setInputFiles('#mergeFiles', csvFiles);
      await waitFor(adjPage, () => document.querySelector('#mergeSaveStatus').textContent.includes('已合并'),
        'CSV 合并完成', { timeout: 20000 });
      assert.match(await adjPage.locator('#mergeSaveStatus').innerText(), /已合并 2 份决策 CSV/);
      const conflicts = await adjPage.locator('#confBody select').count();
      assert.ok(conflicts >= 1, '上传的 CSVs 应产生冲突行');
      await adjPage.locator('#confBody select').first().selectOption('include');
      await adjPage.locator('#mergeSave').click();
      await waitFor(adjPage, () => document.querySelector('#mergeSaveStatus').textContent.includes('已保存'),
        'CSV 保存完成', { timeout: 15000 });
      assert.match(await adjPage.locator('#mergeSaveStatus').innerText(), /仲裁记录 #\d+ 已保存/);
    });

    await check('6 desktop 模式服务器加载入口隐藏/禁用，CSV 流程不变', async () => {
      if (!DESKTOP) throw new Error('缺少 REVIEWFLOW_R12_DESKTOP_URL');
      const desktop = await prepareInstance(DESKTOP, { owner: 'r12bd-owner', taskName: 'r12-desktop-acceptance' });
      const page = await openAs(browser, DESKTOP, desktop.token, 'workspace/merge', { taskId: desktop.taskId });
      const buttonHidden = await page.locator('#mergeServerLoad').evaluate(node => node.hidden);
      const buttonDisabled = await page.locator('#mergeServerLoad').isDisabled();
      assert.ok(buttonHidden || buttonDisabled, '桌面模式必须隐藏或禁用服务器加载入口');
      const hint = page.locator('#mergeServerHint');
      assert.equal(await hint.evaluate(node => node.hidden), false, '桌面模式应显示离线提示');
      assert.ok((await hint.innerText()).includes('服务器全员决策仅在联网团队模式下可用'));

      const csvFiles = [];
      for (const name of ['r12bs1', 'r12bs2']) {
        const file = path.join(TMP, name + '.csv');
        assert.ok(fs.existsSync(file), '复用 server 实例导出的 CSV：' + file);
        csvFiles.push(file);
      }
      await page.setInputFiles('#mergeFiles', csvFiles);
      await waitFor(page, () => document.querySelector('#mergeSaveStatus').textContent.includes('已合并'),
        '桌面 CSV 合并完成', { timeout: 20000 });
      assert.match(await page.locator('#mergeSaveStatus').innerText(), /已合并 2 份决策 CSV/);
      const conflicts = await page.locator('#confBody select').count();
      assert.ok(conflicts >= 1);
      await page.locator('#confBody select').first().selectOption('include');
      await page.locator('#mergeSave').click();
      await waitFor(page, () => document.querySelector('#mergeSaveStatus').textContent.includes('已保存'),
        '桌面 CSV 保存完成', { timeout: 15000 });
      await page.context().close();
    });

    /* ===== 7. en 文案 + 390px 布局 ===== */
    await check('7 en 下开关/引导/服务器加载文案均已翻译', async () => {
      const enOwner = await openAs(browser, SERVER, server.token, 'workspace/home', { language: 'en' });
      await goHomeTab(enOwner, 'tasks');
      const state = await screeningToggleState(enOwner, 'r12badj');
      assert.equal(state.present, true);
      assert.equal(state.label, 'Allow screening participation (off)');
      assert.ok(!cjk(state.label), '开关英文文案不得残留中文');
      await enOwner.context().close();

      const enAdj = await openAs(browser, SERVER, adj.token, 'workspace/screen', { language: 'en' });
      await waitFor(enAdj, () => {
        const text = (document.querySelector('#scrBody') || {}).textContent || '';
        return text.includes('You do not have permission to screen this task yet');
      }, 'en 初筛引导说明', { timeout: 25000 });
      const notice = (await enAdj.locator('#scrBody').innerText()).trim();
      assert.ok(notice.includes('You do not have permission to screen this task yet'),
        '初筛引导说明必须英文：' + notice.slice(0, 200));
      assert.ok(!cjk(notice.split('\n')[0]), '引导文案不得残留中文：' + notice.split('\n')[0]);
      await nav(enAdj, 'merge');
      const buttonLabel = (await enAdj.locator('#mergeServerLoad').innerText()).trim();
      assert.equal(buttonLabel, '⇣ Load all screener decisions from server');
      assert.ok(!cjk(buttonLabel));
      const { status } = await serverLoad(enAdj);
      assert.ok(status.includes('Double-blind protection'), '防泄盲引导必须英文：' + status);
      assert.ok(!cjk(status), '防泄盲引导不得残留中文：' + status);
      /* 成功加载文案同样必须是英文（暂停后一键加载）。 */
      await setStageModes(ownerPage, ['paused', 'paused', 'paused']);
      await nav(enAdj, 'merge');
      const loaded = await serverLoad(enAdj);
      assert.match(loaded.status, /^Loaded decisions from \d+ screeners on the server\.$/,
        '服务器加载成功文案必须英文：' + loaded.status);
      assert.ok(!cjk(loaded.status));
      await enAdj.context().close();
    });

    await check('7 390px 视口下仲裁页按钮行不溢出', async () => {
      const mobile = await openAs(browser, SERVER, adj.token, 'workspace/merge', { viewport: { width: 390, height: 844 } });
      await mobile.waitForTimeout(600);
      const layout = await mobile.evaluate(() => {
        const row = document.querySelector('#mergeServerLoad').parentElement;
        const box = row.getBoundingClientRect();
        const controls = [...row.querySelectorAll('button, span, select, label')]
          .filter(node => getComputedStyle(node).display !== 'none' && node.getBoundingClientRect().width > 0)
          .map(node => {
            const rect = node.getBoundingClientRect();
            return { id: node.id || node.className, left: rect.left, right: rect.right, top: rect.top };
          });
        return {
          rowLeft: box.left, rowRight: box.right, rowWidth: box.width,
          scrollWidth: row.scrollWidth, clientWidth: row.clientWidth,
          docScrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth,
          controls,
        };
      });
      assert.ok(layout.docScrollWidth <= layout.innerWidth + 1,
        `页面不得横向溢出：${layout.docScrollWidth} > ${layout.innerWidth}`);
      assert.ok(layout.scrollWidth <= layout.clientWidth + 1,
        `按钮行不得溢出：scrollWidth=${layout.scrollWidth} clientWidth=${layout.clientWidth}`);
      for (const control of layout.controls) {
        assert.ok(control.left >= layout.rowLeft - 0.5 && control.right <= layout.rowRight + 0.5,
          `控件 ${control.id} 越界：${control.left}..${control.right} vs ${layout.rowLeft}..${layout.rowRight}`);
      }
      await mobile.screenshot({ path: path.join(ARTIFACTS, 'r12-merge-390.png'), fullPage: true });
      await mobile.context().close();
    });

    /* 页面无脚本错误（403 属预期业务响应，不算脚本错误）。 */
    const pageErrors = [ownerPage, adjPage].filter(Boolean).flatMap(page => page.r12Errors || []);
    await check('0 全程无未捕获脚本错误', async () => {
      assert.deepEqual(pageErrors, [], '页面不得有脚本错误：' + pageErrors.join(' | '));
    });
  } finally {
    await browser.close();
  }

  console.log(`R12 arbitration browser acceptance: ${failures.length ? 'FAIL' : 'PASS'} (${checks} checks)`);
  console.log(JSON.stringify({ checks, errors: failures.length }));
  if (failures.length) {
    for (const failure of failures) console.log('FAILED', failure.name, '\n', failure.message.split('\n').slice(0, 6).join('\n'));
    process.exit(1);
  }
})().catch(error => {
  console.error('R12 arbitration browser acceptance: CRASH');
  console.error(error && error.stack || error);
  console.log(JSON.stringify({ checks, errors: failures.length + 1 }));
  process.exit(1);
});
