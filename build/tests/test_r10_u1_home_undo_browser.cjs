'use strict';
/* r10-u1 · 首页/撤销泳道 —— 真实浏览器行为测试（Playwright · fixture 页加载**真实**
 * custom_frontend/workflow_controls.js，只桩掉网络与主应用状态）。
 *
 * 运行：
 *   REVIEWFLOW_PLAYWRIGHT=E:/rf214-playwright/node_modules/playwright \
 *   PLAYWRIGHT_BROWSERS_PATH=C:/Users/16073/AppData/Local/ms-playwright \
 *   node build/tests/test_r10_u1_home_undo_browser.cjs
 *
 * 覆盖（破坏该条实现即红）：
 *  · F13 零任务时「当前任务」卡渲染空态（不是「Loading scope…」），有任务时回到
 *       当前任务卡，任务列表未定时仍是载入占位（三态互斥）。
 *  · F14 零任务时概览顶部出现一步引导卡，按钮直达「任务与团队」标签。
 *  · F15 设置标签不再与侧栏页面逐字/近似同名。
 *  · F27 半步：只有理由草稿时模态正文列出题名与所选理由、Save 置灰并带 title、
 *       「清空草稿」清空下拉并关闭模态（不留未保存状态）。
 *  · F62/F65 errorText：403 不再把未译英文原串拼在中译句后面（中英混排）；
 *       已注册的后端英文串命中译文。
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.REVIEWFLOW_PLAYWRIGHT || 'playwright');

const root = path.resolve(__dirname, '..', '..');
const frontend = path.join(root, 'custom_frontend');

const FIXTURE = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<style>:root{--bg:#fff;--surface:#fff;--ink:#172b32;--muted:#60757c;--primary:#2f6f6a;--primary-dark:#1f4e4a;--primary-light:#e3f0ee;--border:#d5e3e1;--danger:#ac573e;--font-body:system-ui}
body{margin:0;font:16px system-ui}[hidden]{display:none!important}.row{display:flex;gap:8px;flex-wrap:wrap}.card{border:1px solid var(--border);border-radius:10px;padding:12px}
button{font:inherit}.input{border:1px solid var(--border);border-radius:6px;padding:4px 8px}</style></head>
<body><div id="appMain">
<section class="page on" data-p="home">
  <h3 style="margin:10px 0">任务管理</h3>
  <div class="card"><div class="row" style="margin:0">
    <input class="input" id="newTaskName" placeholder="新任务名称"><input class="input" id="newTaskDesc" placeholder="任务描述">
    <button class="btn b-inc" id="newTaskBtn">＋ 新建任务</button>
    <label class="hint"><input type="checkbox" id="showArchived"> 显示已归档任务</label></div></div>
  <div class="hubgrid" id="hubGrid"></div>
  <section class="card" id="teamPanel" hidden aria-labelledby="teamTitle"><h3 id="teamTitle">团队进度</h3></section>
  <h3>功能一览</h3>
  <div class="steps"><div class="step"><b>导入文献</b></div></div>
  <select class="input" id="taskSel"></select>
  <div id="wf-raw-al">
    <select class="input" id="alStrategy"><option value="max">max</option></select>
    <input class="input" id="alSeed" type="number" value="42">
    <input class="input" id="alStopThreshold" type="number" value="10">
    <input class="input" id="alReorderInterval" type="number" value="20">
    <button class="btn" id="alSaveSettings">保存</button>
  </div>
</section>
<section class="page" data-p="screen">
  <div class="al-mode"><button id="alModeImport" class="on"><span>导入顺序</span></button>
    <button id="alModeAI"><span>机器学习排序</span></button>
    <span class="chip" id="alBadge" style="display:none"></span><span class="hint" id="alHint"></span></div>
  <div class="row"><span class="chip" id="scrPos">—</span><span id="scrHist"></span><span id="scrHistCounts"></span></div>
  <div class="card" id="scrCard"><div id="scrBody"></div></div>
</section>
<section class="page" data-p="fulltext"><div class="row"><span id="ftDocLabel"></span><b id="ftDocTitle"></b><span id="ftDocChip"></span></div><div id="annList"></div></section>
<section class="page" data-p="bench"><div id="cbTitle"></div><div id="cbForms"></div><div id="cbLabel"></div><div id="cbStatus"></div><div id="cbSaveProg"></div><button id="cbSave"></button></section>
</div><div id="toasts"></div></body></html>`;

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1280, height: 900}, locale: 'zh-CN'});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  /* 必须给文档一个真实 http 源：about:blank/setContent 的不可见源访问
     localStorage 会抛 Access is denied，workflow_controls.js 在模块顶层读
     rf_workflow_order，异常会中断后续装配（含 renderCurrentTask）。
     这里用 Playwright 路由直接供页，不起任何服务器、不触达产品数据。 */
  await page.route('http://rf-fixture.local/**', route => route.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: FIXTURE}));
  await page.goto('http://rf-fixture.local/fixture.html', {waitUntil: 'domcontentloaded'});

  /* 主应用契约桩：只桩网络与全局状态，业务实现全部来自产品源文件。 */
  await page.evaluate(() => {
    window.$ = (s, r) => (r || document).querySelector(s);
    window.$$ = (s, r) => Array.from((r || document).querySelectorAll(s));
    /* 产品侧的 T() 以 window.RFLang 为准（index.html:5139）；fixture 提供同签名
       的最小字典桩：命中「注册串→译文」映射即返回译文，否则原样返回。 */
    window.translations = {'Undo is limited to your own workspace.': '撤销仅限你自己的工作区。'};
    window.RFLang = {t: (tpl, ...args) => {
      const text = window.translations[String(tpl)] ?? String(tpl);
      return text.replace(/\{(\d+)\}/g, (m, d) => args[+d] === undefined ? m : String(args[+d]));
    }, register: () => {}, get: () => 'zh-CN', set: () => {}, capture: () => null, render: s => s, source: () => null};
    window.T = (tpl, ...args) => window.RFLang.t(tpl, ...args);
    window.el = (tag, attrs, ...children) => {
      const node = document.createElement(tag);
      if (attrs) for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined) continue;
        if (k === 'class') node.className = v;
        else if (k === 'text') node.textContent = v;
        else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
        else node.setAttribute(k, v);
      }
      for (const c of children.flat(Infinity)) if (c !== null && c !== undefined) node.append(c);
      return node;
    };
    window.toasts = [];
    window.toast = msg => window.toasts.push(String(msg));
    window.go = async () => true;
    window.taskApi = () => '/api/tasks/fixture';
    window.scrQ = () => '';
    window.curFt = () => null;
    window.apiPendingWrites = 0;
    window.api = async () => ({entries: [], can_manage: false, task_kind: 'personal', version: 1, stages: {screen: {mode: 'all'}, fulltext: {mode: 'all'}, coding: {mode: 'all'}}});
    window.S = {user: {username: 'fixture', user_id: 1}, task: {task_id: 't1', name: 'Fixture task'}, tasks: [{task_id: 't1', name: 'Fixture task'}], screener: 'fixture', page: 'home', articles: [], decisions: {}, scrIdx: 0};
  });
  await page.addScriptTag({path: path.join(frontend, 'workflow_controls.js')});
  await page.waitForFunction(() => window.WorkflowControls?.renderCurrentTask);

  const check = async (name, fn) => { await fn(); console.log('PASS', name); };

  /* ---------------- F13 / F14 ---------------- */
  await check('F13 零任务渲染空态而不是「Loading scope…」', async () => {
    const text = await page.evaluate(() => {
      S.tasks = []; S.task = null;
      WorkflowControls.renderCurrentTask();
      return document.getElementById('wf-current-task').textContent;
    });
    assert.match(text, /No task yet/, '零任务必须显示空态标题');
    assert.match(text, /Create a task/, '空态必须给下一步动作');
    assert.doesNotMatch(text, /Loading scope/, '零任务不得停留在载入占位');
  });
  await check('F14 零任务时一步引导卡可见，按钮切到「任务与团队」并聚焦新建输入框', async () => {
    assert.equal(await page.evaluate(() => document.getElementById('wf-first-step').hidden), false);
    await page.locator('#wf-first-step button').click();
    await page.waitForTimeout(50);
    const state = await page.evaluate(() => ({
      tab: document.getElementById('wf-tab-tasks').getAttribute('aria-selected'),
      panelHidden: document.getElementById('wf-panel-tasks').hidden,
      focused: document.activeElement?.id}));
    assert.equal(state.tab, 'true', '引导卡必须切到任务与团队标签');
    assert.equal(state.panelHidden, false);
    assert.equal(state.focused, 'newTaskName', '必须把焦点放到新建任务输入框');
  });
  await check('F13 有任务时回到当前任务卡，引导卡隐藏（对照）', async () => {
    const state = await page.evaluate(() => {
      S.tasks = [{task_id: 't1', name: 'Fixture task'}]; S.task = {task_id: 't1', name: 'Fixture task'};
      WorkflowControls.renderCurrentTask();
      return {text: document.getElementById('wf-current-task').textContent, first: document.getElementById('wf-first-step').hidden};
    });
    assert.match(state.text, /Fixture task/);
    assert.doesNotMatch(state.text, /No task yet/);
    assert.equal(state.first, true, '有任务时引导卡必须隐藏');
  });
  await check('F13 任务列表未定（有任务但上下文未定）仍是载入占位', async () => {
    const text = await page.evaluate(() => {
      S.tasks = [{task_id: 't1', name: 'Fixture task'}]; S.task = null;
      WorkflowControls.renderCurrentTask();
      return document.getElementById('wf-current-task').textContent;
    });
    assert.match(text, /Loading scope/, '未定上下文时保留载入占位，不误判为空态');
  });

  /* ---------------- F15 ---------------- */
  await check('F15 设置标签与侧栏页面不再同名', async () => {
    const labels = await page.evaluate(() => [...document.querySelectorAll('.wf-tabs [role=tab]')].map(t => t.textContent));
    assert.deepEqual(labels, ['Overview', 'Tasks & team', 'Screening policy settings', 'Machine Learning', 'Coding evidence requirements', 'Appearance & storage']);
    assert.ok(!labels.includes('Screening settings'), 'F15：不得与侧栏「文献初筛」近似同名');
    assert.ok(!labels.includes('数据提取与编码'), 'F15：不得与编码页逐字同名');
  });

  /* ---------------- F27 ---------------- */
  await check('F27 只有理由草稿：模态列出草稿、Save 置灰并说明、清空草稿即关闭', async () => {
    await page.evaluate(() => {
      S.page = 'screen'; S.screenEditingKey = null;
      S.articles = [{zotero_key: 'k1', title: 'Draft Article Title'}]; S.scrIdx = 0;
      const body = document.getElementById('scrBody');
      body.textContent = '';
      const reason = el('select', {class: 'input', id: 'excReason'},
        el('option', {value: '', text: '—'}),
        el('option', {value: '人群不符', text: '人群不符'}),
        el('option', {value: '__custom__', text: '其他（自定义）'}));
      const custom = el('input', {class: 'input', id: 'excCustomReason', hidden: ''});
      body.append(el('div', {class: 'row scr-decisions'}, reason, custom));
      WorkflowControls.markDecisionClean('screen');
      reason.value = '人群不符';
      reason.dispatchEvent(new Event('change', {bubbles: true}));
    });
    assert.equal(await page.evaluate(() => WorkflowControls.decisionDirty()), true, '选理由后应判为 dirty');
    await page.evaluate(() => { window.guardResult = WorkflowControls.guard(); }).catch(() => {});
    await page.waitForSelector('.wf-dirty-dialog[open]');
    const dialog = page.locator('.wf-dirty-dialog');
    const text = await dialog.innerText();
    assert.match(text, /Unsaved screening reason/, 'F27：模态必须列出理由草稿区');
    assert.match(text, /Draft Article Title/, 'F27：草稿必须显示文献题名');
    assert.match(text, /人群不符/, 'F27：草稿必须显示所选理由');
    const save = dialog.getByRole('button', {name: 'Save', exact: true});
    assert.equal(await save.isDisabled(), true, 'F27：无物可存时 Save 必须置灰');
    assert.match(await save.getAttribute('title'), /决策/, 'F27：置灰必须以 title 说明做法');
    await dialog.locator('[data-wf-clear-reason-draft]').click();
    await page.waitForTimeout(60);
    assert.equal(await page.locator('.wf-dirty-dialog').count(), 0, 'F27：清空草稿后模态关闭');
    const after = await page.evaluate(() => ({value: document.getElementById('excReason').value, dirty: WorkflowControls.decisionDirty()}));
    assert.equal(after.value, '', 'F27：清空草稿必须把理由下拉清空');
    assert.equal(after.dirty, false, 'F27：清空后必须回到干净状态（不再拦导航）');
  });

  /* ---------------- F62 / F65 ---------------- */
  await check('F65 403 不再拼接未译英文原串（消除中英混排）', async () => {
    const text = await page.evaluate(() => WorkflowControls.errorText({status: 403, message: 'Only the task owner can manage collaborators.'}));
    assert.equal(text, 'You do not have permission for this action.', 'F65：未注册的 403 细节不得拼接：' + text);
  });
  await check('F65 非 403 的技术细节仍保留（对照：不吞掉有用的错误信息）', async () => {
    const text = await page.evaluate(() => WorkflowControls.errorText({status: 500, message: 'Disk quota exceeded'}));
    assert.match(text, /Disk quota exceeded/, '非 403 的未译细节应保留，便于排查');
  });
  await check('F62 已注册的后端英文串经 T() 命中译文（双键）', async () => {
    const text = await page.evaluate(() => WorkflowControls.errorText({status: 400, message: 'Undo is limited to your own workspace.'}));
    assert.match(text, /撤销仅限你自己的工作区。/, '已注册串必须命中译文');
  });

  assert.deepEqual(errors, [], '页面不得有未捕获异常');
  console.log(JSON.stringify({checks: 11, errors}));
  await browser.close();
})().catch(error => {
  console.error(String(error.stack || error));
  process.exit(1);
});
