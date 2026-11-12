'use strict';

// 直接执行产品模块，经实际入口触发对话框与关系裁决。
const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { webcrypto, createHash } = require('node:crypto');
const vm = require('node:vm');

const frontend = join(__dirname, '..', '..', 'custom_frontend');
const readSource = name => readFileSync(join(frontend, name), 'utf8');

class Element {
  constructor(tagName) {
    this.tagName = tagName;
    this.attrs = {};
    this.dataset = {};
    this.childNodes = [];
    this.listeners = {};
    this.classList = { add: () => {} };
    this.open = false;
  }
  get textContent() { return this.childNodes.map(node => node.textContent).join(''); }
  set textContent(value) {
    this.replaceChildren({ nodeType: 3, textContent: String(value) });
  }
  get isConnected() { return this.tagName === 'body' || Boolean(this.parentNode?.isConnected); }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return this.attrs[name]; }
  append(...nodes) {
    for (const node of nodes) {
      this.childNodes.push(node);
      node.parentNode = this;
    }
  }
  prepend(node) {
    this.childNodes.unshift(node);
    node.parentNode = this;
  }
  replaceChildren(...nodes) {
    for (const node of this.childNodes) node.parentNode = null;
    this.childNodes = [];
    this.append(...nodes);
  }
  remove() {
    if (!this.parentNode) return;
    const siblings = this.parentNode.childNodes;
    siblings.splice(siblings.indexOf(this), 1);
    this.parentNode = null;
  }
  find(predicate) {
    for (const child of this.childNodes) {
      if (predicate(child)) return child;
      const result = child.find?.(predicate);
      if (result) return result;
    }
  }
  collect(predicate) {
    return this.childNodes.flatMap(node => [
      ...(predicate(node) ? [node] : []), ...(node.collect?.(predicate) || [])
    ]);
  }
  querySelector(selector) {
    return selector.startsWith('.') ? this.find(node => node.className === selector.slice(1)) : null;
  }
  closest(selector) {
    for (let node = this; node; node = node.parentNode) {
      if (node.tagName === selector) return node;
    }
    return null;
  }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  insertAdjacentHTML() {}
  showModal() { this.open = true; }
  close() {
    this.open = false;
    for (const fn of this.listeners.close || []) fn();
  }
}

const owner = '1';
const proposal = { id: 'draft', actor_id: '2', a: 'R1', b: 'R2',
  type: 'study', status: 'mine', evidence: 'Evidence' };
function snapshot(proposals = [], confirmed = []) {
  return { state: { links: [] }, confirmed, proposals, can_adjudicate: true, can_write: true,
    shared_revision: 4, reports: ['R1', 'R2', 'R3'].map(key => ({ key, title: key })) };
}

async function mount(apiImpl) {
  const body = new Element('body'), app = new Element('main'), choice = new Element('section'),
    arbitration = new Element('section');
  app.id = 'appMain';
  choice.id = 'anDependentPanel';
  app.append(choice, arbitration);
  body.append(app);
  const listeners = {}, rows = [], calls = [], toasts = [];
  let locale = 1;
  const sandbox = {
    document: {
      body,
      createElement: tag => new Element(tag),
      getElementById: id => body.find(node => node.id === id),
      querySelector: selector => selector === '#appMain' ? app :
        selector === '.page[data-p="merge"]' ? arbitration : null,
      querySelectorAll: selector => {
        if (selector === '[data-mp-text]') return body.collect(node => node.dataset?.mpText);
        if (selector === '.mp-dialog[open] [data-mp-close]') return body.collect(
          node => node.dataset?.mpClose && node.closest('dialog')?.open);
        if (selector === '.mp-dialog') return body.collect(node => node.tagName === 'dialog');
        return [];
      },
      addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); }
    },
    RFLang: { register: items => rows.push(...items) },
    T: value => rows.find(row => row.includes(value))?.[locale] ?? value,
    api: async (url, options) => { calls.push({ url, options }); return apiImpl(url, options); },
    toast: message => toasts.push(message),
    taskApi: () => '/api/tasks/T1',
    S: { task: { task_id: 'T1' }, user: { user_id: owner }, page: 'merge' },
    MutationObserver: class { observe() {} },
    Node: { TEXT_NODE: 3 }, crypto: webcrypto, TextEncoder
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  for (const name of ['report_evidence_locale.js', 'report_evidence.js']) {
    vm.runInContext(readSource(name), sandbox, { filename: name });
  }
  await new Promise(resolve => setImmediate(resolve));
  return {
    calls, toasts, sandbox, rows,
    dialogs: () => body.collect(node => node.tagName === 'dialog'),
    openChoices: () => sandbox.document.getElementById('mp-dependent-choice').onclick(),
    openAdjudication: () => sandbox.document.getElementById('mp-arbitration-link').onclick(),
    switchLanguage(next) {
      locale = next;
      for (const fn of listeners['reviewflow:language-changed'] || []) fn();
    }
  };
}

test('L-21 localizes the accessible close name when opened and updates it in place on language change', async () => {
  const env = await mount(url => url.endsWith('/analysis/effects') ? { effects: [] } : snapshot());
  await env.openChoices();
  const [dialog] = env.dialogs();
  const close = dialog.find(node => node.tagName === 'button' && node.attrs['aria-label']);
  assert.ok(dialog.open);
  assert.equal(close.getAttribute('aria-label'), 'Close');

  env.switchLanguage(2);
  assert.equal(close.getAttribute('aria-label'), 'Fermer');
  await close.onclick();
  assert.equal(env.dialogs().length, 0);
});

test('L-22 repeated analysis request failures do not leave hidden dialogs in the document', async () => {
  const env = await mount(url => {
    if (url.endsWith('/analysis/effects')) throw new Error('request failed');
    return snapshot();
  });
  await env.openChoices();
  await env.openChoices();
  assert.deepEqual(env.dialogs(), []);
  assert.deepEqual(env.toasts, ['request failed', 'request failed']);
});


const pairChanged = 'Proposal changed since you viewed it. Reload and re-confirm.';

function relationButton(env, text) {
  return env.dialogs()[0].find(node => node.tagName === 'button' && node.textContent === text);
}

test('F1 hashes the displayed pair in Python Unicode order and sends the digest on confirm only', async () => {
  // U+E000 < U+10000 按 Unicode 码点成立；按默认 UTF-16 排序恰好相反。
  const link = { ...proposal, a: '\u{10000}', b: '\uE000' };
  const expected = createHash('sha256').update('\uE000\x1f\u{10000}', 'utf8').digest('hex');
  const env = await mount((url, options) => {
    if (!options) return snapshot([link]);
    assert.equal(options.body.action, options.body.expected_revision === 4 ? 'confirm' : 'unlink');
    if (options.body.action === 'confirm') {
      assert.equal(options.body.a, link.a);
      assert.equal(options.body.b, link.b);
      assert.equal(options.body.expected_pair_digest, expected);
      return { ...snapshot([], [{ ...link, status: 'agreed' }]), shared_revision: 5 };
    }
    assert.ok(!Object.hasOwn(options.body, 'expected_pair_digest'));
    return snapshot();
  });
  await env.openAdjudication();
  await relationButton(env, 'Confirm relationship').onclick();
  const unlink = relationButton(env, 'Unlink confirmed relationship');
  assert.ok(unlink, 'success redraws the confirmed relation');
  await unlink.onclick();
  assert.equal(env.calls.filter(call => call.options?.method === 'POST').length, 2);
  assert.deepEqual(env.toasts, []);
});

test('F1 stale digest refreshes the displayed proposal, shows a translated message, and awaits another click', async () => {
  const latest = { ...proposal, b: 'R3' };
  let catalogueReads = 0;
  const env = await mount((url, options) => {
    if (!options) return snapshot([catalogueReads++ ? latest : proposal]);
    throw Object.assign(new Error(pairChanged), { status: 409 });
  });
  await env.openAdjudication();
  await relationButton(env, 'Confirm relationship').onclick();

  assert.equal(catalogueReads, 2);
  assert.equal(env.calls.filter(call => call.options?.method === 'POST').length, 1);
  assert.ok(env.dialogs()[0].textContent.includes(latest.b));
  const notice = env.dialogs()[0].find(node => node.attrs?.role === 'alert');
  assert.equal(notice.textContent, 'The proposer changed this proposal. Refresh the list and confirm again.');
  assert.deepEqual(env.toasts, []);
  const row = env.rows.find(row => row[0] === '提案已被提案人修改，请刷新后重新确认');
  assert.equal(row.length, 10);
  assert.ok(row.every(cell => cell.trim()));
});


function legacyDigestRejection() {
  return Object.assign(new Error('Extra inputs are not permitted'), {
    status: 422,
    validationIssues: [{ type: 'extra_forbidden', loc: ['body', 'expected_pair_digest'] }]
  });
}

test('F1 strict legacy validation triggers one compatibility retry after rechecking the proposal', async () => {
  let reads = 0, writes = 0;
  const env = await mount((url, options) => {
    if (!options) { reads++; return snapshot([proposal]); }
    if (++writes === 1) {
      assert.match(options.body.expected_pair_digest, /^[0-9a-f]{64}$/);
      throw legacyDigestRejection();
    }
    assert.equal(reads, 2);
    assert.equal(options.body.expected_revision, 4);
    assert.equal(options.body.actor_id, proposal.actor_id);
    assert.equal(options.body.link_id, proposal.id);
    assert.ok(!Object.hasOwn(options.body, 'expected_pair_digest'));
    return snapshot([], [{ ...proposal, status: 'agreed' }]);
  });
  await env.openAdjudication();
  await relationButton(env, 'Confirm relationship').onclick();
  assert.equal(writes, 2);
  assert.ok(relationButton(env, 'Unlink confirmed relationship'));
  assert.deepEqual(env.toasts, []);
});

for (const scenario of [
  { label: 'pair changed', refresh: () => snapshot([{ ...proposal, b: 'R3' }]) },
  { label: 'actor changed', refresh: () => snapshot([{ ...proposal, actor_id: '8' }]) },
  { label: 'shared revision changed', refresh: () => ({ ...snapshot([proposal]), shared_revision: 5 }) },
  { label: 'catalogue read fails', refresh: () => { throw new Error('catalogue unavailable'); } },
  { label: 'task changed', refresh: env => {
    env.sandbox.S.task.task_id = 'T2';
    return snapshot([proposal]);
  } },
]) {
  test('F1 skips legacy retry if ' + scenario.label, async () => {
    let reads = 0;
    const env = await mount((url, options) => {
      if (!options) return reads++ ? scenario.refresh(env) : snapshot([proposal]);
      throw legacyDigestRejection();
    });
    await env.openAdjudication();
    await relationButton(env, 'Confirm relationship').onclick();
    assert.equal(reads, 2);
    assert.equal(env.calls.filter(call => call.options?.method === 'POST').length, 1);
  });
}

test('F1 does not reread or retry a distinct 422 validation error', async () => {
  const error = Object.assign(new Error('Bad relationship type'), {
    status: 422, validationIssues: [{ type: 'literal_error', loc: ['body', 'action'] }]
  });
  const env = await mount((url, options) => {
    if (!options) return snapshot([proposal]);
    throw error;
  });
  await env.openAdjudication();
  await relationButton(env, 'Confirm relationship').onclick();
  assert.equal(env.calls.length, 2);
  assert.equal(env.dialogs()[0].querySelector('.mp-error').textContent, error.message);
});
