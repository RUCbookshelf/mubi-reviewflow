/* review67 L-44：以原文件运行 GRADE 面板，验证推导依据跨渲染恢复。 */
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../custom_frontend/analysis_grade.js'), 'utf8');

function node(tag, attrs = {}, ...children) {
  const handlers = new Map();
  const item = {
    tag, attrs: { ...attrs }, text: attrs.text || '', value: attrs.value || '',
    hidden: Object.hasOwn(attrs, 'hidden'), title: attrs.title || '', dataset: {}, style: {},
    classList: { toggle() {} },
    get textContent() { return this.text + this.children.map(child => child.textContent).join(''); },
    set textContent(value) { this.text = value; this.children = []; },
    append(...nodes) { this.children.push(...nodes.flat(Infinity).filter(Boolean)); },
    appendChild(child) { this.append(child); },
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); },
    setAttribute(name, value) { this.attrs[name] = value; },
    addEventListener(type, handler) {
      if (!handlers.has(type)) handlers.set(type, []);
      handlers.get(type).push(handler);
    },
    emit(type) { for (const handler of handlers.get(type) || []) handler(); },
    descendants(predicate) {
      return this.children.flatMap(child => [
        ...(predicate(child) ? [child] : []), ...child.descendants(predicate),
      ]);
    },
    querySelector(selector) {
      return this.descendants(child => child.attrs.class?.split(' ').includes(selector.slice(1)))[0] || null;
    },
    querySelectorAll(selector) { return this.descendants(child => selector.split(',').includes(child.tag)); },
    children: [],
  };
  item.append(...children);
  return item;
}

const combo = { comparison: 'Treatment vs control', outcome: 'Reading', timepoint: 'Week 8' };
const signals = {
  rob: { available: true, rule: 'judged studies', affected_studies: 2 },
  publication_bias: { available: false, reason: '研究不足' },
};
const synthesis = {
  model: 'random', n_studies: 3, pooled: 0.42, ci_low: 0.2, ci_high: 0.64,
  i2_percent: 14, publication_bias_test_available: false,
};
const translations = new Map([
  ['偏倚风险', 'Risk of bias'], ['规则 {0}', 'Rule {0}'], ['{0}：{1}', '{0}: {1}'],
]);

function makeEnv(assessments = [], options = {}) {
  const calls = [];
  const storage = options.storage || new Map();
  const drafts = new Map();
  const document = node('document');
  const analysis = node('main');
  const synthesisCard = { after: panel => analysis.append(panel) };
  document.head = node('head');
  document.getElementById = id => id === 'anJumpEffects' ? synthesisCard : null;
  document.querySelector = selector => selector === '.page[data-p="analysis"]' ? analysis : null;
  document.createElement = node;
  let language = 'zh';
  const sandbox = {
    document, el: node,
    S: { task: { task_id: options.taskId || 'T1' }, taskVersion: 1, user: { username: options.username || 'reviewer' } },
    localStorage: {getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    WorkflowControls: {registerDraft:(name,guard)=>drafts.set(name,guard)},
    anEffects: [{ ...combo, study_id: 'S1', measure: 'SMD' }],
    T: (key, ...args) => (language === 'en' ? translations.get(key) || key : key)
      .replace(/\{(\d+)\}/g, (placeholder, i) => args[i] ?? placeholder),
    taskApi: () => '/api/tasks/T1',
    api: async (url, options) => {
      calls.push({ url, options });
      if (url.endsWith('/analysis/grade/export?format=sof_table')) return { assessments };
      if (url.includes('/analysis/grade/auto-suggest?')) {
        return { downgrades: { rob: 'serious' }, signal_sources: signals, synthesis, final_quality: 'moderate' };
      }
      if (url.endsWith('/analysis/grade/save')) return { final_quality: 'moderate' };
      throw new Error('unexpected request: ' + url);
    },
  };
  sandbox.window = sandbox;
  vm.runInNewContext(source, sandbox, { filename: 'analysis_grade.js' });
  const row = () => {
    const current = analysis.querySelector('.rf-grade-row');
    assert.ok(current, 'GRADE row is available');
    return current;
  };
  return {
    row, calls, storage, drafts, sandbox, analysis,
    emit: event => document.emit('reviewflow:' + event),
    switchLanguage() {
      language = 'en';
      document.emit('reviewflow:language-changed');
    },
    click(text) {
      const [button] = row().descendants(child => child.tag === 'button' && child.textContent === text);
      assert.ok(button && !button.disabled, text);
      button.emit('click');
    },
  };
}

const settle = () => new Promise(resolve => setImmediate(resolve));
const rationale = row => row.querySelector('.rf-grade-rationale');
const riskTooltip = row => row.descendants(child => child.tag === 'span' && child.textContent === 'ⓘ')[0];

test('GRADE rationale and tooltip survive save, analysis refresh and language change', async () => {
  const env = makeEnv();
  env.emit('analysis-loaded');
  await settle();
  env.click('自动建议');
  await settle();

  const initialRow = env.row();
  assert.equal(rationale(initialRow).hidden, false);
  assert.match(rationale(initialRow).textContent, /合成口径：random 模型 · 3 项研究/);
  assert.match(rationale(initialRow).textContent, /发表偏倚检验不可用/);
  assert.equal(riskTooltip(initialRow).hidden, false);
  assert.match(riskTooltip(initialRow).title, /偏倚风险：polish.affected_count: 2/);
  assert.equal(riskTooltip(initialRow).attrs['aria-label'], riskTooltip(initialRow).title);

  env.click('保存评估');
  await settle();
  const save = env.calls.find(call => call.url.endsWith('/analysis/grade/save'));
  assert.equal(save.options.method, 'POST');
  assert.equal(save.options.body.signal_sources.rob, signals.rob);
  assert.equal(save.options.body.signal_sources.publication_bias, signals.publication_bias);
  assert.equal(Object.hasOwn(save.options.body, 'synthesis'), false);

  env.emit('analysis-core-loaded');
  assert.notEqual(env.row(), initialRow);
  assert.equal(rationale(env.row()).hidden, false);
  assert.match(rationale(env.row()).textContent, /合成口径：random 模型 · 3 项研究/);
  assert.match(riskTooltip(env.row()).title, /polish.affected_count: 2/);

  env.switchLanguage();
  assert.match(rationale(env.row()).textContent, /Risk of bias: polish.affected_count: 2/);
  assert.equal(riskTooltip(env.row()).attrs['aria-label'], 'Risk of bias: polish.affected_count: 2');
  assert.equal(env.calls.filter(call => call.url.includes('/auto-suggest?')).length, 1);
});

test('GRADE rebuilds saved signal sources without fabricating a synthesis line', async () => {
  const env = makeEnv([{ ...combo, assessor: 'reviewer', downgrades: { rob: 'serious' }, signal_sources: signals }]);
  env.emit('analysis-loaded');
  await settle();

  const row = env.row();
  assert.equal(rationale(row).hidden, false);
  assert.match(rationale(row).textContent, /偏倚风险：polish.affected_count: 2/);
  assert.match(rationale(row).textContent, /发表偏倚：研究不足/);
  assert.doesNotMatch(rationale(row).textContent, /合成口径|合并效应/);
  assert.equal(riskTooltip(row).hidden, false);
  assert.match(riskTooltip(row).title, /polish.affected_count: 2/);
  assert.equal(env.calls.length, 1, '仅读取已保存的评估');
});

test('GRADE leaves rationale hidden without a saved factor signal', async () => {
  const env = makeEnv([{ ...combo, signal_sources: { initial_design: 'rct' } }]);
  assert.equal(rationale(env.row()).hidden, true);
  env.emit('analysis-loaded');
  await settle();
  assert.equal(rationale(env.row()).hidden, true);
  assert.equal(riskTooltip(env.row()).hidden, true);
});

test('GRADE draft reload and guard are isolated by task and account; save clears the draft', async () => {
  const env=makeEnv();env.emit('analysis-loaded');await settle();
  const note=env.row().querySelector('.rf-grade-note');note.value='Unsaved rationale';note.emit('input');
  assert.equal(env.drafts.get('grade').dirty(),true);
  assert.equal(env.storage.size,1);
  const again=makeEnv([], {storage:env.storage});again.emit('analysis-loaded');await settle();
  assert.equal(again.row().querySelector('.rf-grade-note').value,'Unsaved rationale');
  assert.equal(again.drafts.get('grade').dirty(),true);
  const otherAccount=makeEnv([], {storage:env.storage,username:'other'});
  const otherTask=makeEnv([], {storage:env.storage,taskId:'T2'});
  assert.equal(otherAccount.row().querySelector('.rf-grade-note').value,'');
  assert.equal(otherTask.row().querySelector('.rf-grade-note').value,'');
  assert.equal(await again.drafts.get('grade').save(),true);
  assert.equal(again.drafts.get('grade').dirty(),false);
  assert.equal(env.storage.size,0);
});

test('GRADE human note stays separate and repeated saves do not append machine summaries', async () => {
  const env=makeEnv([{...combo,assessor:'reviewer',override_notes:'Researcher note',signal_sources:{...signals,_reviewflow_notes:{version:1,human_note:'Researcher note',legacy_note:'',overrides:{rob:{auto:'very_serious',user:'serious'}}}}}]);
  env.emit('analysis-loaded');await settle();
  assert.equal(env.row().querySelector('.rf-grade-note').value,'Researcher note');
  env.click('自动建议');await settle();
  const risk=env.row().descendants(child=>child.tag==='select'&&child.attrs['aria-label']==='偏倚风险')[0];
  risk.value='very_serious';risk.emit('change');
  env.click('保存评估');await settle();env.click('保存评估');await settle();
  const saves=env.calls.filter(call=>call.url.endsWith('/grade/save'));
  assert.equal(saves.length,2);
  for(const call of saves){
    assert.equal(call.options.body.override_notes,'Researcher note');
    assert.equal(call.options.body.signal_sources._reviewflow_notes.human_note,'Researcher note');
    assert.equal(call.options.body.signal_sources._reviewflow_notes.overrides.rob.user,'very_serious');
  }
});

test('GRADE failed saves and changes typed during a successful save remain guarded', async () => {
  const env=makeEnv();env.emit('analysis-loaded');await settle();
  const note=env.row().querySelector('.rf-grade-note');note.value='Before';note.emit('input');
  let resolve;env.sandbox.api=()=>new Promise(done=>{resolve=done;});
  env.click('保存评估');assert.equal(env.drafts.get('grade').saving(),true);
  note.value='After';note.emit('input');resolve({final_quality:'high'});await settle();
  assert.equal(env.drafts.get('grade').dirty(),true);
  assert.equal(env.drafts.get('grade').saving(),false);
  env.sandbox.api=async()=>{throw new Error('offline');};
  assert.equal(await env.drafts.get('grade').save(),false);
  assert.equal(env.drafts.get('grade').dirty(),true);
  assert.equal(JSON.parse([...env.storage.values()][0]).note,'After');
});
