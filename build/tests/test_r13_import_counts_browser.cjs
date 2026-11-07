'use strict';
/* r13「批次统计显示」泳道行为钉（真实 Chromium · DOM 渲染断言）。
 *
 * 缺陷：index.html 的 RFLang.t 对每个 {n} 参数做 String()（index.html:5151），而
 * unified_import_workspace.js 的批次卡片曾把 U.literal 返回的 <span> 元素当参数传入
 * → 卡片统计行五个数字全部显示为 "[object HTMLSpanElement]"，十语言同坏，
 * 相对 2.1.3（a080fde 传纯数字）回归。
 *
 * 本钉断言用户可见结果（破坏即红）：
 *   - 每张批次卡片的统计行含各自的真实数字（两张卡片数值不同，防「只对第一张」）；
 *   - 统计行不含 "[object"；
 *   - 每项保留单位（前四项 records / 末项 groups），且单位随语言本地化；
 *   - 至少两种非中文语言（en/es）与中文模板各自渲染正确；
 *   - r14：停在「全部批次」列表切语言（reviewflow:language-changed）后，
 *     单位词随语言同步变化且**不重新请求 /import-batches**（用缓存数据重渲染）。
 *
 * 运行（Git Bash 内联前缀）：
 *   REVIEWFLOW_PLAYWRIGHT=E:/rf214-playwright/node_modules/playwright \
 *   PLAYWRIGHT_BROWSERS_PATH=C:/Users/16073/AppData/Local/ms-playwright \
 *   C:/Users/16073/AppData/Local/hermes/node/node.exe build/tests/test_r13_import_counts_browser.cjs
 */
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const { chromium } = require(process.env.REVIEWFLOW_PLAYWRIGHT || 'playwright');
const { loadScriptManifest, findWorkspaceModule, manifestFiles } = require('../script_manifest.cjs');

const root = path.resolve(__dirname, '../..'), frontend = path.join(root, 'custom_frontend');
const manifest = loadScriptManifest(root);
const workspace = findWorkspaceModule(root, manifest);
const exportHelpers = manifestFiles(manifest, ['import_export_workspace.js'])[0];
/* 真实词典文件：批次统计模板在 workflow_locale.js，记录/候选组单位在
   import_review_refined_locale.js，其余为工作区常用词条。 */
const locales = manifestFiles(manifest, ['workflow_locale.js', 'import_review_locale.js',
  'import_export_locale.js', 'import_review_refined_locale.js', 'interface_locale.js']);

/* 两批数值互不相同：任何「把参数顺序/数值串错」或「只渲染第一张卡片」都会红。 */
const BATCHES = [
  { batch_id: 'batch-A', id: 'batch-A', created_at: '2026-11-01T10:00:00',
    files: [{ filename: 'Alpha.ris' }], raw_source_records: 25, active_records: 19,
    pending_records: 4, metadata_review_count: 2, pending_candidate_groups: 3 },
  { batch_id: 'batch-B', id: 'batch-B', created_at: '2026-11-02T11:00:00',
    files: [{ filename: 'Beta.ris' }], raw_source_records: 7, active_records: 0,
    pending_records: 7, metadata_review_count: 0, pending_candidate_groups: 1 },
];

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const html = '<!doctype html><html lang="en"><head><meta charset="utf-8"></head><body><div id="appMain" class="view"><section class="page rf-import-page"><div id="review"></div></section></div></body></html>';
  /* localStorage 型模块需要安全上下文；file:// 不算，故用具名 http 地址（全部 api 走桩）。 */
  await page.route('http://r13counts.test/**', route => route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html }));
  await page.goto('http://r13counts.test/');
  await page.evaluate(batches => {
    window.localeRows = []; window.localeIndex = 1;
    window.RFLang = { get: () => ['zh-CN', 'en', 'fr', 'ru', 'es', 'ja', 'pt', 'de', 'sr', 'ko'][localeIndex],
      register: rows => localeRows.push(...rows) };
    /* 与 index.html:5147-5153 的 RFLang.t 同语义：命中词典取当前语言模板，参数
       一律 String() 化——元素节点在此变成 "[object HTMLSpanElement]"。 */
    window.T = (tpl, ...args) => {
      const key = String(tpl), row = localeRows.findLast(r => r[1] === key || r[0] === key);
      let s = row ? row[localeIndex] : key;
      if (args.length) s = s.replace(/\{(\d+)\}/g, (m, d) => args[+d] === undefined ? m : String(args[+d]));
      return s;
    };
    window.el = (tag, attrs = {}) => {
      const x = document.createElement(tag);
      for (const [k, v] of Object.entries(attrs)) {
        if (k === 'text') x.textContent = v;
        else if (k === 'onclick') x.onclick = v;
        else x.setAttribute(k, v);
      }
      return x;
    };
    window.S = { page: 'import', task: { task_id: 'fixture' }, user: { username: 'owner' } };
    window.taskApi = () => '/fixture'; window.scrQ = () => '';
    window.renderImportReport = () => {};
    window.toast = () => {}; window.go = async () => true; window.version = 1;
    window.WorkflowControls = { scope: () => 'fixture', settings: () => ({ can_manage: true }),
      version: () => version, acceptImportVersion: v => version = v, refresh: async () => {},
      error: () => {}, errorText: e => e.message };
    window.api = async () => ({ batches, total: batches.length });
  }, BATCHES);
  for (const file of locales) await page.addScriptTag({ path: path.join(frontend, file) });
  /* 只取 import_export_workspace.js 的节点/字面量/按钮/图标构造器（UI.tabs 之前），
     与既有 import_review_browser.cjs / test_r10_u2_behavior_browser.cjs 同款注入法。 */
  const ui = fs.readFileSync(path.join(frontend, exportHelpers), 'utf8');
  await page.addScriptTag({ content: ui.slice(0, ui.indexOf(' UI.tabs=')) + '})();' });
  await page.evaluate(() => {
    ImportExportWorkspace.reviewHost = document.getElementById('review');
    ImportExportWorkspace.importPolicy = () => {};
    ImportExportWorkspace.showImport = () => {};
  });
  for (const file of ['import_review_model.js', 'import_review_updates.js', 'import_review_loading.js', workspace])
    await page.addScriptTag({ path: path.join(frontend, file) });

  const cardTexts = () => page.evaluate(() =>
    [...document.querySelectorAll('#wf-import-review .history-batch')].map(card => ({
      file: card.querySelector('h3')?.textContent || '',
      counts: card.querySelector('p')?.textContent || '',
    })));
  const renderList = async localeIndex => {
    await page.evaluate(index => { window.localeIndex = index; return WorkflowImports.list(); }, localeIndex);
    await page.waitForFunction(() => document.querySelectorAll('#wf-import-review .history-batch').length === 2);
    return cardTexts();
  };

  const checks = [];
  const check = async (name, fn) => { await fn(); checks.push(name); console.log('PASS ' + name); };

  /* —— 英文（localeIndex 1）：每张卡片各自五个真实数字 + 单位，且无 [object —— */
  await check('en: 批次卡片统计行含真实数字且无 [object', async () => {
    const cards = await renderList(1);
    assert.equal(cards.length, 2, '应渲染两张批次卡片');
    const a = cards.find(c => c.file.includes('Alpha.ris')), b = cards.find(c => c.file.includes('Beta.ris'));
    assert.ok(a && b, '两张卡片都应按文件名出现：' + JSON.stringify(cards));
    assert.match(a.counts, /Source records:\s*25\s+records/, 'A 来源记录数：' + a.counts);
    assert.match(a.counts, /Activated:\s*19\s+records/, 'A 已激活数：' + a.counts);
    assert.match(a.counts, /Staged:\s*4\s+records/, 'A 暂存数：' + a.counts);
    assert.match(a.counts, /Metadata proposals:\s*2\s+records/, 'A 元数据提议数：' + a.counts);
    assert.match(a.counts, /Pending candidates:\s*3\s+groups/, 'A 待核验候选组数：' + a.counts);
    assert.match(b.counts, /Source records:\s*7\s+records/, 'B 来源记录数：' + b.counts);
    assert.match(b.counts, /Activated:\s*0\s+records/, 'B 已激活数（0 必须可见）：' + b.counts);
    assert.match(b.counts, /Staged:\s*7\s+records/, 'B 暂存数：' + b.counts);
    assert.match(b.counts, /Metadata proposals:\s*0\s+records/, 'B 元数据提议数：' + b.counts);
    assert.match(b.counts, /Pending candidates:\s*1\s+groups/, 'B 待核验候选组数：' + b.counts);
    for (const c of cards) assert.ok(!/\[object/.test(c.counts), '统计行不得出现 [object：' + c.counts);
    const all = await page.evaluate(() => document.querySelector('#wf-import-review').textContent);
    assert.ok(!/\[object (HTML\w+Element|Object)\]/.test(all), '整块导入核验区不得出现元素字符串化：' + all.slice(0, 400));
  });

  /* —— 西班牙语（localeIndex 4）：模板与单位都要本地化，数字仍可见 —— */
  await check('es: 西语模板 + 本地化单位 + 真实数字', async () => {
    const cards = await renderList(4);
    const a = cards.find(c => c.file.includes('Alpha.ris')), b = cards.find(c => c.file.includes('Beta.ris'));
    assert.ok(a && b, '两张卡片都应按文件名出现：' + JSON.stringify(cards));
    assert.match(a.counts, /Registros de origen:\s*25\s+registros/, 'A 西语来源记录数：' + a.counts);
    assert.match(a.counts, /Activados:\s*19\s+registros/, 'A 西语已激活数：' + a.counts);
    assert.match(a.counts, /En espera:\s*4\s+registros/, 'A 西语暂存数：' + a.counts);
    assert.match(a.counts, /Propuestas de metadatos:\s*2\s+registros/, 'A 西语元数据提议数：' + a.counts);
    assert.match(a.counts, /Candidatos pendientes:\s*3\s+grupos/, 'A 西语候选组数：' + a.counts);
    assert.match(b.counts, /Registros de origen:\s*7\s+registros/, 'B 西语来源记录数：' + b.counts);
    for (const c of cards) assert.ok(!/\[object/.test(c.counts), '西语统计行不得出现 [object：' + c.counts);
  });

  /* —— 中文（localeIndex 0）：模板与单位「条记录 / 组」—— */
  await check('zh: 中文模板 + 条记录/组 单位 + 真实数字', async () => {
    const cards = await renderList(0);
    const a = cards.find(c => c.file.includes('Alpha.ris'));
    assert.ok(a, 'A 卡片应存在：' + JSON.stringify(cards));
    assert.match(a.counts, /来源记录：\s*25\s+条记录/, 'A 中文来源记录数：' + a.counts);
    assert.match(a.counts, /已激活：\s*19\s+条记录/, 'A 中文已激活数：' + a.counts);
    assert.match(a.counts, /暂存：\s*4\s+条记录/, 'A 中文暂存数：' + a.counts);
    assert.match(a.counts, /元数据提议：\s*2\s+条记录/, 'A 中文元数据提议数：' + a.counts);
    assert.match(a.counts, /待核验候选：\s*3\s+组/, 'A 中文候选组数：' + a.counts);
    for (const c of cards) assert.ok(!/\[object/.test(c.counts), '中文统计行不得出现 [object：' + c.counts);
  });

  /* —— 每张卡片只出现一次统计行，且数字与自身批次对应（防跨卡片串值） —— */
  await check('统计行数字与各自批次严格对应（不跨卡片串值）', async () => {
    const cards = await cardTexts();
    const a = cards.find(c => c.file.includes('Alpha.ris')), b = cards.find(c => c.file.includes('Beta.ris'));
    assert.ok(a && b, '两张卡片都应按文件名出现：' + JSON.stringify(cards));
    assert.ok(!/\b19\b/.test(b.counts), 'B 卡片不得出现 A 的 19：' + b.counts);
    assert.ok(!/\b25\b/.test(b.counts), 'B 卡片不得出现 A 的 25：' + b.counts);
    assert.ok(!/\b7\b/.test(a.counts), 'A 卡片不得出现 B 的 7：' + a.counts);
  });

  /* —— r14：列表视图切语言（语言切换事件）必须重渲染，单位词同步，且不发请求 ——
   * 缺陷：批次统计行的单位词是渲染时译好再拼进 T() 参数的；旧监听器只在
   * batch 存在（详情视图）时 render()，「全部批次」列表停留时切语言→句子变、
   * records/groups 仍是旧语言。修复后用缓存数据重渲染，不多发 /import-batches。 */
  await check('r14: 列表视图切语言后单位词同步且不重新请求', async () => {
    /* 先在英文下渲染列表，再把 api 包成计数器（此后任何请求都会 +1）。 */
    const before = await renderList(1);
    assert.match(before.find(c => c.file.includes('Alpha.ris')).counts, /25\s+records/,
      '切语言前应为英文单位：' + JSON.stringify(before));
    await page.evaluate(() => {
      window.__r14ApiCalls = 0;
      const original = window.api;
      window.api = (...args) => { window.__r14ApiCalls += 1; return original(...args); };
    });
    /* 只切换语言并派发事件——不调用 I.list()，模拟用户停在列表点语言菜单。 */
    await page.evaluate(() => {
      window.localeIndex = 4;  // es
      document.dispatchEvent(new CustomEvent('reviewflow:language-changed', { detail: 'es' }));
    });
    await page.waitForFunction(() => {
      const card = document.querySelector('#wf-import-review .history-batch p');
      return card && /Registros de origen/.test(card.textContent);
    }, null, { timeout: 5000 });
    const after = await cardTexts();
    assert.equal(after.length, 2, '切语言后仍应有两张批次卡片：' + JSON.stringify(after));
    const a = after.find(c => c.file.includes('Alpha.ris')), b = after.find(c => c.file.includes('Beta.ris'));
    assert.ok(a && b, '两张卡片都应按文件名出现：' + JSON.stringify(after));
    assert.match(a.counts, /Registros de origen:\s*25\s+registros/, '切语言后 A 单位应为西语：' + a.counts);
    assert.match(a.counts, /Candidatos pendientes:\s*3\s+grupos/, '切语言后 A 候选组单位应为西语：' + a.counts);
    assert.match(b.counts, /Registros de origen:\s*7\s+registros/, '切语言后 B 单位应为西语：' + b.counts);
    assert.ok(!/\brecords\b/.test(a.counts), '切语言后不得残留旧语言单位 records：' + a.counts);
    assert.ok(!/\bgroups\b/.test(a.counts), '切语言后不得残留旧语言单位 groups：' + a.counts);
    const calls = await page.evaluate(() => window.__r14ApiCalls);
    assert.equal(calls, 0, '语言切换重渲染不得重新请求 /import-batches，实际请求 ' + calls + ' 次');
  });

  assert.deepEqual(errors, [], '页面不得有脚本错误：' + errors.join(' | '));
  console.log(`R13 import counts browser nail: PASS (${checks.length} checks)`);
  await browser.close();
})().catch(error => {
  console.error('R13 import counts browser nail: FAIL');
  console.error(error && error.stack || error);
  process.exit(1);
});
