'use strict';
// r10-u6 真浏览器钉子：F04 轨迹日界、F07 PRISMA 自动填充反馈与缺失项面板、
// F63 复筛轮次 409 契约与刷新、F66 证据工作区 409→自动刷新→重试成功。
//
// 前置（隔离实例，端口/数据目录独立于其它泳道）：
//   python build/tests/report_evidence_instance.py --port 18778 --data-dir E:/rf214tmp/r10/u6/data
//   python build/tests/report_evidence_prep.py --url http://127.0.0.1:18778 \
//          --data-dir E:/rf214tmp/r10/u6/data --session-out E:/rf214tmp/r10/u6/session.json
// 运行：
//   REVIEWFLOW_PLAYWRIGHT=E:/rf214-playwright/node_modules/playwright \
//   PLAYWRIGHT_BROWSERS_PATH=C:/Users/16073/AppData/Local/ms-playwright \
//   REVIEWFLOW_U6_URL=http://127.0.0.1:18778 \
//   REVIEWFLOW_U6_SESSION=E:/rf214tmp/r10/u6/session.json \
//   node build/tests/test_r10_u6_browser.cjs      （exit 0 = 全部通过）
//
// 每条都断言**用户可见结果**；回退修复（例如把 since 改回 UTC 裸拼、去掉
// 缺失项面板前置、让 409 不自动刷新）都会使对应 check 抛错而非静默通过。
const fs = require('node:fs');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.REVIEWFLOW_PLAYWRIGHT || 'playwright');
const url = process.env.REVIEWFLOW_U6_URL || 'http://127.0.0.1:18778';
const sessionPath = process.env.REVIEWFLOW_U6_SESSION || 'E:/rf214tmp/r10/u6/session.json';
const session = JSON.parse(fs.readFileSync(sessionPath, 'utf8'));
const artifacts = process.env.REVIEWFLOW_U6_ARTIFACTS || 'E:/rf214tmp/r10/u6/artifacts';
fs.mkdirSync(artifacts, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));

  // 记录轨迹明细请求的查询串（F04 断言用）。
  const traceQueries = [];
  page.on('request', request => {
    const parsed = new URL(request.url());
    if (parsed.pathname.endsWith('/trace/events')) traceQueries.push(parsed.searchParams);
  });

  await page.addInitScript(token => sessionStorage.setItem('rf_token', token), session.token);
  await page.goto(url + '/#workspace/home');
  await page.waitForFunction(id => S.user && S.task && S.task.task_id === id, session.task, { timeout: 20000 });

  let checks = 0;
  const check = async (name, fn) => {
    try { await fn(); checks += 1; console.log('PASS', name); }
    catch (error) {
      await page.screenshot({ path: artifacts + '/fail-' + name.replace(/[^\w\u4e00-\u9fa5]+/g, '_').slice(0, 40) + '.png', fullPage: true });
      throw error;
    }
  };
  const toastHas = text => page.waitForFunction(
    s => [...document.querySelectorAll('.toast')].some(node => node.textContent.includes(s)), text, { timeout: 9000 });

  // ---- F04：轨迹明细的 since/until 必须是浏览器本地时区的日界 ----
  await check('F04 轨迹默认筛选=全部阶段+最近7天，日界按本地时区序列化', async () => {
    await page.evaluate(() => go('trace'));
    await page.waitForSelector('#researchTracePage .trace-tabs', { timeout: 15000 });
    await page.waitForFunction(() => document.querySelectorAll('#researchTracePage [role=tab]').length === 3, null, { timeout: 15000 });
    // 切到「记录明细」（默认 tab 是概览，不会自动请求明细以外的查询；直接点 tab）
    await page.evaluate(() => {
      const tab = [...document.querySelectorAll('#researchTracePage [role=tab]')].find(node => node.textContent.trim() === '记录明细');
      tab && tab.click();
    });
    await page.waitForFunction(() => document.querySelectorAll('#researchTracePage .trace-filters').length === 1, null, { timeout: 15000 });
    assert.ok(traceQueries.length > 0, '轨迹明细未发起任何 /trace/events 请求');
    const query = traceQueries[traceQueries.length - 1];
    const since = query.get('since');
    const until = query.get('until');
    assert.ok(since && until, '明细请求缺少 since/until：' + query.toString());
    // 期望值：浏览器本地「今天-6 天 00:00」与「今天 23:59:59.999」
    const expected = await page.evaluate(() => {
      const now = new Date();
      const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 6, 0, 0, 0, 0);
      const end = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59, 999);
      return { since: start.toISOString(), until: end.toISOString() };
    });
    assert.equal(since, expected.since, 'since 不是本地时区日界（UTC 裸拼回归？）：' + since);
    assert.equal(until, expected.until, 'until 不是本地当日末毫秒：' + until);
    assert.equal(query.get('stage'), null, '默认筛选必须包含全部阶段');
    // 输入框默认值必须是最近 7 天（含今天）的本地日期
    const dates = await page.evaluate(() => {
      const inputs = [...document.querySelectorAll('#researchTracePage .trace-filters input[type=date]')];
      return inputs.map(input => input.value);
    });
    assert.equal(dates.length, 2, '明细筛选应有两个日期输入');
    const localDate = iso => {
      const d = new Date(iso);
      return [d.getFullYear(), String(d.getMonth() + 1).padStart(2, '0'), String(d.getDate()).padStart(2, '0')].join('-');
    };
    assert.equal(dates[0], localDate(expected.since), '开始日期默认值不是最近 7 天起点');
    assert.equal(dates[1], localDate(expected.until), '结束日期默认值不是本地今天');
  });

  // ---- F07：PRISMA 自动填充反馈 + 缺失项清单面板 ----
  await check('F07 自动填充给出可核对反馈，校验前一次性列出缺失项', async () => {
    await page.evaluate(() => go('prisma'));
    await page.waitForFunction(() => {
      const section = document.querySelector('.page[data-p="prisma"]');
      return section && !section.hidden && !section.inert;
    }, null, { timeout: 15000 });
    await page.evaluate(() => { document.querySelectorAll('.toast').forEach(node => node.remove()); });
    await page.click('#prAutoFill');
    await toastHas('没有可填充的字段：请先在上游填写识别来源与数量。');
    // 生成时先出清单：识别来源 + 纳入研究数（≥2 项）
    await page.click('#prGenerate');
    await page.waitForFunction(() => {
      const panel = document.getElementById('prMissing');
      return panel && !panel.hidden && panel.querySelectorAll('li').length >= 2;
    }, null, { timeout: 8000 });
    const listed = await page.evaluate(() => [...document.querySelectorAll('#prMissing li')].map(node => node.textContent));
    assert.ok(listed.some(text => text.includes('识别来源')), '缺失项清单未列出识别来源：' + JSON.stringify(listed));
    assert.ok(listed.some(text => text.includes('纳入研究数')), '缺失项清单未列出纳入研究数：' + JSON.stringify(listed));
    assert.match(await page.locator('#prMissing').textContent(), /请先补齐以下内容（\d+ 项）/);
    // 补齐后清单消失；自动填充开始真正写入并给出计数
    await page.evaluate(() => {
      document.getElementById('prRowsDb').append(prIdentifiedRow('db', ['database', 'register']));
      const row = document.getElementById('prRowsDb').lastElementChild;
      row.querySelectorAll('input')[0].value = 'WOS';
      row.querySelectorAll('input')[1].value = '100';
      const included = document.getElementById('prIncluded');
      included.value = '6';
      included.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await page.click('#prAutoFill');
    await toastHas('已填充');
    assert.equal(await page.locator('#prMissing').isVisible(), false, '补齐后缺失项面板必须消失');
    const filled = await page.evaluate(() => ({
      screened: document.getElementById('prScreened').value,
      highlighted: document.querySelectorAll('.page[data-p="prisma"] input.pr-filled').length,
    }));
    assert.equal(filled.screened, '100', '自动填充未按勾稽规则写入进入初筛记录数');
    assert.ok(filled.highlighted > 0, '自动填充未高亮被改写的字段');
  });

  // ---- F63：screening-rounds 版本冲突 → 结构化 code → 前端刷新到最新版本 ----
  await check('F63 轮次 409 回 workflow_version_conflict 且前端刷新到最新版本', async () => {
    const result = await page.evaluate(async () => {
      const base = taskApi();
      const live = await api(base + '/workflow-settings');
      let caught = null;
      try {
        // 故意用过期版本触发冲突
        await api(base + '/screening-rounds', { method: 'POST', body: { action: 'start', expected_policy_version: Math.max(1, live.version + 1) } });
      } catch (error) {
        caught = { status: error.status, code: error.code, version: error.workflowSettings?.version, message: error.message };
      }
      await WorkflowControls.refresh('conflict').catch(() => {});
      return { liveVersion: live.version, caught, controllerVersion: WorkflowControls.version() };
    });
    assert.equal(result.caught?.status, 409, '过期版本必须回 409');
    assert.equal(result.caught?.code, 'workflow_version_conflict',
      '轮次 409 未带 workflow_version_conflict code（前端无法自动刷新）：' + JSON.stringify(result.caught));
    assert.equal(result.caught?.version, result.liveVersion, '409 载荷必须带最新版本');
    assert.equal(result.controllerVersion, result.liveVersion, '刷新后 WorkflowControls.version() 必须是新值');
    assert.doesNotMatch(result.caught?.message || '', /Workflow policy changed\. Refresh and retry\.$/,
      '仍回旧英文串（未本地化/未收编）');
  });

  // ---- F66：证据工作区 409 → revision 自动刷新 → 原对话框重试成功 ----
  await check('F66 409 后自动刷新 revision，重试保存成功', async () => {
    const base = url + '/api/tasks/' + encodeURIComponent(session.task) + '/report-evidence';
    const headers = { Authorization: 'Bearer ' + session.token, 'Content-Type': 'application/json' };
    const current = await (await fetch(base, { headers })).json();
    const bumped = await fetch(base, {
      method: 'PUT',
      headers: { ...headers, 'X-Request-ID': crypto.randomUUID() },
      body: JSON.stringify({
        expected_revision: current.revision, operation_id: crypto.randomUUID(),
        state: current.state, label: '另一窗口保存', stage: 'coding',
      }),
    });
    assert.equal(bumped.status, 200, '外部窗口推进 revision 必须成功');

    await page.evaluate(() => go('screen'));
    await page.waitForSelector('#mp-screen-link', { state: 'attached', timeout: 20000 });
    await page.waitForFunction(() => {
      const button = document.getElementById('mp-screen-link');
      return button && !button.hidden;
    }, null, { timeout: 20000 });
    await page.click('#mp-screen-link');
    const dialog = page.locator('dialog.mp-dialog[open]');
    await dialog.waitFor();
    const evidence = dialog.locator('.mp-field:has(span:text-is("依据／原文片段（可稍后补充）")) textarea');
    await evidence.fill('F66 冲突重试草稿');
    await dialog.getByRole('button', { name: '保存我的线索' }).click();
    await dialog.locator('.mp-error').waitFor({ timeout: 15000 });
    assert.match(await dialog.locator('.mp-error').textContent(), /已自动刷新为最新内容/,
      '409 后必须呈现“已自动刷新为最新内容”提示');
    assert.equal(await dialog.count(), 1, '冲突后对话框必须保留现场供重试');
    // 重试：此时 revision 已随自动刷新更新，应直接成功
    await dialog.getByRole('button', { name: '保存我的线索' }).click();
    await toastHas('个人线索已保存');
    await dialog.waitFor({ state: 'detached' });
    // 落库校验：重新打开关系管理应能看到该草稿内容
    await page.click('#mp-screen-link');
    await dialog.waitFor();
    await dialog.getByRole('button', { name: '查看已有关系' }).click().catch(() => {});
    await page.waitForFunction(() => document.body.textContent.includes('F66 冲突重试草稿'), null, { timeout: 15000 });
  });

  assert.deepEqual(errors, [], '页面不得抛出未捕获脚本错误');
  console.log(JSON.stringify({ checks, errors, url, task: session.task }));
  await browser.close();
})().catch(error => {
  console.error(String(error && error.stack || error).replace(/Bearer [^\s'"]+/g, 'Bearer [redacted]'));
  process.exit(1);
});
