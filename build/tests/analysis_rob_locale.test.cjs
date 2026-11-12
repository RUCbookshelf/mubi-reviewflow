/* review67 L-38 / F1：用实际 API 错误解析和词典注册验证结构化响应。 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const index = read('custom_frontend/index.html');
const locale = read('custom_frontend/analysis_rob_locale.js');
const contractCodes = ['coscreen/risk_of_bias_contract.py', 'coscreen/rob_storage.py']
  .flatMap(file => [...read(file).matchAll(/RobContractError\(\s*["']([a-z_]+)["']/g)].map(match => match[1]));

function run(language, status, detail) {
  const rows = [], dictionary = new Map(), sources = {};
  const context = vm.createContext({
    window: {}, AUTH_TOKEN: null, API_BASE: '',
    fetch: async () => ({ ok:false, status, json:async () => ({ detail }) }),
    RFLang: {
      register(entries) {
        for (const row of entries) {
          rows.push(row);
          for (const key of row) dictionary.set(key, row);
        }
      },
      has: key => dictionary.has(key),
      t: (key, ...args) => context.interpolate(dictionary.get(key)?.[language] || key, args)
    },
    Error
  });
  const slice = (from, to) => {
    const start = index.indexOf(from);
    const end = index.indexOf(to, start + from.length);
    assert.ok(start >= 0 && end > start, from);
    return index.slice(start, end);
  };
  context.window.RFLang = context.RFLang;
  vm.runInContext(slice('function interpolate(template, args)', 'function rememberTemplate'), context);
  vm.runInContext(slice('function T(tpl, ...args)', 'function el('), context);
  const register = context.window.RFRegisterServerMessages;
  context.RFRegisterServerMessages = entries => {
    Object.assign(sources, entries);
    register(entries);
  };
  context.RFLang.register([[
    '你尚未获得参与本任务筛选的权限，请联系任务负责人开启「允许参与筛选」后再试。',
    ...Array(9).fill('Screening permission is required.')
  ]]);
  vm.runInContext(locale, context);
  vm.runInContext(slice('class ApiError extends Error', 'function clearStoredToken'), context);
  return { context, rows: rows.slice(1), sources };
}

test('55 个 RoB 错误均有十种译文和一致的命名占位符', () => {
  const { rows, sources } = run(0, 400, {});
  assert.equal(new Set(contractCodes).size, 55);
  assert.deepEqual(Object.keys(sources).sort(), [...new Set(contractCodes)].sort().map(code => 'analysis.rob.' + code));
  assert.equal(rows.length, 55);
  const placeholders = value => (value.match(/\{\w+\}/g) || []).sort();
  for (const row of rows) {
    assert.equal(row.length, 10, row[0]);
    for (const cell of row) {
      assert.ok(cell.trim(), row[0]);
      assert.deepEqual(placeholders(cell), placeholders(row[0]), row[0]);
    }
  }
});

test('错误解析保留 RoB 状态、键及参数，并用具名参数渲染本地文案', async () => {
  const { context } = run(1, 400, {
    error_code:'invalid_field', message_key:'analysis.rob.invalid_field',
    params:{ unused:'ignored', field:'result_id' }, message:'raw backend message'
  });
  await assert.rejects(context.apiRequest('/api/test'), error => {
    assert.equal(error.status, 400);
    assert.equal(error.errorCode, 'invalid_field');
    assert.equal(error.messageKey, 'analysis.rob.invalid_field');
    assert.equal(error.message, '“result_id” must contain text.');
    assert.equal(error.rfLocalized, true);
    return true;
  });
  assert.equal(context.window.RFServerMessage('team.screening_not_enabled'), 'Screening permission is required.');
  assert.match(context.window.RFServerMessage('analysis.rob.invalid_domain', { domain:'selection' }), /“selection”/);
  assert.match(context.window.RFServerMessage('analysis.rob.invalid_domain', { domains:['selection','reporting'] }), /“selection, reporting”/);
  assert.match(context.window.RFServerMessage('analysis.rob.unsupported_framework', { framework:'RoB X' }), /“RoB X”/);
  assert.equal(context.window.RFServerMessage('analysis.rob.unknown'), null);
});

test('RoB 409 与 403 不暴露后端技术串', async () => {
  for (const [status, code, expected] of [
    [409, 'revision_conflict', '评估版本已变更，请刷新后重试。'],
    [403, 'reviewer_forbidden', '当前评估者无权修改他人的独立评估。']
  ]) {
    const { context } = run(0, status, {
      error_code:code, message_key:'analysis.rob.' + code, params:{}, message:'raw backend message'
    });
    await assert.rejects(context.apiRequest('/api/test'), error => {
      assert.equal(error.status, status);
      assert.equal(error.message, expected);
      return true;
    });
  }
});

test('仅保留 422 的字段位置和类型供 F1 识别旧接口', async () => {
  const { context } = run(1, 422, [{
    type:'extra_forbidden', loc:['body','expected_pair_digest'], msg:'Extra inputs are not permitted', input:'value'
  }]);
  await assert.rejects(context.apiRequest('/api/test'), error => {
    assert.deepEqual(JSON.parse(JSON.stringify(error.validationIssues)), [
      { type:'extra_forbidden', loc:['body','expected_pair_digest'] }
    ]);
    return true;
  });
});
