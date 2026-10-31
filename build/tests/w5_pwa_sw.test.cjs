'use strict';
/* R8 · W5 PWA node 冒烟钉子：custom_frontend/sw.js 的语法（vm 编译安装即过）＋
   策略函数（RF_SW 纯函数直测）＋ fetch 行为（桩 SW 环境回放）＋ manifest/词条。
   撤销任一策略（缓存 /api/、跨源缓存、离线伪造内容、快照协议断裂）即红。
   注：sw.js 的 IIFE 在装载时捕获 self（=桩全局对象），断网场景必须原地改写
   同一对象的 fetch 属性，不得用展开副本（副本进不了闭包）。 */
const test = require('node:test'),
      assert = require('node:assert/strict'),
      fs = require('node:fs'),
      path = require('node:path'),
      vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..', '..');
const FRONTEND = path.join(ROOT, 'custom_frontend');
const SRC = fs.readFileSync(path.join(FRONTEND, 'sw.js'), 'utf8');

/* ---- 桩：Cache API（match 按 URL 键；返回 clone 防止 body 复用串扰） ---- */
function makeCaches() {
  const store = new Map();
  const norm = (r) => (typeof r === 'string' ? r : (r && r.url) || String(r));
  return {
    _store: store,
    open: async (name) => {
      if (!store.has(name)) store.set(name, new Map());
      const m = store.get(name);
      return {
        put: (req, res) => { m.set(norm(req), res); return Promise.resolve(); },
        match: async (req) => {
          const v = m.get(norm(req));
          return v === undefined ? undefined : v.clone();
        },
      };
    },
    keys: async () => [...store.keys()],
    delete: async (n) => store.delete(n),
  };
}

/* ---- 桩：ServiceWorkerGlobalScope 形状（self/location/registration/clients…） ---- */
function makeSandbox(opts = {}) {
  const ctx = {};
  ctx._listeners = {};
  ctx.location = new URL(opts.origin || 'https://reviewflow.test/');
  ctx.registration = { scope: opts.scope || ctx.location.href };
  ctx.Response = Response; ctx.Request = Request; ctx.URL = URL; ctx.Promise = Promise;
  ctx.console = console;
  ctx.addEventListener = (t, fn) => { (ctx._listeners[t] = ctx._listeners[t] || []).push(fn); };
  ctx._clientsById = {};
  ctx.clients = { get: async (id) => ctx._clientsById[id] || null };
  ctx.caches = makeCaches();
  ctx.fetch = opts.fetch || (async () => { throw new Error('network down'); });
  ctx.self = ctx;
  vm.createContext(ctx);
  vm.runInNewContext(SRC, ctx); /* 语法错误在此直接抛出（语法钉） */
  return ctx;
}

/* 第 2 轮评审：真实 SW FetchEvent 里导航请求的 clientId 是「发起导航的旧文档」，
   新文档 id 在 resultingClientId。桩必须按真实契约构造这两个不同的值——
   旧桩把同一个 'c7' 既当 clientId 又当 source.id，正好把生产缺陷全绿盖住。 */
function fireFetch(ctx, request, ids = {}) {
  const rec = { called: false, promise: null, wait: null };
  const ev = {
    request,
    clientId: 'previousDoc' in ids ? ids.previousDoc : 'c' + (++fireFetch.n),
    resultingClientId: ids.resultingClientId,
    respondWith: (p) => { rec.called = true; rec.promise = Promise.resolve(p); },
    waitUntil: (p) => { rec.wait = p; },
  };
  for (const h of ctx._listeners.fetch) h(ev);
  return rec;
}
fireFetch.n = 0;

function fireMessage(ctx, data, source) {
  for (const h of (ctx._listeners.message || [])) h({ data, source });
}

const get = (url, extra = {}) => ({ method: 'GET', url, mode: 'no-cors', destination: '', ...extra });

/* ============================== 安装与策略 ============================== */

test('sw.js installs in a SW-like global, registers fetch/message/activate, no install hook', () => {
  const ctx = makeSandbox();
  assert.ok(ctx.RF_SW, 'RF_SW 策略句柄应挂到 self');
  assert.equal(ctx.RF_SW.version, '20261030-r8-W5PWA1');
  assert.equal(ctx._listeners.fetch.length, 1);
  assert.equal(ctx._listeners.message.length, 1);
  assert.equal(ctx._listeners.activate.length, 1);
  assert.equal(ctx._listeners.install, undefined, '零 precache 红线：不得有 install 拉取钩子');
  assert.ok(!SRC.includes('importScripts'), '零额外请求红线：不得 importScripts');
});

test('policy: versioned same-origin GET JS/CSS → cache-first-runtime', () => {
  const ctx = makeSandbox();
  assert.equal(ctx.RF_SW.strategyFor(get('https://reviewflow.test/app.js?v=2.1.4', { destination: 'script' })), 'cache-first-runtime');
  assert.equal(ctx.RF_SW.strategyFor(get('https://reviewflow.test/x.css?v=20261030-r8-W5PWA1', { destination: 'style' })), 'cache-first-runtime');
});

test('policy: document navigations → network-first-shell (both shapes)', () => {
  const ctx = makeSandbox();
  assert.equal(ctx.RF_SW.strategyFor(get('https://reviewflow.test/', { mode: 'navigate', destination: 'document' })), 'network-first-shell');
  assert.equal(ctx.RF_SW.strategyFor(get('https://reviewflow.test/index.html', { mode: 'navigate' })), 'network-first-shell');
});

test('policy: api navigations never become the shell (r2 review finding #2)', () => {
  const ctx = makeSandbox();
  for (const path of ['/api/runtime', '/api/health', '/api/tasks/1/export/decisions']) {
    assert.equal(ctx.RF_SW.strategyFor(get('https://reviewflow.test' + path, { mode: 'navigate', destination: 'document' })),
      'network-only', 'API 导航不得进快照键：' + path);
  }
  /* 子路径部署同样成立 */
  const sub = makeSandbox({ origin: 'https://reviewflow.test/reviewflow/', scope: 'https://reviewflow.test/reviewflow/' });
  assert.equal(sub.RF_SW.strategyFor(get('https://reviewflow.test/reviewflow/api/runtime', { mode: 'navigate' })), 'network-only');
  /* 跨源导航即便形态是 navigate 也不当壳（SW 只认自己 scope 内的文档） */
  assert.equal(ctx.RF_SW.strategyFor(get('https://elsewhere.test/', { mode: 'navigate' })), 'network-only');
  /* scope 外导航：子路径部署下，根路径文档不属于该 SW */
  assert.equal(sub.RF_SW.strategyFor(get('https://reviewflow.test/outside', { mode: 'navigate' })), 'network-only');
});

test('policy: api/plain/POST/cross-origin/image are network-only (never cached)', () => {
  const ctx = makeSandbox();
  assert.equal(ctx.RF_SW.strategyFor(get('https://reviewflow.test/api/tasks')), 'network-only');
  assert.equal(ctx.RF_SW.strategyFor(get('https://reviewflow.test/api/tasks?x=1', { destination: 'script' })), 'network-only');
  assert.equal(ctx.RF_SW.strategyFor(get('https://reviewflow.test/app.js', { destination: 'script' })), 'network-only', '无 v 令牌的脚本不缓存（令牌制失效手段依赖）');
  assert.equal(ctx.RF_SW.strategyFor({ method: 'POST', url: 'https://reviewflow.test/app.js?v=1', destination: 'script' }), 'network-only');
  assert.equal(ctx.RF_SW.strategyFor(get('https://cdn.test/x.js?v=1', { destination: 'script' })), 'network-only', '跨源一律放行（隐私红线：只触碰同源）');
  assert.equal(ctx.RF_SW.strategyFor(get('https://reviewflow.test/i.png?v=1', { destination: 'image' })), 'network-only');
});

test('policy: subpath deployment is isomorphic (scope-relative shell key)', () => {
  const ctx = makeSandbox({ origin: 'https://reviewflow.test/reviewflow/', scope: 'https://reviewflow.test/reviewflow/' });
  assert.equal(ctx.RF_SW.strategyFor(get('https://reviewflow.test/reviewflow/', { mode: 'navigate' })), 'network-first-shell');
  assert.equal(ctx.RF_SW.strategyFor(get('https://reviewflow.test/reviewflow/app.js?v=1', { destination: 'script' })), 'cache-first-runtime');
  assert.equal(ctx.RF_SW.shellKey(), 'https://reviewflow.test/reviewflow/', '快照键必须是 scope 相对推导，不得写死绝对路径');
});

/* ============================== fetch 行为 ============================== */

test('behavior: versioned asset first hit fills cache; offline second hit serves cache', async () => {
  let netCalls = 0;
  const ctx = makeSandbox({ fetch: async () => { netCalls++; return new Response('console.log(1)', { status: 200 }); } });
  const url = 'https://reviewflow.test/app.js?v=20261030-r8-W5PWA1';
  const r1 = fireFetch(ctx, get(url, { destination: 'script' }));
  assert.ok(r1.called, 'cache-first 资产必须 respondWith');
  assert.equal(await (await r1.promise).text(), 'console.log(1)');
  assert.equal(netCalls, 1);
  const rt = ctx.caches._store.get(ctx.RF_SW.runtimeCache);
  assert.ok(rt && rt.has(url), '回填必须落在 runtime 缓存且以带令牌 URL 为键');
  /* 断网后第二次请求：缓存命中，不走网络 */
  ctx.fetch = async () => { throw new Error('down'); };
  const r2 = fireFetch(ctx, get(url, { destination: 'script' }));
  assert.equal(await (await r2.promise).text(), 'console.log(1)', '离线时由缓存兜底');
  assert.equal(netCalls, 1, '第二次命中不得再走网络');
});

test('behavior: online navigation caches the shell under the scope key', async () => {
  const ctx = makeSandbox({ fetch: async () => new Response('<html>live</html>', { status: 200, headers: { 'content-type': 'text/html; charset=utf-8' } }) });
  const r = fireFetch(ctx, get('https://reviewflow.test/', { mode: 'navigate' }));
  const resp = await r.promise;
  assert.equal(resp.status, 200);
  assert.equal(await resp.text(), '<html>live</html>');
  const shell = ctx.caches._store.get(ctx.RF_SW.shellCache);
  assert.ok(shell && shell.has('https://reviewflow.test/'), '快照键 = scope（相对化）');
});

test('behavior: a non-HTML navigation response never overwrites the shell (r2 finding #2)', async () => {
  const ctx = makeSandbox({ fetch: async () => new Response('<html>shell</html>', { status: 200, headers: { 'content-type': 'text/html' } }) });
  await (await fireFetch(ctx, get('https://reviewflow.test/', { mode: 'navigate' })).promise);
  /* 用户地址栏直接打开一个返回 JSON 的下载/导出路径（同源、非 /api 前缀也算） */
  ctx.fetch = async () => new Response('{"version":"2.1.4"}', { status: 200, headers: { 'content-type': 'application/json' } });
  const r = fireFetch(ctx, get('https://reviewflow.test/report/export.json', { mode: 'navigate' }));
  await r.promise;
  const shell = ctx.caches._store.get(ctx.RF_SW.shellCache);
  assert.equal(await shell.get('https://reviewflow.test/').text(), '<html>shell</html>',
    '非 text/html 响应不得覆盖离线壳快照');
});

test('behavior: offline navigation falls back to snapshot + banner protocol answers', async () => {
  const ctx = makeSandbox({ fetch: async () => new Response('<html>live</html>', { status: 200, headers: { 'content-type': 'text/html' } }) });
  await (await fireFetch(ctx, get('https://reviewflow.test/', { mode: 'navigate' })).promise);
  /* 断网重载（同一 worker 实例：原地换 fetch 结局，进入闭包可见） */
  ctx.fetch = async () => { throw new Error('offline'); };
  const pushed = [];
  const NEW_DOC = 'newDoc1';
  ctx._clientsById[NEW_DOC] = { id: NEW_DOC, postMessage: (m) => pushed.push(m) };
  /* 真实契约：clientId = 发起导航的旧文档；resultingClientId = 被交付快照的新文档 */
  const rec = fireFetch(ctx, get('https://reviewflow.test/', { mode: 'navigate' }),
    { previousDoc: 'oldDoc0', resultingClientId: NEW_DOC });
  assert.ok(rec.called);
  const snap = await rec.promise;
  assert.equal(await snap.text(), '<html>live</html>', '离线导航回退最近快照（不伪造新内容）');
  await Promise.resolve(); /* clients.get 是异步：让直推机会落地 */
  await Promise.resolve();
  assert.ok(pushed.some((m) => m && m.type === 'rf-offline-snapshot'), '兜底时应直推到新文档');
  /* 查询协议（权威通道）：新文档（source.id = resultingClientId）必须得到 true。
     旧实现把标记写在 event.clientId 上，这里必为 false——这就是生产上提示条
     从不显示的根因。 */
  const replies = [];
  fireMessage(ctx, { type: 'rf-query-offline-snapshot' }, { id: NEW_DOC, postMessage: (m) => replies.push(m) });
  assert.equal(replies.length, 1);
  assert.equal(replies[0].type, 'rf-offline-snapshot-state');
  assert.equal(replies[0].offlineSnapshot, true, '标记键必须是 resultingClientId');
  /* 旧文档 id 不得被误标（标记表键语义） */
  const oldReplies = [];
  fireMessage(ctx, { type: 'rf-query-offline-snapshot' }, { id: 'oldDoc0', postMessage: (m) => oldReplies.push(m) });
  assert.equal(oldReplies[0].offlineSnapshot, false, '不得把旧文档 id 当成被兜底文档');
  /* 恢复联网：页面 online 后发 leave，再查即清除 */
  fireMessage(ctx, { type: 'rf-leave-offline-snapshot' }, { id: NEW_DOC, postMessage: () => {} });
  const replies2 = [];
  fireMessage(ctx, { type: 'rf-query-offline-snapshot' }, { id: NEW_DOC, postMessage: (m) => replies2.push(m) });
  assert.equal(replies2.length, 1);
  assert.equal(replies2[0].type, 'rf-offline-snapshot-state');
  assert.equal(replies2[0].offlineSnapshot, false);
});

test('behavior: stale snapshot markers are pruned against live clients (no unbounded growth)', async () => {
  const ctx = makeSandbox({ fetch: async () => { throw new Error('offline'); } });
  await (await ctx.caches.open(ctx.RF_SW.shellCache)).put(ctx.RF_SW.shellKey(),
    new Response('<html>shell</html>', { status: 200, headers: { 'content-type': 'text/html' } }));
  /* 第一次兜底：文档 LIVE 被标记 */
  const LIVE = 'liveDoc';
  ctx._clientsById[LIVE] = { id: LIVE };
  await (await fireFetch(ctx, get('https://reviewflow.test/', { mode: 'navigate' }),
    { previousDoc: 'gone1', resultingClientId: LIVE })).promise;
  /* 第二次兜底：文档 CLOSED 被标记后随即关闭（不派发 online，不发 leave） */
  ctx._clientsById.CLOSED = { id: 'CLOSED' };
  await (await fireFetch(ctx, get('https://reviewflow.test/', { mode: 'navigate' }),
    { previousDoc: 'gone2', resultingClientId: 'CLOSED' })).promise;
  delete ctx._clientsById.CLOSED;
  /* 活文档清单只剩 LIVE：下一次导航时对账应把 CLOSED 丢掉 */
  ctx.clients.matchAll = async () => [{ id: LIVE }];
  await (await fireFetch(ctx, get('https://reviewflow.test/', { mode: 'navigate' }),
    { previousDoc: 'gone3', resultingClientId: LIVE })).promise;
  await new Promise((r) => setTimeout(r, 0)); /* matchAll 对账是异步 */
  const closed = [];
  fireMessage(ctx, { type: 'rf-query-offline-snapshot' }, { id: 'CLOSED', postMessage: (m) => closed.push(m) });
  assert.equal(closed[0].offlineSnapshot, false, '已关闭文档的标记必须被对账清掉');
  const live = [];
  fireMessage(ctx, { type: 'rf-query-offline-snapshot' }, { id: LIVE, postMessage: (m) => live.push(m) });
  assert.equal(live[0].offlineSnapshot, true, '仍存活的兜底文档标记不得被误删');
});

test('behavior: network-only requests are never intercepted nor cached', async () => {
  const ctx = makeSandbox({ fetch: async () => new Response('{"ok":true}', { status: 200 }) });
  const rec = fireFetch(ctx, get('https://reviewflow.test/api/tasks'));
  assert.equal(rec.called, false, 'network-only 不得 respondWith');
  assert.equal(ctx.caches._store.size, 0, '任何缓存都不应有条目');
});

test('behavior: uncached asset while offline degrades to honest 503 (no fake success)', async () => {
  const ctx = makeSandbox({ fetch: async () => { throw new Error('offline'); } });
  const rec = fireFetch(ctx, get('https://reviewflow.test/never.js?v=1', { destination: 'script' }));
  assert.ok(rec.called);
  const resp = await rec.promise;
  assert.equal(resp.status, 503);
});

test('behavior: activate purges caches of other versions', async () => {
  const ctx = makeSandbox();
  await (await ctx.caches.open('rf-runtime-OLD')).put('x', new Response('x'));
  await (await ctx.caches.open(ctx.RF_SW.runtimeCache)).put('y', new Response('y'));
  await (await ctx.caches.open(ctx.RF_SW.shellCache)).put('z', new Response('z'));
  const rec = { wait: null };
  for (const h of ctx._listeners.activate) h({ waitUntil: (p) => { rec.wait = p; } });
  await rec.wait;
  const names = await ctx.caches.keys();
  assert.deepEqual(names.sort(), [ctx.RF_SW.runtimeCache, ctx.RF_SW.shellCache].sort(), '只保留本版本缓存，旧版本整体废弃');
});

/* ============================== manifest / 词条 ============================== */

test('manifest.webmanifest: relative start_url/scope, icons exist on disk', () => {
  const m = JSON.parse(fs.readFileSync(path.join(FRONTEND, 'manifest.webmanifest'), 'utf8'));
  assert.equal(m.start_url, './', 'start_url 必须相对（根路径与 reviewflow 子路径同构）');
  assert.equal(m.scope, './', 'scope 必须相对');
  assert.ok(m.name && m.short_name);
  assert.ok(Array.isArray(m.icons) && m.icons.length >= 3);
  assert.ok(m.icons.some((i) => i.purpose === 'maskable'));
  for (const icon of m.icons) {
    assert.ok(fs.existsSync(path.join(FRONTEND, icon.src)), 'manifest 图标缺失：' + icon.src);
    assert.equal(icon.type, 'image/png');
  }
});

test('behavior: mark lands before pruning even when matchAll resolves first (r3 race)', async () => {
  /* 第 3 轮评审 low：matchAll 若先于 mark 返回，新文档会被当成「已关闭」删掉。
     构造 matchAll 立即 resolve、fetch 慢 30ms 的最坏顺序；若实现对账早于
     mark 或未把本事件 id 排除，新文档回执会是 false。 */
  const ctx = makeSandbox({ fetch: async () => { await new Promise((r) => setTimeout(r, 30)); throw new Error('offline'); } });
  await (await ctx.caches.open(ctx.RF_SW.shellCache)).put(ctx.RF_SW.shellKey(),
    new Response('<html>shell</html>', { status: 200, headers: { 'content-type': 'text/html' } }));
  /* matchAll 立刻返回「空活文档表」——最恶劣的对账快照 */
  ctx.clients.matchAll = async () => [];
  const NEW = 'freshDoc';
  const rec = fireFetch(ctx, get('https://reviewflow.test/', { mode: 'navigate' }),
    { previousDoc: 'oldDoc', resultingClientId: NEW });
  await rec.promise;
  /* 等所有微任务与定时器落地（对账 promise + clients.get） */
  await new Promise((r) => setTimeout(r, 60));
  const replies = [];
  fireMessage(ctx, { type: 'rf-query-offline-snapshot' }, { id: NEW, postMessage: (m) => replies.push(m) });
  assert.equal(replies[0].offlineSnapshot, true,
    '刚被交付快照的文档标记不得被对账误删（先 mark 后对账 + keepId 排除）');
});

/* ---- DOM 桩：只实现 offline_banner 用到的那一小撮 API，用于几何/布局钉 ---- */
function makeDom(opts = {}) {
  const store = {};
  function style() {
    const s = { cssText: '', display: '', paddingTop: '', transform: '', opacity: '' };
    /* cssText 写入时把关键声明同步成可读属性（真实浏览器会解析） */
    return new Proxy(s, {
      set(t, k, v) {
        if (k === 'cssText') { t.cssText = v; const m = /padding-top:([^;]+)/.exec(v); if (m) t.paddingTop = m[1]; }
        else t[k] = v;
        return true;
      },
    });
  }
  function el(tag) {
    const node = {
      tagName: String(tag).toUpperCase(), id: '', className: '', childNodes: [], attributes: {},
      style: style(), dataset: {}, textContent: '', parentNode: null, _listeners: {}, _rect: { width: 0, height: opts.barHeight || 18, top: 0 },
      setAttribute(k, v) { this.attributes[k] = v; if (k === 'id') this.id = v; },
      getAttribute(k) { return this.attributes[k]; },
      appendChild(c) { c.parentNode = this; this.childNodes.push(c); return c; },
      insertBefore(c) { c.parentNode = this; this.childNodes.unshift(c); return c; },
      removeChild(c) { const i = this.childNodes.indexOf(c); if (i >= 0) this.childNodes.splice(i, 1); c.parentNode = null; return c; },
      addEventListener(t, fn) { (this._listeners[t] = this._listeners[t] || []).push(fn); },
      getBoundingClientRect() {
        /* 真实浏览器里 transform 会移动 border-box，且对子元素同样生效（header
           下移 → 三个控件随之移动）；内联 top（fixed/absolute 元素上的让位）
           直接决定 border-box 顶边。这里两者都算进去，「让位后是否仍在条下面」
           才是可断言的。 */
        let dy = 0;
        for (let n = this; n; n = n.parentNode) {
          const m = /translateY\((-?[\d.]+)px\)/.exec((n.style && n.style.transform) || '');
          if (m) dy += parseFloat(m[1]);
        }
        const inlineTop = this.style && this.style.top ? parseFloat(this.style.top) : null;
        const top = (inlineTop === null ? this._rect.top : inlineTop) + dy;
        return { width: this._rect.width, height: this._rect.height, top, left: 0, bottom: top + this._rect.height };
      },
      /* 只支持组件自用到的 `.site-header` 形态（按 class 名线性查找） */
      querySelector(sel) {
        const want = String(sel).replace(/^\./, '');
        const stack = [...this.childNodes];
        while (stack.length) {
          const n = stack.shift();
          if (String(n.className).split(/\s+/).includes(want)) return n;
          stack.push(...n.childNodes);
        }
        return null;
      },
      click() { for (const fn of (this._listeners.click || [])) fn({}); },
    };
    return node;
  }
  const body = el('body');
  const doc = {
    readyState: 'complete',
    body,
    _byId: store,
    createElement: (t) => el(t),
    getElementById(id) { return findByPred(node => node.id === id || node.attributes.id === id); },
    addEventListener() {},
  };
  function findByPred(pred, from) {
    const root = from || body;
    const stack = [root];
    while (stack.length) {
      const n = stack.pop();
      if (pred(n)) return n;
      for (const c of (n.childNodes || [])) stack.push(c);
    }
    return null;
  }
  return { el, body, doc, findByPred };
}

const BANNER_SRC = fs.readFileSync(path.join(FRONTEND, 'offline_banner.js'), 'utf8');

function bootBanner(opts = {}) {
  const dom = makeDom(opts);
  const rows = [];
  const posted = [];
  const health = [];
  const listeners = {};
  const ctx = {
    window: { addEventListener(t, fn) { (listeners['win:' + t] = listeners['win:' + t] || []).push(fn); } },
    document: dom.doc,
    RFLang: { register: (r) => rows.push(...r), t: (k) => k },
    navigator: {
      serviceWorker: {
        controller: { postMessage: (m) => posted.push(m) },
        addEventListener(t, fn) { (listeners['sw:' + t] = listeners['sw:' + t] || []).push(fn); },
      },
    },
    setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms || 0, 5)),
    clearTimeout: (h) => clearTimeout(h),
    MutationObserver: undefined,
    AbortController: undefined,
    /* computed 桩：内联优先，其次元素上登记的 CSS 基线（模拟样式表值）。 */
    getComputedStyle: (el) => ({
      paddingTop: el.style.paddingTop || el._cssPaddingTop || '0px',
      position: el._cssPosition || 'static',
      top: el.style.top || el._cssTop || 'auto',
    }),
    fetch: (url) => {
      health.push(url);
      if (opts.healthOk === false) return Promise.reject(new Error('down'));
      const status = opts.healthStatus === undefined ? 200 : opts.healthStatus;
      return Promise.resolve({ ok: status === 200, status });
    },
    Promise, console,
  };
  ctx.self = ctx;
  vm.createContext(ctx);
  vm.runInNewContext(BANNER_SRC, ctx);
  /* 模块把 message 监听挂在 navigator.serviceWorker 上：这里直接回调它 */
  const fireMessageToBanner = (data) => {
    for (const fn of (listeners['sw:message'] || [])) fn({ data });
  };
  const fireWindow = (type, payload) => {
    for (const fn of (listeners['win:' + type] || [])) fn(payload || {});
  };
  return { dom, rows, posted, health, ctx, fireMessageToBanner, fireWindow };
}

test('DOM: login header yields below the banner, never under it (r4 medium)', async () => {
  /* 第 4 轮评审 medium 原话：提示条固定盖住登录页顶部控制条，语言/浅色深色/
     暂停动效全部点不到。根因：条是 body 下 position:fixed;z-index:2147483000，
     而 #authScreen .site-header（三控件）在 #authScreen(position:fixed;z-index:100)
     内 top:30px。修复必须让位而不是靠调层级——这里钉的是几何事实：
     条出现后，header 的底边仍在条底之下、header 顶边不高于条底。 */
  const b = bootBanner({ barHeight: 51 });
  const { findByPred, el } = b.dom;
  const auth = el('div'); auth.id = 'authScreen'; auth.style.display = 'flex';
  auth._rect = { width: 1280, height: 800, top: 0 };
  const header = el('header'); header.className = 'site-header';
  header._rect = { width: 1200, height: 39, top: 30 }; /* 宽视口 top:30px（窄视口 20px 同理现算） */
  const langBtn = el('button'); langBtn.id = 'rf-language-button';
  langBtn._rect = { width: 75, height: 39, top: 30 };
  const themeBtn = el('button'); themeBtn.id = 'auTheme';
  themeBtn._rect = { width: 99, height: 39, top: 30 };
  const motionBtn = el('button'); motionBtn.id = 'auMotion';
  motionBtn._rect = { width: 99, height: 39, top: 30 };
  header.appendChild(langBtn); header.appendChild(themeBtn); header.appendChild(motionBtn);
  auth.appendChild(header);
  b.dom.doc.body.appendChild(auth);

  b.fireMessageToBanner({ type: 'rf-offline-snapshot' });
  const bar = findByPred(n => n.id === 'rfOfflineSnapshotBanner');
  assert.ok(bar, '提示条应已显示');
  const barBottom = bar.getBoundingClientRect().bottom; /* 条实测高度 51，top:0 */
  assert.equal(Math.round(bar.getBoundingClientRect().height), 51, '条高按实测值参与让位计算');

  /* 三控件（随 header 一起位移）必须整体落在条底之下 */
  for (const [name, btn] of [['语言', langBtn], ['深色/浅色', themeBtn], ['暂停动效', motionBtn]]) {
    const r = btn.getBoundingClientRect();
    assert.ok(r.top >= barBottom,
      `${name}控件必须让到条底之下：控件 top=${r.top}，条底=${barBottom}`);
  }
  assert.ok(/translateY\(\d+px\)/.test(header.style.transform || ''),
    '让位必须落在 .site-header 上（#authScreen 的 padding 挪不动 absolute 定位的 header）');
  assert.ok(header.style.transform === 'translateY(25px)',
    `应下移 (条底51+间隙4) - 原 top30 = 25px，实际 ${header.style.transform}`);

  /* 收起必须精确复原：内联 transform 回到空串，header 回到 top:30 */
  findByPred(n => n.id === 'rfOfflineSnapshotBannerClose').click();
  assert.equal(header.style.transform, '', '收起后 header 的内联 transform 必须还原为空');
  assert.equal(header.getBoundingClientRect().top, 30, '收起后 header 必须回到原位置');
  assert.equal(findByPred(n => n.id === 'rfOfflineSnapshotBanner'), null);
});

test('DOM: app layer yields too — sidebar and main get the strip height (r4 medium)', async () => {
  /* 登录态之外的另一半：条同样压在 #appShell（侧栏顶部内容）与 #appMain
     的 .top 控制条上。这里钉「加了条高、且只加一次、收起精确还原」。 */
  const b = bootBanner({ barHeight: 51 });
  const { findByPred, el } = b.dom;
  const auth = el('div'); auth.id = 'authScreen'; auth.style.display = 'flex'; /* 登录层还在 → 先走 header 分支 */
  const header = el('header'); header.className = 'site-header';
  header._rect = { width: 1200, height: 39, top: 30 };
  auth.appendChild(header);
  const shell = el('aside'); shell.id = 'appShell';
  shell._cssPaddingTop = '14px'; /* .sb 的 CSS 内边距：基线必须取计算值 */
  const main = el('main'); main.id = 'appMain';
  main._cssPaddingTop = '0px';
  b.dom.doc.body.appendChild(auth);
  b.dom.doc.body.appendChild(shell);
  b.dom.doc.body.appendChild(main);

  b.fireMessageToBanner({ type: 'rf-offline-snapshot' });
  assert.ok(findByPred(n => n.id === 'rfOfflineSnapshotBanner'), '提示条应已显示');
  assert.equal(shell.style.paddingTop, '', '登录态下应用层未展示：不该被预留（此时走 header 位移）');

  /* 进入应用：登录层隐藏。观察器在真实 DOM 里会触发；这里直接发消息重算路径
     ——用同一条 reserve() 真身，避免另造入口。 */
  auth.style.display = 'none';
  b.fireMessageToBanner({ type: 'rf-offline-snapshot-state', offlineSnapshot: true });
  assert.equal(shell.style.paddingTop, '69px', '侧栏应加「原计算 14px + 条高 51px + 间隙 4px」');
  assert.equal(main.style.paddingTop, '55px', '主区应加「原计算 0px + 条高 51px + 间隙 4px」');
  assert.equal(shell.style.paddingTop, '69px', '重复触发不得叠加（幂等）');

  /* 顶部贴合的浮层入口（侧栏开关）也必须让到条底之下，而不是抬高层级去压条。
     位移按「当前 rect.top 与条底+间隙 的差」现算：8 → 55（条底 51 + 间隙 4）。 */
  const sheet = el('div'); sheet.id = 'rfNavigationSheet';
  sheet._cssPosition = 'fixed'; sheet._cssTop = '8px';
  sheet._rect = { width: 44, height: 44, top: 8 };
  b.dom.doc.body.appendChild(sheet);
  b.fireMessageToBanner({ type: 'rf-offline-snapshot-state', offlineSnapshot: true });
  assert.equal(sheet.style.top, '55px', '侧栏开关顶边必须落到条底（51）+ 间隙（4）处');
  assert.ok(sheet.getBoundingClientRect().top >= 51, '侧栏开关不得留在条底之上');

  /* 已经位于条下方的形态（窄屏把同一元素改成 top:auto!important;bottom:12px，
     贴底显示）不得被挪：判定必须看真实重叠，而不是「element 在名单里」。 */
  const below = bootBanner({ barHeight: 51 });
  const lowAuth = below.dom.el('div'); lowAuth.id = 'authScreen'; lowAuth.style.display = 'none';
  below.dom.doc.body.appendChild(lowAuth);
  const lowSheet = below.dom.el('div'); lowSheet.id = 'rfNavigationSheet';
  lowSheet._cssPosition = 'fixed'; lowSheet._cssTop = 'auto';
  lowSheet._rect = { width: 44, height: 44, top: 770 }; /* 贴底：远离条 */
  below.dom.doc.body.appendChild(lowSheet);
  below.fireMessageToBanner({ type: 'rf-offline-snapshot' });
  assert.ok(!lowSheet.style.top, '没被条压住的浮层不得被挪动（贴底形态保持原样）');


  /* 登出：应用层还原、登录层重新让位 */
  auth.style.display = 'flex';
  b.fireMessageToBanner({ type: 'rf-offline-snapshot-state', offlineSnapshot: true });
  assert.equal(shell.style.paddingTop, '', '回到登录态后侧栏内边距必须还原');
  assert.equal(main.style.paddingTop, '', '回到登录态后主区内边距必须还原');
  assert.ok(/translateY\(25px\)/.test(header.style.transform || ''), '回到登录态 header 重新让位');
});

test('DOM: banner is a thin fixed strip, not a viewport-sized overlay (r3 high)', async () => {
  const b = bootBanner();
  /* 触发展示：模拟 SW 直推快照事件（真实路径之一） */
  const { doc, findByPred } = b.dom;
  b.fireMessageToBanner({ type: 'rf-offline-snapshot' });
  const bar = findByPred(n => n.id === 'rfOfflineSnapshotBanner' || n.attributes.id === 'rfOfflineSnapshotBanner');
  assert.ok(bar, '提示条应已插入 body');
  assert.ok(/position:fixed/.test(bar.style.cssText), '必须 fixed 定位（不被 body flex 拉伸）');
  assert.ok(/flex:0 0 auto/.test(bar.style.cssText), '必须禁止 flex 拉伸');
  assert.ok(/height:auto/.test(bar.style.cssText), '必须显式 height:auto');
  assert.ok(!/width:100%/.test(bar.style.cssText), '不得写 width:100% 配合 stretch');
  assert.ok(bar.style.maxHeight || /max-height:/.test(bar.style.cssText), '必须有高度上限');
  assert.equal(bar.parentNode, doc.body, '提示条挂 body 且不参与 flex 主轴');
  /* 关闭按钮存在且可点掉 */
  const close = findByPred(n => n.id === 'rfOfflineSnapshotBannerClose' || n.attributes.id === 'rfOfflineSnapshotBannerClose');
  assert.ok(close, '必须有自助关闭按钮');
  close.click();
  assert.equal(findByPred(n => n.id === 'rfOfflineSnapshotBanner'), null, '点关闭后提示条必须消失');
});

test('DOM: recovery is detected by probing same-origin /api/health when onLine stays true', async () => {
  const b = bootBanner({ healthOk: true });
  const { findByPred } = b.dom;
  b.fireMessageToBanner({ type: 'rf-offline-snapshot' });
  assert.ok(findByPred(n => n.id === 'rfOfflineSnapshotBanner'), '先显示');
  await new Promise((r) => setTimeout(r, 30));
  assert.ok(b.health.includes('/api/health'), '必须探测同源 /api/health');
  assert.equal(findByPred(n => n.id === 'rfOfflineSnapshotBanner'), null,
    '后端可达即自动收起（不能只靠永不到来的 online 事件）');
  assert.ok(b.posted.some((m) => m.type === 'rf-leave-offline-snapshot'), '收起时须通知 SW 清旗');
});

test('DOM: a 404 from /api/health does not count as recovered (static-host false positive)', async () => {
  const b = bootBanner({ healthStatus: 404 });
  const { findByPred } = b.dom;
  b.fireMessageToBanner({ type: 'rf-offline-snapshot' });
  await new Promise((r) => setTimeout(r, 30));
  assert.ok(b.health.includes('/api/health'), '必须真的探测过');
  assert.ok(findByPred(n => n.id === 'rfOfflineSnapshotBanner'),
    '404 ≠ 后端可用（静态托管对任意路径都回 404），不得据此收起');
});

test('DOM: recovery clears the login note on both paths (no stale offline lie)', async () => {
  for (const mode of ['probe', 'state']) {
    const b = bootBanner({ healthOk: mode === 'probe' });
    const { findByPred, el } = b.dom;
    const auth = el('div'); auth.id = 'authScreen'; auth.style.display = 'flex';
    b.dom.doc.body.appendChild(auth);
    b.fireMessageToBanner({ type: 'rf-offline-snapshot' });
    assert.ok(findByPred(n => n.id === 'rfOfflineStartupNote'), mode + '：先有说明');
    if (mode === 'probe') await new Promise((r) => setTimeout(r, 30));
    else b.fireMessageToBanner({ type: 'rf-offline-snapshot-state', offlineSnapshot: false });
    assert.equal(findByPred(n => n.id === 'rfOfflineStartupNote'), null,
      mode + '：恢复后说明必须消失（清除要先于改样式，否则会被观察器重建）');
    assert.equal(findByPred(n => n.id === 'rfOfflineSnapshotBanner'), null, mode + '：提示条同时消失');
  }
});

test('DOM: login note is created on snapshot delivery and removed afterwards (r3 stale-lie)', async () => {
  const b = bootBanner();
  const { findByPred, el } = b.dom;
  const auth = el('div'); auth.id = 'authScreen'; auth.style.display = 'flex';
  b.dom.doc.body.appendChild(auth);
  b.fireMessageToBanner({ type: 'rf-offline-snapshot' });
  const note = findByPred(n => n.id === 'rfOfflineStartupNote');
  assert.ok(note, '离线快照交付时登录屏须有说明');
  assert.ok(/position:relative/.test(note.style.cssText) && /z-index:6/.test(note.style.cssText),
    '说明必须绘制在 .login-bg（z-index:0）之上');
  assert.equal(note.parentNode, auth, '说明挂在 #authScreen 内（随层一起开关）');
  /* 恢复联网：online → 说明与提示条都要清掉 */
  b.fireMessageToBanner({ type: 'rf-offline-snapshot-state', offlineSnapshot: false });
  assert.equal(findByPred(n => n.id === 'rfOfflineStartupNote'), null, '不再离线时说明必须清除（不得留陈旧谎言）');
  assert.equal(findByPred(n => n.id === 'rfOfflineSnapshotBanner'), null);
});

test('offline_banner.js: self-contained IIFE, four ten-language rows, textContent only', () => {
  const src = fs.readFileSync(path.join(FRONTEND, 'offline_banner.js'), 'utf8');
  assert.ok(/^\s*(?:\/\*[\s\S]*?\*\/\s*)*\(function\s*\(\s*\)/.test(src), '必须是 IIFE 自包含模块');
  assert.ok(src.trim().endsWith('})();'));
  assert.ok(!src.includes('innerHTML'), '红线：不得使用 innerHTML');
  assert.ok(src.includes('textContent'));
  const rows = [];
  const ctx = { window: {}, RFLang: { register: (r) => rows.push(...r) } };
  vm.runInNewContext(src, ctx); /* navigator 缺席 → init() 直接返回，不触 DOM */
  assert.equal(rows.length, 4, '快照提示 2 条 + 启动登录态说明 1 条 + 关闭按钮 1 条');
  assert.ok(rows[0][0].startsWith('当前离线'), '主提示必须如实说明「壳 + 数据需联网」');
  assert.ok(!/可查看最近缓存的页面/.test(src), '不得再声称缓存页面可完整浏览');
  assert.ok(rows[2][0].includes('无法校验登录状态'), '第 3 条：离线重载登录态说明');
  assert.equal(rows[3][1], 'Hide this notice', '第 4 条：提示条必须有自助关闭出口');
  for (const row of rows) {
    assert.equal(row.length, 10, '十语言列');
    for (const cell of row) assert.ok(typeof cell === 'string' && cell.trim(), '词条不得有空列');
  }
});

test('offline_banner.js: banner geometry cannot be stretched to the viewport (r3 high)', () => {
  /* 第 3 轮评审 high：index.html 的 body{display:flex;height:100vh;overflow:hidden}
     会把 block 级 flex 子项的交叉轴（高度）拉到 100vh。提示条必须是 fixed
     定位、显式 height:auto 且 flex:0 0 auto，否则会变成盖住登录页的全屏色块。 */
  const src = fs.readFileSync(path.join(FRONTEND, 'offline_banner.js'), 'utf8');
  assert.ok(src.includes('position:fixed'), '提示条必须脱离 body 的 flex 布局（fixed）');
  assert.ok(src.includes('flex:0 0 auto'), '必须显式禁止 flex 拉伸');
  assert.ok(src.includes('height:auto'), '必须显式 height:auto');
  assert.ok(src.includes('max-height:'), '必须有高度上限兜底');
  assert.ok(!/cssText\s*=\s*'[^']*position:sticky/.test(src), 'sticky 会被 body 的 align-items:stretch 拉满屏');
  /* 自助出口 + 恢复路径 */
  assert.ok(src.includes("'rfOfflineSnapshotBannerClose'") || src.includes("BANNER_ID + 'Close'"), '必须有关闭按钮');
  assert.ok(src.includes("fetch('/api/health'"), '恢复判定只打同源 /api/health（零第三方出站）');
  assert.ok(src.includes('probeBackend') && src.includes('scheduleProbe'), 'onLine 恒真时靠回环探测收起');
  assert.ok(src.includes('clearLoginNote') && src.includes('NOTE_ID'), '登录说明必须有清理路径（评审 #3 陈旧谎言）');
  assert.ok(src.includes('z-index:6'), '登录说明必须绘制在 .login-bg（z-index:0）之上');
});

