'use strict';
/* r10-u1 · F24「存为常用理由」移出决策行 —— 真实浏览器行为测试
 * （Playwright · fixture 页加载**真实** custom_frontend/workflow_controls.js，
 *   只桩掉网络与主应用状态；settings.can_manage=true 走真实 can_manage 分支）。
 *
 * 运行：
 *   REVIEWFLOW_PLAYWRIGHT=E:/rf214-playwright/node_modules/playwright \
 *   PLAYWRIGHT_BROWSERS_PATH=C:/Users/16073/AppData/Local/ms-playwright \
 *   node build/tests/test_r10_u1_reason_save_browser.cjs
 *
 * 批准单 F24：「开启理由库后该按钮与自定义输入框挤进决策行（10px 间距 flex），
 * 误点即静默 POST 写库，无确认无撤销」→ 方案「按钮移出决策行：收进理由下拉末尾
 * 作为特殊选项『＋ 将此理由存为常用』；或点击后弹含『撤销』动作的 toast」。
 *
 * 每条检查破坏对应实现即红：
 *  · 决策行里只有三个决策按钮（把按钮塞回 select.parentElement 即红）；
 *  · 动作是下拉末尾的特殊项，取值 __save_reason__（不冒充理由取值）；
 *  · 选中动作项**立即还原**原取值，不改变本次决策理由（动作被当理由提交即红）；
 *  · 选中动作项会真的 POST 一次 reason-library（不写库即红——动作成了空操作）；
 *  · 写入成功的反馈带「撤销」按钮，点它发 DELETE 撤回同一条目（无撤销即红）；
 *  · 自定义输入框仍住在 .wf-custom-reason-row（移动端整行布局契约，对照组）。
 */
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require(process.env.REVIEWFLOW_PLAYWRIGHT || 'playwright');

const frontend = path.resolve(__dirname, '..', '..', 'custom_frontend');

/* workflow_controls.js 在加载期与 refreshPolicy 里读写这些宿主节点，fixture 必须
   全部在位（缺一个就会在该处以 null.textContent 抛错，测试变红而非假绿）。 */
const FIXTURE = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<style>:root{--bg:#fff;--surface:#fff;--ink:#172b32;--muted:#60757c;--primary:#2f6f6a;--primary-dark:#1f4e4a;--primary-light:#e3f0ee;--border:#d5e3e1;--danger:#ac573e;--font-body:system-ui}
body{margin:0;font:16px system-ui}[hidden]{display:none!important}
.row{display:flex;gap:8px;flex-wrap:wrap}.card{border:1px solid var(--border);border-radius:10px;padding:12px}
button{font:inherit}.input{border:1px solid var(--border);border-radius:6px;padding:4px 8px}
.toast{padding:8px 12px}</style></head>
<body><div id="appMain">
<section class="page on" data-p="home">
  <div class="hubgrid" id="hubGrid"></div>
  <div id="wf-reasons"></div>
  <div id="teamTitle"></div><div id="wf-team-title"></div><div id="wf-policy"></div>
  <div id="wf-requirements"></div><div id="wf-sources"></div><div id="wf-team-decisions"></div>
  <div id="wf-personal-maybe"></div>
  <div id="wf-settings-notice"></div><div id="wf-policy-recovery" hidden></div>
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
    <button id="alModeAI"><span>机器学习排序</span></button><button id="alModeRandom"><span>随机</span></button>
    <span class="chip" id="alBadge" style="display:none"></span><span class="hint" id="alHint"></span></div>
  <div class="row"><span class="chip" id="scrPos">—</span><span id="scrHist"></span><span id="scrHistCounts"></span></div>
  <div class="card" id="scrCard"><div id="scrBody">
    <div class="row scr-decisions">
      <button class="btn b-inc" id="btnInc">1 纳入</button>
      <button class="btn b-may" id="btnMay">2 待定</button>
      <button class="btn b-exc" id="btnExc">3 排除</button>
      <select class="input" id="excReason"><option value="">—</option><option value="人群不符">人群不符</option><option value="__custom__">其他（自定义）</option></select>
      <input class="input" id="excCustomReason" hidden>
    </div>
  </div></div>
</section>
<section class="page" data-p="fulltext"><div class="row">
  <button id="ftPrevDoc">‹</button>
  <select id="ftReason"><option value="">—</option></select><input id="ftCustomReason" hidden></div></section>
<section class="page" data-p="bench"><button id="cbSave"></button></section>
</div><div id="toasts"></div></body></html>`;

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1280, height: 900}});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));

  /* 真实 http 源（不透明源下 localStorage 会抛 Access is denied，模块顶层就读它）。 */
  await page.route('http://rf-f24.local/**', route =>
    route.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: FIXTURE}));
  await page.goto('http://rf-f24.local/fixture.html', {waitUntil: 'domcontentloaded'});

  await page.evaluate(() => {
    window.$ = (s, r) => (r || document).querySelector(s);
    window.$$ = (s, r) => Array.from((r || document).querySelectorAll(s));
    window.T = (tpl, ...args) => String(tpl)
      .replace(/\{(\d+)\}/g, (m, d) => args[+d] === undefined ? m : String(args[+d]));
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
    window.toast = m => window.toasts.push(String(m));
    window.go = async () => true;
    window.taskApi = () => '/api/tasks/t1';
    window.scrQ = () => '';
    window.curFt = () => null;
    window.apiPendingWrites = 0;
    window.calls = [];
    window.api = async (path, opts) => {
      window.calls.push({path, method: opts?.method || 'GET', body: opts?.body || null});
      if (path.startsWith('/api/tasks/t1/reason-library')) {
        if (opts?.method === 'POST') return {id: 'r-new-1', text: opts.body.text};
        if (opts?.method === 'DELETE') return {deleted: true};
        return {entries: [{id: 'e1', text: '人群不符'}], can_manage: true, task_kind: 'personal'};
      }
      return {can_manage: true, task_kind: 'personal', version: 1,
        stages: {
          screen: {mode: 'all', source_ids: [], show_maybe: true},
          fulltext: {mode: 'all', source_ids: [], show_maybe: true},
          coding: {mode: 'all', source_ids: []},
        },
        sources: [], evidence_fields: [], evidence_required_field_ids: [],
        mapping_issues: [], submission_evidence: {available: false}};
    };
    window.S = {
      user: {username: 'fixture', user_id: 1}, task: {task_id: 't1', name: 'Fixture task'},
      tasks: [{task_id: 't1', name: 'Fixture task'}], screener: 'fixture', page: 'screen',
      articles: [{zotero_key: 'k1', title: 'A'}], decisions: {}, scrIdx: 0,
    };
  });
  await page.addScriptTag({path: path.join(frontend, 'workflow_controls.js')});
  await page.waitForFunction(() => window.WorkflowControls?.decorateReasons);
  await page.evaluate(() => window.WorkflowControls.refresh('entry', false));
  await page.waitForFunction(() => window.WorkflowControls.settings()?.can_manage === true, {timeout: 5000});
  await page.evaluate(() => window.WorkflowControls.decorateReasons('screen'));
  await page.waitForTimeout(80);

  const check = async (name, fn) => { await fn(); console.log('PASS', name); };

  await check('F24 决策行只有三个决策按钮，动作项不在行内', async () => {
    const row = await page.evaluate(() => {
      const decisions = document.querySelector('.scr-decisions');
      /* 动作项现在是 <option>（住在 select 里），而 select 本来就在决策行——
         所以只能查「行内除 option 以外」的元素，否则会把下拉项误判成行内按钮。 */
      const stray = [...decisions.querySelectorAll('[data-wf-save-reason], .wf-reason-save-icon')]
        .filter(el => el.tagName !== 'OPTION');
      return {
        buttons: [...decisions.querySelectorAll('button')].map(b => b.textContent),
        strayMarkup: stray.length > 0,
        strayTag: stray[0]?.tagName || null,
        legacyLabel: [...decisions.querySelectorAll('button')].some(b => /Save as reusable reason/.test(b.textContent)),
      };
    });
    assert.deepEqual(row.buttons, ['1 纳入', '2 待定', '3 排除'], 'F24：决策行只应有三个决策按钮');
    assert.equal(row.strayMarkup, false,
      'F24：动作项不得再以按钮形式挂进决策行（误点即静默写库），实为 ' + row.strayTag);
    assert.equal(row.legacyLabel, false, 'F24：旧按钮文案应随按钮一并移除');
  });

  await check('F24 动作是下拉末尾的特殊项，有自己的取值', async () => {
    const option = await page.evaluate(() => {
      const select = document.getElementById('excReason');
      const node = select.querySelector('[data-wf-save-reason]');
      return {
        text: node?.textContent || '', value: node?.value || '',
        last: select.options[select.options.length - 1] === node,
        duplicates: select.querySelectorAll('[data-wf-save-reason]').length,
      };
    });
    assert.match(option.text, /将此理由存为常用/, 'F24：动作项文案必须说明后果');
    assert.equal(option.value, '__save_reason__', 'F24：动作项必须用独立取值，不能冒充理由');
    assert.equal(option.last, true, 'F24：动作项固定在下拉末尾');
    assert.equal(option.duplicates, 1, 'F24：重复 decorate 不得叠加动作项');
  });

  await check('F24 选中动作项立即还原原取值（不改变本次决策理由）', async () => {
    const state = await page.evaluate(async () => {
      const select = document.getElementById('excReason');
      window.calls.length = 0;
      select.value = '人群不符';
      select.dispatchEvent(new Event('change', {bubbles: true}));
      const afterPick = select.value;
      select.value = '__save_reason__';
      select.dispatchEvent(new Event('change', {bubbles: true}));
      await new Promise(r => setTimeout(r, 120));
      return {afterPick, restored: select.value, calls: window.calls.slice()};
    });
    assert.equal(state.afterPick, '人群不符');
    assert.equal(state.restored, '人群不符', 'F24：动作值绝不能被当成排除理由留在下拉里');
  });

  await check('F24 选中动作项真的写库一次（reason-library POST）', async () => {
    const posts = await page.evaluate(() => window.calls.filter(c => c.method === 'POST'));
    assert.equal(posts.length, 1, 'F24：选中动作项必须且只 POST 一次');
    assert.match(posts[0].path, /\/reason-library\?stage=screen$/);
    assert.deepEqual(posts[0].body, {text: '人群不符', stage: 'screen'});
  });

  await check('F24 写入反馈带「撤销」，点它 DELETE 撤回同一条目', async () => {
    const box = page.locator('.wf-undo-toast');
    await box.waitFor({state: 'visible', timeout: 3000});
    assert.match(await box.innerText(), /已存为常用理由：人群不符/, 'F24：反馈必须说清存了什么');
    await box.getByRole('button', {name: '撤销'}).click();
    await page.waitForTimeout(160);
    const del = await page.evaluate(() => window.calls.filter(c => c.method === 'DELETE'));
    assert.equal(del.length, 1, 'F24：撤销必须真的发 DELETE（不能只是关掉 toast）');
    assert.match(del[0].path, /\/reason-library\/r-new-1\?stage=screen$/, 'F24：撤销必须删掉刚写入的那一条');
  });

  await check('F24 自定义理由框仍在 .wf-custom-reason-row（对照组：布局契约不变）', async () => {
    const state = await page.evaluate(() => {
      const custom = document.getElementById('excCustomReason');
      const row = custom.parentElement;
      return {rowClass: row.className, rowChildren: row.children.length,
        inDecisionRow: row.classList.contains('scr-decisions')};
    });
    assert.match(state.rowClass, /wf-custom-reason-row/, 'F24：自定义输入框必须留在专属行（移动端整行布局依赖它）');
    assert.equal(state.rowChildren, 1, 'F24：该行内不得再挂第二个元素（按钮已移出）');
    assert.equal(state.inDecisionRow, false);
  });

  assert.deepEqual(errors.filter(e => !/RFLang is not defined/.test(e)), [],
    '页面不得有未捕获异常（RFLang 未注入属 fixture 桩缺失，与产品代码无关）');
  console.log(JSON.stringify({checks: 6, errors}));
  await browser.close();
})().catch(error => {
  console.error(String(error.stack || error));
  process.exit(1);
});
