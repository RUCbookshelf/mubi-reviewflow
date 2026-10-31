'use strict';
/* R8 · W5 PWA 真实 DOM 钉子（第 4 轮评审 medium 原话）：
   「提示条固定盖住登录页顶部控制条，语言/浅色深色/暂停动效全部点不到 …
     补真实 DOM 钉子（登录页三控件可点击）」

 为什么必须是真浏览器：这条 finding 的全部内容是「谁盖住谁」，只有真实
 布局引擎（position/z-index/transform/elementFromPoint/真实点击）能判。
 同一轮的另一半——`#authScreen .site-header` 是 position:absolute;top:30px，
 给 #authScreen 加内边距挪不动它——同样只能在真实盒模型里看出来。

 做法：本地静态托管 custom_frontend（只读，不写任何文件），从页面里用
 MessageEvent 触发 offline_banner 的「快照已交付」分支（该模块把监听挂在
 navigator.serviceWorker 上），然后断言：
   1) 提示条确实出现（前提成立，否则下面的「没盖住」是空断言）；
   2) 语言 / 深色浅色 / 暂停动效 三个控件：中心点 elementFromPoint 不在条内；
   3) 三个控件都能被 Playwright 真实点击并产生各自的效果（语言菜单展开、
      主题切换、动效 aria-pressed 翻转）；
   4) 登录页所有可见的 button/input/a 中，没有一个整体落在条带里；
   5) 应用态（登录屏隐藏、外壳显示）：侧栏与 #appMain 顶部 .top 也不被压；
   6) 关闭按钮收起后，header 精确回到原位（内联 transform 复原）。
 桌面 1280x800 与窄屏 390x844 各跑一遍（窄屏 .site-header 是 top:16px）。

 运行（CI 同款约定，见 .github/workflows/release.yml 的 Provision playwright）：
   REVIEWFLOW_PLAYWRIGHT=<path-to-playwright> \
   node build/tests/w5_pwa_banner_browser.cjs
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
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.webmanifest': 'application/manifest+json',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
};

/* 只托管本工作树的前端静态文件；/api/* 一律 404（离线态的真实面貌），
   /sw.js 也 404 —— 本钉子用消息注入触发提示条，不起 Service Worker，
   避免注册态污染两次断言的时序。 */
function serveFrontend() {
  const server = http.createServer((req, res) => {
    const pathname = decodeURIComponent(String(req.url).split('?')[0]);
    if (pathname.startsWith('/api/') || pathname === '/sw.js') {
      res.writeHead(404, { 'content-type': 'text/plain' });
      res.end('not found');
      return;
    }
    const rel = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '');
    const file = path.join(FRONTEND, rel);
    if (!file.startsWith(FRONTEND) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404, { 'content-type': 'text/plain' });
      res.end('not found');
      return;
    }
    res.writeHead(200, { 'content-type': MIME[path.extname(file).toLowerCase()] || 'application/octet-stream' });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

/* 触发 offline_banner 的快照分支（模块的监听在 navigator.serviceWorker 上）。 */
const deliverSnapshot = () => {
  navigator.serviceWorker.dispatchEvent(new MessageEvent('message', { data: { type: 'rf-offline-snapshot' } }));
};

/* 三个控件的中心点命中测试 + 是否被条盖住。 */
function hitReport() {
  const bar = document.getElementById('rfOfflineSnapshotBanner');
  if (!bar) return { bar: false };
  const out = { bar: true, barBottom: bar.getBoundingClientRect().bottom, controls: {}, underBar: [] };
  for (const id of ['rf-language-button', 'auTheme', 'auMotion']) {
    const el = document.getElementById(id);
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) { out.controls[id] = { visible: false }; continue; }
    const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    out.controls[id] = {
      visible: true,
      top: r.top,
      hitId: hit ? (hit.id || hit.tagName) : null,
      covered: !!(hit && hit.closest && hit.closest('#rfOfflineSnapshotBanner')),
    };
  }
  const strip = bar.getBoundingClientRect();
  out.underBar = [...document.querySelectorAll('#authScreen button, #authScreen input, #authScreen a')]
    .filter((el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && r.top < strip.bottom && r.bottom > strip.top;
    })
    .map((el) => el.id || String(el.className));
  return out;
}

async function runViewport(browser, origin, viewport) {
  const page = await browser.newPage({ viewport });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  try {
    await page.goto(origin + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#authScreen', { state: 'visible' });
    await page.waitForTimeout(200);

    await page.evaluate(deliverSnapshot);
    await page.waitForTimeout(120);

    const report = await page.evaluate(hitReport);
    assert.equal(report.bar, true, `${viewport.width}px：提示条必须先出现，否则「没盖住」是空断言`);
    assert.ok(report.barBottom > 0, `${viewport.width}px：条必须有真实高度`);

    /* 2) 三个控件都不在条内（可见的才断言——窄屏按设计隐藏动效按钮文案） */
    assert.equal(report.controls['rf-language-button'].visible, true, `${viewport.width}px：语言控件可见`);
    assert.equal(report.controls['auTheme'].visible, true, `${viewport.width}px：深色/浅色控件可见`);
    for (const [id, info] of Object.entries(report.controls)) {
      if (!info.visible) continue;
      assert.equal(info.covered, false,
        `${viewport.width}px：${id} 中心点被提示条盖住（top=${info.top}，条底=${report.barBottom}）`);
      assert.ok(info.top >= report.barBottom,
        `${viewport.width}px：${id} 顶边 top=${info.top} 必须让到条底 ${report.barBottom} 之下`);
    }

    /* 4) 登录页没有任何可见控件整体落在条带内 */
    assert.deepEqual(report.underBar, [],
      `${viewport.width}px：这些登录页控件仍压在提示条条带里：${report.underBar.join(', ')}`);

    /* 3) 真实点击三控件各自生效 */
    const themeBefore = await page.evaluate(() => document.documentElement.dataset.theme);
    await page.click('#auTheme', { timeout: 4000 });
    const themeAfter = await page.evaluate(() => document.documentElement.dataset.theme);
    assert.notEqual(themeAfter, themeBefore, `${viewport.width}px：点击深色/浅色必须真的切换主题`);

    if (report.controls.auMotion && report.controls.auMotion.visible) {
      const before = await page.evaluate(() => document.getElementById('auMotion').getAttribute('aria-pressed'));
      await page.click('#auMotion', { timeout: 4000 });
      const after = await page.evaluate(() => document.getElementById('auMotion').getAttribute('aria-pressed'));
      assert.notEqual(after, before, `${viewport.width}px：点击暂停动效必须真的切换 aria-pressed`);
    }

    await page.click('#rf-language-button', { timeout: 4000 });
    const menuOpen = await page.evaluate(() => !document.getElementById('rf-language-menu').hidden);
    assert.equal(menuOpen, true, `${viewport.width}px：点击语言必须真的展开语言菜单`);
    await page.keyboard.press('Escape').catch(() => {});
    await page.evaluate(() => {
      const btn = document.getElementById('rf-language-button');
      if (document.getElementById('rf-language-menu').hidden === false) btn.click();
    });

    /* 6) 收起必须精确复原 */
    const headerBefore = await page.evaluate(() => {
      const h = document.querySelector('#authScreen .site-header');
      return { transform: h.style.transform, top: Math.round(h.getBoundingClientRect().top) };
    });
    assert.notEqual(headerBefore.transform, '',
      `${viewport.width}px：条在时 header 必须有让位位移（而不是被条盖着）`);
    await page.click('#rfOfflineSnapshotBannerClose', { timeout: 4000 });
    const headerAfter = await page.evaluate(() => {
      const h = document.querySelector('#authScreen .site-header');
      return {
        transform: h.style.transform,
        top: Math.round(h.getBoundingClientRect().top),
        bar: !!document.getElementById('rfOfflineSnapshotBanner'),
      };
    });
    assert.equal(headerAfter.bar, false, `${viewport.width}px：关闭后提示条必须消失`);
    assert.equal(headerAfter.transform, '', `${viewport.width}px：关闭后 header 的内联 transform 必须还原为空`);
    assert.ok(headerAfter.top < headerBefore.top, `${viewport.width}px：关闭后 header 必须回到上方原位`);

    assert.deepEqual(errors, [], `${viewport.width}px：页面不得有运行时错误`);
  } finally {
    await page.close();
  }
}

async function runResize(browser, origin) {
  /* 跨断点缩放：.site-header 的 CSS top 在宽/窄视口不同（30px / 16px）且条在
     窄屏会换行变高。位移量必须每次按「当前 rect 反推的原始 top」现算——缓存
     首次基线会让窄断点下的位移偏小，控制条上沿又滑回条下面（本组自查实测
     700px 时三控件 top=41 而条底=51）。 */
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  try {
    await page.goto(origin + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#authScreen', { state: 'visible' });
    await page.waitForTimeout(200);
    await page.evaluate(deliverSnapshot);
    await page.waitForTimeout(120);
    for (const width of [700, 390, 760, 1024, 1280]) {
      await page.setViewportSize({ width, height: 800 });
      await page.waitForTimeout(200);
      const report = await page.evaluate(hitReport);
      assert.equal(report.bar, true, `${width}px：提示条必须仍在`);
      for (const [id, info] of Object.entries(report.controls)) {
        if (!info.visible) continue;
        assert.equal(info.covered, false, `${width}px：缩放到该宽度后 ${id} 又被提示条盖住`);
        assert.ok(info.top >= report.barBottom,
          `${width}px：${id} 顶边 top=${info.top} 必须仍让到条底 ${report.barBottom} 之下`);
      }
    }
    assert.deepEqual(errors, [], '缩放：页面不得有运行时错误');
  } finally {
    await page.close();
  }
}

async function runAppLayer(browser, origin) {
  /* 应用态：条同样压在 #appShell 与 #appMain 的 .top 控制条上。这里直接切换
     两个层的 display（与 enterApp/logout 的真实写法一致），让模块的观察器
     与 relayout 走真身，再断言两处顶部控件不被覆盖、关闭后内联 padding 复原。 */
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  try {
    await page.goto(origin + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#authScreen', { state: 'visible' });
    await page.waitForTimeout(200);
    await page.evaluate(deliverSnapshot);
    await page.waitForTimeout(120);

    await page.evaluate(() => {
      document.getElementById('authScreen').style.display = 'none';
      document.getElementById('appShell').style.display = '';
      document.getElementById('appMain').style.display = '';
      document.querySelector('.footer').style.display = '';
    });
    await page.waitForTimeout(150);

    const app = await page.evaluate(() => {
      const bar = document.getElementById('rfOfflineSnapshotBanner');
      const strip = bar.getBoundingClientRect();
      const top = document.querySelector('#appMain .top').getBoundingClientRect();
      const checked = {};
      for (const id of ['rf-app-language-button', 'whoName', 'logoutBtn']) {
        const el = document.getElementById(id);
        if (!el) continue;
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height) { checked[id] = { visible: false }; continue; }
        const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
        checked[id] = { visible: true, covered: !!(hit && hit.closest && hit.closest('#rfOfflineSnapshotBanner')) };
      }
      return {
        barBottom: strip.bottom,
        topBarTop: top.top,
        checked,
        shellPad: document.getElementById('appShell').style.paddingTop,
        mainPad: document.getElementById('appMain').style.paddingTop,
      };
    });

    assert.ok(app.topBarTop >= app.barBottom,
      `应用态：主区顶部 .top（top=${app.topBarTop}）必须让到条底 ${app.barBottom} 之下`);
    assert.notEqual(app.shellPad, '', '应用态：侧栏必须为提示条让出顶部内边距');
    for (const [id, info] of Object.entries(app.checked)) {
      if (!info.visible) continue;
      assert.equal(info.covered, false, `应用态：${id} 不得被提示条盖住`);
    }

    /* 侧栏开关（#rfNavigationSheet）是 fixed;top:8px 的圆钮，层级低于条——
       同一类「可点入口被盖」缺陷。这里把它也纳进断言：中心点不被条覆盖，
       且命中测试落回开关本身（= 真实可点）。注意本文件是静态托管、没有登录
       会话，sidebar_sheet.js 的 setOpen() 会因 loggedIn() 为假而拒绝展开，
       所以这里不点它、只钉「没被条挡住」这一几何事实。
       另外：先显出来、等一拍再量——真实路径里 sidebar_sheet.js 的 refresh()
       也是异步把 hidden 摘掉的，模块靠 MutationObserver 补让位。同 tick 测量
       会读到让位之前的几何。 */
    await page.evaluate(() => { document.getElementById('rfNavigationSheet').hidden = false; });
    await page.waitForTimeout(120);
    const fab = await page.evaluate(() => {
      const sheet = document.getElementById('rfNavigationSheet');
      const r = sheet.getBoundingClientRect();
      const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
      const hit = document.elementFromPoint(cx, cy);
      const toggle = document.getElementById('rfSidebarToggle');
      const tr = toggle.getBoundingClientRect();
      const thit = document.elementFromPoint(tr.x + tr.width / 2, tr.y + tr.height / 2);
      return {
        top: r.top, height: r.height,
        covered: !!(hit && hit.closest && hit.closest('#rfOfflineSnapshotBanner')),
        centerHitId: hit ? (hit.id || hit.tagName) : null,
        toggleCovered: !!(thit && thit.closest && thit.closest('#rfOfflineSnapshotBanner')),
        toggleHitId: thit ? (thit.id || thit.tagName) : null,
      };
    });
    assert.ok(fab.top >= app.barBottom,
      `应用态：侧栏开关 top=${fab.top} 必须让到条底 ${app.barBottom} 之下`);
    assert.equal(fab.covered, false, '应用态：侧栏开关（#rfNavigationSheet）不得被提示条盖住');
    assert.equal(fab.toggleCovered, false, '应用态：侧栏开关内部按钮不得被提示条盖住');
    assert.ok(fab.toggleHitId && fab.toggleHitId !== 'rfOfflineSnapshotBanner',
      `应用态：侧栏开关中心必须命中开关本身（实际命中 ${fab.toggleHitId}）`);

    await page.evaluate(() => document.getElementById('rfOfflineSnapshotBannerClose').click());
    const restored = await page.evaluate(() => ({
      shellPad: document.getElementById('appShell').style.paddingTop,
      mainPad: document.getElementById('appMain').style.paddingTop,
      sheetTop: document.getElementById('rfNavigationSheet').style.top,
      bar: !!document.getElementById('rfOfflineSnapshotBanner'),
    }));
    assert.equal(restored.bar, false, '应用态：关闭后提示条必须消失');
    assert.equal(restored.shellPad, '', '应用态：关闭后侧栏内边距必须还原');
    assert.equal(restored.mainPad, '', '应用态：关闭后主区内边距必须还原');
    assert.equal(restored.sheetTop, '', '应用态：关闭后侧栏开关的内联 top 必须还原');

    assert.deepEqual(errors, [], '应用态：页面不得有运行时错误');
  } finally {
    await page.close();
  }
}

(async () => {
  const server = await serveFrontend();
  const origin = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch({ headless: true });
  try {
    for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }]) {
      await runViewport(browser, origin, viewport);
    }
    await runAppLayer(browser, origin);
    await runResize(browser, origin);
    console.log('PASS W5 offline banner never occludes login controls '
      + '(three controls clickable at 1280px and 390px, app-layer top bar clear, close restores layout)');
  } finally {
    await browser.close();
    await new Promise((r) => server.close(r));
  }
})().catch((error) => { console.error(error); process.exit(1); });
