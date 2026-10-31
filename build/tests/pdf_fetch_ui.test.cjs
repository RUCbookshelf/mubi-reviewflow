/* W2 · PDF 自动获取前端钉子（node --test；受控桩三路径）。
 *
 * 复刻 build/tests/ml_batch.test.cjs 的 vm 手法：custom_frontend/pdf_fetch.js
 * **原文件逐字**在 vm 中运行；document/RFLang/T/api/S/toast 为受控桩。
 * 钉住的治理红线：
 * - 十语言自包含：RFLang.register 全部行 10 列非空；IIFE；无 innerHTML（textContent 渲染）；
 * - 默认关零渲染：settings.enabled=false 时初筛卡不出现「获取全文」且除 settings GET 外零请求；
 * - 显式手动触发：仅在点击按钮后发起 POST fetch；
 * - 三路径降级：成功（页数回显+toast）/ 无 OA（提示）/ 网络失败（code 映射提示，
 *   异常不外溢）；403 disabled 与 502 upstream 分别映射；
 * - Crossref 建议只读呈现（复制按钮），不发起写请求。
 */
'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');

const src = fs.readFileSync(path.join(__dirname, '..', '..', 'custom_frontend', 'pdf_fetch.js'), 'utf8');

function makeEl(tag, registry) {
  const el = {
    tagName: tag, children: [], textContent: '', disabled: false, checked: false,
    value: '', className: '', attrs: {}, listeners: {},
    setAttribute(key, value) {
      this.attrs[key] = String(value);
      if (key === 'id') registry.set(String(value), this);
    },
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    append(...nodes) {
      for (const node of nodes) {
        if (node === undefined || node === null || node === false) continue;
        this.children.push(node);
        if (typeof node === 'string') this.textContent += node;
        else if (node && node.nodeType === 3) this.textContent += node.text;
      }
    },
    replaceChildren(...nodes) { this.children = []; this.textContent = ''; this.append(...nodes); },
  };
  return el;
}

function makeEnv(apiImpl) {
  const calls = [];
  const toasts = [];
  const registry = new Map();
  const docListeners = {};
  const observers = [];
  const document = {
    createElement: tag => makeEl(tag, registry),
    createTextNode: text => ({ text: String(text), nodeType: 3 }),
    getElementById: id => registry.get(id) || null,
    addEventListener: (type, fn) => { (docListeners[type] = docListeners[type] || []).push(fn); },
  };
  const panel = makeEl('section', registry);
  registry.set('wf-panel-coding', panel);
  const scrBody = makeEl('div', registry);
  registry.set('scrBody', scrBody);
  scrBody._hasDecisions = false;
  scrBody.querySelector = () => (scrBody._hasDecisions ? {} : null);
  class MutationObserver {
    constructor(cb) { observers.push(cb); }
    observe() {}
  }
  const T = (s, ...args) => {
    let out = String(s);
    args.forEach((arg, i) => { out = out.split('{' + i + '}').join(String(arg)); });
    return out;
  };
  const api = async (p, opts) => {
    calls.push([p, opts]);
    if (apiImpl) return apiImpl(p, opts);
    throw new Error('unexpected api call: ' + p);
  };
  const opens = [];
  const anchors = [];
  const confirms = [];
  let confirmAnswer = true;
  document.documentElement = { lang: 'zh-CN' };
  document.body = makeEl('body', registry);
  const realAppendChild = document.body.append.bind(document.body);
  document.body.append = (...nodes) => {
    for (const node of nodes) if (node && node.tagName === 'a') anchors.push(node);
    realAppendChild(...nodes);
  };
  const sandbox = {
    document, MutationObserver, T, api, setTimeout, clearTimeout, console, Promise,
    RFLang: { rows: [], register(rows) { this.rows.push(...rows); } },
    S: { task: { task_id: 'T1' }, screener: null, scrIdx: 0,
         articles: [{ zotero_key: 'A1', doi: '10.1/x', title: 'Trial' }] },
    toast: (text, kind) => toasts.push([text, kind]),
    API_BASE: '',
    navigator: {},
    URL: { createObjectURL: (blob) => 'blob:stub-' + (opens.length + 1),
           revokeObjectURL: () => {} },
    confirm: (message) => { confirms.push(message); return confirmAnswer; },
  };
  sandbox.window = sandbox;
  sandbox.window.open = (url, target) => { opens.push([url, target]); return {}; };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: 'pdf_fetch.js' });
  return { sandbox, calls, toasts, registry, docListeners, observers, scrBody, panel,
           opens, anchors, confirms,
           setConfirm: (value) => { confirmAnswer = value; } };
}

const flush = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setTimeout(r, 0)); };
const settingsOn = {
  enabled: true, version: 2, email: 'a@b.test',
  unpaywall_base_url: 'https://api.unpaywall.org',
  crossref_base_url: 'https://api.crossref.org',
  outbound_endpoints: ['https://api.unpaywall.org', 'https://api.crossref.org'],
};
const settingsOff = Object.assign({}, settingsOn, { enabled: false, version: 1 });

test('module is a self-contained IIFE with no innerHTML (textContent rendering redline)', () => {
  assert.ok(/^\s*\/\*[\s\S]*?\*\//.test(src) || src.includes('(function ()'));
  assert.ok(src.includes('(function () {'));
  assert.ok(/RFLang\.register\(/.test(src));
  assert.ok(!/\.innerHTML\s*=/.test(src), '渲染红线：绝不 innerHTML 赋值');
});

test('registers ten-language rows (10 non-empty columns each)', async () => {
  const env = makeEnv(async p => {
    if (p.endsWith('/pdf-fetch/settings')) return settingsOff;
    throw new Error('unexpected ' + p);
  });
  await flush();
  const rows = env.sandbox.RFLang.rows;
  assert.ok(rows.length >= 25, '词条数（zh/en/fr/ru/es/ja/pt/de/sr/ko 源串自包含）');
  for (const row of rows) {
    assert.equal(row.length, 10, JSON.stringify(row[0]));
    for (const cell of row) assert.ok(String(cell).trim(), JSON.stringify(row[0]));
  }
});

test('default off: screening card stays empty and only the settings GET is issued', async () => {
  const env = makeEnv(async p => {
    if (p.endsWith('/pdf-fetch/settings')) return settingsOff;
    throw new Error('no further calls allowed: ' + p);
  });
  await flush();
  env.scrBody._hasDecisions = true;
  env.observers.forEach(cb => cb());           // 模拟初筛卡渲染完成
  await flush();
  assert.equal(env.registry.get('rfPdfFetchRow') || null, null, '未启用时初筛卡不得出现「获取全文」行');
  assert.equal(env.calls.length, 1, '默认关：除 settings 判定外零请求');
  assert.equal(env.calls[0][0], '/api/tasks/T1/pdf-fetch/settings');
  const card = env.registry.get('rfPdfFetchCard');
  assert.ok(card, '设置卡仍渲染（owner 可开启）');
  const hasBatch = card.children.some(child => child.tagName === 'h4');
  assert.equal(hasBatch, false, '未启用时批量入口不渲染');
});

test('enabled: card button appears and success path reports pages + toast', async () => {
  const env = makeEnv(async (p, opts) => {
    if (p.endsWith('/pdf-fetch/settings')) return settingsOn;
    if (p === '/api/tasks/T1/fulltext/A1/fetch' && opts && opts.method === 'POST')
      return { outcome: 'saved', n_pages: 7, oa_url: 'https://oa.example/x.pdf' };
    throw new Error('unexpected ' + p);
  });
  await flush();
  env.scrBody._hasDecisions = true;
  env.observers.forEach(cb => cb());
  await flush();
  const row = env.registry.get('rfPdfFetchRow');
  assert.ok(row, '启用后初筛卡必须出现「获取全文」行');
  const buttons = row.children.filter(child => child.tagName === 'button');
  assert.equal(buttons.length, 2, '获取全文 + 元数据建议两个按钮');
  assert.equal(buttons[0].textContent, '获取全文');
  const status = row.children.find(child => child.attrs && child.attrs.role === 'status');
  buttons[0].listeners.click[0]();              // 显式手动触发
  await flush();
  assert.ok(status.textContent.includes('7'), '成功路径回显页数: ' + status.textContent);
  assert.equal(env.toasts.length, 1);
  const fetchCalls = env.calls.filter(([p, opts]) => p.endsWith('/fetch') && opts && opts.method === 'POST');
  assert.equal(fetchCalls.length, 1, '只有用户点击才发 POST');
});

test('no-OA path surfaces the localized no-OA message', async () => {
  const env = makeEnv(async (p, opts) => {
    if (p.endsWith('/pdf-fetch/settings')) return settingsOn;
    if (p.endsWith('/fetch') && opts && opts.method === 'POST') return { outcome: 'no_oa' };
    throw new Error('unexpected ' + p);
  });
  await flush();
  env.scrBody._hasDecisions = true;
  env.observers.forEach(cb => cb());
  await flush();
  const row = env.registry.get('rfPdfFetchRow');
  const buttons = row.children.filter(child => child.tagName === 'button');
  const status = row.children.find(child => child.attrs && child.attrs.role === 'status');
  buttons[0].listeners.click[0]();
  await flush();
  assert.equal(status.textContent, '未找到开放获取的 PDF。');
});

test('network failure maps code to a graceful message and never escapes', async () => {
  for (const [error, expected] of [
    [{ status: 502, code: 'pdf_fetch_upstream', reason: 'unreachable', message: 'up' }, '无法连接获取服务，请稍后重试。'],
    [{ status: 403, code: 'pdf_fetch_disabled', message: 'off' }, '任务未启用 PDF 自动获取（默认关）。'],
    [{ status: 422, code: 'pdf_fetch_invalid_pdf', message: 'bad' }, '下载内容不是有效的 PDF。'],
  ]) {
    const env = makeEnv(async (p, opts) => {
      if (p.endsWith('/pdf-fetch/settings')) return settingsOn;
      if (p.endsWith('/fetch') && opts && opts.method === 'POST') throw error;
      throw new Error('unexpected ' + p);
    });
    await flush();
    env.scrBody._hasDecisions = true;
    env.observers.forEach(cb => cb());
    await flush();
    const row = env.registry.get('rfPdfFetchRow');
    const buttons = row.children.filter(child => child.tagName === 'button');
    const status = row.children.find(child => child.attrs && child.attrs.role === 'status');
    buttons[0].listeners.click[0]();
    await flush();
    assert.equal(status.textContent, expected);
  }
});

test('metadata suggestions render read-only with copy buttons (no write requests)', async () => {
  const env = makeEnv(async p => {
    if (p.endsWith('/pdf-fetch/settings')) return settingsOn;
    if (p.endsWith('/metadata-suggestions'))
      return { suggestions: { doi: '10.9/x', year: 2020 }, read_only: true, matched_by: 'title' };
    throw new Error('unexpected ' + p);
  });
  await flush();
  env.scrBody._hasDecisions = true;
  env.observers.forEach(cb => cb());
  await flush();
  const row = env.registry.get('rfPdfFetchRow');
  const buttons = row.children.filter(child => child.tagName === 'button');
  const host = env.registry.get('rfPdfFetchSuggestions');
  assert.ok(host, '建议宿主节点存在');
  buttons[1].listeners.click[0]();              // 「元数据建议」手动触发
  await flush();
  const writes = env.calls.filter(([, opts]) => opts && opts.method && opts.method !== 'GET');
  assert.equal(writes.length, 0, '建议流只读：不得发起任何写请求');
  const lines = host.children.filter(child => child.tagName === 'div');
  assert.equal(lines.length, 2, 'doi + year 两条建议');
  assert.ok(lines.some(line => line.children.some(c => c.tagName === 'button' && c.textContent === '复制')),
    '每条建议带「复制」按钮');
});


/* ---------------- r3 复审⑥：r2 三条修复必须各自有破坏即红钉子 ---------------- */

test('overwrite flow: 409 would_replace prompts, declining keeps screen silent', async () => {
  const env = makeEnv(async (p, opts) => {
    if (p.endsWith('/pdf-fetch/settings')) return settingsOn;
    if (p.endsWith('/fetch') && opts && opts.method === 'POST') {
      const error = new Error('conflict');
      error.status = 409;
      error.code = 'pdf_fetch_would_replace';
      throw error;
    }
    throw new Error('unexpected ' + p);
  });
  await flush();
  env.scrBody._hasDecisions = true;
  env.observers.forEach(cb => cb());
  await flush();
  const row = env.registry.get('rfPdfFetchRow');
  const [fetchButton] = row.children.filter(child => child.tagName === 'button');
  const status = row.children.find(child => child.attrs && child.attrs.role === 'status');

  env.setConfirm(false);
  fetchButton.listeners.click[0]();
  await flush();
  assert.equal(env.confirms.length, 1, '已有全文时必须先确认');
  assert.ok(/已有全文/.test(env.confirms[0]), '确认文案：' + env.confirms[0]);
  assert.equal(env.calls.filter(([p, o]) => p.includes('/fetch') && o && o.method === 'POST').length, 1,
    '拒绝确认后不得自动重发');
  assert.equal(status.textContent, '', '拒绝后不应报错，静默返回');

  // 接受确认 → 带 overwrite=1 重发
  env.setConfirm(true);
  fetchButton.listeners.click[0]();
  await flush();
  const posts = env.calls.filter(([p, o]) => p.includes('/fetch') && o && o.method === 'POST');
  assert.equal(posts.length, 3, '第二次点击 = 409 + overwrite 重发: ' + JSON.stringify(posts.map(x => x[0])));
  assert.ok(posts[2][0].includes('overwrite=1'), '重发必须带 overwrite=1: ' + posts[2][0]);
});

test('batch summary surfaces invalid/missing so entries never vanish', async () => {
  const env = makeEnv(async (p, opts) => {
    if (p.endsWith('/pdf-fetch/settings')) return settingsOn;
    if (p.endsWith('/articles')) return { articles: [{ zotero_key: 'A1', doi: '10.1/x' }] };
    if (p.endsWith('/fulltext/fetch-batch')) {
      assert.deepEqual(opts.body.keys, ['A1'], '批量只提交有 DOI 的键');
      return { aborted: false,
               summary: { saved: 3, no_oa: 0, failed: 0, skipped: 1,
                          missing: 4, invalid: 2, blocked: 1,
                          unresolvable: 1, trace_conflict: 1 },
               results: [{ zotero_key: 'A1', outcome: 'skipped', reason: 'already_has_pdf' },
                         { zotero_key: 'A2', outcome: 'skipped', reason: 'stage_paused' }] };
    }
    throw new Error('unexpected ' + p);
  });
  await flush();
  const card = env.registry.get('rfPdfFetchCard');
  const batchButton = card.children
    .flatMap(child => child.children || [])
    .find(child => child.tagName === 'button' && child.textContent === '批量获取');
  assert.ok(batchButton, '启用后必须渲染批量入口');
  batchButton.listeners.click[0]();
  await flush();
  const status = card.children.find(child => child.attrs && child.attrs.role === 'status'
    && child.textContent.includes('批量完成'));
  assert.ok(status, '必须渲染批量汇总');
  const text = status.textContent;
  assert.ok(text.includes('3'), '成功数: ' + text);
  // invalid 2 + blocked 1 + unresolvable 1 + trace_conflict 1 = 5
  assert.ok(text.includes('另有 5 条内容无效'), '额外计数必须含全：' + text);
  assert.ok(text.includes('4 条未找到'), 'missing 必须展示: ' + text);
  assert.ok(text.includes('已有全文 1'), '跳过明细按 reason 拆开: ' + text);
  assert.ok(text.includes('工作流拦截 1'), '工作流拦截必须单列: ' + text);
});

test('open PDF uses the authenticated blob request, not a bare link', async () => {
  const env = makeEnv(async (p, opts) => {
    if (p.endsWith('/pdf-fetch/settings')) return settingsOn;
    if (p.endsWith('/fetch') && opts && opts.method === 'POST') return { outcome: 'saved', n_pages: 2 };
    if (p.endsWith('/fulltext/A1') && opts && opts.blob) return new Blob(['%PDF-1.4']);
    throw new Error('unexpected ' + p);
  });
  await flush();
  env.scrBody._hasDecisions = true;
  env.observers.forEach(cb => cb());
  await flush();
  const row = env.registry.get('rfPdfFetchRow');
  const [fetchButton] = row.children.filter(child => child.tagName === 'button');
  fetchButton.listeners.click[0]();
  await flush();
  const status = row.children.find(child => child.attrs && child.attrs.role === 'status');
  const openButton = status.children.find(child => child.tagName === 'button'
    && child.textContent === '打开 PDF');
  assert.ok(openButton, '成功后必须提供「打开 PDF」按钮（非裸 <a href>）');
  openButton.listeners.click[0]();
  await flush();
  const blobCalls = env.calls.filter(([p, o]) => p.endsWith('/fulltext/A1') && o && o.blob === true);
  assert.equal(blobCalls.length, 1,
    '必须经 api(..., {blob:true}) 取字节（裸导航不带 Bearer 头会 401）');
  assert.equal(env.opens.length, 1, '只能打开一个标签页（r3 复审⑦ 双开回归）: ' + JSON.stringify(env.opens));
  assert.ok(String(env.opens[0][0]).startsWith('blob:'), '打开的是本地 blob URL');
  assert.equal(env.anchors.length, 0, '不得再走 anchor 兜底（会开出第二个标签页）');
});

test('index.html loads the module with the r8 w2pdf token', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', '..', 'custom_frontend', 'index.html'), 'utf8');
  assert.ok(html.includes('./pdf_fetch.js?v=20261030-r8-w2pdf1'),
    'index.html 必须以 ?v=20261030-r8-w2pdf1 令牌加载 pdf_fetch.js');
});
