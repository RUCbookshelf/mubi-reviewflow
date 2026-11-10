'use strict';
/* R8 · W5 PWA 离线壳：真实 Service Worker 生命周期与离线导航钉子
   （2.1.4 登记尾巴③：三层覆盖里缺「真浏览器 SW 层」）

   为什么必须起真 SW：`w5_pwa_sw.test.cjs` 是 Node vm 桩（策略函数/假 caches），
   `w5_pwa_banner_browser.cjs` 刻意 404 `/sw.js`、用 MessageEvent 手工注入提示条。
   两者都碰不到「注册 → activate → controller → 真实 CacheStorage → setOffline
   后 reload 由壳快照交付」这条链路。本文件补的正是这一层。

   链路（根路径与 `/reviewflow/` 子路径各跑一遍，验证 scope 相对化同构）：
   1) 本地静态托管 custom_frontend（只读）：真实返回 sw.js、manifest.webmanifest
      与带 ?v= 的资产；`/api/*` 返回 JSON（模拟真实后端），且全站 no-store，
      排除 HTTP 缓存冒充 SW 缓存。
   2) `page.goto(app)` → 等 activate → reload 一次拿 controller（sw.js 没有
      `clients.claim()`，首次加载不受控，这是生产同款行为）。
   3) 在线受控加载后：`caches.keys()` 含 `rf-runtime-20261030-r8-W5PWA1`，且该
      缓存里有 `offline_banner.js?v=…` 条目；shell 缓存条目键 ===
      `registration.scope`，快照体是 HTML 壳。
   4) 负向：受控页面 fetch `/api/…` **且** 地址栏导航到 scope 内的 `/api/…`
      （JSON，content-type application/json）→ 任何缓存都不得出现 /api/ 条目，
      壳快照也不得被 JSON 顶掉（r2 finding #2 的真实浏览器侧）。
   5) `context.setOffline(true)` → `page.reload()` → 页面仍渲染应用壳，且
      `#rfOfflineSnapshotBanner` 出现——走真实 SW `resultingClientId` 标记 +
      `postMessage` 查询回执，不注入任何 MessageEvent。
   6) 离线兜底期间 `navigator.serviceWorker.controller` 必须仍在，确认不是
      HTTP 缓存或浏览器原生错误页。

   时序：`waitUntil()` 以 100ms 间隔轮询条件（不是固定 sleep），配合
   `page.waitForSelector` 与显式 `context.serviceWorkers()` 等待；临时端口
   `listen(0)`。注意 `page.waitForFunction` 不能用来跑 async 判定——Playwright
   1.64 会把 async 函数返回的 Promise 对象当 truthy 立即返回（实测
   `async () => false` 14ms 就 resolve），那会静默假绿。

   运行（CI 同款约定，见 .github/workflows/release.yml 的 Provision playwright）：
     REVIEWFLOW_PLAYWRIGHT=<path-to-playwright> \
     PLAYWRIGHT_BROWSERS_PATH=<browsers> \
     node build/tests/w5_pwa_sw_browser.cjs
   未配置 playwright 时本文件不参与门禁（由 test_r8_w5_pwa_static_guards.py 的
   skipif 负责），因此这里不做静默跳过——缺依赖就显式失败，避免假绿。 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.REVIEWFLOW_PLAYWRIGHT || 'playwright');

const FRONTEND = process.env.RF_W5_FRONTEND
  ? path.resolve(process.env.RF_W5_FRONTEND)
  : path.resolve(__dirname, '..', '..', 'custom_frontend');

/* sw.js 的版本令牌是三层策略共享的事实来源（静态守卫 test_r8_w5_pwa_static_guards.py
   同样钉字面量）；这里刻意写死期望名：版本名漂移而浏览器层没跟上时要变红。 */
const VERSION = '20261030-r8-W5PWA1';
const RUNTIME_CACHE = 'rf-runtime-' + VERSION;
const SHELL_CACHE = 'rf-shell-' + VERSION;
const VERSIONED_ASSET = 'offline_banner.js?v=' + VERSION;
const SUBPATH = '/reviewflow/';

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.webmanifest': 'application/manifest+json',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
};

/* 静态托管 custom_frontend（只读）。
   - `/` 与 `/reviewflow/` 都映射到 index.html（子路径部署同构）。
   - 任意含 `api` 路径段的 GET 返回 JSON：根部署是 `/api/x`，子路径部署
     `/reviewflow/api/x`（r2 finding #2 的场景 C）。
   - 全站 `Cache-Control: no-store`：让断言只能由 CacheStorage（SW）满足，
     排除 Chromium HTTP 缓存离线兜底的假绿。 */
function serveFrontend() {
  const root = path.resolve(FRONTEND);
  const server = http.createServer((req, res) => {
    const pathname = decodeURIComponent(String(req.url).split('?')[0].split('#')[0]);
    /* API 路径：/api/…、/reviewflow/api/…（前后缀都要能判出来）。 */
    if (/(^|\/)api(\/|$)/.test(pathname)) {
      res.writeHead(200, {
        'content-type': 'application/json; charset=utf-8',
        'cache-control': 'no-store',
      });
      res.end(JSON.stringify({ ok: true, api: true, path: pathname }));
      return;
    }
    let relPath = pathname;
    if (relPath === SUBPATH.slice(0, -1)) relPath = SUBPATH;
    if (relPath.startsWith(SUBPATH)) relPath = relPath.slice(SUBPATH.length - 1);
    const rel = relPath === '/' || relPath === '' ? 'index.html' : relPath.replace(/^\/+/, '');
    const file = path.resolve(root, rel);
    if (!(file === root || file.startsWith(root + path.sep)) ||
        !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' });
      res.end('not found');
      return;
    }
    res.writeHead(200, {
      'content-type': MIME[path.extname(file).toLowerCase()] || 'application/octet-stream',
      'cache-control': 'no-store',
    });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

/* 条件轮询（不靠固定 sleep，也不用 page.waitForFunction 跑 async 判定——
   实测 Playwright 1.64 的 waitForFunction 把 async 函数返回的 Promise 对象
   直接当 truthy 立即返回，`async () => false` 14ms 就 resolve，会静默假绿）。
   每 100ms 重新求值一次，直到条件为 true 或超时；超时把最后一次观测值打进
   错误信息。 */
const POLL_INTERVAL_MS = 100;

/* 2.1.5 收尾清债 B（全量跑偶发超时、单跑绿）：每个等待点的预算统一从固定
   30s 提到 60s（可经 RF_W5_WAIT_BUDGET_MS 覆写），只放大「等多久」，不碰
   任何断言本身。全量 battery 下机器高负载时 SW install/activate 或冷导航
   偶发超 30s；等待条件本身已是条件轮询（waitUntil/probe），无需放宽。 */
const WAIT_BUDGET_MS = Number(process.env.RF_W5_WAIT_BUDGET_MS || 60000);

async function waitUntil(probe, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  for (;;) {
    try {
      last = await probe();
    } catch (e) {
      last = { error: String((e && e.message) || e) };
    }
    if (last === true) return;
    if (Date.now() >= deadline) {
      throw new Error(`等待超时（${timeoutMs}ms）：${label}；最后观测=${JSON.stringify(last)}`);
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
  }
}

/* 等 SW 注册并 activate（sw.js 无 install 钩子、无 skipWaiting，但首次注册时
   一旦 install/activate 完成即成为 active）。 */
async function waitForActivated(context, page) {
  /* 显式 serviceWorkers() 等待：拿到 worker 句柄再读它的状态。 */
  await waitUntil(async () => context.serviceWorkers().length > 0
    ? true : 'no-service-worker', WAIT_BUDGET_MS, '浏览器上下文里出现 service worker');
  const workerUrl = context.serviceWorkers()[0].url();
  assert.match(workerUrl, /\/sw\.js$/, `SW 脚本 URL 形态漂移：${workerUrl}`);
  await waitUntil(() => page.evaluate(async () => {
    const reg = await navigator.serviceWorker.getRegistration();
    if (!reg || !reg.active) return 'no-active';
    return reg.active.state;
  }).then((state) => (state === 'activated' ? true : state)), WAIT_BUDGET_MS, 'Service Worker 进入 activated');
}

/* 在线加载 → 等 activate → reload 拿 controller。
   sw.js 没有 clients.claim()，首次文档不受控；reload 后导航才由 SW 接管
   （真实生产用户升级/二次打开时的路径）。 */
async function loadControlled(context, page, appUrl) {
  await page.goto(appUrl, { waitUntil: 'domcontentloaded', timeout: WAIT_BUDGET_MS });
  await waitForActivated(context, page);
  await page.reload({ waitUntil: 'domcontentloaded', timeout: WAIT_BUDGET_MS });
  /* controller 判定是同步取值，可以用 waitForFunction。 */
  await page.waitForFunction(() => !!navigator.serviceWorker.controller, null, { timeout: WAIT_BUDGET_MS });
}

/* 轮询直到两个缓存都出现预期条目（cache.put 是异步的，不能拍脑袋等一拍）。 */
async function waitForCaches(page, { assetUrl, shellKey }) {
  await waitUntil(() => page.evaluate(async (want) => {
    const names = await caches.keys();
    const hasRuntime = names.includes(want.runtimeName);
    const hasShell = names.includes(want.shellName);
    const runtimeUrls = hasRuntime
      ? (await (await caches.open(want.runtimeName)).keys()).map((r) => r.url) : [];
    const shellUrls = hasShell
      ? (await (await caches.open(want.shellName)).keys()).map((r) => r.url) : [];
    return (runtimeUrls.includes(want.assetUrl) && shellUrls.includes(want.shellKey))
      ? true
      : { hasRuntime, hasShell, runtimeEntries: runtimeUrls.length, shellUrls };
  }, { runtimeName: RUNTIME_CACHE, shellName: SHELL_CACHE, assetUrl, shellKey }), WAIT_BUDGET_MS,
  `缓存出现 ${RUNTIME_CACHE} / ${SHELL_CACHE} 预期条目`);
}

async function readCacheReport(page, shellKey) {
  return page.evaluate(async (want) => {
    const names = await caches.keys();
    const runtime = names.includes(want.runtimeName) ? await caches.open(want.runtimeName) : null;
    const shell = names.includes(want.shellName) ? await caches.open(want.shellName) : null;
    const runtimeUrls = runtime ? (await runtime.keys()).map((r) => r.url) : [];
    const shellUrls = shell ? (await shell.keys()).map((r) => r.url) : [];
    const snapResp = shell ? await shell.match(want.shellKey) : null;
    return {
      names,
      runtimeUrls,
      shellUrls,
      snapText: snapResp ? await snapResp.text() : null,
    };
  }, { runtimeName: RUNTIME_CACHE, shellName: SHELL_CACHE, shellKey });
}

function assertRuntimeEntries(report, scope, origin, label) {
  assert.ok(report.names.includes(RUNTIME_CACHE),
    `${label}：caches.keys() 必须含 ${RUNTIME_CACHE}，实际 ${report.names.join(', ')}`);
  const wanted = scope + VERSIONED_ASSET;
  assert.ok(report.runtimeUrls.some((u) => u === wanted),
    `${label}：runtime 缓存必须含带 ?v= 的 ${VERSIONED_ASSET}，期望 ${JSON.stringify(wanted)}，`
    + `实际 ${JSON.stringify(report.runtimeUrls.filter((u) => u.indexOf('offline_banner') >= 0))}`);
  for (const url of report.runtimeUrls) {
    assert.ok(url.includes('?v='), `${label}：runtime 缓存只允许带 ?v= 令牌的条目，发现 ${url}`);
    assert.ok(url.startsWith(origin), `${label}：runtime 缓存只允许同源条目，发现 ${url}`);
  }
}

function assertShellEntry(report, scope, label) {
  assert.ok(report.names.includes(SHELL_CACHE),
    `${label}：caches.keys() 必须含 ${SHELL_CACHE}，实际 ${report.names.join(', ')}`);
  assert.deepEqual(report.shellUrls, [scope],
    `${label}：shell 缓存条目键必须恰好等于 registration.scope（${scope}），实际 ${report.shellUrls.join(', ')}`);
  assert.ok(report.snapText && report.snapText.includes('id="authScreen"') &&
    report.snapText.includes('id="appShell"'),
    `${label}：shell 快照必须是应用壳 HTML（含 #authScreen/#appShell），实际长度 ${report.snapText ? report.snapText.length : 'null'}`);
}

/* 负向：/api/ 绝不进缓存，且不得顶掉壳快照。 */
async function assertApiNeverCached(page, apiUrl, scope, label) {
  /* (1) 受控页面内同源 GET（destination=''）→ strategy network-only。 */
  const viaFetch = await page.evaluate(async (url) => {
    const resp = await fetch(url, { cache: 'no-store' });
    return { status: resp.status, type: resp.headers.get('content-type') || '', text: await resp.text() };
  }, apiUrl);
  assert.equal(viaFetch.status, 200, `${label}：/api/ 请求应直连拿到 JSON`);
  assert.match(viaFetch.type, /application\/json/, `${label}：模拟后端必须返回 JSON`);
  assert.ok(viaFetch.text.includes('"api"'), `${label}：JSON 响应体形态漂移`);

  /* (2) 地址栏导航到 scope 内的 /api/（navigate，content-type JSON）：
     r2 finding #2 —— 旧实现把任一 navigate 当壳，JSON 会覆盖 shellKey() 快照。 */
  const response = await page.goto(apiUrl, { waitUntil: 'domcontentloaded', timeout: WAIT_BUDGET_MS });
  assert.ok(response, `${label}：/api/ 导航必须有响应`);
  assert.equal(response.status(), 200, `${label}：/api/ 导航应直连成功`);
  const body = await response.text();
  assert.ok(body.includes('"api"'), `${label}：/api/ 导航响应体应是 JSON`);

  const report = await readCacheReport(page, scope);
  assertShellEntry(report, scope, label + '（API 导航后）');
  const everything = report.runtimeUrls.concat(report.shellUrls);
  assert.deepEqual(everything.filter((u) => u.includes('/api/')), [],
    `${label}：/api/ 响应绝不允许进入任何缓存，发现 ${everything.filter((u) => u.includes('/api/')).join(', ')}`);
}

async function runScenario(browser, origin, { appPath, label }) {
  const appUrl = origin + appPath;
  const apiUrl = origin + appPath + 'api/runtime';
  const expectedScope = origin + appPath;
  const context = await browser.newContext();
  const page = await context.newPage();
  try {
    await loadControlled(context, page, appUrl);

    const reg = await page.evaluate(async () => {
      const registration = await navigator.serviceWorker.getRegistration();
      return { scope: registration && registration.scope, controller: !!navigator.serviceWorker.controller };
    });
    assert.equal(reg.controller, true, `${label}：reload 后必须有 controller（sw.js 不带 clients.claim，首次加载不受控属正常）`);
    assert.equal(reg.scope, expectedScope, `${label}：registration.scope 必须是 ${expectedScope}`);

    await waitForCaches(page, { assetUrl: expectedScope + VERSIONED_ASSET, shellKey: expectedScope });

    const online = await readCacheReport(page, expectedScope);
    assertRuntimeEntries(online, expectedScope, origin, label);
    /* 缓存条目必须是真实脚本体（不是错误页/空体）：离线 reload 靠它执行。 */
    const cachedAsset = await page.evaluate(async (want) => {
      const resp = await (await caches.open(want.cacheName)).match(want.url);
      return resp ? {
        ok: resp.ok,
        type: resp.headers.get('content-type') || '',
        text: await resp.text(),
      } : null;
    }, { cacheName: RUNTIME_CACHE, url: expectedScope + VERSIONED_ASSET });
    assert.ok(cachedAsset, `${label}：runtime 缓存必须能命中断言用的带 ?v= 脚本`);
    assert.equal(cachedAsset.ok, true, `${label}：runtime 缓存条目必须是 ok 响应`);
    assert.match(cachedAsset.type, /javascript/, `${label}：runtime 缓存条目必须是 JS 内容类型，实际 ${cachedAsset.type}`);
    assert.ok(cachedAsset.text.includes('RFLang'),
      `${label}：runtime 缓存条目必须是真实的 offline_banner.js 脚本体（含 RFLang 注册）`);
    /* shell 缓存键 = registration.scope，且只有一个键（快照在 scope 上）。 */
    assertShellEntry(online, expectedScope, label);
    /* 在线受控页面此时不应有离线提示条（后面离线态出现才算「由快照交付」）。 */
    assert.equal(await page.evaluate(() => !!document.getElementById('rfOfflineSnapshotBanner')), false,
      `${label}：在线页面不得出现离线快照提示条`);

    await assertApiNeverCached(page, apiUrl, expectedScope, label);

    /* 回到应用壳，重新受控；壳快照应在 API 导航后仍是 HTML。 */
    await page.goto(appUrl, { waitUntil: 'domcontentloaded', timeout: WAIT_BUDGET_MS });
    await page.waitForFunction(() => !!navigator.serviceWorker.controller, null, { timeout: WAIT_BUDGET_MS });
    const afterApi = await readCacheReport(page, expectedScope);
    assertShellEntry(afterApi, expectedScope, label + '（返回应用后）');

    /* ---- 离线：reload 必须由壳快照交付，提示条走真实 SW postMessage ---- */
    await context.setOffline(true);
    const offlineResponse = await page.reload({ waitUntil: 'domcontentloaded', timeout: WAIT_BUDGET_MS });
    assert.ok(offlineResponse, `${label}：离线 reload 必须由 SW 返回壳，而不是浏览器原生错误页`);
    assert.equal(offlineResponse.status(), 200, `${label}：离线壳快照应以 200 交付`);
    await page.waitForSelector('#rfOfflineSnapshotBanner', { timeout: WAIT_BUDGET_MS });

    const offline = await page.evaluate(() => {
      const bar = document.getElementById('rfOfflineSnapshotBanner');
      const auth = document.getElementById('authScreen');
      return {
        href: location.href,
        title: document.title,
        controller: !!navigator.serviceWorker.controller,
        bar: bar ? { attr: bar.getAttribute('data-rf-offline-banner'), text: bar.textContent } : null,
        close: !!document.getElementById('rfOfflineSnapshotBannerClose'),
        authScreen: !!auth,
        authDisplay: auth ? getComputedStyle(auth).display : null,
        appShell: !!document.getElementById('appShell'),
        bodyTextLen: document.body ? document.body.innerText.length : 0,
      };
    });

    assert.equal(offline.href, appUrl, `${label}：离线 reload 后 URL 必须仍是应用页（不是 chrome-error）`);
    assert.equal(offline.controller, true, `${label}：离线文档必须仍由 SW 控制`);
    assert.equal(offline.title.includes('ReviewFlow'), true, `${label}：离线文档必须是应用壳（title 来自 index.html）`);
    assert.equal(offline.authScreen, true, `${label}：离线文档必须含登录屏（应用壳）`);
    assert.notEqual(offline.authDisplay, 'none', `${label}：离线壳的登录屏必须可见`);
    assert.equal(offline.appShell, true, `${label}：离线文档必须含应用主壳 #appShell`);
    assert.ok(offline.bodyTextLen > 0, `${label}：离线壳必须有可读内容（不是空白页）`);
    assert.ok(offline.bar, `${label}：离线快照交付后必须出现 #rfOfflineSnapshotBanner`);
    assert.equal(offline.bar.attr, '1', `${label}：提示条必须带 data-rf-offline-banner=1`);
    assert.match(offline.bar.text, /当前离线|You are offline/,
      `${label}：提示条文案必须是离线快照说明，实际：${offline.bar.text.slice(0, 120)}`);
    assert.equal(offline.close, true, `${label}：提示条必须渲染自助关闭按钮`);

    /* 离线期间不得新增 /api/ 缓存（banner 的 /api/health 探测也走 network-only，失败即降级）。 */
    const offlineCaches = await readCacheReport(page, expectedScope);
    assertShellEntry(offlineCaches, expectedScope, label + '（离线）');
    const offlineAll = offlineCaches.runtimeUrls.concat(offlineCaches.shellUrls);
    assert.deepEqual(offlineAll.filter((u) => u.includes('/api/')), [],
      `${label}：离线期间 /api/ 也不得进入缓存`);

    await context.setOffline(false);
  } finally {
    await context.close();
  }
}

(async () => {
  const server = await serveFrontend();
  const origin = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch({ headless: true });
  try {
    /* 根路径部署。 */
    await runScenario(browser, origin, { appPath: '/', label: '根路径' });
    /* 子路径部署：同构再跑一遍（场景 B）。 */
    await runScenario(browser, origin, { appPath: SUBPATH, label: '子路径 /reviewflow/' });
    console.log('PASS W5 offline shell service worker end-to-end '
      + `(${RUNTIME_CACHE} entry for ${VERSIONED_ASSET}, shell key = registration.scope, `
      + 'offline reload served from shell snapshot with real postMessage banner, /api/ never cached, '
      + 'root and /reviewflow/ scopes)');
  } finally {
    await browser.close();
    await new Promise((r) => server.close(r));
  }
})().catch((error) => { console.error(error); process.exit(1); });
