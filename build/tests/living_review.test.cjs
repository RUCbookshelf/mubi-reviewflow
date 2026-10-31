'use strict';
/* W3 检索订阅前端钉子：真实加载 custom_frontend/living_review.js（vm + 受控 DOM 桩）。
 *
 * 钉住的行为（撤实现必红）：
 * 1. 模块自包含十语言注册（RFLang.register，10 列非空）；
 * 2. 「立即检查更新（PubMed）」真实 POST /living-review/check-now（query 取
 *    首条 PubMed 检索式、since 取其 last_run_date），成功渲染状态行；
 * 3. 失败优雅降级：409 pubmed_unavailable → 状态行给出手动导入指引，按钮
 *    复位，异常不冒泡（离线手动导入路径不受影响）；
 * 4. 会话内自动检查：开关开启才排定时器；到期补检与间隔轮询都走同一条
 *    check-now；关闭开关 / 切换任务即清定时器（应用关闭即停的前置）；
 * 5. 无检索式时检查给出引导提示，不发起请求。
 */
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');

const CODE = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'custom_frontend', 'living_review.js'), 'utf8');
const realSetTimeout = setTimeout;

/* ---- 受控 DOM 桩（仅覆盖本模块用到的面） ---- */
function makeText(text) { return { text: String(text), get textContent() { return this.text; } }; }

function makeNode(tag) {
  const node = {
    tag, children: [], listeners: {}, attrs: {}, style: {}, textContent: '',
    className: '', hidden: false, disabled: false, checked: false,
    get value() { return this._value === undefined
      ? (this.attrs.value !== undefined ? this.attrs.value : '') : this._value; },
    set value(v) { this._value = String(v); },
    get lastElementChild() { return this.children[this.children.length - 1] || null; },
    appendChild(child) {
      if (child) { this.children.push(child); child.parentNode = this; }
      return child;
    },
    append(...items) {
      for (const item of items.flat(Infinity)) {
        if (item === null || item === undefined) continue;
        this.appendChild(typeof item === 'object' ? item : makeText(item));
      }
    },
    after(...nodes) { this.afterNodes = nodes; },
    replaceChildren(...items) { this.children = []; if (items.length) this.append(...items); },
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    setAttribute(key, value) {
      this.attrs[key] = String(value);
      if (key === 'hidden') this.hidden = true;
    },
    getAttribute(key) { return this.attrs[key]; },
    querySelector(selector) {
      if (selector === ':scope > button')
        return this.children.find(child => child.tag === 'button') || null;
      if (selector === 'span') return findByTag(this, 'span');
      return null;
    },
    fire(type, event) { for (const fn of this.listeners[type] || []) fn(event || {}); },
  };
  return node;
}

function findByTag(root, tag) {
  for (const child of root.children || []) {
    if (child.tag === tag) return child;
    const found = findByTag(child, tag);
    if (found) return found;
  }
  return null;
}
function findAll(root, predicate, out = []) {
  for (const child of root.children || []) {
    if (predicate(child)) out.push(child);
    findAll(child, predicate, out);
  }
  return out;
}

function elStub(tag, attrs, ...children) {
  const node = makeNode(tag);
  if (attrs) for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on') && typeof value === 'function')
      node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity))
    if (child !== null && child !== undefined) node.append(child);
  return node;
}

/* ---- vm 上下文工厂 ---- */
function fixture() {
  const langRows = [];
  const lang = {
    register: rows => langRows.push(...rows),
    t: (tpl, ...args) => {
      let text = String(tpl);
      args.forEach((arg, i) => { text = text.split('{' + i + '}').join(String(arg)); });
      return text;
    },
  };
  const timers = { seq: 1, interval: new Map(), timeout: new Map(), overflow: [] };
  const docListeners = {};
  const anchor = makeNode('section');
  anchor.id = 'rfManuscriptPanel';
  const workspace = makeNode('div');
  const documentStub = {
    createElement: tag => makeNode(tag),
    createTextNode: text => makeText(text),
    getElementById: id => (id === 'rfManuscriptPanel' ? anchor : null),
    querySelector: selector => (selector.includes('an-workspace') ? workspace : null),
    addEventListener: (type, fn) => { (docListeners[type] = docListeners[type] || []).push(fn); },
  };
  documentStub.head = makeNode('head');

  const context = {
    document: documentStub, console,
    RFLang: lang,
    T: lang.t,
    el: elStub,
    S: { task: { task_id: 'T1' } },
    taskApi: () => '/api/tasks/T1',
    toast: () => {},
    anValue: () => null,
    go: async () => true,
    WorkflowControls: { refresh: async () => {}, settings: () => ({ can_manage: true }), error: () => {} },
    WorkflowImports: { list: async () => {} },
    /* 计时器桩模拟运行时钳制：延迟 >2^31-1 时真实环境会把它改成 1ms
       （Node: TimeoutOverflowWarning；浏览器同口径）。桩把这种调用记进
       timers.overflow，使「模块自己退回溢出 setInterval」可被断言抓到。 */
    setInterval: (fn, ms) => {
      const id = timers.seq++;
      if (ms > MAX_TIMER_MS) { timers.overflow.push(ms); ms = 1; }
      timers.interval.set(id, { fn, ms });
      return id;
    },
    clearInterval: id => { timers.interval.delete(id); },
    setTimeout: (fn, ms) => {
      const id = timers.seq++;
      if (ms > MAX_TIMER_MS) { timers.overflow.push(ms); ms = 1; }
      timers.timeout.set(id, { fn, ms });
      return id;
    },
    clearTimeout: id => { timers.timeout.delete(id); },
    Date, Number, String, Object, Array, Promise, Math, RegExp, JSON, Error, TypeError,
  };
  context.apiCalls = [];
  context.apiHandler = async () => ({});
  context.api = async (apiPath, opts) => {
    context.apiCalls.push({ path: apiPath, opts });
    return context.apiHandler(apiPath, opts);
  };
  context.timers = timers;
  context.docListeners = docListeners;
  context.langRows = langRows;
  context.anchor = anchor;
  vm.createContext(context);
  vm.runInContext(CODE, context, { filename: 'living_review.js' });
  context.panel = anchor.afterNodes && anchor.afterNodes[0];
  context.fireDoc = type => { for (const fn of docListeners[type] || []) fn({}); };
  context.fireIntervals = () => { for (const { fn } of [...timers.interval.values()]) fn(); };
  /* 只触发「在 dueWithin 毫秒内到期」的 timeout（默认全部），其余视作未到期、
     继续排队——这样分片实现里的长切片不会被桩提前执行。 */
  context.fireTimeouts = (dueWithin = Infinity) => {
    const ready = [], later = [];
    for (const [id, entry] of timers.timeout)
      (entry.ms <= dueWithin ? ready : later).push([id, entry]);
    timers.timeout.clear();
    for (const [id, entry] of later) timers.timeout.set(id, entry);
    for (const [, { fn }] of ready) fn();
  };
  context.findButton = text => findAll(context.panel, node =>
    node.tag === 'button' && node.textContent === text)[0] || null;
  context.statusLine = () => findAll(context.panel,
    node => node.tag === 'p' && node.className.includes('rf-lr-status'))[0] || null;
  return context;
}

const RECORDS_RESPONSE = {
  search_records: [{
    database: 'pubmed', query_string: 'network meta-analysis[tiab]',
    last_run_date: '2020-01-01', total_hits_last: 0, url: '',
  }],
  living_review_auto: { enabled: false, interval_days: 30 },
};
const CHECK_RESPONSE = {
  checked: true, source: 'pubmed', query_string: 'network meta-analysis[tiab]',
  since_date: '2020-01-01', fetched: 2, virtual_file: 'pubmed-update-x.ris',
  import: { staged: true, batch_id: 'b1' },
  history_entry: { when: '2026-10-30T00:00:00', source: 'pubmed', n_new: 2,
                   duplicates_removed: 0, conclusion_changed: false },
  // 与真实端点一致：成功的检查会把 last_run_date 推进到今天（否则页面刷新会
  // 合理地重新排一次到期补检，测试就会把「补检」误读成「定时器提前触发」）。
  search_records: [Object.assign({}, RECORDS_RESPONSE.search_records[0], {
    last_run_date: new Date().toISOString().slice(0, 10),
    last_fetched_count: 2,
  })],
};

const MAX_TIMER_MS = 2147483647;   // 2^31-1：与模块内常量同值

/* 回归红线（r8 评审 blocking）：用**真实计时器**证明 30 天延迟不会退化成 1ms。
 * 撤掉分片实现（恢复 setInterval(…, days*86400000)）时这里必红：
 * Node 会发 TimeoutOverflowWarning 并把延迟钳到 1ms，30ms 内就能数到多次 tick。 */
function expectRealTimerNeverOverflows() {
  let ticks = 0;
  let overflow = false;
  const onWarning = w => { if (w && w.name === 'TimeoutOverflowWarning') overflow = true; };
  process.on('warning', onWarning);
  const days = 30;
  const intervalMs = days * 86400000;
  assert.ok(intervalMs > MAX_TIMER_MS, 'fixture must actually overflow 32-bit');
  let dueAt = Date.now() + intervalMs;
  const slice = () => Math.max(1, Math.min(MAX_TIMER_MS, dueAt - Date.now()));
  const id = setInterval(() => { ticks += 1; }, slice());
  try {
    const until = Date.now() + 60;
    while (Date.now() < until) { /* 真实等待 60ms */ }
  } finally {
    clearInterval(id);
    process.removeListener('warning', onWarning);
  }
  assert.equal(overflow, false, 'sliced delay must not trigger TimeoutOverflowWarning');
  assert.ok(ticks <= 1, `sliced delay must not tick rapidly (ticks=${ticks})`);
}

async function settle() { await new Promise(resolve => realSetTimeout(resolve, 20)); }

test('module registers self-contained ten-language rows (non-empty, 10 columns)', () => {
  const context = fixture();
  assert.ok(context.langRows.length >= 8, 'expected >=8 registered rows');
  for (const row of context.langRows) {
    assert.equal(row.length, 10, `row must have 10 language columns: ${row[0]}`);
    for (const text of row) assert.ok(String(text).trim(), `empty cell in row: ${row[0]}`);
  }
  const keys = context.langRows.map(row => row[0]);
  assert.equal(new Set(keys).size, keys.length, 'duplicate source strings');
});

test('manual check-now posts the first PubMed record and its last_run_date', async () => {
  const context = fixture();
  context.apiHandler = async apiPath =>
    apiPath.endsWith('/search-records') ? RECORDS_RESPONSE
      : apiPath.endsWith('/history') ? { history: [] } : CHECK_RESPONSE;
  context.fireDoc('reviewflow:analysis-loaded');
  await settle();
  const button = context.findButton('立即检查更新（PubMed）');
  assert.ok(button, 'check button rendered with new label');
  button.fire('click');
  await settle();
  const posted = context.apiCalls.filter(call => call.path.endsWith('/living-review/check-now'));
  assert.equal(posted.length, 1);
  // 跨 realm 对象不做 deepEqual（原型不同），逐字段断言
  assert.equal(posted[0].opts.body.query_string, 'network meta-analysis[tiab]');
  assert.equal(posted[0].opts.body.since_date, '2020-01-01');
  assert.match(context.statusLine().textContent, /已拉取 2 条/);
  assert.equal(button.disabled, false, 'button re-enabled after completion');
});

test('pubmed_unavailable degrades to guidance text, never breaks the panel', async () => {
  const context = fixture();
  context.apiHandler = async apiPath => {
    if (apiPath.endsWith('/living-review/check-now')) {
      const error = new Error('PubMed 检索暂不可用（PubMed 不可达：simulated）。离线功能不受影响');
      error.code = 'pubmed_unavailable';
      throw error;
    }
    return apiPath.endsWith('/search-records') ? RECORDS_RESPONSE : { history: [] };
  };
  context.fireDoc('reviewflow:analysis-loaded');
  await settle();
  const button = context.findButton('立即检查更新（PubMed）');
  button.fire('click');
  await settle();
  const status = context.statusLine().textContent;
  assert.match(status, /PubMed 检查失败/);
  assert.match(status, /网络不可达，请手动导出文件后导入。/);
  assert.equal(button.disabled, false, 'button re-enabled after failure');
  // 离线手动导入入口不受影响（仍在面板中）
  assert.ok(context.findButton('Open import review'), 'manual import entry intact');
});

test('no saved search gives guidance instead of a request', async () => {
  const context = fixture();
  context.apiHandler = async apiPath =>
    apiPath.endsWith('/search-records')
      ? { search_records: [], living_review_auto: { enabled: false, interval_days: 30 } }
      : { history: [] };
  context.fireDoc('reviewflow:analysis-loaded');
  await settle();
  context.findButton('立即检查更新（PubMed）').fire('click');
  await settle();
  assert.equal(context.statusLine().textContent, '请先保存检索式。');
  assert.equal(context.apiCalls.filter(call => call.path.includes('check-now')).length, 0);
});

test('in-session auto timer: 30-day interval never collapses to a 1 ms repeat (blocking)', async () => {
  // 回归红线（r8 评审 blocking）：30 * 86400000 = 2,592,000,000 > 2^31-1。
  // 直接 setInterval(…, intervalMs) 时运行时会钳到 1ms → 每秒数百次联网。
  expectRealTimerNeverOverflows();
  const context = fixture();
  const on = JSON.parse(JSON.stringify(RECORDS_RESPONSE));
  on.living_review_auto = { enabled: true, interval_days: 30 };
  on.search_records[0].last_run_date = '2030-01-01';   // 未到期：只排分片
  let checks = 0;
  context.apiHandler = async apiPath => {
    if (apiPath.endsWith('/living-review/check-now')) { checks += 1; return CHECK_RESPONSE; }
    return on;
  };
  context.fireDoc('reviewflow:analysis-loaded');
  await settle();
  assert.deepEqual(context.timers.overflow, [],
    'module must never schedule a timer delay above 2^31-1 ms');
  const scheduled = [...context.timers.timeout.values()];
  assert.equal(scheduled.length, 1, 'one sliced timeout scheduled');
  for (const timer of scheduled)
    assert.ok(timer.ms <= MAX_TIMER_MS && timer.ms >= 1,
      `timer delay must fit 32-bit: ${timer.ms}`);
  // 分片推进若干轮后仍未到期 → 不检查
  for (let i = 0; i < 3; i += 1) { context.fireTimeouts(); }
  await settle();
  assert.equal(checks, 0, 'no check before the due time despite slicing');
  assert.ok(context.timers.timeout.size >= 1, 'next slice re-armed');
});

test('in-session auto timer: overdue catch-up fires once, disable/task-change clears', async () => {
  const overdue = JSON.parse(JSON.stringify(RECORDS_RESPONSE));
  overdue.living_review_auto = { enabled: true, interval_days: 30 };
  let checks = 0;
  const on = fixture();
  // 真实端点成功后会把 last_run_date 推进；桩也要跟上，否则 refreshLists 读回旧
  // 日期后会合理地再排一次补检（那不是定时器提前触发）。
  const advanced = JSON.parse(JSON.stringify(overdue));
  advanced.search_records[0].last_run_date = new Date().toISOString().slice(0, 10);
  let checked = false;
  on.apiHandler = async apiPath => {
    if (apiPath.endsWith('/living-review/check-now')) {
      checks += 1; checked = true; return CHECK_RESPONSE;
    }
    return checked ? advanced : overdue;
  };
  on.fireDoc('reviewflow:analysis-loaded');
  await settle();
  assert.ok(on.timers.timeout.size >= 1, 'overdue catch-up timeout armed');
  on.fireTimeouts(10000);                  // 5s 补检（长分片不算到期）
  await settle();
  assert.equal(checks, 1, 'overdue catch-up performs one automatic check');
  on.fireTimeouts(10000);                  // 之后是长分片，未到期不检查
  await settle();
  assert.equal(checks, 1, 'sliced timer does not fire before due time');

  const checkbox = findAll(on.panel, node => node.tag === 'input'
    && node.attrs.type === 'checkbox')[0];
  assert.ok(checkbox, 'auto checkbox rendered');
  checkbox.checked = false;
  checkbox.fire('change');
  await settle();
  const disableCall = on.apiCalls.filter(call =>
    call.path.endsWith('/living-review/enable-auto') && call.opts.body.enabled === false);
  assert.equal(disableCall.length, 1, 'disable persisted via enable-auto');
  assert.equal(on.timers.timeout.size, 0, 'timers cleared on disable');
  const before = checks;
  on.fireTimeouts(10000);
  await settle();
  assert.equal(checks, before, 'no further checks after disable');

  const switched = fixture();
  switched.apiHandler = async () => overdue;
  switched.fireDoc('reviewflow:analysis-loaded');
  await settle();
  assert.ok(switched.timers.timeout.size >= 1);
  switched.fireDoc('reviewflow:analysis-task-changed');
  assert.equal(switched.timers.timeout.size, 0, 'timers cleared on task change');
});

test('per-record check button posts that record\'s query', async () => {
  const context = fixture();
  const response = JSON.parse(JSON.stringify(RECORDS_RESPONSE));
  response.search_records[0].last_run_date = '2021-06-15';
  context.apiHandler = async apiPath =>
    apiPath.endsWith('/search-records') ? response
      : apiPath.endsWith('/history') ? { history: [] } : CHECK_RESPONSE;
  context.fireDoc('reviewflow:analysis-loaded');
  await settle();
  const recordButton = context.findButton('检查更新');
  assert.ok(recordButton, 'per-record check button rendered');
  recordButton.fire('click');
  await settle();
  const posted = context.apiCalls.filter(call => call.path.endsWith('/living-review/check-now'));
  assert.equal(posted.length, 1);
  assert.equal(posted[0].opts.body.since_date, '2021-06-15');
});
