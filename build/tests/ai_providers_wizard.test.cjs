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
    ,{ id:'multi', name_key:'Multi', kinds:['llm'], formats:[{format:'openai_chat',base_url:'https://multi.test/v1',models:['chat']},{format:'anthropic',base_url:'https://multi.test/messages',models:['native']}] }
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
  getAttribute(name) { return this[name]; }
  querySelector() { return this.children.flatMap(child => child.children).find(child => child?.checked); }
  querySelectorAll(selector) { return this.children.flatMap(child => child instanceof Element ? [...(selector === '[data-rf-wizard]' && child['data-rf-wizard'] ? [child] : []), ...child.querySelectorAll(selector)] : []); }
  focus() {}
}
function setup({ mode = 'server', reject = false, runtimeReject = false, runTest = async () => ({ latency_ms:23 }) } = {}) {
  const calls = [], locales = [], toasts = [];
  const context = {
    window:{}, T:(key, ...args) => key.replace(/\{(\d+)\}/g, (_, n) => args[n]),
    toast:(...args) => toasts.push(args), el:(...args) => new Element(...args),
    RFLang:{register:rows => locales.push(...rows)},
    api:async (url, opts) => {
      calls.push([url,opts?.method]);
      if (url === '/api/ai/providers') { if (typeof reject === 'function' ? reject() : reject) throw Error('Unavailable'); return catalog; }
      if (url === '/api/runtime') { if (runtimeReject) throw Error('Unavailable'); return { mode }; }
      return runTest();
    }
  };
  for (const file of ['ai_providers_locale.js','ai_providers_catalog.js']) {
    vm.runInNewContext(fs.readFileSync(path.join(dir,file),'utf8'), context);
  }
  const ui = context.window.RFAIProviders;
  function mount(kind = 'llm') {
    const fields = {provider:new Element('input'), baseUrl:new Element('input'), model:new Element('input')};
    const wizard = ui.mount({kind,...fields,draftName:kind + '_format'});
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

test('a provider with one supported format applies directly; custom selection preserves fields', async () => {
  const { ui, mount } = setup();
  const wizard = mount();
  await ui.load();
  wizard.select.value = 'deepseek';
  wizard.select.events.change();
  assert.equal(wizard.fields.provider.value, 'deepseek');
  assert.equal(wizard.fields.baseUrl.value, 'https://example.test/v1');
  assert.equal(wizard.fields.model.value, 'deepseek-chat');
  assert.deepEqual(wizard.datalist.children.map(x => x.value), ['deepseek-chat','deepseek-reasoner']);
  assert.equal(wizard.chooser.hidden,true);
  wizard.format.events.click();
  assert.equal(wizard.chooser.children[2].children[0].disabled,true);
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
  assert.equal(wizard.chooser.children[2].children[0].value,'systemone');
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
  assert.equal(status.textContent,'已保存配置连接正常 · 延迟 31 ms');
  assert.equal(button.textContent,'测试已保存配置');
  assert.equal(button.disabled,false);
  attempt = button.events.click();
  reject(Error('Fixture failure'));
  await attempt;
  assert.match(status.textContent,/Fixture failure/);
  assert.equal(toasts.length,1);
});

test('cancel keeps the selected provider and reopening uses its formats and defaults', async () => {
  const {ui,mount} = setup(), wizard = mount(); await ui.load();
  wizard.select.value = 'multi'; wizard.select.events.change();
  wizard.chooser.children.at(-1).events.click();
  assert.equal(wizard.select.value,'multi');
  assert.throws(()=>wizard.getFormat(),/请先确认/);
  wizard.format.events.click();
  assert.equal(wizard.chooser.children.length,5);
  wizard.chooser.children.at(-2).events.click();
  assert.equal(wizard.fields.provider.value,'multi');
  assert.equal(wizard.fields.baseUrl.value,'https://multi.test/v1');
});

test('changing format preserves edited endpoint and model, and manual provider preserves protocol', async () => {
  const {ui,mount} = setup(), wizard = mount(); await ui.load();
  wizard.select.value = 'multi'; wizard.select.events.change(); wizard.chooser.children.at(-2).events.click();
  wizard.fields.baseUrl.value = 'https://gateway.test'; wizard.fields.model.value = 'custom-model';
  wizard.format.events.click();
  const native = wizard.chooser.children[2].children[0]; native.checked = true; native.events.change();
  wizard.chooser.children.at(-2).events.click();
  assert.equal(wizard.fields.baseUrl.value,'https://gateway.test'); assert.equal(wizard.fields.model.value,'custom-model');
  assert.equal(wizard.getFormat(),'anthropic');
  wizard.fields.provider.value = 'My Gateway'; wizard.fields.provider.events.input();
  assert.equal(wizard.getFormat(),'anthropic'); assert.equal(wizard.select.value,'');
});

test('snapshot restores selected provider, pending radio and open chooser after language rerender', async () => {
  const {ui,mount} = setup(), first = mount(); await ui.load();
  first.select.value = 'multi'; first.select.events.change();
  const native = first.chooser.children[2].children[0]; native.checked = true; native.events.change();
  const snap = ui.snapshotWizards(new Element('div',{},first.element));
  const next = mount(); ui.restoreWizards(new Element('div',{},next.element),snap); await flush();
  assert.equal(next.select.value,'multi'); assert.equal(next.chooser.hidden,false);
  assert.equal(next.chooser.children[2].children[0].checked,true);
  next.chooser.children.at(-2).events.click();
  assert.equal(next.getFormat(),'anthropic'); assert.equal(next.fields.model.value,'native');
});

test('catalog failure exposes retry and runtime failure explains the restricted providers', async () => {
  let unavailable = true;
  const {ui,mount,calls} = setup({reject:()=>unavailable}), wizard = mount(); await flush();
  const status = wizard.element.children.at(-1);
  assert.match(status.children[0].text,/目录暂不可用/);
  unavailable = false; status.children[1].events.click(); await flush();
  assert.equal(wizard.label.hidden,false);
  assert.equal(calls.filter(call=>call[0] === '/api/ai/providers').length,2);
  const failedRuntime = setup({runtimeReject:true}), other = failedRuntime.mount(); await failedRuntime.ui.load();
  assert.match(other.element.children.at(-1).children[0].text,/运行模式暂不可用/);
});

test('wizard locale rows populate all ten languages and expose all provider aliases', () => {
  const {locales} = setup();
  assert.ok(locales.every(row => row.length === 10 && [...row].every(Boolean)));
  assert.equal(locales.filter(row => row.aliases?.[0].startsWith('ai_providers.')).length,14);
});
