'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const dir = process.env.RF_WIZARD_SOURCE || path.join(__dirname, '../../custom_frontend');
const catalog = {
  supported_formats: { openai_chat: true, openai_responses: false, anthropic: true, systemone: true },
  providers: [
    { id:'deepseek', name_key:'ai_providers.deepseek', kinds:['llm'], formats:[
      { format:'openai_chat', base_url:'https://example.test/v1', models:['deepseek-chat','deepseek-reasoner'] },
      { format:'openai_responses', base_url:'https://example.test/v2', models:[] }
    ] },
    { id:'local_ollama', name_key:'ai_providers.local_ollama', desktop_only:true, kinds:['llm'], formats:[{format:'openai_chat'}] },
    { id:'commandcode_goat', name_key:'ai_providers.commandcode_goat', kinds:['decision'], formats:[{format:'systemone'}] }
  ]
};
class Element {
  constructor(tag, attrs = {}, ...children) {
    this.tag = tag; this.children = children; this.events = {};
    this.value = ''; Object.assign(this, attrs);
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(event, listener) { this.events[event] = listener; }
  setAttribute(name, value) { this[name] = value; }
  querySelector() { return this.children.flatMap(child => child.children).find(child => child?.checked); }
  focus() {}
}
function setup({ mode = 'server', reject = false, runTest = async () => ({ latency_ms:23 }) } = {}) {
  const calls = [], locales = [], toasts = [];
  const context = {
    window:{}, T:(key, ...args) => key.replace(/\{(\d+)\}/g, (_, n) => args[n]),
    toast:(...args) => toasts.push(args), el:(...args) => new Element(...args),
    RFLang:{register:rows => locales.push(...rows)},
    api:async (url, opts) => {
      calls.push([url,opts?.method]);
      if (url === '/api/ai/providers') { if (reject) throw Error('Unavailable'); return catalog; }
      if (url === '/api/runtime') return { mode };
      return runTest();
    }
  };
  for (const file of ['ai_providers_locale.js','ai_providers_catalog.js']) {
    vm.runInNewContext(fs.readFileSync(path.join(dir,file),'utf8'), context);
  }
  const ui = context.window.RFAIProviders;
  function mount(kind = 'llm') {
    const fields = {provider:new Element('input'), baseUrl:new Element('input'), model:new Element('input')};
    const wizard = ui.mount({kind,...fields});
    const [label, format, chooser, datalist] = wizard.element.children;
    return { ...wizard, fields, label, format, chooser, datalist, select:label.children[1] };
  }
  return { ui, calls, locales, toasts, mount };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('concurrent forms share one directory request and filter providers by slot and deployment', async () => {
  const { ui, mount, calls } = setup();
  const general = mount(), decision = mount('decision');
  await ui.load();
  assert.deepEqual(calls, [['/api/ai/providers',undefined],['/api/runtime',undefined]]);
  assert.ok(general.select.children.find(x => x.value === 'local_ollama').disabled);
  assert.ok(general.select.children.find(x => x.value === 'local_ollama').text.endsWith('仅桌面版'));
  assert.ok(!general.select.children.some(x => x.value === 'commandcode_goat'));
  assert.ok(decision.select.children.some(x => x.value === 'commandcode_goat'));
  assert.equal(decision.getFormat(), 'systemone');
  assert.equal(general.select.children.at(-1).value, '');
  const desktop = setup({mode:'desktop'});
  const wizard = desktop.mount();
  await desktop.ui.load();
  assert.equal(wizard.select.children.find(x => x.value === 'local_ollama').disabled, false);
});

test('provider selection gates unsupported formats, applies defaults on confirmation and preserves custom fields', async () => {
  const { ui, mount } = setup();
  const wizard = mount();
  await ui.load();
  wizard.select.value = 'deepseek';
  wizard.select.events.change();
  assert.equal(wizard.fields.provider.value, '');
  const [legend, first, second, confirm] = wizard.chooser.children;
  assert.equal(legend.text,'选择 API 格式');
  assert.equal(first.children[0].checked, true);
  assert.equal(second.children[0].disabled, true);
  confirm.events.click();
  assert.equal(wizard.fields.provider.value, 'deepseek');
  assert.equal(wizard.fields.baseUrl.value, 'https://example.test/v1');
  assert.equal(wizard.fields.model.value, 'deepseek-chat');
  assert.deepEqual(wizard.datalist.children.map(x => x.value), ['deepseek-chat','deepseek-reasoner']);
  wizard.select.value = '';
  wizard.select.events.change();
  wizard.chooser.children.at(-2).events.click();
  wizard.fields.provider.value = 'other';
  wizard.fields.provider.events.input();
  assert.equal(wizard.fields.baseUrl.value, 'https://example.test/v1');
  assert.equal(wizard.getFormat(), 'openai_chat');
  assert.equal(wizard.datalist.children.length, 0);
});

test('directory failure keeps manual protocol selection available', async () => {
  const { mount } = setup({ reject:true });
  const wizard = mount('decision');
  await flush();
  assert.equal(wizard.label.hidden,true);
  wizard.format.events.click();
  assert.equal(wizard.chooser.children[1].children[0].value,'systemone');
  assert.equal(wizard.getFormat(),'systemone');
});

test('kind-scoped connection test disables control in flight and reports success and failure locally', async () => {
  let resolve, reject;
  const { ui, calls, toasts } = setup({runTest:() => new Promise((ok, fail) => { resolve = ok; reject = fail; })});
  const status = new Element('p');
  const endpoint = '/api/tasks/example/ai-suggest/shared-key';
  const button = ui.testButton({endpoint,kind:'decision',status});
  let attempt = button.events.click();
  assert.equal(button.disabled,true);
  assert.equal(button.textContent,'测试中…');
  assert.deepEqual(calls[0],[endpoint + '/test?kind=decision','POST']);
  resolve({latency_ms:31});
  await attempt;
  assert.equal(status.textContent,'连接正常 · 延迟 31 ms');
  assert.equal(button.disabled,false);
  attempt = button.events.click();
  reject(Error('Fixture failure'));
  await attempt;
  assert.match(status.textContent,/Fixture failure/);
  assert.equal(toasts.length,1);
});

test('wizard locale rows populate all ten languages and expose all provider aliases', () => {
  const {locales} = setup();
  assert.ok(locales.every(row => row.length === 10 && [...row].every(Boolean)));
  assert.equal(locales.filter(row => row.aliases?.[0].startsWith('ai_providers.')).length,14);
});
