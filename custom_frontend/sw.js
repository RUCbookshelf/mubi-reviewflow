/* =====================================================================
   木笔ReviewFlow · W5 PWA 离线壳 Service Worker（2.1.4-r8，手写轻量，不引构建链）
   ---------------------------------------------------------------------
   治理红线（空白格计划原文口径）：
   - 联网默认关 / 零额外出站：不做 precache、不做预取、不访问任何外部端点；
     只在页面自身请求经过时被动缓存（runtime caching），且仅同源 GET。
   - 不做离线编辑：/api/ 与一切非「带 ?v= 令牌的 JS/CSS · 文档导航」请求
     一律直连不缓存；离线时的写失败由应用既有错误处理优雅降级。
   - scope 相对化：快照键一律由 self.registration.scope 推导，兼容根路径与
     /reviewflow/ 子路径部署（index.html 注册行同样用相对 './sw.js'+scope './'）。
   策略：
   - 带 ?v= 令牌的同源 JS/CSS（MM5-01 令牌制运行时资产）：cache-first，
     未命中取网络并回填（launcher 对其下发 max-age=86400；令牌一换 URL 即换，
     旧条目随版本化缓存名在 activate 时整体废弃）。
   - 文档导航（index.html）：network-first，但只有「同源 + 在 scope 内 + 非 /api/
     且响应 content-type 为 text/html」才写快照（两道闸，防 API/下载响应顶掉壳）。
     网络失败回退最近一次快照，并经 postMessage 协议告知页面（offline_banner.js
     渲染十语言「离线只读快照」提示条）；标记键 = event.resultingClientId（新文档
     id，导航 fetch 的 event.clientId 是旧文档）。无快照时不兜底不伪造，交由浏览器
     原生错误页（诚实降级）。
   ===================================================================== */
'use strict';

(function (root) {
  var VERSION = '20261030-r8-W5PWA1';
  var RUNTIME_CACHE = 'rf-runtime-' + VERSION;
  var SHELL_CACHE = 'rf-shell-' + VERSION;

  function urlOf(requestUrl) {
    return new URL(requestUrl, root.location.href);
  }

  function isGet(request) {
    return String(request.method || 'GET').toUpperCase() === 'GET';
  }

  function isSameOrigin(requestUrl) {
    try {
      return urlOf(requestUrl).origin === root.location.origin;
    } catch (e) {
      return false;
    }
  }

  function isNavigation(request) {
    return request.mode === 'navigate' || request.destination === 'document';
  }

  /* 唯一允许进入 runtime 缓存的形态：带 ?v= 令牌的同源 GET JS/CSS。 */
  function isVersionedStatic(request) {
    if (!isGet(request) || !isSameOrigin(request.url)) return false;
    var dest = request.destination || '';
    if (dest !== 'script' && dest !== 'style') return false;
    try {
      return urlOf(request.url).searchParams.has('v');
    } catch (e) {
      return false;
    }
  }

  /* 文档导航且确实是「应用壳」：同源、落在 SW scope 内、且不是 API 路径。
     r8 第 2 轮评审：旧实现把任一 navigate 都当壳——用户在地址栏直接打开
     /api/runtime 时，那份 JSON 会以 shellKey() 覆盖 index.html 快照（离线壳
     被 API 响应顶掉，与下方「/api/ 永不缓存」红线自相矛盾）。 */
  function isShellNavigation(request) {
    if (!isNavigation(request)) return false;
    if (!isSameOrigin(request.url)) return false;
    var url;
    try {
      url = urlOf(request.url);
    } catch (e) {
      return false;
    }
    var scope = root.registration.scope || root.location.href;
    if (url.href.indexOf(scope) !== 0) return false;
    if (isApiPath(url.pathname)) return false;
    return true;
  }

  /* API 前缀在根路径与 /reviewflow/ 子路径部署下都判得出来（不写死 '/'）。 */
  function isApiPath(pathname) {
    var p = String(pathname || '');
    return p === '/api' || p.indexOf('/api/') === 0 || p.indexOf('/api/') >= 0;
  }

  function strategyFor(request) {
    if (isShellNavigation(request)) return 'network-first-shell';
    if (isVersionedStatic(request)) return 'cache-first-runtime';
    /* 含全部 /api/、图片、字体、非 HTML 文档与其余形态：直连，永不缓存
       （离线只读红线）。api 导航尤其不得进入快照键。 */
    return 'network-only';
  }


  async function cacheFirstRuntime(request) {
    var cache = await root.caches.open(RUNTIME_CACHE);
    var hit = await cache.match(request);
    if (hit) return hit;
    try {
      var fresh = await root.fetch(request);
      if (fresh && fresh.ok && !fresh.redirected) {
        cache.put(request, fresh.clone()).catch(function () { /* 回填失败不影响本次响应 */ });
      }
      return fresh;
    } catch (e) {
      /* 未缓存且离线：如实回 503 空体，不伪装成功、不让 promise 裸抛。 */
      return new root.Response('', { status: 503, statusText: 'Offline (asset not cached)' });
    }
  }

  /* 快照键 = SW scope（形如 https://host/ 或 https://host/reviewflow/）：
     部署在根路径或子路径都各自稳定，不写死任何绝对路径。 */
  function shellKey() {
    return root.registration.scope;
  }

  /* 只有 HTML 响应才配当「离线壳快照」：双保险。第一道在 isShellNavigation
     按 URL 排除 /api/；第二道在这里按响应类型排除下载/JSON/PDF 等——
     即便将来又出现新的非 HTML 导航路径，也不会污染 shellKey()。 */
  function isHtmlResponse(resp) {
    if (!resp || !resp.ok || resp.redirected) return false;
    var headers = resp.headers;
    var type = headers && typeof headers.get === 'function'
      ? String(headers.get('content-type') || '') : '';
    return type.toLowerCase().indexOf('text/html') === 0;
  }

  async function networkFirstShell(event) {
    var cache = await root.caches.open(SHELL_CACHE);
    try {
      var fresh = await root.fetch(event.request);
      if (isHtmlResponse(fresh)) {
        cache.put(shellKey(), fresh.clone()).catch(function () { /* 快照写失败不影响在线响应 */ });
      }
      return fresh;
    } catch (e) {
      var snap = await cache.match(shellKey());
      if (snap) {
        markSnapshotClient(event);
        /* 先 mark 后对账，并把本事件交付的文档 id 排除在清理之外。 */
        pruneSnapshotClients(snapshotClientId(event));
        return snap;
      }
      throw e; /* 无快照：浏览器原生离线错误页（不伪造内容） */
    }
  }

  /* ---- 离线快照提示协议（页面 offline_banner.js 为对端） ---- */
  /* 键语义（第 2 轮评审修复）：键必须是「被兜底交付的那个新文档」的 client id。
     导航 fetch 里 event.clientId 指向「发起导航的旧文档」（新标签页为空串），
     新文档 id 是 event.resultingClientId；查询回执按 event.source.id 比对。
     旧实现只认 event.clientId，于是回执恒为 false，提示条在生产上从不出现。 */
  var snapshotClients = {}; /* clientId(新文档) -> true */

  function snapshotClientId(event) {
    return event.resultingClientId || event.clientId || '';
  }

  function markSnapshotClient(event) {
    var id = snapshotClientId(event);
    if (!id) return;
    snapshotClients[id] = true;
    /* 尽力直接推一条（页面监听器可能尚未就绪；下方查询协议才是权威通道）。 */
    if (root.clients && typeof root.clients.get === 'function') {
      root.clients.get(id).then(function (client) {
        if (client) client.postMessage({ type: 'rf-offline-snapshot' });
      }).catch(function () { /* 推送失败无碍：页面就绪后会主动查询 */ });
    }
  }

  root.addEventListener('message', function (event) {
    var data = event && event.data;
    if (!data || !event.source || !event.source.id) return;
    if (data.type === 'rf-query-offline-snapshot') {
      event.source.postMessage({
        type: 'rf-offline-snapshot-state',
        offlineSnapshot: snapshotClients[event.source.id] === true
      });
    } else if (data.type === 'rf-leave-offline-snapshot') {
      delete snapshotClients[event.source.id];
    }
  });

  /* 标记表只增不减（第 2 轮评审）：页关闭/崩溃/不派发 online 时旧 id 会残留。
     clients 快照即「当前仍活着的文档」，不在其中的键一律丢弃。
     第 3 轮评审 low（顺序竞态）：若 matchAll 先返回、mark 后落地，刚交付的
     新文档会被当成「已关闭」删掉（实测 matchAll 快于 fetch 时回执为 false）。
     因此 (1) 对账必须在本事件 mark 之后发起；(2) 对账排除 keepId（本事件
     交付的文档 id），双保险。 */
  function pruneSnapshotClients(keepId) {
    if (!root.clients || typeof root.clients.matchAll !== 'function') return;
    root.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
      var alive = {};
      for (var i = 0; i < list.length; i++) if (list[i] && list[i].id) alive[list[i].id] = true;
      /* 新文档可能尚未出现在 matchAll 快照里：本事件 id 永远保留。 */
      if (keepId) alive[keepId] = true;
      for (var id in snapshotClients) if (!alive[id]) delete snapshotClients[id];
    }).catch(function () { /* 对账失败不阻断请求处理 */ });
  }

  /* ---- 事件接线 ---- */
  root.addEventListener('fetch', function (event) {
    var request = event.request;
    /* 非同源 / 非 GET：完全不触碰（隐私红线：只处理本应用自身的静态请求）。 */
    if (!isGet(request) || !isSameOrigin(request.url)) return;
    var strategy = strategyFor(request);
    if (strategy === 'cache-first-runtime') {
      event.respondWith(cacheFirstRuntime(request));
    } else if (strategy === 'network-first-shell') {
      event.respondWith(networkFirstShell(event));
    }
    /* 'network-only'：不 respondWith、不缓存，直连。 */
  });

  /* 版本轮换：激活时清掉非本版本缓存（无 install 阶段——零 precache 红线）。 */
  root.addEventListener('activate', function (event) {
    var keep = {};
    keep[RUNTIME_CACHE] = true;
    keep[SHELL_CACHE] = true;
    event.waitUntil(root.caches.keys().then(function (names) {
      return Promise.all(names
        .filter(function (name) { return !keep[name]; })
        .map(function (name) { return root.caches.delete(name); }));
    }));
  });

  /* 测试/调试句柄：node 冒烟钉子（build/tests/w5_pwa_sw.test.cjs）直接驱动
     这些纯策略函数；生产环境仅用于诊断，不含任何行为开关。 */
  root.RF_SW = {
    version: VERSION,
    runtimeCache: RUNTIME_CACHE,
    shellCache: SHELL_CACHE,
    isGet: isGet,
    isSameOrigin: isSameOrigin,
    isNavigation: isNavigation,
    isShellNavigation: isShellNavigation,
    isApiPath: isApiPath,
    isHtmlResponse: isHtmlResponse,
    isVersionedStatic: isVersionedStatic,
    strategyFor: strategyFor,
    shellKey: shellKey,
    snapshotClientId: snapshotClientId
  };
})(typeof self !== 'undefined' ? self : this);
