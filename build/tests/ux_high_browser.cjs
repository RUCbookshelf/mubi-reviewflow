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
 * - 高7（F53）：导入全屏遮罩挂起（正则路由钉住 /import 与 /import-batch——
 *   单文件导入实际请求 /import 且带 ?screener= 查询串，glob 拦不住——
 *   挂起不响应）→ 取消按钮存在可点击 → 点击后遮罩收起、inert 恢复、
 *   提示如实；已用时长（已等待 {0} 秒）逐秒可见。
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

/* r10.1：夹具期取数加窄重试——同机多套件并行时实例偶发慢响应导致 undici
   “terminated”，夹具阶段整跑崩掉；3 次×600ms 退避只兜网络层抖动，
   4xx/5xx 语义响应原样抛出（不掩盖真实接口错误）。 */
const fetchRetry = async (request, attempts = 3) => {
  let lastError;
  for (let i = 0; i < attempts; i++) {
    try { return await request(); }
    catch (error) {
      lastError = error;
      if (!/terminated|ECONN|socket|network/i.test(String(error && error.message))) throw error;
      await new Promise(resolve => setTimeout(resolve, 600 * (i + 1)));
    }
  }
  throw lastError;
};

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

  await upload('base.ris', ris([BASE1, BASE2, {
    AU: 'Charlie, C', TI: 'Progressive Base Study', PY: '2024', DO: '10.test/base3', ID: 'SHAREKEY3'}]));
  const batchHc = await upload('batchB.ris', ris([CONFLICT1, FRESH]));
  const batchGc = await upload('batchC.ris', ris([CONFLICT2]));
  // batchP：渐进加载竞争现场——23 条记录（>20，records feed 必有第二页），
  // 内含一个【未裁决】的 identity_conflict 候选（保存动作留给 UI 完成）。
  const batchP = await upload('batchP.ris', ris([{
    AU: 'Charlie, C', TI: 'Progressive Base Study Variant', PY: '2024', DO: '10.test/other3', ID: 'SHAREKEY3'},
    ...Array.from({length: 22}, (_, i) => ({
      AU: `Fill, F${i}`, TI: `Progressive Fill Study ${i}`, PY: '2025', DO: `10.test/pfill${i}`}))]));
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
  // batchP 数据前提：records feed 必须分页（>20 条），且存在未裁决候选。
  const pDetail = await (await fetch(`${base}/import-batches/${batchP}?view=all&limit=200`, {headers})).json();
  assert.ok(pDetail.view_total > 20, 'batchP 记录数必须 >20 才有第二页渐进加载，实际 ' + pDetail.view_total);
  const pCands = (await (await fetch(`${base}/import-batches/${batchP}/candidates?view=all`, {headers})).json()).candidates;
  assert.equal(pCands.filter(c => c.status === 'pending' || c.status === 'undecided').length, 1,
    'batchP 必须恰好有一个待裁决候选（UI 保存动作的目标）');

  const session = {token, task, batchHc, batchGc, batchP, url};
  fs.writeFileSync(path.join(sessionDir, 'ux_high_session.json'), JSON.stringify(session, null, 2));
  return session;
}

(async () => {
  /* r10.1：夹具整体带重试——实例慢响应时 undici 偶发 terminated 直接崩整跑；
     重试会另注册一个时间戳后缀用户，无副作用；语义性 4xx/5xx 不重试原样抛。 */
  const fixtures = await (async () => {
    for (let attempt = 0; ; attempt++) {
      try { return await apiFixtures(); }
      catch (error) {
        if (attempt >= 2 || !/terminated|ECONN|socket|network/i.test(String(error && error.message))) throw error;
        await new Promise(resolve => setTimeout(resolve, 700 * (attempt + 1)));
      }
    }
  })();
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
    // 等待加载完成（r10-F32 后口径）：「≥」前缀在 0 计数+加载中不再出现，
    // 不能再当唯一哨兵——改等真负载信号：宿主 aria-busy 解除 + 无「加载中」
    // 空态文案 + 5 个计数就绪 + 无 ≥ 前缀，四条同时成立才算加载完成。
    await page.waitForFunction(() => {
      const host = document.querySelector('#wf-import-review');
      const counts = document.querySelectorAll('#wf-import-review .tabs .count');
      const loadingText = document.querySelector('#wf-import-review .card.empty h3');
      return host && !host.hasAttribute('aria-busy')
        && counts.length === 5 && [...counts].every(c => !c.textContent.startsWith('≥'))
        && !(loadingText && loadingText.textContent.includes('loading'));
    }, null, {timeout: 30000});
    // 再等一个渲染帧，规避「哨兵通过瞬间计数仍在更新」的残余竞态。
    await page.evaluate(() => new Promise(requestAnimationFrame));
  };

  await page.goto(url + '/#workspace/import');
  await page.waitForFunction(() => S.user && S.task && document.getElementById('io-import-tabs'));

  /* ---------- 高4：hc 现场（已决未启用）三口径一致 + 记录可见 ---------- */
  await check('高4: hc 待启用页签计数=1 且不含「≥」（加载完成）', async () => {
    await openBatch('batchB.ris');
    /* r10.1：徽标在加载期合法经历 0→终值迁移，load-sentinel 通过后仍可能有
       一个渲染帧的残余——改为有界等落定（10s），终值仍钉死 '1' 不放松；
       真回归（终值 0）由本断言超时红 + 下一检查「启用按钮 (1) 且可用」双拦。 */
    await page.waitForFunction(() => {
      const badgeReady = document.querySelector('#wf-review-tab-ready .count');
      return badgeReady && badgeReady.textContent === '1';
    }, null, {timeout: 10000});
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
  await check('高4/#6-A: legacy 已决组在任务级分区可见可点，徽标「已核验」（真实形状，无启用环节）', async () => {
    // r10-U2（导入12项 #6-A）：legacy 标题组（任务级 title-duplicate_reviews）
    // 不再并入批次五个分类页签的队列，改由独立分区 [data-legacy-scope]
    // 承载。旧钉「打开任一批次即并入同一列表」随行为变更同批更新（钉的是
    // 新语义：仍在批次页可见、可点、徽标不变），校验强度不减。
    const scope = page.locator('#wf-import-review [data-legacy-scope]');
    await scope.waitFor({state: 'visible', timeout: 15000});
    for (const key of ['duplicate', 'metadata', 'ready', 'completed', 'later']) {
      await page.locator('#wf-review-tab-' + key).click();
      // 批次页签的队列不得含任务级项（批次页只呈现批次范围）。
      assert.equal(await page.locator('#wf-import-review .queue .queue-item:has(.qtitle:text-is("Legacy Same Title Pair"))').count(), 0,
        'legacy 项不得进入批次分类队列（页签 ' + key + '）');
    }
    const queueItem = scope.locator('.legacy-queue .queue-item:has(.qtitle:text-is("Legacy Same Title Pair"))');
    await queueItem.waitFor({state: 'visible', timeout: 15000});
    assert.equal(await queueItem.locator('.badge').filter({hasText: /^已核验$|^Reviewed$/}).count(), 1,
      '任务级分区的已决项徽标必须是「已核验」，实际：' + await queueItem.locator('.badge').allInnerTexts());
    await queueItem.click();
    const legacyCard = page.locator('.main-panel .card').filter({hasText: 'Legacy Same Title Pair'});
    await legacyCard.waitFor({state: 'visible'});
    // 「已核验」精确匹配（不能匹配到「已核验待启用」——legacy 没有启用环节）。
    assert.equal(await legacyCard.locator('.badge').filter({hasText: /^已核验$|^Reviewed$/}).count(), 1,
      'legacy 已决组徽标必须是「已核验」，实际：' + await legacyCard.locator('.badge').allInnerTexts());
  });

  /* ---------- 高4（评审补强）：渐进加载竞争——后台阶段保存后权威计数不被快照回退 ---------- */
  await check('高4: 渐进加载竞争——background 中保存 not_duplicate，complete 后四口径一致且可点击', async () => {
    // 把 records feed 第二页（offset=20）人为延后：batchP 有 23 条记录（>20）。
    // loader 在 resume() 里逐页拉取（import_review_loading.js:10），offset=20
    // 挂起期间 phase==='background'，首屏已可交互——此时经 UI 完成一次
    // not_duplicate 保存；放行后 loader 的 notify 会用首屏快照重建 batch，
    // 若 applyResult 不持久化权威 review_counts，ready 会被回退为保存前旧值。
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    await page.route('**/import-batches/**', async route => {
      const request = route.request();
      const target = request.url();
      if (request.method() === 'GET' && target.includes('view=all')
          && target.includes('offset=20') && !target.includes('/candidates')) await gate;
      await route.continue();
    });
    await page.evaluate(() => window.WorkflowImports.list());
    const card = page.locator('.history-batch').filter({hasText: 'batchP.ris'});
    await card.waitFor({state: 'visible', timeout: 15000});
    await card.getByRole('button').first().click();
    await page.waitForFunction(() => document.querySelector('.load-state')?.dataset.phase === 'background',
      null, {timeout: 30000});
    // 待裁决候选组在默认「重复检查」页签且自动选中；经 UI 点「不同文献，分别保留」。
    const groupCard = page.locator('.main-panel .card');
    await groupCard.waitFor({state: 'visible'});
    await groupCard.getByRole('button').filter({hasText: /不同文献|Not duplicates/}).click();
    await page.waitForFunction(() => document.querySelector('#wf-import-activation button')?.textContent.includes('(1)'),
      null, {timeout: 30000});
    // 后台阶段：真实计数已生效（按钮 (1)），页签带「≥」前缀属加载提示而非旧值。
    assert.match(await badge('ready').textContent(), /^≥\s*1/,
      'background 阶段页签应为「≥ 1」，实际 ' + await badge('ready').textContent());
    release();
    await page.waitForFunction(() => {
      const counts = document.querySelectorAll('#wf-import-review .tabs .count');
      return counts.length === 5 && [...counts].every(c => !c.textContent.startsWith('≥'));
    }, null, {timeout: 30000});
    // 四口径一致：页签徽标 = 启用按钮 (N) = 后端 review_counts.ready = 页签可见记录数。
    assert.equal(await badge('ready').textContent(), '1',
      'complete 后页签计数不得被渐进加载快照回退（评审指出的回归路径）');
    assert.match(await activationButton().textContent(), /\(1\)/);
    assert.equal(await activationButton().isDisabled(), false, '启用按钮必须可点击');
    const detail = await (await fetch(`${url}/api/tasks/${fixtures.task}/import-batches/${fixtures.batchP}?view=all&limit=1`,
      {headers: {authorization: 'Bearer ' + fixtures.token}})).json();
    assert.equal(detail.review_counts.ready, 1, '后端 review_counts.ready 必须为 1');
    await page.locator('#wf-review-tab-ready').click();
    const items = page.locator('#wf-import-review .queue .queue-item');
    assert.equal(await items.count(), 1, '待启用页签可见记录数必须为 1');
    await items.first().click();
    const cardAction = page.locator('.main-panel .card [data-import-activation]');
    assert.equal(await cardAction.count(), 1, '记录卡上应渲染启用动作');
    assert.equal(await cardAction.isDisabled(), false, '记录卡上的启用动作必须可点击');
    await page.unrouteAll({behavior: 'ignore'});
  });

  /* ---------- 高7：遮罩取消 + 已用时长 ---------- */
  await check('高7: 挂起导入遮罩可取消并恢复交互，已用时长逐秒可见', async () => {
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    // 确定性挂起闸门（终局意见修复）：单文件导入实际请求 /import（index.html realImport：
    // files.length>1 才走 /import-batch），且两者都带 ?screener= 查询串——glob **/import-batch
    // 匹配不到带查询串的 URL，此前 PASS 依赖真实请求恰好未完成。此处以正则同时钉住
    // /import 与 /import-batch（含查询串），主请求在 release 前永不落定；取消后浏览器
    // 会中止该 fetch，route.fulfill 可能对已中止的路由拒绝，吞掉该拒绝避免未处理 Promise。
    const hangImport = route => gate.then(
      () => route.fulfill({status: 503, json: {detail: 'released'}})).catch(() => {});
    await page.route(/\/import(-batch)?(\?.*)?$/, hangImport);
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
  console.log(JSON.stringify({checks: 9, errors, fixtures: {task: fixtures.task, batchHc: fixtures.batchHc, batchGc: fixtures.batchGc}}));
  await browser.close();
})().catch(error => {
  console.error(String(error.stack || error).replace(/Bearer [^\s'"\u001b]+/g, 'Bearer [redacted]'));
  process.exit(1);
});
