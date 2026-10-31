/* W1 · zotero_import.js 行为钉子（node --test；真实加载模块 + 受控桩）。
 *
 * 钉子=破坏即红：
 * - 真实 require 模块（node 下 IIFE 无 DOM/RFLang 副作用、可安全加载）；
 * - 十语言完整性：每条词条恰 10 列、非空、{N} 占位符跨语言一致（撤列/漏译即红）；
 * - 无死词条：表内每个中文源串都必须被代码真实引用（T()/CODE_TEXT/模板），
 *   且代码引用的每个 T() 键都必须在表内（双向闭包）；
 * - 错误分流：连接类失败给修复指引；其余按 code 映射到已注册文案，
 *   **绝不透传后端中文**——R4#3 回归钉子：en 界面下错误文本无 CJK 残留；
 * - 集合层级：parent 链深度（含环防御与孤儿集合兜底）；
 * - 选中摘要：过滤未知键、累计 num_items（未知计 0）；
 * - 导入摘要：staged 与直入两形态的模板与参数；
 * - 占位填充：{0}/{1} 与主应用 T() 同语义。
 */
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const U = require('../../custom_frontend/zotero_import.js');

const SRC_PATH = path.join(__dirname, '../../custom_frontend/zotero_import.js');
const CJK = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;

/* 与词典回放工具（test_r7_frontsec_fixes.py）同口径：从源码提取
 * RFLang.register([...]) 的字面量数组——保证钉的是出货文本本身。 */
function extractStrings() {
  const src = fs.readFileSync(SRC_PATH, 'utf8');
  const m = /RFLang\s*\.\s*register\s*\(\s*\[/.exec(src);
  assert.ok(m, 'RFLang.register([ 字面量必须存在于源码（变量形态会破坏词典回放）');
  const start = m.index + m[0].length - 1;
  let depth = 0;
  for (let i = start; i < src.length; i += 1) {
    if (src[i] === '[') depth += 1;
    else if (src[i] === ']') {
      depth -= 1;
      if (depth === 0) return Function('return ' + src.slice(start, i + 1))();
    }
  }
  assert.fail('literal array is not balanced');
}

/* 极简 T()：词典命中原样返回，未命中返回调用方渲染值（与 index.html 语义一致）。 */
function makeT(rows) {
  const dict = new Map(rows.map((row) => [row[0], row[1]]));
  return (tpl, ...args) => {
    const out = dict.has(tpl) ? dict.get(tpl) : tpl;
    return String(out).replace(/\{(\d+)\}/g, (_, i) =>
      args[Number(i)] === undefined || args[Number(i)] === null ? '' : String(args[Number(i)]));
  };
}

test('module loads under node with zero DOM side effects and exports the pure API', () => {
  assert.equal(typeof U, 'object');
  for (const name of ['isConnectFailure', 'errorText', 'collectionDepths',
    'selectionSummary', 'importSummary', 'fill']) {
    assert.equal(typeof U[name], 'function', `${name} must be a function export`);
  }
});

test('ten-language rows: exactly 10 non-empty columns with consistent {N} placeholders', () => {
  const rows = extractStrings();
  assert.ok(rows.length >= 20, `expected the full string table, got ${rows.length} rows`);
  for (const row of rows) {
    assert.equal(row.length, 10, `row must have 10 languages: ${row[0]}`);
    for (const value of row) {
      assert.ok(typeof value === 'string' && value.trim(), 'no empty translation');
      assert.deepEqual((value.match(/\{\d+\}/g) || []).sort(),
        (row[1].match(/\{\d+\}/g) || []).sort(),
        `placeholder mismatch in: ${value}`);
    }
  }
});

const BACKEND_CODES = ['zotero_unreachable', 'zotero_timeout', 'zotero_redirect',
  'zotero_no_items', 'zotero_collection_not_found', 'zotero_too_many_items',
  'zotero_bad_response', 'zotero_http_error', 'zotero_response_too_large',
  'zotero_bad_collection_key', 'zotero_bad_path'];

test('no dead rows: every source string is referenced, every T() key is registered', () => {
  const rows = extractStrings();
  const full = fs.readFileSync(SRC_PATH, 'utf8');
  const keys = new Set(rows.map((row) => row[0]));

  /* R5#3：必须先**剥掉注册表本身**再扫引用——每个词条在表里本就以引号书写，
     直接在全文里 includes 会让 referenced 恒等于全部键（旧钉子恒绿的根因）。
     剥离口径：从 RFLang.register([ 到配对的 ]); 整段删掉，只留代码正文。 */
  const regStart = full.indexOf('RFLang.register([');
  assert.ok(regStart > 0, 'RFLang.register([ 必须存在于源码');
  let depth = 0, regEnd = -1;
  for (let i = full.indexOf('[', regStart); i < full.length; i += 1) {
    if (full[i] === '[') depth += 1;
    else if (full[i] === ']') {
      depth -= 1;
      if (depth === 0) { regEnd = i + 1; break; }
    }
  }
  assert.ok(regEnd > 0, 'register 字面量必须闭合');
  const body = full.slice(0, regStart) + full.slice(regEnd);
  assert.ok(body.length < full.length, '注册表必须被真正剥离');

  /* 代码正文中必须能找到每个中文源串：T('…') 调用、CODE_TEXT 映射值、
     errorText/importSummary 的返回字面量，三种形态都落在正文里。 */
  const dead = [...keys].filter((key) => !body.includes(key));
  assert.deepEqual(dead, [], `未使用的词条必须删除：${dead.join(' / ')}`);

  /* 反向：T() 字面量键必须全部注册（漏注册会在非中文界面显示原文） */
  const tCalls = [...body.matchAll(/\bT\('([^']*)'/g)].map((m) => m[1]);
  const missing = [...new Set(tCalls)].filter((key) => !keys.has(key));
  assert.deepEqual(missing, [], `T() 使用了未注册的键：${missing.join(' / ')}`);
});

test('dead-row detector really bites (inject a dead row, must be caught)', () => {
  /* 破坏即红自检：把注入死词条的源码喂给同一检测逻辑，必须检出。
     这条钉子存在的意义就是防止上面那条再次退化成恒真。 */
  const full = fs.readFileSync(SRC_PATH, 'utf8');
  const regStart = full.indexOf('RFLang.register([');
  const probe = '["这条词条永远没人用", "unused", "inutile", "не используется", '
    + '"no usado", "未使用", "não usado", "ungenutzt", "неискоришћено", "미사용"],';
  const poisoned = full.slice(0, full.indexOf('\n', regStart) + 1) + probe
    + full.slice(full.indexOf('\n', regStart) + 1);

  const rows = (() => {
    const m = /RFLang\s*\.\s*register\s*\(\s*\[/.exec(poisoned);
    const start = m.index + m[0].length - 1;
    let d = 0;
    for (let i = start; i < poisoned.length; i += 1) {
      if (poisoned[i] === '[') d += 1;
      else if (poisoned[i] === ']') {
        d -= 1;
        if (d === 0) return Function('return ' + poisoned.slice(start, i + 1))();
      }
    }
    return [];
  })();
  const keys = rows.map((row) => row[0]);
  assert.ok(keys.includes('这条词条永远没人用'), '注入必须先被解析进表');
  const regStart2 = poisoned.indexOf('RFLang.register([');
  let depth2 = 0, regEnd2 = -1;
  for (let i = poisoned.indexOf('[', regStart2); i < poisoned.length; i += 1) {
    if (poisoned[i] === '[') depth2 += 1;
    else if (poisoned[i] === ']') {
      depth2 -= 1;
      if (depth2 === 0) { regEnd2 = i + 1; break; }
    }
  }
  const body = poisoned.slice(0, regStart2) + poisoned.slice(regEnd2);
  const dead = keys.filter((key) => !body.includes(key));
  assert.deepEqual(dead, ['这条词条永远没人用'], '死词条检测必须真的咬住');
});

test('every backend error code renders non-CJK text in English (R4#3 i18n leak nail)', () => {
  const rows = extractStrings();
  const T = makeT(rows);
  for (const code of BACKEND_CODES) {
    const raw = U.errorText({ code, message: 'Zotero 本地 API HTTP 404。' }, '');
    assert.ok(raw, `${code} must render something`);
    const en = T(raw);
    assert.equal(CJK.test(en), false,
      `${code} 在英文界面泄漏中文：${en}`);
    assert.ok(en.trim(), `${code} must render non-empty text`);
  }
  /* 未登记的 code 也不得拼接后端中文原文 */
  const unknown = T(U.errorText({ code: 'zotero_totally_new', message: '服务端中文' }, ''));
  assert.equal(CJK.test(unknown), false, `未登记 code 泄漏中文：${unknown}`);
});

test('connection failures map to repair guidance, not raw error text', () => {
  for (const code of ['zotero_unreachable', 'zotero_timeout', 'zotero_redirect']) {
    assert.equal(U.isConnectFailure({ code }), true, code);
    const text = U.errorText({ code, message: 'Connection refused' });
    assert.ok(text.indexOf('Zotero 7') >= 0, 'guidance mentions Zotero 7');
    assert.ok(text.indexOf('设置') >= 0 && text.indexOf('重试') >= 0,
      'guidance points to the Zotero setting and suggests retry');
    assert.ok(text.indexOf('Connection refused') === -1, 'raw error must not leak');
  }
  assert.equal(U.isConnectFailure({ code: 'zotero_no_items' }), false);
  assert.equal(U.isConnectFailure(null), false);
});

test('no_items and unknown-code errors use registered strings only', () => {
  assert.equal(U.errorText({ code: 'zotero_no_items' }),
    '所选集合没有可导入的文献条目。');
  /* R4#3：不再把后端原文拼进模板——未登记 code 回落通用外壳（本身是注册词条） */
  assert.equal(U.errorText({ code: 'other', message: 'boom' }),
    '从本机 Zotero 读取失败，请稍后重试或改用导出文件导入。');
  assert.equal(U.errorText(null, 'fallback'),
    '从本机 Zotero 读取失败，请稍后重试或改用导出文件导入。');
});

test('collectionDepths follows parent chains with cycle and orphan guards', () => {
  const rows = [
    { key: 'ROOT1', name: 'root', parent: null },
    { key: 'CHILD1', name: 'child', parent: 'ROOT1' },
    { key: 'GRAND1', name: 'grand', parent: 'CHILD1' },
    { key: 'ORPHAN', name: 'orphan', parent: 'MISSING' },
    { key: 'CY1', name: 'a', parent: 'CY2' }, { key: 'CY2', name: 'b', parent: 'CY1' },
  ];
  const depths = U.collectionDepths(rows);
  assert.equal(depths.ROOT1, 0);
  assert.equal(depths.CHILD1, 1);
  assert.equal(depths.GRAND1, 2);
  assert.equal(depths.ORPHAN, 0, 'orphan parent falls back to depth 0');
  assert.ok(depths.CY1 <= 12 && depths.CY2 <= 12, 'cycle is bounded, never loops');
  assert.deepEqual(U.collectionDepths([]), {});
});

test('selectionSummary filters unknown keys and sums num_items (unknown counts 0)', () => {
  const collections = [
    { key: 'A', num_items: 3 }, { key: 'B', num_items: 2 }, { key: 'C' },
  ];
  assert.deepEqual(U.selectionSummary(['A', 'B', 'NOPE'], collections),
    { count: 2, items: 5, keys: ['A', 'B'] });
  assert.deepEqual(U.selectionSummary(['C'], collections), { count: 1, items: 0, keys: ['C'] });
  assert.deepEqual(U.selectionSummary([], collections), { count: 0, items: 0, keys: [] });
  assert.deepEqual(U.selectionSummary(null, null), { count: 0, items: 0, keys: [] });
});

test('importSummary distinguishes staged and direct-import response shapes', () => {
  const staged = U.importSummary({ staged: true, total_parsed: 12, imported: 0 });
  assert.equal(staged.template, '导入已暂存：解析 {0} 条，待核验后激活。');
  assert.deepEqual(staged.args, [12]);
  const direct = U.importSummary({
    staged: false, total_parsed: 10, exact_duplicate_excess: 2, imported: 8,
  });
  assert.equal(direct.template, '导入完成：解析 {0} 条，精确去重 {1} 条，导入 {2} 条。');
  assert.deepEqual(direct.args, [10, 2, 8]);
  // 缺字段兜底为 0，不产生 NaN
  assert.deepEqual(U.importSummary({}).args, [0, 0, 0]);
});

test('fill substitutes {N} placeholders like the app T()', () => {
  assert.equal(U.fill('a {0} b {1}', ['x', 'y']), 'a x b y');
  assert.equal(U.fill('导入 {0} 条', [7]), '导入 7 条');
  assert.equal(U.fill('{0}-{1}', ['only']), 'only-');
  assert.equal(U.fill(null, []), '');
});
