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

function makeEnv(assessments = []) {
  const calls = [];
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
    S: { task: { task_id: 'T1' }, taskVersion: 1, user: { username: 'reviewer' } },
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
    row, calls,
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
  assert.match(riskTooltip(initialRow).title, /偏倚风险：规则 judged studies · affected_studies=2/);
  assert.equal(riskTooltip(initialRow).attrs['aria-label'], riskTooltip(initialRow).title);

  env.click('保存评估');
  await settle();
  const save = env.calls.find(call => call.url.endsWith('/analysis/grade/save'));
  assert.equal(save.options.method, 'POST');
  assert.equal(save.options.body.signal_sources, signals);
  assert.equal(Object.hasOwn(save.options.body, 'synthesis'), false);

  env.emit('analysis-core-loaded');
  assert.notEqual(env.row(), initialRow);
  assert.equal(rationale(env.row()).hidden, false);
  assert.match(rationale(env.row()).textContent, /合成口径：random 模型 · 3 项研究/);
  assert.match(riskTooltip(env.row()).title, /affected_studies=2/);

  env.switchLanguage();
  assert.match(rationale(env.row()).textContent, /Risk of bias: Rule judged studies · affected_studies=2/);
  assert.equal(riskTooltip(env.row()).attrs['aria-label'], 'Risk of bias: Rule judged studies · affected_studies=2');
  assert.equal(env.calls.filter(call => call.url.includes('/auto-suggest?')).length, 1);
});

test('GRADE rebuilds saved signal sources without fabricating a synthesis line', async () => {
  const env = makeEnv([{ ...combo, assessor: 'reviewer', downgrades: { rob: 'serious' }, signal_sources: signals }]);
  env.emit('analysis-loaded');
  await settle();

  const row = env.row();
  assert.equal(rationale(row).hidden, false);
  assert.match(rationale(row).textContent, /偏倚风险：规则 judged studies · affected_studies=2/);
  assert.match(rationale(row).textContent, /发表偏倚：研究不足/);
  assert.doesNotMatch(rationale(row).textContent, /合成口径|合并效应/);
  assert.equal(riskTooltip(row).hidden, false);
  assert.match(riskTooltip(row).title, /affected_studies=2/);
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
