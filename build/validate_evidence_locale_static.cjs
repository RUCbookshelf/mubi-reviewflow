'use strict';
/* 证据工作区多语言一致性门（UX 批准单 F48，r10-u5）。

   背景：F48 的两处具体缺口（报告关联与证据模块约 25 个用户可见串未注册、
   以及「尚无评估。」与注册键「暂无评估。」一字之差永不命中）已在前序批次修妥；
   缺的是**防复发**——字面量改了、注册键没跟着改时没有任何门会红。

   本门做三件事：
   1. 取**真实注册表**：用 vm 执行全部 custom_frontend/*_locale.js（RFLang.register
      打桩），再求值 index.html 的内联三张表（rows / APP_ROWS / PRISMA_ROWS，引擎
      启动时同样注册）。每行十槽（或 zh+en 两槽由引擎回填英文）且全非空、占位符
      {0}{1}… 跨槽一致。同键重复注册归 r7-frontsec MM5-03 的词典回放钉子，本门不重复判。
   2. 扫描证据工作区模块（报告关联与证据 / 证据上传 / 证据地图）里每一个含中日韩
      文字的字符串字面量（注释已剔除），必须能在注册表或 alias 命中；未注册即失败，
      并额外点名「只差尾部标点」的一字之差变体（F48 原病灶）。
   3. 报告扫描量，扫描器空转即失败（防门假绿）。

   用法（构建期与 CI 同一入口）：
     node build/validate_evidence_locale_static.cjs [<repo-root>]
   退出码 0 = 通过；1 = 失败（stderr 说明具体字面量与原因）。
*/

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(process.argv[2] || path.join(__dirname, '..'));
const frontend = path.join(root, 'custom_frontend');
const TIMEOUT_MS = Number(process.env.REVIEWFLOW_VALIDATOR_TIMEOUT_MS || 20000);

/* 参与扫描的模块：证据工作区。证据地图的词条落在 analysis_locale.js 与内联表，
   因此注册表 = 全部 *_locale.js ∪ index.html 内联三表。 */
const MODULES = ['report_evidence.js', 'workflow_evidence.js', 'evidence_mapper.js'];
const INLINE_TABLES = ['rows', 'APP_ROWS', 'PRISMA_ROWS'];
const PLACEHOLDER = /\{(\d+)\}/g;
const CJK = /[\u4e00-\u9fff]/;

const problems = [];

function fail(message) {
  process.stderr.write(message + '\n');
  process.exit(1);
}

function read(file) {
  const full = path.join(frontend, file);
  if (!fs.existsSync(full)) fail('缺少文件：custom_frontend/' + file);
  return fs.readFileSync(full, 'utf8');
}

function placeholders(text) {
  return [...String(text).matchAll(PLACEHOLDER)].map(match => match[1]).sort().join(',');
}

/* 槽位口径与 index.html 的 register() 一致：要么整行十槽（全部显式），要么
   zh+en 两槽（其余语言由引擎回填英文）。其他长度都是漏字，必须红。 */
function validateRow(row, file) {
  if (!Array.isArray(row)) return;
  if (row.length !== 10 && row.length !== 2) {
    problems.push(`${file} 词条有 ${row.length} 槽（须十槽，或 zh+en 两槽由引擎回填）：${String(row[0]).slice(0, 50)}`);
    return;
  }
  if (row.some(value => typeof value !== 'string' || !value.trim())) {
    problems.push(`${file} 词条有空槽：${String(row[0]).slice(0, 50)}`);
    return;
  }
  if (row.length !== 10) return;
  const primary = placeholders(row[1]);
  row.forEach((value, slot) => {
    if (slot && placeholders(value) !== primary) {
      problems.push(`${file} 占位符跨槽不一致（第 ${slot} 槽）：${String(row[0]).slice(0, 50)}`);
    }
  });
}

const keys = new Set();
const aliases = new Set();
let rowCount = 0;

function absorbRow(row, file) {
  validateRow(row, file);
  if (!Array.isArray(row) || row.length < 2) return;
  rowCount += 1;
  for (const value of row) if (typeof value === 'string') keys.add(value);
  if (Array.isArray(row.aliases)) {
    for (const alias of row.aliases) {
      if (typeof alias !== 'string' || !alias.trim()) {
        problems.push(`${file} 别名有空槽：${String(row[0]).slice(0, 50)}`);
        continue;
      }
      aliases.add(alias);
    }
  }
}

/* ---- 1a. *_locale.js：vm 执行（被注释掉的注册不算） ---- */
const localeFiles = fs.readdirSync(frontend).filter(name => name.endsWith('_locale.js')).sort();
for (const file of localeFiles) {
  const src = read(file);
  const sandbox = {
    console: { warn() {}, log() {}, error() {} },
    RFLang: {
      register(rows) {
        if (!Array.isArray(rows)) return;
        for (const row of rows) absorbRow(row, file);
      },
      t: value => value,
      get: () => 'zh-CN',
      index: () => 0,
      capture: () => null,
      render: value => value,
    },
    document: {
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {},
      removeEventListener() {},
      getElementById: () => null,
      documentElement: { lang: '' },
      body: { appendChild() {}, addEventListener() {} },
    },
    navigator: { language: 'zh-CN' },
    localStorage: { getItem: () => null, setItem() {} },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  try {
    vm.runInNewContext(src, sandbox, { filename: file, timeout: TIMEOUT_MS });
  } catch (error) {
    problems.push(`${file} 执行失败：${String(error && error.message).slice(0, 200)}`);
  }
}

/* ---- 1b. index.html 内联三表（引擎启动时同样注册） ---- */
function stripComments(src) {
  let out = '';
  let index = 0;
  let quote = null;
  while (index < src.length) {
    const char = src[index];
    const next = src[index + 1];
    if (quote) {
      out += char;
      if (char === '\\') { out += next === undefined ? '' : next; index += 2; continue; }
      if (char === quote) quote = null;
      index += 1;
      continue;
    }
    if (char === '/' && next === '/') {
      while (index < src.length && src[index] !== '\n') index += 1;
      continue;
    }
    if (char === '/' && next === '*') {
      const end = src.indexOf('*/', index + 2);
      index = end < 0 ? src.length : end + 2;
      out += ' ';
      continue;
    }
    if (char === "'" || char === '"' || char === '`') quote = char;
    out += char;
    index += 1;
  }
  return out;
}

function literals(src) {
  const found = [];
  let index = 0;
  while (index < src.length) {
    const char = src[index];
    if (char !== "'" && char !== '"' && char !== '`') { index += 1; continue; }
    const quote = char;
    let value = '';
    let closed = false;
    index += 1;
    while (index < src.length) {
      const current = src[index];
      if (current === '\\') { value += src[index + 1] === undefined ? '' : src[index + 1]; index += 2; continue; }
      if (current === quote) { closed = true; index += 1; break; }
      if (quote !== '`' && current === '\n') break;
      value += current;
      index += 1;
    }
    if (closed) found.push(value);
    else index += 1;
  }
  return found;
}

function bracketEnd(src, open) {
  let depth = 0;
  let quote = null;
  for (let index = open; index < src.length; index += 1) {
    const char = src[index];
    if (quote) {
      if (char === '\\') { index += 1; continue; }
      if (char === quote) quote = null;
      continue;
    }
    if (char === "'" || char === '"' || char === '`') { quote = char; continue; }
    if (char === '/' && src[index + 1] === '/') { while (index < src.length && src[index] !== '\n') index += 1; continue; }
    if (char === '/' && src[index + 1] === '*') { const end = src.indexOf('*/', index + 2); index = end < 0 ? src.length : end + 1; continue; }
    if (char === '[') depth += 1;
    else if (char === ']') { depth -= 1; if (depth === 0) return index; }
  }
  return -1;
}

const html = stripComments(read('index.html'));
let inlineRows = 0;
for (const name of INLINE_TABLES) {
  const decl = new RegExp('const\\s+' + name + '\\s*=\\s*\\[', 'g');
  const match = decl.exec(html);
  if (!match) fail(`index.html 未找到内联词典 ${name}（结构变化需同步本门）`);
  const open = match.index + match[0].length - 1;
  const close = bracketEnd(html, open);
  if (close < 0) fail(`index.html 的内联词典 ${name} 未闭合`);
  let table;
  try {
    table = vm.runInNewContext('(' + html.slice(open, close + 1) + ')', {}, { timeout: TIMEOUT_MS });
  } catch (error) {
    fail(`index.html 的内联词典 ${name} 无法求值：${String(error && error.message).slice(0, 200)}`);
  }
  if (!Array.isArray(table)) fail(`index.html 的内联词典 ${name} 不是数组`);
  for (const row of table) {
    /* 编译期注释隔断可能在数组里留下空位（[,…]）；空位不是词条，跳过。 */
    if (row === undefined || row === null) continue;
    absorbRow(row, 'index.html#' + name);
    inlineRows += 1;
  }
}
if (inlineRows === 0) fail('index.html 内联词典未取到任何词条——解析失效（防止门空转）');

if (problems.length) {
  fail('证据工作区词典结构不合格：\n  ' + problems.join('\n  '));
}
if (rowCount === 0) fail('未取到任何注册词条——注册桩失效（防止门空转）');

/* ---- 2. 字面量扫描（剔除注释；含模板字面量） ---- */
/* 「一字之差」检测（F48 原病灶「尚无评估。」vs 注册键「暂无评估。」）：
   两种形态都要抓——
   a) 只差尾部标点：核心串相同、整串不同；
   b) 只差一个汉字：长度相同、仅 1 位不同（「尚」/「暂」）——这条是原病灶的
      真实形态，旧的「去尾部标点」归一根本抓不到，会把它当成普通未注册串。
   两种都点名，让修复者知道「改动的是同一个词条」而不是去补一条新键。 */
const TRAILING = /[。．.！!？?：:；;，,、\s]+$/u;
const core = text => text.replace(TRAILING, '');
const registryCores = new Map();
for (const key of keys) {
  const normalized = core(key);
  if (!registryCores.has(normalized)) registryCores.set(normalized, key);
}

function differsByOneChar(left, right) {
  if (left.length !== right.length || left === right) return false;
  let diff = 0;
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] !== right[index] && (diff += 1) > 1) return false;
  }
  return diff === 1;
}
const coreToKeys = new Map();
for (const key of keys) {
  const normalized = core(key);
  if (!coreToKeys.has(normalized)) coreToKeys.set(normalized, []);
  coreToKeys.get(normalized).push(key);
}

const missing = [];
const nearMiss = [];
const seen = new Set();
let scanned = 0;
for (const file of MODULES) {
  const src = stripComments(read(file));
  for (const literal of literals(src)) {
    if (!CJK.test(literal)) continue;
    scanned += 1;
    if (keys.has(literal) || aliases.has(literal)) continue;
    if (seen.has(file + '\u0000' + literal)) continue;
    seen.add(file + '\u0000' + literal);
    missing.push(`${file}: ${JSON.stringify(literal)}`);
    const registered = registryCores.get(core(literal));
    if (registered && registered !== literal) {
      nearMiss.push(`${file}: ${JSON.stringify(literal)} 与注册键 ${JSON.stringify(registered)} 只差尾部标点`);
      continue;
    }
    /* b) 只差一个汉字：核心串长度相同、仅 1 位不同（「尚」/「暂」是 F48 的原病灶）。
       这里必须扫**全部**核心串——原病灶的两侧核心串并不相等（尚无评估 ≠ 暂无评估），
       只查同名核心永远抓不到。 */
    for (const [registeredCore, candidates] of registryCores) {
      if (!differsByOneChar(core(literal), registeredCore)) continue;
      nearMiss.push(`${file}: ${JSON.stringify(literal)} 与注册键 ${JSON.stringify(candidates)} 只差一个汉字`);
      break;
    }
  }
}

if (nearMiss.length) {
  fail('F48：字面量与注册键只差标点/一个汉字（永不命中，非中文界面会漏中文）：\n  ' + nearMiss.join('\n  '));
}
if (missing.length) {
  fail('F48：证据工作区存在未注册的用户可见文案（须补十语言词条）：\n  ' + missing.join('\n  '));
}
if (scanned === 0) fail('F48：未扫描到任何中文字面量——扫描器失效（防止门空转）');

console.log(JSON.stringify({
  status: 'passed',
  evidence_modules: MODULES.length,
  locale_files: localeFiles.length,
  inline_rows: inlineRows,
  registry_rows: rowCount,
  scanned_literals: scanned,
}, null, 2));
