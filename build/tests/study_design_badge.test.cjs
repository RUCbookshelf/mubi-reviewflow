'use strict';
/* W4 study_design_badge.js 行为钉子——node 真实加载 + 受控桩（ml_batch.test.cjs 范式）。
 *
 * 覆盖（破坏即红）：
 *  - 模块真实加载：IIFE 在受控桩环境运行并挂载 window.StudyDesignBadge；
 *  - 十语言词条：RFLang.register 每行恰 10 列且全非空；
 *  - 徽标生命周期：无数据占位「…」→ 数据到达 paint 补全（textContent/className）；
 *    键缺失/接口失败/available:false → 节点移除（优雅降级，无未处理拒绝）；
 *  - 每作用域一次拉取（去重），失败后同作用域不再重试（无重试风暴）；
 *  - 预填不代存：判非 RCT → 仅设置 #excReason 值「研究设计不符」+ 派发 change
 *    + title 提示；RCT/未识别/已有值 → 不动；用户改过 → 不再打扰；
 *    数据晚到补填后重置 WorkflowControls 决策基线（markDecisionClean）。
 *  - 语言切换事件重绘徽标。
 */
const test = require('node:test'), assert = require('node:assert/strict'),
      fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', '..', 'custom_frontend', 'study_design_badge.js'), 'utf8');

/* ---- 受控桩：迷你 DOM + 全局助手（与 index.html 的 T/el/api 语义对齐） ---- */
function makeContext(overrides = {}) {
  const created = [];                       // 全部创建过的元素（querySelectorAll 数据源）
  class FakeEvent { constructor(type, opts) { this.type = type; this.bubbles = !!(opts && opts.bubbles); } }
  function makeNode(tag) {
    const node = {
      tagName: tag, textContent: '', className: '', attrs: {}, children: [],
      parentNode: null, _listeners: {},
      setAttribute(k, v) { this.attrs[k] = String(v); },
      getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
      removeAttribute(k) { delete this.attrs[k]; },
      append(...nodes) { for (const n of nodes) { if (n && typeof n === 'object') { n.parentNode = this; this.children.push(n); } } },
      appendChild(n) { this.append(n); return n; },
      removeChild(n) { const i = this.children.indexOf(n); if (i >= 0) this.children.splice(i, 1); n.parentNode = null; },
      addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); },
      dispatchEvent(ev) {
        for (const fn of this._listeners[ev.type] || []) fn(ev);
        if (ev.bubbles) document._emit(ev.type, this);   // 冒泡到 document（与浏览器一致）
        return true;
      },
    };
    Object.defineProperty(node, 'isConnected', { get() { return !!node.parentNode; } });
    created.push(node);
    return node;
  }
  const listeners = {};
  const document = {
    createElement: tag => makeNode(tag),
    head: makeNode('head'),
    _emit(type, target) { for (const fn of listeners[type] || []) fn({ type, target }); },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    querySelectorAll(sel) {
      if (sel !== '[data-rf-sd-badge]') throw Error('stub only supports [data-rf-sd-badge]');
      return created.filter(n => n.getAttribute('data-rf-sd-badge') !== null);
    },
    getElementById(id) { return created.find(n => n.attrs.id === id) || null; },
  };

  let lang = 0;
  const dict = { };                         // zh -> [zh, en, ...]
  // 模块复用全局词典的既有设计类别词条（analysis_locale.js / index.html 注册）——
  // 桩里预置 zh/en 两列即可驱动语言切换断言
  for (const [zh, en] of [
    ['Meta 分析', 'Meta-analysis'], ['系统综述', 'Systematic review'],
    ['随机对照试验', 'Randomized controlled trial'], ['队列研究', 'Cohort study'],
    ['病例对照', 'Case-control'], ['横断面研究', 'Cross-sectional study'],
    ['诊断准确性', 'Diagnostic accuracy'],
  ]) dict[zh] = [zh, en];
  function T(source, ...args) {
    const row = dict[source];
    const text = (row && row[lang]) || source;
    return String(text).replace(/\{(\d+)\}/g, (_, i) => (i < args.length ? String(args[i]) : '{' + i + '}'));
  }
  function el(tag, attrs = {}, ...children) {
    const node = makeNode(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null) continue;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else node.setAttribute(k, v);
    }
    for (const c of children.flat(Infinity)) if (c != null) node.append(c);
    return node;
  }

  const ctx = Object.assign({
    document, el, T, Event: FakeEvent,
    /* 模块在数据到达后经 requestAnimationFrame 再 paint 一帧（覆盖「先取值、
       后入树」顺序）。桩里以 setImmediate 实现，await tick() 即可推进。 */
    requestAnimationFrame: cb => setImmediate(cb),
    RFLang: { register: rows => { for (const row of rows) dict[row[0]] = row; } },
    S: { task: { task_id: 'T1' }, user: { user_id: 'U1' }, screener: 'A', articles: [{ zotero_key: 'K1' }], scrIdx: 0 },
    taskApi: () => '/api/tasks/T1',
    scrQ: () => '?screener=A',
    api: async () => { throw Error('not stubbed'); },
    WorkflowControls: undefined,
    lang, dict, created, listeners,
    setLang(next) { lang = next; },
  }, overrides);
  ctx.window = ctx;                         // 模块挂 window.StudyDesignBadge
  vm.createContext(ctx);
  vm.runInContext(SOURCE, ctx);
  return ctx;
}
const tick = () => new Promise(resolve => setImmediate(resolve));

test('module loads into stubs, exports frozen API, registers 10-language rows', () => {
  const rows = [];
  const ctx = makeContext({ RFLang: { register: r => rows.push(...r) } });
  assert.ok(ctx.window.StudyDesignBadge);
  assert.ok(Object.isFrozen(ctx.window.StudyDesignBadge));
  assert.equal(typeof ctx.window.StudyDesignBadge.badge, 'function');
  assert.equal(typeof ctx.window.StudyDesignBadge.prefill, 'function');
  assert.ok(rows.length >= 5, '至少 5 条新增词条');
  for (const row of rows) {
    assert.equal(row.length, 10, '十语言列');
    for (const cell of row) assert.ok(String(cell).trim(), '词条单元格非空');
  }
});

test('placeholder only while in flight; known key fills; unknown key yields null', async () => {
  let resolveApi;
  const calls = [];
  const ctx = makeContext({ api: async p => { calls.push(p); return new Promise(r => { resolveApi = r; }); } });
  const host = ctx.document.createElement('div');
  const badge = ctx.window.StudyDesignBadge.badge({ zotero_key: 'K1' });
  host.appendChild(badge);
  assert.equal(badge.textContent, '…');
  assert.equal(badge.getAttribute('data-rf-sd-badge'), 'K1');
  const badge2 = host.appendChild(ctx.window.StudyDesignBadge.badge({ zotero_key: 'K2' }));   // 第二次：同作用域不再发请求
  await tick();
  assert.equal(calls.length, 1, '每作用域一次拉取');
  assert.equal(calls[0], '/api/tasks/T1/study-designs?screener=A');
  resolveApi({ available: true, rule_version: 1, count: 1, items: {
    K1: { label: 'rct', recognized: true, is_rct: true, source: 'title_abstract',
          matched_keyword: 'randomised controlled trial', suggest_exclusion_reason: null },
  } });
  await tick(); await tick();
  assert.equal(badge.textContent, '随机对照试验', 'paint 以 textContent 补全');
  assert.match(badge.className, /rf-sd-rct/);
  assert.ok(badge.getAttribute('title').includes('randomised controlled trial'), 'tooltip 携带命中关键词');
  // K2 无分类 → 已入树的占位节点被移除（优雅降级：不臆测）
  assert.ok(!badge2.isConnected, '无分类键的已入树徽标必须从 DOM 移除');
  assert.equal(host.children.includes(badge2), false);
  // 数据就绪后新建的无分类键 badge() 直接返回 null（不产生永不消失的「…」）
  assert.equal(ctx.window.StudyDesignBadge.badge({ zotero_key: 'K2' }), null,
               '数据就绪且无此键：badge() 必须返回 null（交给调用方 filter 剔除）');
});

test('DOM ORDER: badge created before it is appended still settles (no permanent placeholder)', async () => {
  /* 复刻评审指出的真实顺序：renderScreen 在 body.append(...) 的实参求值阶段
     调用 StudyDesignBadge.badge(r)，节点此刻尚未入树；数据可能在 append 之前
     就已就绪（缓存命中/已失败）。旧实现在该顺序下留下永久「…」，本钉子把它
     钉死。桩里 isConnected 由 parentNode 决定，且 querySelectorAll 只返回
     挂载节点——与浏览器语义一致。 */
  // 场景 A：取数已完成（成功）后才创建节点 → 直接终态，无「…」
  const ctx = makeContext({ api: async () => ({ available: true, rule_version: 1, count: 2, items: {
    HIT: { label: 'systematic_review', recognized: true, is_rct: false, source: 'title_abstract',
           matched_keyword: 'systematic review', suggest_exclusion_reason: '研究设计不符' },
  } }) });
  ctx.window.StudyDesignBadge.badge({ zotero_key: 'WARM' });   // 预热缓存（节点被调用方丢弃）
  await tick(); await tick();
  const created = ctx.window.StudyDesignBadge.badge({ zotero_key: 'HIT' });   // 取值阶段：未入树
  assert.equal(created.isConnected, false, '前置：取值时节点尚未入树');
  assert.equal(created.textContent, '系统综述', '缓存命中 → 取值即终态，绝不是「…」');
  const host = ctx.document.createElement('div');
  host.appendChild(created);
  assert.equal(created.textContent, '系统综述', '入树后保持终态');

  // 场景 B：无该键 → 取值即 null（调用方 filter 剔除，node 不入树）
  assert.equal(ctx.window.StudyDesignBadge.badge({ zotero_key: 'MISS' }), null);

  // 场景 C：取数失败后新建 → null（不留永久「…」）
  const ctxFail = makeContext({ api: async () => { throw Error('offline'); } });
  ctxFail.window.StudyDesignBadge.badge({ zotero_key: 'X' });
  await tick(); await tick();
  assert.equal(ctxFail.window.StudyDesignBadge.badge({ zotero_key: 'X' }), null,
               '失败后就绪且无分类 → null（不是永久占位）');
});

test('truncated:true degrades entirely (no partial badges that look like "unrecognized")', async () => {
  let calls = 0;
  const ctx = makeContext({ api: async () => { calls++; return { available: true, truncated: true,
    rule_version: 1, count: 1, items: { K1: { label: 'rct', recognized: true, is_rct: true } } }; } });
  const host = ctx.document.createElement('div');
  const badge = host.appendChild(ctx.window.StudyDesignBadge.badge({ zotero_key: 'K1' }));
  await tick(); await tick();
  assert.ok(!badge.isConnected, 'truncated=true → 整体降级，占位节点移除（不展示部分结果）');
  assert.equal(ctx.window.StudyDesignBadge.badge({ zotero_key: 'K1' }), null);
  assert.equal(calls, 1, '降级后同作用域零重试');
});

test('api rejection and available:false both degrade silently with no retry storm', async () => {
  let fail = true;
  let calls = 0;
  const ctx = makeContext({ api: async () => { calls++; if (fail) throw Error('offline'); return { available: true, items: {} }; } });
  const host = ctx.document.createElement('div');
  const badge = host.appendChild(ctx.window.StudyDesignBadge.badge({ zotero_key: 'K1' }));
  await tick(); await tick();
  assert.ok(!badge.isConnected, '拉取失败 → 徽标节点移除');
  assert.equal(calls, 1);
  ctx.window.StudyDesignBadge.badge({ zotero_key: 'K1' });   // 同作用域不再重试
  await tick(); await tick();
  assert.equal(calls, 1, '失败后同作用域零重试（无重试风暴）');
  fail = false;
  const ctx2 = makeContext({ api: async () => { calls++; return { available: false, items: { K1: { label: 'rct' } } }; } });
  const badge2 = ctx2.document.createElement('div')
    .appendChild(ctx2.window.StudyDesignBadge.badge({ zotero_key: 'K1' }));
  await tick(); await tick();
  assert.ok(!badge2.isConnected, 'available:false → 同样降级移除');
});
test('prefill fills the exclusion control only for recognized non-RCT, dispatches change, never saves', async () => {
  const events = [];
  const ctx = makeContext({ api: async () => ({ available: true, rule_version: 1, count: 3, items: {
    COHORT: { label: 'cohort', recognized: true, is_rct: false, source: 'title_abstract',
              matched_keyword: 'cohort', suggest_exclusion_reason: '研究设计不符' },
    RCT: { label: 'rct', recognized: true, is_rct: true, source: 'title_abstract',
           matched_keyword: 'randomised controlled trial', suggest_exclusion_reason: null },
    PLAIN: { label: 'other', recognized: false, is_rct: false, source: null,
             matched_keyword: null, suggest_exclusion_reason: null },
  } }) });
  ctx.window.StudyDesignBadge.badge({ zotero_key: 'COHORT' });
  await tick(); await tick();

  const select = ctx.document.createElement('select');
  select.setAttribute('id', 'excReason');
  select.value = '';
  select.addEventListener('change', () => events.push('change'));
  // 队列研究（判非 RCT）→ 预选「研究设计不符」
  assert.equal(ctx.window.StudyDesignBadge.prefill(select, { zotero_key: 'COHORT' }), true);
  assert.equal(select.value, '研究设计不符');
  assert.deepEqual(events, ['change'], '复用 ai_suggest 采纳＝填控件模式：派发 change');
  assert.ok(String(select.getAttribute('title')).length > 0, '预填带人工核对提示 title');
  // RCT / 未识别 / 已有值 → 一律不动
  const s2 = ctx.document.createElement('select'); s2.value = '';
  assert.equal(ctx.window.StudyDesignBadge.prefill(s2, { zotero_key: 'RCT' }), false);
  const s3 = ctx.document.createElement('select'); s3.value = '';
  assert.equal(ctx.window.StudyDesignBadge.prefill(s3, { zotero_key: 'PLAIN' }), false);
  assert.equal(s3.value, '');
  const s4 = ctx.document.createElement('select'); s4.value = '人群不符';
  assert.equal(ctx.window.StudyDesignBadge.prefill(s4, { zotero_key: 'COHORT' }), false);
  assert.equal(s4.value, '人群不符', '不覆盖已选值（含已存理由恢复）');
});

test('late data arrival prefills the current card and re-baselines decision cleanliness', async () => {
  let resolveApi;
  const marks = [];
  const ctx = makeContext({
    api: () => new Promise(r => { resolveApi = r; }),
    WorkflowControls: { markDecisionClean: stage => marks.push(stage) },
  });
  const select = ctx.document.createElement('select');
  select.setAttribute('id', 'excReason');
  select.value = '';
  ctx.window.StudyDesignBadge.badge({ zotero_key: 'K1' });   // 首屏：数据未到
  await tick();
  assert.equal(select.value, '', '数据未到时不预填');
  resolveApi({ available: true, rule_version: 1, count: 1, items: {
    K1: { label: 'case_control', recognized: true, is_rct: false, source: 'raw_json',
          matched_keyword: 'case-control', suggest_exclusion_reason: '研究设计不符' } } });
  await tick(); await tick();
  assert.equal(select.value, '研究设计不符', '数据晚到 → 补填当前卡');
  assert.deepEqual(marks, ['screen'], '补填后重置决策干净基线（不误报未保存草稿）');
  // 用户改过 → 不再自动改值
  const select2 = ctx.document.createElement('select');
  select2.setAttribute('id', 'excReason');
  select2.value = '人群不符';
  ctx.document._emit('change', select2);   // 用户亲手改（非模块派发）
  ctx.window.StudyDesignBadge.prefill(select2, { zotero_key: 'K1' });
  assert.equal(select2.value, '人群不符', '用户改过则不打扰');
});

test('cache invalidates on import-completed / workflow-changed (re-review issue 3)', async () => {
  /* 复审实测的缺口：同作用域缓存永不失效 → 新导入激活的文献此后返回 null（无
     徽标也无提示），只有切任务/重开应用才恢复。修复后：两个事件到达即丢弃
     缓存，下一次渲染重新取数（仍每作用域一次，不引入轮询）。 */
  let calls = 0;
  let payload = { available: true, truncated: false, rule_version: 1, count: 1,
                  items: { OLD: { label: 'cohort', recognized: true, is_rct: false,
                                  source: 'title_abstract', suggest_exclusion_reason: '研究设计不符' } } };
  const ctx = makeContext({
    api: async () => { calls++; return payload; },
    S: { task: { task_id: 'T1' }, user: { user_id: 'U1' }, screener: 'A',
         page: 'home', articles: [], scrIdx: 0 },
  });
  ctx.window.StudyDesignBadge.badge({ zotero_key: 'OLD' });
  await tick(); await tick();
  assert.equal(calls, 1);
  // 新文献尚未出现在目录里 → 无徽标（此前的永久状态就是这里）
  assert.equal(ctx.window.StudyDesignBadge.badge({ zotero_key: 'NEW' }), null);
  assert.equal(calls, 1, '未失效前同作用域不重取');
  // 导入激活 → 后端目录已有 NEW
  payload = { available: true, truncated: false, rule_version: 1, count: 2,
              items: { OLD: payload.items.OLD, NEW: { label: 'rct', recognized: true, is_rct: true,
                     source: 'title_abstract', matched_keyword: 'randomised trial',
                     suggest_exclusion_reason: null } } };
  ctx.document._emit('reviewflow:import-completed', {});
  const host = ctx.document.createElement('div');
  /* 真实顺序：renderScreen 在 append 实参求值阶段调用 badge()，同一 tick 内入树 */
  const fresh = host.appendChild(ctx.window.StudyDesignBadge.badge({ zotero_key: 'NEW' }));
  assert.ok(fresh && fresh.getAttribute('data-rf-sd-badge') === 'NEW', '失效后重新进入「在途」而非返回 null');
  assert.equal(fresh.textContent, '…', '失效后首渲染是占位（数据仍在途中）');
  await tick(); await tick();
  assert.equal(calls, 2, '失效后恰好重取一次');
  assert.equal(fresh.textContent, '随机对照试验', '新导入文献拿到徽标');
  // workflow-changed 同样失效
  ctx.document._emit('reviewflow:workflow-changed', {});
  ctx.window.StudyDesignBadge.badge({ zotero_key: 'NEW' });
  await tick(); await tick();
  assert.equal(calls, 3, 'workflow-changed 也丢缓存并重取一次');
  // 失效不引入轮询：无事件时再多次渲染不增加请求
  for (let i = 0; i < 5; i++) ctx.window.StudyDesignBadge.badge({ zotero_key: 'NEW' });
  await tick(); await tick();
  assert.equal(calls, 3, '无事件时零额外请求（非轮询）');
});

test('invalidation on the screening page re-renders without discarding a dirty reason', async () => {
  let calls = 0;
  const ctx = makeContext({
    api: async () => { calls++; return { available: true, truncated: false, rule_version: 1,
      count: 1, items: { K1: { label: 'rct', recognized: true, is_rct: true,
      source: 'title_abstract', matched_keyword: 'randomised trial', suggest_exclusion_reason: null } } }; },
    S: { task: { task_id: 'T1' }, user: { user_id: 'U1' }, screener: 'A', page: 'screen',
         articles: [{ zotero_key: 'K1' }], scrIdx: 0 },
    WorkflowControls: { decisionDirty: () => true, markDecisionClean: () => {} },
  });
  let renders = 0;
  ctx.renderScreen = () => { renders++; };
  vm.runInContext('renderScreen = function () { globalThis.__renders++; };', ctx);
  ctx.__renders = 0;
  ctx.window.StudyDesignBadge.badge({ zotero_key: 'K1' });
  await tick(); await tick();
  ctx.document._emit('reviewflow:import-completed', {});
  await tick();
  assert.equal(ctx.__renders, 0, '理由草稿未保存（decisionDirty）→ 不重绘卡片，避免丢掉用户输入');
  assert.equal(ctx.window.StudyDesignBadge.badge({ zotero_key: 'K1' }) !== null, true,
               '徽标仍可在下次渲染时重取（dirty 不阻塞取数）');
});

test('language change repaints badges through the dictionary', async () => {
  const ctx = makeContext({ api: async () => ({ available: true, rule_version: 1, count: 1, items: {
    K1: { label: 'rct', recognized: true, is_rct: true, source: 'title_abstract',
          matched_keyword: 'randomised controlled trial', suggest_exclusion_reason: null } } }) });
  const badge = ctx.document.createElement('div').appendChild(ctx.window.StudyDesignBadge.badge({ zotero_key: 'K1' }));
  await tick(); await tick();
  assert.equal(badge.textContent, '随机对照试验');
  ctx.setLang(1);
  ctx.document._emit('reviewflow:language-changed', {});
  assert.equal(badge.textContent, 'Randomized controlled trial', '语言切换事件重绘');
});
