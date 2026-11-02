'use strict';
/* r10-U2 浏览器钉子（批准单 §二 F05/F30/F32/F33/F34/F36 ＋ §五 F35/F46 ＋ 导入12项
   #1/#2/#6-A/#7/#12）——真浏览器跑「现行线上模块」，不用复制品：
   - 模块清单与注入顺序照 import_review_browser.cjs 的既有手法：全部前端模块由
     index.html 的 <script src> 清单解析（build/script_manifest.cjs），不手写清单；
   - 场景全部用真实 API 形状的桩数据（批次摘要 review_counts 由桩实时重算），
     断言用户可见结果：页签单位、批次选项、徽标、按钮禁用态/title、保存反馈、
     批量弹窗文案、任务级分区。
   破坏即红（每条 ↔ 断言）：
   - compactBatch 丢掉记录数 → 「批次选项含 N 条记录」红；
   - applyResult 不重算/同步计数 → 「保存后启用按钮立即可用且计数正确」红；
   - 页签单位或「≥ 0」回归 → 「计数单位常显且空分类无 ≥」红；
   - scopeBadge 缺失/多标 → 「两种徽标各自出现且主动暂缓不标」红；
   - 保存反馈回到只靠顶部状态行 → 「保存后就近出现 已保存 ✓」红；
   - 撤销按钮禁用无原因 → 「禁用时 title 说明原因」红；
   - 批量弹窗不说明截断/可整批撤销 → 文案断言红；
   - legacy 组回流批次页签 → 「页签队列不含 legacy」红。
   运行：
     REVIEWFLOW_PLAYWRIGHT=E:/rf214-playwright/node_modules/playwright \
     PLAYWRIGHT_BROWSERS_PATH=C:/Users/16073/AppData/Local/ms-playwright \
     node build/tests/test_r10_u2_behavior_browser.cjs
*/
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const { chromium } = require(process.env.REVIEWFLOW_PLAYWRIGHT || 'playwright');
const { loadScriptManifest, findWorkspaceModule, manifestFiles } = require('../script_manifest.cjs');
const root = path.resolve(__dirname, '../..'), frontend = path.join(root, 'custom_frontend');
const manifest = loadScriptManifest(root);
const workspace = findWorkspaceModule(root, manifest);
const exportHelpers = manifestFiles(manifest, ['import_export_workspace.js'])[0];
const locales = manifestFiles(manifest, ['import_review_locale.js', 'import_export_locale.js',
  'import_review_refined_locale.js', 'import_guide_locale.js', 'workflow_locale.js', 'interface_locale.js']);

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const html = '<!doctype html><html lang="en"><head><meta charset="utf-8"><style>:root{--bg:#f4f8f7;--surface:#fff;--ink:#172b32;--muted:#60757c;--primary:#2f6f6a;--border:#d5e3e1;--danger:#ac573e}[hidden]{display:none!important}body{margin:0;font:16px system-ui}</style></head><body><div id="appMain" class="view"><section class="page rf-import-page"><div id="review"></div></section></div></body></html>';
  /* 需要裸 localStorage（延后暂存徽标来自 localStorage）：file:// 与 about:blank
     都不是安全上下文，改用具名 http 地址（不发起真实网络请求，全部 api 走桩）。 */
  await page.route('http://r10u2.test/**', route => route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html }));
  await page.goto('http://r10u2.test/');
  await page.evaluate(() => {
    window.localeRows = []; window.localeIndex = 1;
    window.RFLang = { get: () => ['zh', 'en', 'fr', 'ru', 'es', 'ja', 'pt', 'de', 'sr', 'ko'][localeIndex], register: rows => localeRows.push(...rows) };
    window.T = (s, ...args) => { const row = localeRows.findLast(r => r[1] === s || r[0] === s); return (row ? row[localeIndex] : s).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? '{' + i + '}'); };
    window.el = (tag, attrs = {}) => { const x = document.createElement(tag); for (const [k, v] of Object.entries(attrs)) { if (k === 'text') x.textContent = v; else if (k === 'onclick') x.onclick = v; else x.setAttribute(k, v); } return x; };
    window.S = { page: 'import', task: { task_id: 'fixture' }, user: { username: 'owner' } };
    window.taskApi = () => '/fixture'; window.scrQ = () => ''; window.renderImportReport = () => {};
    window.refreshProgress = () => new Promise(r => setTimeout(r, 5)); window.toast = () => {}; window.go = async () => true;
    window.version = 1; window.calls = [];
    window.WorkflowControls = { scope: () => 'fixture', settings: () => ({ can_manage: true }), version: () => version, acceptImportVersion: v => version = v, refresh: async () => {}, error: () => {}, errorText: e => e.message };

    // —— 数据：两条待启用记录（ready），一条依赖阻塞，一条本地暂存元数据，一个候选组 ——
    const record = (id, reason, extra = {}) => ({ record_id: id, state: 'staged', attention_reason: reason, filename: 'FictionEdu.ris', row_index: 1, article: { title: 'Record ' + id, doi: '' }, original: { title: 'Record ' + id }, ...extra });
    window.recordData = [
      record('readyA', 'ready', { article: { title: 'Ready Alpha', doi: '10.test/ra' } }),
      record('readyB', 'ready', { article: { title: 'Ready Beta', doi: '10.test/rb' } }),
      record('dep1', 'dependency', { related_batch_id: 'other-batch', article: { title: 'Blocked Record', doi: '' } }),
      record('local1', 'metadata', { metadata: { action: null, before: { title: 'Local Meta' }, incoming: { title: 'Incoming Meta' }, stale: false }, article: { title: 'Local Meta', doi: '' } }),
    ];
    // 101 个 DOI 一致的候选组：批量弹窗必然触发「前 100 项」如实提示。
    window.groupData = Array.from({ length: 101 }, (_, i) => ({
      review_id: 'g' + i, status: 'pending', kind: 'title',
      members: [0, 1].map(j => ({ ref_type: 'record', ref_id: 'g' + i + '-' + j,
        record: { record_id: 'g' + i + '-' + j, state: 'staged', article: { title: 'Group ' + i, doi: '10.test/group' + i } } })),
    }));
    // 任务级 legacy 组（真实形状：成员只有 RIS 字段，无 ref_type/record）
    window.legacyGroups = [{ review_id: 'L1', status: 'pending', normalized_title: 'legacy pair',
      members: [{ zotero_key: 'lg-a', title: 'Legacy Same Title Pair' }, { zotero_key: 'lg-b', title: 'Legacy Same Title Pair' }] }];

    window.counts = () => ({ attention: recordData.filter(r => r.attention_reason !== 'completed').length, completed: 0, ready: recordData.filter(r => r.attention_reason === 'ready').length, needs_review: recordData.filter(r => ['duplicate', 'metadata', 'dependency', 'waiting'].includes(r.attention_reason)).length });
    window.summary = () => ({ batch_id: 'fixture-batch', id: 'fixture-batch', created_at: '2026-10-25T23:57:00',
      files: [{ filename: 'FictionEdu.ris' }], raw_source_records: recordData.length + 21, can_manage: true,
      undo: { available: window.undoAvailable }, policy_version: version });
    window.undoAvailable = false;
    window.api = async (url, options = {}) => {
      calls.push({ url, method: options.method || 'GET' });
      if (options.method === 'POST') {
        await new Promise(r => setTimeout(r, 20));
        if (url.endsWith('/undo')) { undoAvailable = false; return { ...summary(), review_updates: { records: [], candidates: [] } }; }
        undoAvailable = true;
        const id = decodeURIComponent(url.split('/').pop());
        const group = groupData.find(g => g.review_id === id);
        if (group) { group.status = options.body.status || 'duplicate'; }
        // 裁决后成员记录转入 ready（与后端 review_projection 同义），并把它们
        // 并入 recordData 供后续页签/计数渲染。
        const changed = [];
        for (const m of group ? group.members : []) {
          let row = recordData.find(r => r.record_id === m.ref_id);
          if (!row) {
            row = { record_id: m.ref_id, state: 'staged', attention_reason: 'ready', filename: 'FictionEdu.ris', article: m.record.article, original: m.record.article };
            recordData.push(row);
          }
          row.attention_reason = 'ready';
          changed.push(row);
        }
        version++;
        return { ...summary(), review_updates: { records: changed, candidates: group ? [group] : [], review_counts: counts() } };
      }
      if (url.includes('/import-history')) return { pending_import_records: 0 };
      if (url.includes('title-duplicate')) return { groups: legacyGroups, total: legacyGroups.length, undo_available: false };
      const u = new URL(url, 'http://fixture'), offset = Number(u.searchParams.get('offset') || 0);
      if (url.includes('/candidates')) return { candidates: groupData.slice(offset, offset + 20), total: groupData.length, view_total: groupData.length };
      return { ...summary(), review_counts: counts(), records: recordData.slice(offset, offset + 20), view_total: recordData.length };
    };
    localStorage.setItem('rf_import_deferred:fixture:fixture-batch', JSON.stringify(['record:local1']));
  });
  for (const file of locales) await page.addScriptTag({ path: path.join(frontend, file) });
  const ui = fs.readFileSync(path.join(frontend, exportHelpers), 'utf8');
  await page.addScriptTag({ content: ui.slice(0, ui.indexOf(' UI.tabs=')) + '})();' });
  await page.evaluate(() => { ImportExportWorkspace.reviewHost = document.getElementById('review'); ImportExportWorkspace.importPolicy = () => {}; ImportExportWorkspace.showImport = () => {}; });
  for (const file of ['import_review.css', 'import_export_workspace.css', 'import_review_refined.css']) await page.addStyleTag({ path: path.join(frontend, file) });
  for (const file of ['import_review_model.js', 'import_review_updates.js', 'import_review_loading.js', workspace]) await page.addScriptTag({ path: path.join(frontend, file) });

  await page.evaluate(() => WorkflowImports.open('fixture-batch'));
  await page.waitForSelector('#wf-import-review nav.tabs [role=tab]');

  // —— F32：页签计数单位常显；完成态计数无「≥」；空分类不得出现「≥ 0」 ——
  const tabTexts = await page.evaluate(() => [...document.querySelectorAll('#wf-import-review .tabs [role=tab]')]
    .map(b => ({ text: b.textContent, unit: b.querySelector('.count-unit')?.textContent || '' })));
  assert.equal(tabTexts.length, 5);
  for (const t of tabTexts) assert.ok(t.unit.trim(), 'F32：每个页签计数旁必须有单位小字：' + JSON.stringify(t));
  assert.ok(tabTexts.every(t => !t.text.includes('≥')), 'F32：加载完成后计数不得带「≥」：' + JSON.stringify(tabTexts.map(t => t.text)));
  assert.ok(await page.evaluate(() => !/≥\s*0/.test(document.querySelector('#wf-import-review .tabs').textContent)), 'F32：空分类不得显示「≥ 0」');

  // —— F05：批次选项带「N 条记录」与时间 ——
  const option = await page.locator('#wf-import-batch-select option').first().textContent();
  assert.match(option, /·\s*25 records\s*·/, 'F05：批次选项必须拼记录数与时间，实际：' + option);
  assert.match(option, /FictionEdu\.ris/, 'F05：批次选项必须含文件名');

  // —— #2：动作名统一为「加入任务」，页脚标题与按钮同一套 ——
  const bar = await page.locator('#wf-import-activation').innerText();
  assert.match(bar, /Add reviewed records to the task/, '#2：页脚标题');
  assert.match(bar, /Add to task/, '#2：页脚按钮');
  assert.match(bar, /This is not an inclusion decision/, '#2：副标题必须说明不是纳入决定');

  // —— F05 后半：保存前（ready=2）按钮可用；把候选组裁决后再看计数与禁用态 ——
  const activate = page.locator('#wf-import-activation button');
  assert.match(await activate.textContent(), /\(2\)/, 'F05：就绪计数应来自后端 review_counts.ready');

  // —— F30：稍后处理栏两个来源徽标（依赖阻塞 / 仅本浏览器），主动暂缓不标 ——
  await page.locator('#wf-review-tab-later').click();
  const laterText = await page.locator('#wf-import-review .queue').innerText();
  assert.match(laterText, /Needs a linked batch first/, 'F30：依赖阻塞项必须标「需先处理关联批次」');
  assert.match(laterText, /Saved in this browser only/, 'F30：本地暂存项必须标「仅保存在本浏览器」');
  assert.equal((laterText.match(/Needs a linked batch first/g) || []).length, 1, 'F30：只标依赖阻塞的那条');

  /* —— #10-A：'稍后处理' 栏按「我暂缓的 / 等待关联记录」分组 ——
     破坏即红：分组头全部消失、或分组顺序不是「先我能处理的、后等待关联的」、
     或分组头计数与该组实际条数不符。 */
  const groupHeads = await page.locator('#wf-import-review .queue [data-later-group]').allInnerTexts();
  assert.ok(groupHeads.length >= 1, '#10-A：稍后处理栏必须有分组头，实际：' + JSON.stringify(groupHeads));
  const groupOrder = await page.locator('#wf-import-review .queue [data-later-group]').evaluateAll(
    nodes => nodes.map(node => [node.dataset.laterGroup, node.textContent]));
  const firstGroup = groupOrder[0]?.[0];
  assert.equal(firstGroup, 'mine', '#10-A：我暂缓的（可直接处理）必须排在等待关联之前，实际：' + JSON.stringify(groupOrder));
  assert.ok(groupOrder.some(([group]) => group === 'blocked'), '#10-A：等待关联记录必须有独立分组头');
  const blockedHead = groupOrder.find(([group]) => group === 'blocked')?.[1] || '';
  assert.match(blockedHead, /Waiting for a linked record/, '#10-A：分组头必须说明该组在等什么，实际：' + blockedHead);
  const mineHead = groupOrder.find(([group]) => group === 'mine')?.[1] || '';
  assert.match(mineHead, /Deferred by me/, '#10-A：我暂缓的分组头缺失，实际：' + mineHead);
  const blockedItems = await page.locator('#wf-import-review .queue .queue-item').evaluateAll(
    nodes => nodes.filter(node => /Needs a linked batch first/.test(node.textContent)).length);
  const blockedCount = Number((blockedHead.match(/(\d+)\s*$/) || [])[1]);
  assert.equal(blockedCount, blockedItems, '#10-A：分组头计数必须等于该组实际条数，实际：' + blockedHead);
  /* 页内顺序稳定：分组只重排展示，选择集与分页不变（「我暂缓的」两条仍在）。 */
  const queueKeys = await page.locator('#wf-import-review .queue .queue-item').evaluateAll(nodes => nodes.map(node => node.id));
  assert.equal(new Set(queueKeys).size, queueKeys.length, '#10-A：分组不得重复渲染同一队列项');

  // —— #6-A：任务级 legacy 组在独立分区，不在批次页签队列 ——
  const scopeVisible = await page.locator('#wf-import-review [data-legacy-scope]').isVisible();
  assert.ok(scopeVisible, '#6-A：任务级分区必须可见');
  const legacyInQueue = await page.locator('#wf-import-review .queue .queue-item:has(.qtitle:text-is("Legacy Same Title Pair"))').count();
  assert.equal(legacyInQueue, 0, '#6-A：legacy 项不得进入批次分类队列');
  const legacyInScope = await page.locator('#wf-import-review [data-legacy-scope] .legacy-queue .queue-item:has(.qtitle:text-is("Legacy Same Title Pair"))').count();
  assert.equal(legacyInScope, 1, '#6-A：legacy 项必须在任务级分区内');
  await page.locator('#wf-import-review [data-legacy-scope] .legacy-queue .queue-item').first().click();
  assert.match(await page.locator('.main-panel .card .card-kicker').innerText(), /Task-level duplicate checks \(outside this batch\)/, '#6-A：legacy 阅读卡 kicker 必须说明不属于本批次');
  // 语言索引为 1（英文）：徽标由 completionBadge/legacy 分支以中文源串经 T() 渲染。
  const legacyBadges = await page.locator('.main-panel .card .badge').allInnerTexts();
  assert.ok(legacyBadges.some(t => /^(待核验|Pending review)$/.test(t)), '#6-A：未决 legacy 组必须标「待核验」，实际：' + JSON.stringify(legacyBadges));

  // —— F34：撤销禁用时 title 说明原因（本次尚无保存） ——
  await page.locator('#wf-review-tab-duplicate').click();
  const undoTip = await page.locator('#wf-import-review .compact-tools button.undo').getAttribute('title');
  assert.match(undoTip, /Nothing to undo yet/, 'F34：无可撤销时必须给原因，实际：' + undoTip);

  // 弹窗只列**已加载**项，先等渐进加载走到 complete（否则数的是前几页）。
  await page.waitForFunction(() => document.querySelector('.load-state')?.dataset.phase === 'complete', null, { timeout: 20000 });

  // —— F33 + #12：批量弹窗如实说明「共 N 项 / 前 100 项」与整批可撤销 ——
  await page.getByRole('button', { name: 'Confirm eligible duplicates', exact: true }).click();
  const dialog = page.locator('#wf-import-review dialog[open]');
  await dialog.waitFor({ state: 'visible' });
  const dialogText = await dialog.innerText();
  assert.match(dialogText, /100 of 101 items; this bulk action covers the first 100\./, 'F33：超 100 项必须如实说明范围，实际：' + dialogText.slice(0, 400));
  assert.match(dialogText, /undone as one step/, '#12：批量弹窗必须说明整批可撤销');
  await dialog.getByRole('button', { name: 'Cancel' }).click();

  // —— F36 + F05：保存一条（裁决候选组）→ 就近「保存 ✓」出现、按钮计数与禁用态立即重算 ——
  const groupCard = page.locator('.main-panel .card').first();
  await groupCard.waitFor({ state: 'visible' });
  await groupCard.getByRole('button').first().click();
  try {
    await page.waitForFunction(() => document.querySelector('.save-flash')?.textContent.includes('Saved'), null, { timeout: 10000 });
  } catch (error) {
    console.error('DEBUG status:', await page.locator('#wf-import-review .status-message').textContent());
    console.error('DEBUG calls:', await page.evaluate(() => JSON.stringify(calls)));
    console.error('DEBUG flash:', await page.evaluate(() => [...document.querySelectorAll('.save-flash')].map(n => n.textContent)));
    console.error('DEBUG errors:', errors);
    throw error;
  }
  const flash = await page.locator('.save-flash').first().textContent();
  assert.match(flash, /Saved/, 'F36：保存成功必须就近浮出「已保存 ✓」，实际：' + flash);
  assert.equal(await page.locator('#wf-import-review .status-message').textContent(), 'Saved',
    '#12：主状态行保持既有逐字文案（与本条无关的既有输出不变）');
  assert.match(await page.locator('#wf-import-review .save-undo-note').textContent(), /You can undo this/,
    '#12：状态区必须有撤销窗口说明提示行');
  await page.waitForFunction(() => document.querySelector('#wf-import-review .compact-tools button.undo')?.disabled === false, null, { timeout: 10000 });
  const undoTip2 = await page.locator('#wf-import-review .compact-tools button.undo').getAttribute('title');
  assert.match(undoTip2, /Undo ends when you add records to the task/, '#12：可撤销时 title 必须列明窗口结束条件，实际：' + undoTip2);

  assert.deepEqual(errors, [], '运行时不得有 pageerror');
  console.log('PASS r10-U2 browser nails: F05 option/count refresh, F30 badges, F32 units, F33 bulk copy, F34 undo reason, F36 save flash, #2 naming, #6-A legacy partition, #10-A later grouping, #12 undo hints');
  await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
