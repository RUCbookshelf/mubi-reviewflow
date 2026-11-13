'use strict';
/* r10-u3 移动端泳道 —— 真实浏览器行为测试（Playwright · 隔离实例 · 窄视口）。
 *
 * 运行（由 build/tests/test_r10_u3_mobile_gate.py 起停实例后驱动；也可手工）：
 *   REVIEWFLOW_PLAYWRIGHT=E:/rf214-playwright/node_modules/playwright \
 *   PLAYWRIGHT_BROWSERS_PATH=C:/Users/16073/AppData/Local/ms-playwright \
 *   REVIEWFLOW_R10U3_URL=http://127.0.0.1:<port> \
 *   node build/tests/test_r10_u3_mobile_browser.cjs        （exit 0 = 全部通过）
 *
 * 真机不可得（任务书「特别说明」）：这里用 Playwright 窄视口 360/390/492/760px
 * 代替，并把触控命中区断言在 ≥44px。每条断言只看**用户可见结果**（控件可见/
 * 不可见、尺寸、点一下之后发生了什么），回退任一修复都会让对应 check 抛错：
 *  · F37 无理由库管理权时「其他（自定义）」输入框必须占满决策行；复筛页移动端
 *       不再出现桌面键盘提示。
 *  · F38 排除理由行默认折叠（决策条回到按钮行高度）；触屏第一次点「3 排除」
 *       只展开不提交，选好理由再点才提交。
 *  · F38 实体键盘 3（本轮修复）：折叠态下按 3，理由控件必须**变得可见**——
 *       否则「请先选择排除理由」的提示指向一个看不见的控件（死路）。
 *  · F39 轨迹入口命中区 ≥44×44 且仍可点进轨迹页。
 *  · F40 首页设置入口完整可用，PRISMA 预览和分析目录保留可见滚动条。
 *  · F41 底部阶段导航在三个工作流页可用，点击后切页并同步当前项。
 *  · F42 复筛决策条与编码保存条贴在底部导航上方，滚到底时内容不被遮挡。
 *  · F43 「停止」按钮有可见短标签，高度仍是 48px。
 *  · F44 DOI 出现在「文献信息」折叠区标题上；展开后类型/DOI 等字段齐全。
 *  · 对照组：1280px 桌面宽度下，折叠类、阶段切换条、短标签、整行规则全都不生效。
 */
const assert = require('node:assert/strict');
const { chromium } = require(process.env.REVIEWFLOW_PLAYWRIGHT || 'playwright');

const url = (process.env.REVIEWFLOW_R10U3_URL || '').replace(/\/$/, '');
if (!url) throw new Error('REVIEWFLOW_R10U3_URL 未设置：本脚本由 pytest 门控在隔离实例上运行');

const WIDTHS = [360, 390, 492, 760];
const HEIGHT = 751;
const HIT = 44;

const RIS = ['One', 'Two', 'Three', 'Four', 'Five'].map(i => [
  'TY  - JOUR',
  'AU  - Alpha' + i + ', Ann',
  'TI  - Mobile Probe Study ' + i + ' About Hypertension Screening',
  'JO  - Journal of Mobile Probe ' + i,
  'PY  - 202' + i,
  'DO  - 10.test/u3.' + i,
  'AB  - Abstract for study ' + i + '. Long enough to occupy a couple of lines on a narrow viewport.',
  'ER  - ',
].join('\n')).join('\n');

async function token() {
  const stamp = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  const user = 'r10u3' + stamp;
  const password = 'R10U3-browser-2026!';
  let response = await fetch(url + '/api/auth/register', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: user, password }),
  });
  let data = response.ok ? await response.json() : null;
  if (!data) {
    response = await fetch(url + '/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: user, password }),
    });
    assert.ok(response.ok, '前置登录失败：' + response.status);
    data = await response.json();
  }
  const headers = { Authorization: 'Bearer ' + data.token };
  const created = await fetch(url + '/api/tasks', {
    method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: 'r10-u3-' + stamp }),
  });
  assert.ok(created.ok, '前置任务创建失败：' + created.status);
  const task = await created.json();
  const form = new FormData();
  form.append('file', new Blob([RIS], { type: 'application/octet-stream' }), 'probe.ris');
  const imported = await fetch(
    url + '/api/tasks/' + (task.task_id || task.id) + '/import?screener=' + user,
    { method: 'POST', headers, body: form });
  assert.ok(imported.ok, '前置导入失败：' + imported.status);
  const taskUrl = url + '/api/tasks/' + (task.task_id || task.id);
  const records = await fetch(taskUrl + '/articles', { headers });
  assert.ok(records.ok, '前置题录读取失败：' + records.status);
  const { articles } = await records.json();
  assert.equal(articles.length, 5, '前置导入应有五篇题录');
  const reviewArticle = articles.find(article => article.title === 'Mobile Probe Study Four About Hypertension Screening');
  const codingArticle = articles.find(article => article.title === 'Mobile Probe Study Five About Hypertension Screening');
  assert.ok(reviewArticle && codingArticle, '前置复筛与编码需各有一篇题录');
  const setup = [
    ['/decisions', { zotero_key: reviewArticle.zotero_key, decision: 'include', reason: '' }],
    ['/decisions', { zotero_key: codingArticle.zotero_key, decision: 'include', reason: '' }],
    ['/stage2/decisions', { zotero_key: codingArticle.zotero_key, decision: 'include', reason: '' }],
    ['/coding/scheme', { replace: true, scheme: { version: 1, dimensions: Array.from(
      { length: 10 }, (_, index) => ({ name: '合成编码字段 ' + (index + 1), dtype: 'text' }),
    ) } }],
  ];
  for (const [path, body] of setup) {
    const seeded = await fetch(taskUrl + path, {
      method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    assert.ok(seeded.ok, '前置测试数据写入失败 ' + path + '：' + seeded.status);
  }
  return data.token;
}

(async () => {
  const authToken = await token();
  const browser = await chromium.launch({ headless: true });
  let checks = 0;
  const check = async (name, fn) => {
    try { await fn(); checks++; console.log('PASS', name); }
    catch (error) { console.log('FAIL', name); throw error; }
  };

  const open = async (width) => {
    const page = await browser.newPage({ viewport: { width, height: HEIGHT } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(sessionToken => sessionStorage.setItem('rf_token', sessionToken),
                             authToken);
    await page.goto(url + '/#', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => { try { return Boolean(S && S.task); } catch (_) { return false; } },
                              null, { timeout: 60000 });
    /* 工作流设置的自动刷新可能短暂占住渲染；稳定后再进页面。 */
    await page.waitForTimeout(3000);
    return { page, errors };
  };
  /* go() 返回 Promise 且页面可能正被一次自动刷新接管：重试直到目标页真的可用。 */
  const gotoPage = async (page, id, readySelector) => {
    for (let attempt = 0; attempt < 5; attempt++) {
      await page.evaluate(target => { void go(target); }, id);
      if (!readySelector) { await page.waitForTimeout(2200); return; }
      try { await page.waitForSelector(readySelector, { timeout: 12000 }); return; }
      catch (_) { await page.waitForTimeout(2200); }
    }
    throw new Error('页面 ' + id + ' 未能就绪（' + readySelector + '）');
  };
  /* 移动呈现层由 mobile_concept.js 在 MutationObserver 的下一帧统一施加；刚渲染
     完就采样会与它赛跑（本轮实测 760px 偶发）。这里等它落定：折叠类已施加，
     或理由已有取值（预填/沿用时不折叠——那也是正确状态）。等不到就让断言去红。 */
  const settle = async page => {
    try {
      await page.waitForFunction(() => {
        const bar = document.querySelector('.scr-decisions');
        if (!bar) return false;
        const select = document.querySelector('#excReason');
        return bar.classList.contains('rf-reason-collapsed') || Boolean(select && select.value);
      }, null, { timeout: 15000 });
    } catch (_) { /* 交给断言：产品若不折叠，这里超时即红 */ }
  };
  const findNav = async (width) => {
    const { page, errors } = await open(width);
    await gotoPage(page, 'screen', '#scrBody .scr-title');
    await settle(page);
    return { page, errors };
  };

  /* ---------------- F37 ---------------- */
  await check('F37 无理由库管理权时自定义输入框仍占满决策行（原缺陷：约 1/3 列宽）', async () => {
    for (const width of WIDTHS) {
      const { page } = await findNav(width);
      /* 强制展开理由行（折叠态由 F38 覆盖），选「其他（自定义）」 */
      await page.evaluate(() => document.querySelector('.scr-decisions')
        ?.classList.remove('rf-reason-collapsed'));
      await page.selectOption('#excReason', '__custom__');
      await page.waitForTimeout(250);
      /* 原缺陷只在**没有理由库管理权**时出现：workflow_controls.js 的
         decorateReasons 仅当 settings.can_manage 才建 .wf-custom-reason-row 包裹，
         否则输入框直接挂在 .scr-decisions 的三列网格里 → 只得到 1/3 列宽。
         这里把输入框挪回「无包裹」的真实形状来量测，才是这条缺陷本身。
         （留在 row 里量是没有区分度的：row 的规则在基线上就已给全宽。） */
      const measured = await page.evaluate(() => {
        const input = document.querySelector('#excCustomReason');
        const bar = document.querySelector('.scr-decisions');
        if (!input || !bar) return null;
        bar.append(input);
        const box = input.getBoundingClientRect();
        const style = getComputedStyle(bar);
        const contentWidth = bar.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
        return { width: box.width, contentWidth,
                 display: getComputedStyle(input).display,
                 gridColumn: getComputedStyle(input).gridColumn,
                 wrapped: Boolean(input.parentElement.closest('.wf-custom-reason-row')) };
      });
      assert.ok(measured, 'F37：自定义理由输入框必须存在（' + width + 'px）');
      assert.equal(measured.wrapped, false, 'F37：量测形状必须是无包裹的真实缺陷形状');
      assert.equal(measured.display, 'block', 'F37：输入框必须可见（' + width + 'px）');
      assert.ok(Math.abs(measured.width - measured.contentWidth) <= 4,
        'F37：' + width + 'px 下无包裹时输入框宽 ' + measured.width + '，决策栏内容宽 ' + measured.contentWidth +
        '（grid-column=' + measured.gridColumn + '），未占满决策行——原缺陷约 1/3 列宽');
      await page.close();
    }
  });

  await check('F37 复筛页移动端不再常驻桌面键盘提示', async () => {
    for (const width of WIDTHS) {
      const { page } = await open(width);
      await gotoPage(page, 'fulltext');
      const hint = await page.evaluate(() => {
        const node = [...document.querySelectorAll('.ft-decide .hint')]
          .find(candidate => candidate.textContent.includes('键盘'));
        if (!node) return { present: false };
        return { present: true, display: getComputedStyle(node).display,
                 visible: node.offsetParent !== null };
      });
      assert.equal(hint.visible, false,
        'F37：' + width + 'px 复筛页仍显示桌面键盘提示（display=' + hint.display + '）');
      await page.close();
    }
  });

  /* ---------------- F38 ---------------- */
  await check('F38 排除理由行默认折叠，决策条回到按钮行高度', async () => {
    for (const width of WIDTHS) {
      const { page } = await findNav(width);
      const state = await page.evaluate(() => {
        const bar = document.querySelector('.scr-decisions');
        const reason = document.querySelector('#excReason');
        return { collapsed: bar.classList.contains('rf-reason-collapsed'),
                 reasonVisible: reason.offsetParent !== null,
                 barHeight: Math.round(bar.getBoundingClientRect().height),
                 viewportHeight: innerHeight };
      });
      assert.equal(state.collapsed, true, 'F38：' + width + 'px 无理由时应默认折叠');
      assert.equal(state.reasonVisible, false, 'F38：折叠态理由下拉必须不可见');
      /* 折叠后决策条 = 按钮行高度（批准单实测旧值 138px） */
      assert.ok(state.barHeight <= 90,
        'F38：' + width + 'px 折叠后决策条仍高 ' + state.barHeight + 'px（应回到按钮行高度）');
      assert.ok(state.barHeight / state.viewportHeight < 0.13,
        'F38：' + width + 'px 决策条占视口 ' +
        (state.barHeight / state.viewportHeight * 100).toFixed(1) + '%（原缺陷 18.4%）');
      await page.close();
    }
  });

  await check('F38 触屏两段式：第一次点「3 排除」只展开不提交，选好理由再点才提交', async () => {
    const { page } = await findNav(390);
    const before = await page.evaluate(() => document.querySelector('#scrBody .scr-title').textContent);
    await page.locator('#btnExc').click();
    await page.waitForTimeout(500);
    const afterFirst = await page.evaluate(() => ({
      title: document.querySelector('#scrBody .scr-title').textContent,
      collapsed: document.querySelector('.scr-decisions').classList.contains('rf-reason-collapsed'),
      reasonVisible: document.querySelector('#excReason').offsetParent !== null,
      focused: document.activeElement?.id,
    }));
    assert.equal(afterFirst.title, before, 'F38：第一次点 3 不得提交决策（文献被误排除）');
    assert.equal(afterFirst.collapsed, false, 'F38：第一次点 3 必须展开理由行');
    assert.equal(afterFirst.reasonVisible, true, 'F38：展开后理由下拉必须可见');
    assert.equal(afterFirst.focused, 'excReason', 'F38：展开后焦点必须落在理由下拉');
    await page.selectOption('#excReason', { index: 1 });
    await page.waitForTimeout(200);
    await page.locator('#btnExc').click();
    await page.waitForFunction(title => document.querySelector('#scrBody .scr-title')?.textContent !== title,
                               before, { timeout: 20000 });
    console.log('     （第二篇已载入，第一点未提交）');
    await page.close();
  });

  await check('F38 实体键盘 3 不再指向隐藏控件（折叠态先展开，提示与焦点都在）', async () => {
    for (const width of [360, 390]) {
      const { page } = await findNav(width);
      /* 确保是折叠 + 无理由：清掉会话记忆与已保存理由 */
      await page.evaluate(() => {
        try { sessionStorage.clear(); } catch (_) {}
        const bar = document.querySelector('.scr-decisions');
        bar.classList.add('rf-reason-collapsed');
        const reason = document.querySelector('#excReason');
        reason.value = '';
      });
      const before = await page.evaluate(() => ({
        collapsed: document.querySelector('.scr-decisions').classList.contains('rf-reason-collapsed'),
        reasonVisible: document.querySelector('#excReason').offsetParent !== null,
      }));
      assert.equal(before.collapsed, true, 'F38：前置折叠态未成立');
      assert.equal(before.reasonVisible, false, 'F38：前置条件要求理由控件不可见');
      await page.evaluate(() => document.body.focus());
      await page.keyboard.press('3');
      await page.waitForTimeout(600);
      const after = await page.evaluate(() => ({
        reasonVisible: document.querySelector('#excReason').offsetParent !== null,
        message: document.querySelector('.scr-reason-error')?.textContent || null,
        focused: document.activeElement?.id,
        title: document.querySelector('#scrBody .scr-title').textContent,
      }));
      assert.equal(after.reasonVisible, true,
        'F38：' + width + 'px 折叠态按 3 后理由控件仍然不可见——提示指向看不见的控件（死路）');
      assert.equal(after.message, '请先选择排除理由',
        'F38：' + width + 'px 按 3 仍须给出文字说明（F08 不得因展开而丢失）');
      assert.equal(after.focused, 'excReason', 'F38：焦点必须落在（此时可见的）理由控件');
      await page.close();
    }
  });

  /* ---------------- F39 ---------------- */
  await check('F39 轨迹入口命中区 ≥44×44 且仍能跳转', async () => {
    for (const width of WIDTHS) {
      const { page } = await findNav(width);
      await page.locator('.rfma-bottom-nav [data-rfma-page="more"]').click();
      const entry = page.locator('.rfma-more[open]').getByRole('button', { name: '轨迹', exact: true });
      const box = await entry.boundingBox();
      assert.ok(box, 'F39：轨迹入口必须存在（' + width + 'px）');
      assert.ok(box.width >= HIT, 'F39：' + width + 'px 命中区宽 ' + box.width + 'px < 44');
      assert.ok(box.height >= HIT, 'F39：' + width + 'px 命中区高 ' + box.height + 'px < 44');
      await entry.click();
      await page.waitForFunction(() => S.page === 'trace');
      await page.close();
    }
  });

  /* ---------------- F40 ---------------- */
  await check('F40 首页设置入口完整可用，分析目录与 PRISMA 保留滚动条', async () => {
    for (const width of [360, 390]) {
      const { page } = await open(width);
      await page.locator('.rfma-bottom-nav [data-rfma-page="home"]').click();
      const tabs = page.locator('.wf-tabs');
      await tabs.waitFor({ state: 'visible' });
      const home = await tabs.evaluate(node => ({
        overflow: node.scrollWidth - node.clientWidth,
        visible: [...node.querySelectorAll('[role="tab"]')].filter(tab => tab.checkVisibility()).length,
      }));
      assert.equal(home.visible, 6, 'F40：首页六项设置入口必须可见');
      assert.ok(home.overflow <= 1, 'F40：' + width + 'px 首页设置入口不得横向溢出');
      await page.locator('#wf-tab-models').click();
      await page.locator('#wf-panel-models').waitFor({ state: 'visible' });
      await page.locator('#rfmaHomeBack').click();
      await tabs.waitFor({ state: 'visible' });
      await gotoPage(page, 'analysis');
      const analysis = await page.evaluate(() => {
        const node = document.querySelector('.an-jump-categories');
        if (!node) return null;
        return { scrollable: node.scrollWidth - node.clientWidth,
                 scrollbarWidth: getComputedStyle(node).scrollbarWidth };
      });
      assert.ok(analysis, 'F40：分析目录横向条必须存在');
      assert.ok(analysis.scrollable > 0, 'F40：' + width + 'px 分析目录本应可横向滚动');
      assert.notEqual(analysis.scrollbarWidth, 'none', 'F40：分析目录仍隐藏滚动条');
      await gotoPage(page, 'prisma');
      const prisma = await page.evaluate(() => {
        const node = document.querySelector('#prView');
        return node ? { scrollbarWidth: getComputedStyle(node).scrollbarWidth } : null;
      });
      assert.ok(prisma, 'F40：PRISMA 预览容器必须存在');
      assert.notEqual(prisma.scrollbarWidth, 'none', 'F40：PRISMA 预览仍隐藏滚动条');
      await page.close();
    }
  });

  await check('F40 轨迹时间线在窄视口可横向滚动且滚动条不是 none', async () => {
    const { page } = await open(390);
    await gotoPage(page, 'trace');
    await page.waitForTimeout(1500);
    const measured = await page.evaluate(() => {
      const node = document.querySelector('.trace-timeline');
      if (!node) return null;
      return { scrollable: node.scrollWidth - node.clientWidth,
               scrollbarWidth: getComputedStyle(node).scrollbarWidth,
               overflowX: getComputedStyle(node).overflowX };
    });
    assert.ok(measured, 'F40：轨迹时间线必须存在');
    assert.notEqual(measured.scrollbarWidth, 'none', 'F40：轨迹时间线仍隐藏滚动条');
    assert.notEqual(measured.overflowX, 'visible', 'F40：轨迹时间线必须可横向滚动');
    await page.close();
  });

  /* ---------------- F41 ---------------- */
  await check('F41 底部阶段导航可点击且当前项随页面切换', async () => {
    for (const width of [360, 390]) {
      const { page } = await findNav(width);
      const nav = page.locator('.rfma-bottom-nav');
      for (const [stage, label] of [['fulltext', '复筛'], ['bench', '编码'], ['screen', '初筛']]) {
        const button = nav.locator('[data-rfma-page="' + stage + '"]');
        assert.equal(await button.innerText(), label);
        await button.click();
        await page.waitForFunction(id => S.page === id &&
          document.querySelector('.rfma-bottom-nav [data-rfma-page="' + id + '"]')?.getAttribute('aria-current') === 'page',
        stage);
        assert.equal(await nav.locator('[aria-current="page"]').count(), 1, 'F41：只有当前阶段应标记为当前页');
      }
      await page.close();
    }
  });

  /* ---------------- F42 ---------------- */
  await check('F42 复筛决策条与编码保存条贴在底栏上方，滚到底不遮挡编码内容', async () => {
    for (const width of WIDTHS) {
      const { page } = await open(width);
      for (const [stage, selector, button] of [
        ['fulltext', '.ft-decide', '#ftInc'],
        ['bench', '.rf-mobile-coding-footer', '#cbSave'],
      ]) {
        await gotoPage(page, stage, selector);
        const dock = page.locator(selector);
        const nav = page.locator('.rfma-bottom-nav');
        const before = await dock.boundingBox();
        const bounds = await nav.boundingBox();
        assert.ok(before && bounds && before.height > 0, 'F42：' + width + 'px 动作栏和底栏必须可见');
        assert.ok(Math.abs(before.y + before.height - bounds.y) <= 1,
          'F42：' + width + 'px ' + stage + ' 动作栏必须贴在底部导航上方');
        await page.locator(button).scrollIntoViewIfNeeded();
        const hit = await page.locator(button).evaluate(node => {
          const box = node.getBoundingClientRect();
          return node.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
        });
        assert.ok(hit, 'F42：' + width + 'px ' + stage + ' 动作按钮被遮挡');
      }
      await page.waitForFunction(() => S.codingFormReady);
      const coding = await page.evaluate(async () => {
        const footer = document.querySelector('.rf-mobile-coding-footer');
        const view = document.querySelector('#appMain>.view');
        view.scrollTop = view.scrollHeight;
        await new Promise(resolve => requestAnimationFrame(resolve));
        const units = document.querySelectorAll('#cbForms>.wf-coding-unit');
        const last = units[units.length - 1];
        const summary = last?.querySelector('summary');
        const rect = summary?.getBoundingClientRect();
        return {
          barBottom: footer.getBoundingClientRect().bottom,
          barTop: footer.getBoundingClientRect().top,
          navTop: document.querySelector('.rfma-bottom-nav').getBoundingClientRect().top,
          units: units.length,
          scrolled: view.scrollTop,
          lastBottom: last?.getBoundingClientRect().bottom,
          lastHit: rect && summary.contains(document.elementFromPoint(
            rect.x + rect.width / 2, rect.y + rect.height / 2,
          )),
        };
      });
      assert.equal(coding.units, 10, 'F42：必须量测真实渲染的合成编码表');
      assert.ok(coding.scrolled > 0, 'F42：编码页必须实际滚动');
      assert.ok(Math.abs(coding.barBottom - coding.navTop) <= 1, 'F42：滚动后编码保存条仍贴在底栏上方');
      assert.ok(coding.lastHit && coding.lastBottom <= coding.barTop + 1,
        'F42：' + width + 'px 滚到底后最后一个编码字段 ' + coding.lastBottom + ' 被保存条遮挡（top=' + coding.barTop + '）');
      await page.close();
    }
  });

  /* ---------------- F43 ---------------- */
  await check('F43「停止」按钮有可见短标签且命中区仍是 48px', async () => {
    for (const width of WIDTHS) {
      const { page } = await findNav(width);
      await page.locator('#rfmaAppTools').click();
      await page.locator('.page[data-p="screen"]>.rf-mobile-sheet[open] .rfma-source-entry').click();
      await page.locator('.rf-mobile-queue.rfma-source-open .rf-mobile-stop-label').waitFor({ state: 'visible' });
      const stop = await page.evaluate(() => {
        const button = document.querySelector('.rf-stop-summary');
        const label = document.querySelector('.rf-mobile-stop-label');
        if (!button) return null;
        const rect = button.getBoundingClientRect();
        return { width: Math.round(rect.width), height: Math.round(rect.height),
                 labelText: label ? label.textContent : null,
                 labelDisplay: label ? getComputedStyle(label).display : null,
                 labelWidth: label ? Math.round(label.getBoundingClientRect().width) : 0 };
      });
      assert.ok(stop, 'F43：停止按钮必须存在');
      assert.equal(stop.labelText, '停止', 'F43：必须显示两字短标签「停止」');
      assert.notEqual(stop.labelDisplay, 'none', 'F43：短标签必须可见');
      assert.ok(stop.labelWidth > 0, 'F43：短标签必须有实际宽度');
      assert.ok(stop.height >= 48, 'F43：' + width + 'px 按钮高度 ' + stop.height + 'px < 48');
      assert.ok(stop.width >= 48, 'F43：' + width + 'px 按钮宽度 ' + stop.width + 'px < 48');
      await page.locator('.rfma-source-open .rf-stop-summary').click();
      await page.locator('#alStopPanel').waitFor({ state: 'visible' });
      await page.close();
    }
  });

  /* ---------------- F44 ---------------- */
  await check('F44 DOI 在「文献信息」折叠区标题上可见，展开后字段齐全', async () => {
    for (const width of WIDTHS) {
      const { page } = await findNav(width);
      const collapsed = await page.evaluate(() => {
        const summary = document.querySelector('.rf-mobile-article-details>summary');
        const chip = document.querySelector('.scr-doi');
        if (!summary) return null;
        return { summary: summary.textContent.trim(),
                 chipText: chip ? chip.textContent : null,
                 chipDisplay: chip ? getComputedStyle(chip).display : null,
                 summaryHeight: Math.round(summary.getBoundingClientRect().height) };
      });
      assert.ok(collapsed, 'F44：「文献信息」折叠区必须存在');
      assert.ok(collapsed.chipText, 'F44：DOI chip 必须存在于卡片（仅桌面可见也需有值）');
      assert.ok(collapsed.summary.includes('DOI'),
        'F44：' + width + 'px 折叠区标题未含 DOI——DOI 在移动端仍然不可见；实际「' +
        collapsed.summary + '」');
      assert.equal(collapsed.chipDisplay, 'none', 'F44：DOI chip 本体仍按原设计隐藏（改为提级显示）');
      assert.ok(collapsed.summaryHeight >= HIT,
        'F44：折叠区标题命中区 ' + collapsed.summaryHeight + 'px < 44');
      await page.evaluate(() => { document.querySelector('.rf-mobile-article-details').open = true; });
      await page.waitForTimeout(300);
      const expanded = await page.evaluate(() => {
        const panel = document.querySelector('#rfScreenMetadata');
        return panel ? { hidden: panel.hidden,
                         text: [...panel.querySelectorAll('p')].map(row => row.textContent) } : null;
      });
      assert.ok(expanded, 'F44：文献信息面板必须存在');
      assert.equal(expanded.hidden, false, 'F44：展开后面板必须可见');
      for (const field of ['DOI', '文献类型']) {
        assert.ok(expanded.text.some(row => row.includes(field)),
          'F44：展开后必须看到「' + field + '」，实际：' + JSON.stringify(expanded.text));
      }
      await page.close();
    }
  });

  /* ---------------- 对照组：桌面不受影响 ---------------- */
  await check('对照组 1280px 桌面：折叠类/阶段条/短标签/自定义框整行规则都不生效', async () => {
    const { page } = await open(1280);
    await gotoPage(page, 'screen', '#scrBody .scr-title');
    const desktop = await page.evaluate(() => ({
      collapsed: document.querySelector('.scr-decisions')?.classList.contains('rf-reason-collapsed'),
      reasonVisible: document.querySelector('#excReason')?.offsetParent !== null,
      stageTabs: document.querySelectorAll('.rf-mobile-stage-tabs').length,
      /* 布尔化：元素不存在时 `?.offsetParent` 是 undefined，直接比较 null 会假红。 */
      stopLabel: Boolean(document.querySelector('.rf-mobile-stop-label')?.offsetParent),
      customWidth: (() => {
        const input = document.querySelector('#excCustomReason');
        if (!input) return null;
        return Math.round(input.getBoundingClientRect().width);
      })(),
      inlineMax: document.querySelector('#excCustomReason')?.style.maxWidth || null,
    }));
    assert.equal(desktop.collapsed, false, '对照组：桌面不得施加折叠类');
    assert.equal(desktop.reasonVisible, true, '对照组：桌面理由下拉必须常显');
    assert.equal(desktop.stageTabs, 0, '对照组：桌面不得出现移动阶段切换条');
    assert.equal(desktop.stopLabel, false, '对照组：桌面不得出现移动「停止」短标签');
    assert.equal(desktop.inlineMax, '210px', '对照组：桌面内联 max-width 必须保持原值');
    await page.close();
  });

  /* ---------------- 无脚本错误 ---------------- */
  await check('窄视口全流程无页面脚本错误', async () => {
    const { page, errors } = await open(390);
    await gotoPage(page, 'screen', '#scrBody .scr-title');
    await gotoPage(page, 'fulltext');
    await gotoPage(page, 'bench');
    await gotoPage(page, 'trace');
    await page.waitForTimeout(800);
    const relevant = errors.filter(message => !/ResizeObserver loop/i.test(message));
    assert.deepEqual(relevant, [], '窄视口出现页面脚本错误：' + JSON.stringify(relevant));
    await page.close();
  });

  console.log('\n' + checks + ' checks passed');
  await browser.close();
})().catch(error => {
  console.error(error);
  process.exit(1);
});
