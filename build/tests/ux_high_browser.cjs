'use strict';
/* UX 五项高危浏览器实测（Playwright · 隔离实例 · 真实后端数据）。
 *
 * 覆盖：
 * - 高4（F29/F05，本轮回归修复核心）：真实导入 API 构造 identity_conflict
 *   候选（成员 [('active',article), ('record',staged)]）→ 裁决 not_duplicate
 *   但【不】启用（hc 现场：后端 review_counts.ready=1）→ UI 实测
 *   ①「待启用」页签计数 ②启用按钮「(N)」 ③实际可启用记录 三者一致，
 *   且 staged 成员记录以待启用记录形态在列表可见；随后真实点击启用，
 *   验证计数如实预告了启用结果（启用后按钮 (0)、已完成栏出现「已启用」组）。
 *   已决并启用（gc）与真实 legacy 标题组（成员只有 zotero_key 等 RIS 字段，
 *   徽标「已核验」）一并实测——上轮“legacy 用自造成员形状”的败因由此根治。
 * - 高7（F53）：导入全屏遮罩挂起（路由拦截 import-batch 不响应）→
 *   取消按钮存在可点击 → 点击后遮罩收起、inert 恢复、提示如实；
 *   已用时长（已等待 {0} 秒）逐秒可见。
 *
 * 运行（实例需已起，前端=本工作树）：
 *   REVIEWFLOW_GUARD_URL=http://127.0.0.1:18775 \
 *   REVIEWFLOW_PLAYWRIGHT=E:/rf214-playwright/node_modules/playwright \
 *   node build/tests/ux_high_browser.cjs
 * fixture 账号/任务/批次在 cjs 内经真实 HTTP API 构造，并把 session 写入
 * $REVIEWFLOW_UX_SESSION_DIR/ux_high_session.json（默认 E:/uxt-r4-ux）供追溯。
 */
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const {chromium} = require(process.env.REVIEWFLOW_PLAYWRIGHT || 'playwright');

const url = (process.env.REVIEWFLOW_GUARD_URL || 'http://127.0.0.1:18775').replace(/\/$/, '');
const sessionDir = process.env.REVIEWFLOW_UX_SESSION_DIR || 'E:/uxt-r4-ux';
fs.mkdirSync(sessionDir, {recursive: true});

/* ---------- 真实 API fixture（与 pytest CompletionBadgeBehaviorTests 同款场景） ---------- */
const ris = entries => entries.map(e => 'TY  - JOUR\n' +
  Object.entries(e).map(([tag, value]) => `${tag}  - ${value}\n`).join('') + 'ER  - ').join('\n');
const BASE1 = {AU: 'Alpha, A', TI: 'Conflict Base One', PY: '2024', DO: '10.test/base1', ID: 'SHAREKEY1'};
const BASE2 = {AU: 'Bravo, B', TI: 'Conflict Base Two', PY: '2024', DO: '10.test/base2', ID: 'SHAREKEY2'};
const CONFLICT1 = {...BASE1, TI: 'Conflict Base One Variant', DO: '10.test/other1'};
const CONFLICT2 = {...BASE2, TI: 'Conflict Base Two Variant', DO: '10.test/other2'};
const FRESH = {AU: 'Fresh, F', TI: 'Fresh Independent Study', PY: '2025', DO: '10.test/fresh'};

async function apiFixtures() {
  const stamp = Date.now().toString(36);
  const auth = await (await fetch(url + '/api/auth/register', {method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({username: 'uxhigh' + stamp, password: 'UxHigh-2026-x'})})).json();
  const token = auth.token;
  const headers = {authorization: 'Bearer ' + token};
  const task = (await (await fetch(url + '/api/tasks', {method: 'POST', headers: {...headers, 'content-type': 'application/json'},
    body: JSON.stringify({name: 'ux-high-' + stamp})})).json()).task_id;
  const base = url + '/api/tasks/' + task;

  const upload = async (name, text) => {
    const form = new FormData();
    form.append('files', new Blob([text], {type: 'application/octet-stream'}), name);
    const result = await (await fetch(base + '/import-batch', {method: 'POST', headers, body: form})).json();
    assert.ok(result.batch_id, '真实导入失败: ' + JSON.stringify(result).slice(0, 300));
    return result.batch_id;
  };
  const decide = async (batchId, status) => {
    const detail = await (await fetch(`${base}/import-batches/${batchId}?view=all&limit=200`, {headers})).json();
    const cands = (await (await fetch(`${base}/import-batches/${batchId}/candidates?view=all`, {headers})).json()).candidates;
    assert.ok(cands.length, '真实冲突候选未生成');
    const response = await fetch(`${base}/import-batches/${batchId}/candidates/${cands[0].review_id}`, {method: 'POST',
      headers: {...headers, 'content-type': 'application/json'},
      body: JSON.stringify({status, expected_policy_version: detail.policy_version})});
    assert.equal(response.status, 200, '真实决策失败: ' + await response.text());
    return cands[0].review_id;
  };

  await upload('base.ris', ris([BASE1, BASE2]));
  const batchHc = await upload('batchB.ris', ris([CONFLICT1, FRESH]));
  const batchGc = await upload('batchC.ris', ris([CONFLICT2]));
  await decide(batchHc, 'not_duplicate');           // hc：已决未启用（成员仍 staged）
  await decide(batchGc, 'not_duplicate');           // gc：已决
  const version = (await (await fetch(`${base}/import-batches/${batchGc}?view=all&limit=200`, {headers})).json()).policy_version;
  const activate = await fetch(`${base}/import-batches/${batchGc}/activate`, {method: 'POST',
    headers: {...headers, 'content-type': 'application/json'}, body: JSON.stringify({expected_policy_version: version})});
  assert.equal(activate.status, 200, '真实启用失败');
  // legacy：真实同标题对（单文件 /import → title_duplicate_reviews）→ 真实决策。
  const legacyForm = new FormData();
  legacyForm.append('file', new Blob([ris([
    {AU: 'Legacy, L', TI: 'Legacy Same Title Pair', PY: '2023', DO: '10.test/lg1'},
    {AU: 'Legacy, L', TI: 'Legacy Same Title Pair', PY: '2023', DO: '10.test/lg2'},
  ])], {type: 'application/octet-stream'}), 'legacy.ris');
  assert.equal((await fetch(base + '/import', {method: 'POST', headers, body: legacyForm})).status, 200);
  const legacy = (await (await fetch(base + '/title-duplicate-reviews?view=all', {headers})).json()).groups;
  assert.ok(legacy.length, '真实 legacy 标题组未生成');
  const decided = await fetch(`${base}/title-duplicate-reviews/${legacy[0].review_id}`, {method: 'POST',
    headers: {...headers, 'content-type': 'application/json'}, body: JSON.stringify({decision: 'not_duplicate', keeper_key: ''})});
  assert.equal(decided.status, 200, '真实 legacy 决策失败');

  // hc 现场钉子：后端 ready=1、已决组仍含 staged 成员（数据前提，UI 断言的前置）。
  const hcDetail = await (await fetch(`${base}/import-batches/${batchHc}?view=all&limit=200`, {headers})).json();
  assert.equal(hcDetail.review_counts.ready, 1, 'hc 现场后端 ready 必须为 1，实际 ' + JSON.stringify(hcDetail.review_counts));
  const hcCands = (await (await fetch(`${base}/import-batches/${batchHc}/candidates?view=all`, {headers})).json()).candidates;
  const stagedMember = hcCands[0].members.find(m => m.ref_type === 'record');
  assert.equal(stagedMember.record.state, 'staged', 'hc 已决组必须仍含 staged 成员记录');
  assert.ok(!('ref_type' in legacy[0].members[0]) && 'zotero_key' in legacy[0].members[0],
    '真实 legacy 成员形状钉子：无 ref_type、有 zotero_key');

  const session = {token, task, batchHc, batchGc, url};
  fs.writeFileSync(path.join(sessionDir, 'ux_high_session.json'), JSON.stringify(session, null, 2));
  return session;
}

(async () => {
  const fixtures = await apiFixtures();
  const browser = await chromium.launch({headless: true});
  const errors = [];
  const page = await browser.newPage({viewport: {width: 1440, height: 950}, locale: 'zh-CN'});
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(token => sessionStorage.setItem('rf_token', token), fixtures.token);
  const check = async (name, fn) => { await fn(); console.log('PASS', name); };

  const badge = key => page.locator(`#wf-review-tab-${key} .count`);
  const activationButton = () => page.locator('#wf-import-activation button');
  const openBatch = async filename => {
    // 批次列表经应用自身导航入口渲染（I.list 会切到核验页签并重建列表，
    // 避免依赖后台预渲染的时序）；随后打开目标批次。
    await page.evaluate(() => window.WorkflowImports.list());
    const card = page.locator('.history-batch').filter({hasText: filename});
    await card.waitFor({state: 'visible', timeout: 15000});
    await card.getByRole('button').first().click();
    // 等待加载完成：「≥」前缀消失（loadState complete）且工具条就绪。
    await page.waitForFunction(() => {
      const counts = document.querySelectorAll('#wf-import-review .tabs .count');
      return counts.length === 5 && [...counts].every(c => !c.textContent.startsWith('≥'));
    }, null, {timeout: 30000});
  };

  await page.goto(url + '/#workspace/import');
  await page.waitForFunction(() => S.user && S.task && document.getElementById('io-import-tabs'));

  /* ---------- 高4：hc 现场（已决未启用）三口径一致 + 记录可见 ---------- */
  await check('高4: hc 待启用页签计数=1 且不含「≥」（加载完成）', async () => {
    await openBatch('batchB.ris');
    assert.equal(await badge('ready').textContent(), '1');
  });
  await check('高4: hc 启用按钮计数 (1) 与页签一致且可用', async () => {
    const button = activationButton();
    assert.match(await button.textContent(), /\(1\)/);
    assert.equal(await button.isDisabled(), false);
  });
  await check('高4: hc 待启用记录在列表可见（queue 有 1 条，可选中阅读）', async () => {
    await page.locator('#wf-review-tab-ready').click();
    const items = page.locator('#wf-import-review .queue .queue-item');
    assert.equal(await items.count(), 1);
    await items.first().click();
    assert.match(await page.locator('.main-panel .card h3').first().textContent(), /Conflict Base One Variant/);
  });
  await check('高4: hc 已决未启用组不以组形态进已完成（成员以记录形态可见，无“记录不可见”）', async () => {
    await page.locator('#wf-review-tab-completed').click();
    const groupCards = page.locator('.main-panel .card', {hasText: '这几条记录是同一篇文献吗？'})
      .or(page.locator('.main-panel .card', {hasText: 'Are these records the same article?'}));
    assert.equal(await groupCards.count(), 0);
  });
  await check('高4: 真实启用——弹窗如实预告 1 条，启用后按钮 (0)、已完成出现「已启用」组', async () => {
    await activationButton().click();
    const dialog = page.locator('dialog[aria-labelledby="wf-activate-title"]');
    await dialog.waitFor({state: 'visible'});
    assert.match(await dialog.innerText(), /1/); // 「本次将启用 1 条已核验记录。」
    await dialog.locator('button.primary').click();
    await page.waitForFunction(() => document.querySelector('#wf-import-activation button')?.textContent.includes('(0)'), null, {timeout: 30000});
    assert.equal(await badge('ready').textContent(), '0');
    assert.equal(await activationButton().isDisabled(), true);
    await page.locator('#wf-review-tab-completed').click();
    // 选中已决组（标题为 active 成员文献名，区别于 fresh 记录）再读阅读卡。
    const queueItem = page.locator('#wf-import-review .queue .queue-item:has(.qtitle:text-is("Conflict Base One"))');
    await queueItem.waitFor({state: 'visible', timeout: 15000});
    await queueItem.first().click();
    const groupCard = page.locator('.main-panel .card');
    assert.match(await groupCard.locator('h3').first().textContent(), /这些记录是否为同一篇文献？|Are these records the same article\?/,
      '选中项必须是候选组卡');
    assert.equal(await groupCard.locator('.badge').filter({hasText: /^已启用$|^Already active$/}).count(), 1,
      '启用后的已决组必须带「已启用」状态徽标，实际：' + await groupCard.locator('.badge').allInnerTexts());
    assert.equal(await queueItem.first().locator('.badge').filter({hasText: /^已启用$|^Already active$/}).count(), 1,
      '队列项同款徽标');
  });

  /* ---------- 高4：gc（已决并启用）与 legacy（真实形状） ---------- */
  await check('高4: gc 已完成栏「已启用」组、无可启用记录', async () => {
    await openBatch('batchC.ris');
    assert.equal(await badge('ready').textContent(), '0');
    assert.match(await activationButton().textContent(), /\(0\)/);
    assert.equal(await activationButton().isDisabled(), true);
    await page.locator('#wf-review-tab-completed').click();
    const queueItem = page.locator('#wf-import-review .queue .queue-item:has(.qtitle:text-is("Conflict Base Two"))');
    await queueItem.waitFor({state: 'visible', timeout: 15000});
    await queueItem.first().click();
    assert.match(await page.locator('.main-panel .card h3').first().textContent(), /这些记录是否为同一篇文献？|Are these records the same article\?/);
    assert.equal(await page.locator('.main-panel .card .badge').filter({hasText: /^已启用$|^Already active$/}).count(), 1,
      '已决并启用的组必须带「已启用」徽标');
  });
  await check('高4: legacy 已决组徽标「已核验」（真实形状，无启用环节）', async () => {
    // legacy 标题组来自任务级 title-duplicate-reviews，打开任一批次即并入同一列表
    //（I.open 的 groups 数据源无条件加载）；已完成分类的队列中选中它再读阅读卡。
    await page.locator('#wf-review-tab-completed').click();
    const queueItem = page.locator('#wf-import-review .queue .queue-item:has(.qtitle:text-is("Legacy Same Title Pair"))');
    await queueItem.waitFor({state: 'visible', timeout: 15000});
    await queueItem.click();
    const legacyCard = page.locator('.main-panel .card').filter({hasText: 'Legacy Same Title Pair'});
    await legacyCard.waitFor({state: 'visible'});
    // 「已核验」精确匹配（不能匹配到「已核验待启用」——legacy 没有启用环节）。
    assert.equal(await legacyCard.locator('.badge').filter({hasText: /^已核验$|^Reviewed$/}).count(), 1,
      'legacy 已决组徽标必须是「已核验」，实际：' + await legacyCard.locator('.badge').allInnerTexts());
    assert.equal(await queueItem.locator('.badge').filter({hasText: /^已核验$|^Reviewed$/}).count(), 1,
      '队列项同款徽标');
  });

  /* ---------- 高7：遮罩取消 + 已用时长 ---------- */
  await check('高7: 挂起导入遮罩可取消并恢复交互，已用时长逐秒可见', async () => {
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    await page.route('**/import-batch', route => gate.then(() => route.fulfill({status: 503, json: {detail: 'released'}})));
    await page.route('**/import-progress/**', route => route.fulfill({json: {job_id: 'x', phase: 'parse', percent: 5}}));
    await page.locator('#io-import-tab-report').click();
    await page.setInputFiles('#importFile', {
      name: 'hang.ris', mimeType: 'application/octet-stream',
      buffer: Buffer.from(ris([{AU: 'Hang, H', TI: 'Hang Study', PY: '2026', DO: '10.test/hang'}]), 'utf8')});
    await page.locator('#ioImportStart').click();
    await page.waitForSelector('#importBusyOverlay:not([hidden])');
    const cancel = page.locator('#importBusyCancel');
    assert.equal(await cancel.isVisible(), true, '遮罩内必须存在可见的取消按钮');
    assert.equal(await cancel.isDisabled(), false);
    await page.waitForFunction(() => /已等待|elapsed|秒/.test(document.getElementById('importBusyElapsed')?.textContent || ''), null, {timeout: 8000});
    assert.equal(await page.evaluate(() => [...document.body.children].filter(node => node.inert).length > 0), true,
      '遮罩期间 body 其余内容应 inert');
    await cancel.click();
    await page.waitForFunction(() => document.getElementById('importBusyOverlay').hidden, null, {timeout: 8000});
    assert.equal(await page.evaluate(() => [...document.body.children].some(node => node.inert)), false,
      '取消后 inert 必须全部恢复');
    await page.waitForFunction(() => (document.querySelector('.toast')?.textContent || '').includes('已取消等待导入结果'), null, {timeout: 8000});
    assert.equal(await page.evaluate(() => document.getElementById('ioImportStart').disabled), false,
      '取消后页面交互恢复（导入按钮可再点）');
    release();
    await page.unrouteAll({behavior: 'ignore'});
  });

  assert.deepEqual(errors, []);
  console.log(JSON.stringify({checks: 8, errors, fixtures: {task: fixtures.task, batchHc: fixtures.batchHc, batchGc: fixtures.batchGc}}));
  await browser.close();
})().catch(error => {
  console.error(String(error.stack || error).replace(/Bearer [^\s'"\u001b]+/g, 'Bearer [redacted]'));
  process.exit(1);
});
