'use strict';
/* r10-U2 · 整页装载钉子（批准单 §二 F61 的接线端）。
 *
 * 为什么需要这一条：test_r10_u2_behavior_browser.cjs 为了隔离只注入
 * import_export_workspace.js 的**帮助前缀**（source.slice(0, indexOf(' UI.tabs='))），
 * 模块尾部的失败——例如把 `UI.oversize`/`UI.maxUploadBytes` 提前到 const 声明之前
 * 读取（TDZ）——在那份桩里根本不会执行。真实页面会把整个模块执行一遍，
 * ReferenceError 一抛，UI.oversize 就永远不存在，index.html 里 realImport 的
 * 「超限文件不上传」二次守卫（window.ImportExportWorkspace?.oversize?.(file)）
 * 也随之静默失效——超限文件照样被送上网，F61 回到原状。
 *
 * 本钉子用真实 custom_frontend/index.html ＋ 真实外链脚本（只桩网络 API），
 * 断言三件事，任何一件破坏即红：
 *   1. 装载期零 pageerror（TDZ/顺序类崩溃立即暴露）；
 *   2. UI.maxUploadBytes === 100 MB 且 UI.oversize 是函数（守卫真的挂上了）；
 *   3. UI.oversize 对新 File 的判定与上限一致（略小于/超过两例）。
 *
 * 运行：
 *   REVIEWFLOW_PLAYWRIGHT=E:/rf214-playwright/node_modules/playwright \
 *   PLAYWRIGHT_BROWSERS_PATH=C:/Users/16073/AppData/Local/ms-playwright \
 *   node build/tests/test_r10_u2_page_load_browser.cjs
 */
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const { chromium } = require(process.env.REVIEWFLOW_PLAYWRIGHT || 'playwright');
const { loadScriptManifest } = require('../script_manifest.cjs');

const root = path.resolve(__dirname, '../..'), frontend = path.join(root, 'custom_frontend');
const index = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
/* 脚本清单照样从 index.html 解析（不手写清单）：只用于报告装载了几个模块，
   实际加载走的是 index.html 自己的 <script src>，与线上完全一致。 */
const manifest = loadScriptManifest(root);

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('http://r10u2.test/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/' || url.pathname === '/index.html')
      return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: index });
    const file = path.join(frontend, decodeURIComponent(url.pathname.replace(/^\/+/, '')));
    if (file.startsWith(frontend) && fs.existsSync(file) && fs.statSync(file).isFile()) {
      const type = file.endsWith('.css') ? 'text/css'
        : file.endsWith('.json') ? 'application/json' : 'text/javascript';
      return route.fulfill({ status: 200, contentType: type + '; charset=utf-8', body: fs.readFileSync(file) });
    }
    return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
  });
  /* 全部后端请求桩成空对象：本钉子只关心前端模块装载与控制台错误。 */
  await page.route('**/api/**', route => route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }));
  await page.goto('http://r10u2.test/', { waitUntil: 'load' });
  await page.waitForTimeout(800);

  assert.deepEqual(errors, [], '整页装载不得抛未捕获脚本错误（TDZ/初始化顺序类崩溃）：' + JSON.stringify(errors));

  const state = await page.evaluate(() => ({
    hasWorkspace: typeof window.ImportExportWorkspace === 'object',
    max: window.ImportExportWorkspace?.maxUploadBytes,
    oversize: typeof window.ImportExportWorkspace?.oversize,
  }));
  assert.equal(state.hasWorkspace, true, 'ImportExportWorkspace 必须在整页装载后存在');
  assert.equal(state.max, 100 * 1024 * 1024, 'F61：前端上限必须导出为 100 MB，实际：' + state.max);
  assert.equal(state.oversize, 'function', 'F61：UI.oversize 必须是函数（realImport 二次守卫依赖它）');

  const verdicts = await page.evaluate(() => {
    const make = size => new File([new Uint8Array(0)], 'probe.ris', { type: 'text/plain' });
    const under = make(), over = make();
    Object.defineProperty(under, 'size', { value: 100 * 1024 * 1024 });
    Object.defineProperty(over, 'size', { value: 100 * 1024 * 1024 + 1 });
    return [window.ImportExportWorkspace.oversize(under), window.ImportExportWorkspace.oversize(over)];
  });
  assert.deepEqual(verdicts, [false, true], 'F61：恰好 100 MB 放行、超出 1 字节拒绝，实际：' + JSON.stringify(verdicts));

  console.log('PASS r10-U2 page-load nail: index.html 全量脚本零 pageerror，UI.oversize/maxUploadBytes 已接线（manifest ' + manifest.length + ' 个模块）');
  await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
