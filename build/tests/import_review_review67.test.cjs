/* review67 M-13/L-33/L-34/L-35：执行工作区实际的弹窗和语言切换处理器。 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const front = path.join(__dirname, '../../custom_frontend');
const workspace = fs.readFileSync(path.join(front, 'unified_import_workspace.js'), 'utf8');
const locale = fs.readFileSync(path.join(front, 'import_review_refined_locale.js'), 'utf8');
const updates = require(path.join(front, 'import_review_updates.js'));

class Element {
  constructor(tag, text = '', attrs = {}) {
    this.tag = tag;
    this.text = text;
    this.children = [];
    this.attrs = { ...attrs };
    this.selected = '';
  }

  append(...elements) { this.children.push(...elements); }
  replaceChildren(...elements) { this.children = elements; }
  setAttribute(key, value) { this.attrs[key] = value; }
  removeAttribute(key) { delete this.attrs[key]; }
  querySelector() { return null; }
  closest() { return null; }
  focus() {}
  addEventListener() {}
  showModal() { this.open = true; }

  set value(value) {
    this.selected = this.children.some(child => child.attrs.value === value) ? value : '';
  }
  get value() { return this.selected; }
  get textContent() { return this.text; }
  set textContent(text) { this.text = text; }
}

function scene(api = async () => ({})) {
  const handlers = new Map();
  const reviewHost = new Element('div');
  const node = (tag, text, attrs) => new Element(tag, text, attrs);
  const context = {
    window: { addEventListener() {} },
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    document: { addEventListener: (name, fn) => handlers.set(name, fn) },
    ImportReviewModel: {},
    ImportReviewUpdates: updates,
    WorkflowControls: {
      scope: () => 'task',
      version: () => 5,
      errorText: () => 'localized-list-error',
    },
    ImportExportWorkspace: {
      node,
      literal: node,
      button: (text, click) => Object.assign(node('button', text), { click }),
      details: (text, content) => Object.assign(node('details', text), { children: [content] }),
      reviewHost,
      showImport() {},
      importPolicy() {},
    },
    T: key => key,
    RFLang: { get: () => 'en' },
    S: { task: { task_id: 'task' } },
    taskApi: () => '/api/tasks/task',
    api,
    requestAnimationFrame() {},
    setInterval() {},
  };
  const expose = [
    'window.testScene={batchGroups,bulkDialog,defaultKeeper,',
    'seed(rows,data){groups=rows;active="batch";batch=data;},',
    'remember(item,keeper){keepers.set(reviewKey(item),keeper);},',
    'spyOnDetail(fn){render=fn;}};',
  ].join('');
  const instrumented = workspace.replace(/\}\)\(\);\s*$/, expose + '})();');
  assert.notEqual(instrumented, workspace);
  vm.runInNewContext(instrumented, context);
  return {
    ...context.window.testScene,
    list: context.window.WorkflowImports.list,
    root: reviewHost.children[0],
    languageChanged: handlers.get('reviewflow:language-changed'),
  };
}

function member(id, refType = 'record') {
  return { ref_id: id, ref_type: refType, article: { title: id } };
}

function candidate(id, flags = {}) {
  return {
    review_id: id,
    members: [member(id + '-a'), member(id + '-b')],
    status: 'pending',
    ...flags,
  };
}

test('M-13 toolbar and dialog read backend eligibility in the batch scope', () => {
  const allowed = candidate('author-title-year', { bulk_eligible: true });
  const blocked = candidate('journal-doi', {
    bulk_eligible: false,
    bulk_ineligible_reason: 'generic_title',
    members: [
      { ...member('journal-a'), article: { title: 'Editorial', doi: '10.1002/(issn)1234' } },
      { ...member('journal-b'), article: { title: 'Editorial', doi: '10.1002/(issn)1234' } },
    ],
  });
  const legacy = candidate('legacy', { legacy: true, bulk_eligible: true });
  const missing = candidate('unknown', { bulk_eligible: 'true' });
  const page = scene();
  page.seed([allowed, blocked, legacy, missing], { can_manage: true });
  assert.deepEqual(Array.from(page.batchGroups(), row => row.review_id),
    ['author-title-year', 'journal-doi', 'unknown']);
  const renderBody = workspace.slice(workspace.indexOf('function render('), workspace.indexOf('function empty('));
  assert.match(renderBody, /const groups=batchGroups\(\)/);
  assert.match(workspace, /'Confirm eligible duplicates',\(\)=>bulkDialog\('candidate'\),!groups\.some\(group=>group\.bulk_eligible===true\)/);
  page.bulkDialog('candidate');
  const dialog = page.root.children.find(child => child.tag === 'dialog');
  assert.equal(dialog.open, true);
  const rows = dialog.children.find(child => child.attrs.class === 'bulk-list').children;
  assert.deepEqual(rows.map(row => row.children[0].text), ['author-title-year-a']);
  const excluded = dialog.children.find(child => child.tag === 'details');
  assert.match(excluded.children[0].children[0].text, /Editorial · import\.merge\.ineligible\.generic_title/);
});

test('L-33 candidate and metadata explanations use two complete localized rows', () => {
  const rows = [];
  vm.runInNewContext(locale, { RFLang: { register(values) { rows.push(...values); } } });
  const candidateRow = rows.find(row => row[1].startsWith('Only loaded candidates marked eligible'));
  const metadataRow = rows.find(row => row[1].startsWith('For the loaded records shown below'));
  for (const row of [candidateRow, metadataRow]) {
    assert.equal(row.length, 10);
    for (const value of row) assert.ok(value.trim());
    assert.ok(workspace.includes(row[1]));
  }
  assert.match(candidateRow[1], /publication DOI.+first author’s surname and year.+Generic or short titles/);
});

test('L-34 cached and saved choices precede the backend suggestion in the batch and AI views', () => {
  const item = candidate('candidate', {
    bulk_eligible: true,
    suggested_keeper: { keeper_record_id: 'candidate-b', reasons: ['has_doi'] },
  });
  const page = scene();
  page.seed([item], { can_manage: true });
  assert.equal(page.defaultKeeper(item), 'candidate-b');
  const saved = { ...item, keeper_record_id: 'candidate-a' };
  assert.equal(page.defaultKeeper(saved), 'candidate-a');
  page.remember(item, 'missing-member');
  assert.equal(page.defaultKeeper(item), 'candidate-b');
  page.remember(item, 'candidate-a');
  assert.equal(page.defaultKeeper(item), 'candidate-a');
  assert.match(workspace, /currentKeeper:defaultKeeper/);
  assert.match(workspace, /let keeper=final\?\(item\.keeper_key\|\|item\.keeper_record_id\):defaultKeeper\(item\)/);

  page.bulkDialog('candidate');
  const dialog = page.root.children.find(child => child.tag === 'dialog');
  const row = dialog.children.find(child => child.attrs.class === 'bulk-list').children[0];
  const select = row.children.find(child => child.tag === 'label').children[0];
  const reason = row.children.find(child => child.tag === 'small');
  const confirm = dialog.children.at(-1);
  assert.equal(select.value, 'candidate-a');
  assert.equal(reason.hidden, true);
  select.value = 'candidate-b';
  select.onchange();
  assert.equal(reason.hidden, false);
  assert.equal(confirm.disabled, false);
  select.value = '';
  select.onchange();
  assert.equal(confirm.disabled, true);
});

test('L-34 default falls back to the first selectable active or staged member', () => {
  const page = scene();
  const both = candidate('candidate', {
    members: [member('staged'), member('active', 'active')],
    suggested_keeper: { keeper_record_id: 'staged' },
  });
  page.seed([both], { can_manage: true });
  page.remember(both, 'staged');
  assert.equal(page.defaultKeeper(both), 'active');
  assert.equal(page.defaultKeeper(candidate('staged-only')), 'staged-only-a');
});

test('L-35 failed list stays visible across a language change with a prior batch in memory', async () => {
  const page = scene(async () => { throw new Error('list failed'); });
  page.seed([], { can_manage: true });
  let detailRenders = 0;
  page.spyOnDetail(() => detailRenders++);
  await page.list();
  const errorView = page.root.children;
  assert.equal(errorView[0].text, 'localized-list-error');
  page.languageChanged();
  assert.equal(detailRenders, 0);
  assert.equal(page.root.children, errorView);
});

test('L-35 older list failure cannot invalidate a newer successful list cache', async () => {
  let rejectOld;
  let calls = 0;
  const page = scene(() => {
    calls++;
    if (calls === 1) return new Promise((resolve, reject) => { rejectOld = reject; });
    return Promise.resolve({ batches: [], total: 0 });
  });
  const old = page.list();
  await page.list();
  rejectOld(new Error('stale list failure'));
  await old;
  const priorList = page.root.children;
  page.languageChanged();
  assert.notEqual(page.root.children, priorList);
  assert.equal(page.root.children[0].text, 'Your imports');
  assert.equal(calls, 2);
});
