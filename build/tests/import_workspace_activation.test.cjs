/* r7-lunafix L-02 钉子：启用（activate）失败不得使导入批次后台分页停滞。
 *
 * 复刻 deepseek 复审的 node 探针（out/external_reviews/
 * gpt_luna_findings_verification.md L-02 节）：
 * - custom_frontend/import_review_loading.js 与 import_review_updates.js
 *   **原文件逐字**在 vm 中运行；
 * - unified_import_workspace.js 的 I.open（:61-74）与 activate（:110）从源文
 *   本按锚点切出后逐字运行（ticket/valid 闭包、++ticket、finally 均为真实
 *   代码）；api/DOM/render 等为受控桩；
 * - 场景 A：后台分页进行中调用 activate() 且 POST /activate 失败 ——
 *   修前：++ticket 使 valid 闭包永久失效 → 分页停在 40/100、600ms 无新请求
 *   （本测试红）；修后：加载器继续拉页直到 complete 100/100（绿）。
 * - 场景 B（对照组）：同环境不触发激活 → 跑到 complete（任何实现下都必须绿）。
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const FRONT = path.join(__dirname, '..', '..', 'custom_frontend');
const srcWorkspace = fs.readFileSync(path.join(FRONT, 'unified_import_workspace.js'), 'utf8');
const srcLoading = fs.readFileSync(path.join(FRONT, 'import_review_loading.js'), 'utf8');
const srcUpdates = fs.readFileSync(path.join(FRONT, 'import_review_updates.js'), 'utf8');

function fail(message, detail) {
  console.error('L-02 NAIL FAILED: ' + message);
  if (detail !== undefined) console.error(JSON.stringify(detail, null, 2));
  process.exit(1);
}

/* —— 从 unified_import_workspace.js 原文切出真实代码（锚点 + 花括号配平） —— */
function cutBraced(source, anchor) {
  const start = source.indexOf(anchor);
  if (start < 0) fail('源文件缺少锚点：' + anchor);
  const braceStart = source.indexOf('{', start);
  if (braceStart < 0) fail('锚点后无函数体：' + anchor);
  let depth = 0;
  for (let i = braceStart; i < source.length; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}') {
      depth--;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  fail('锚点函数体花括号不配平：' + anchor);
}
const cutOpen = cutBraced(srcWorkspace, 'I.open=async(batchId,preserve=false)=>{');
const cutActivate = cutBraced(srcWorkspace, 'async function activate(){');
const idLine = /^ const id=item=>.*$/m.exec(srcWorkspace);
if (!idLine) fail('源文件缺少 id 函数行（const id=item=>…）');
if (!cutOpen.includes('const t=++ticket') || !cutOpen.includes('ImportReviewLoading.create'))
  fail('切出的 I.open 不含真实 ticket/loader 构造（锚点切分失败）');
if (!cutActivate.includes('/activate')) fail('切出的 activate 不含 /activate 调用');

/* —— 每个场景一个隔离 vm：真实加载/覆盖模块 + 切出的真实 I.open/activate + 桩 —— */
function sceneScript() {
  return `
/* 受控桩：DOM / 工作区助手 / 全局 */
const window = globalThis;
const document = {
  createElementNS: () => ({ setAttribute() {} }),
  getElementById: () => null,
  activeElement: null,
  hidden: false,
  dispatchEvent() {},
  addEventListener() {},
};
const matchMedia = () => ({ matches: true });
const requestAnimationFrame = (fn) => 0;
const T = (s) => s;
const CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } };
function mkNode() {
  return {
    append() {}, replaceChildren() {}, setAttribute() {}, removeAttribute() {},
    querySelector: () => null, closest: () => null, children: [], isConnected: true,
    innerHTML: '', style: {}, classList: { add() {} }, dataset: {}, disabled: false,
    value: '', title: '', addEventListener() {}, replaceWith() {},
  };
}
const ImportExportWorkspace = {
  node: mkNode, literal: () => mkNode(), button: () => mkNode(), icon: () => mkNode(),
  showImport() {}, importPolicy() {}, reviewCount: null, details: () => mkNode(),
};
const WorkflowControls = {
  scope: () => 'scope-l02', version: () => 7, refresh: async () => {},
  acceptImportVersion() {}, settings: () => ({ can_manage: true }),
  errorText: (e) => String((e && e.message) || e), error() {},
};
const ImportReviewModel = { reason: () => 'ready', comparison: () => [] };
const S = { task: { task_id: 'T1' }, page: 'import' };
const taskApi = () => '/api/tasks/T1';
const scrQ = () => '';
const go = async () => 'import';
const toast = () => {};
const download = () => {};
const renderImportReport = () => {};
const refreshProgress = async () => {};
const host = mkNode();

/* 真实前端模块（逐字） */
${srcUpdates}
${srcLoading}

/* unified_import_workspace.js 的模块级状态（:5/:59 同名同初值） */
let active=null,batch=null,records=[],groups=[],legacy=[],legacyUndo=false,
    tab='duplicate',selected=null,page=0,listPage=0,ticket=0,context='',busy=false,
    assigning=false,dirty=false,lastView=null,errorMessage='';
let loader=null,loadState=null,overlays=ImportReviewUpdates.create(),
    savedBatch=null,savedLegacyUndo=null,reviewVersion=0,refreshing=false;
let activationInFlight=false;
const reports=new Map();
const I={};
I.remember=report=>{if(report?.batch_id)reports.set(report.batch_id,report);};
I.notice=async()=>{};
function deferred(){return new Set();}
function view(){return {tab:0,selected:null,page:0,vx:0,vy:0,x:0,y:0,body:0,qx:0,qy:0};}
function position(){}
function render(){/* 桩：渲染不在本钉子范围 */}
function loadingStatus(){return mkNode();}

/* 分页 api 桩：records 100 条/每页 20、每页 40ms；/activate 120ms 后 503 */
const PAGE_MS=40, TOTAL=100, PAGE_SIZE=20;
let recordsFeedCalls=0;
const api=async(path)=>{
  if(path.endsWith('/activate')){
    await new Promise(r=>setTimeout(r,120));
    throw Error('simulated 503 from POST /activate');
  }
  const offset=Number((/[?&]offset=(\\d+)/.exec(path)||[,'0'])[1]);
  const limit=Number((/[?&]limit=(\\d+)/.exec(path)||[,'20'])[1]);
  await new Promise(r=>setTimeout(r,PAGE_MS));
  if(path.includes('/import-batches/B1/candidates')) return {candidates:[],view_total:0};
  if(path.includes('/import-batches/B1?')) {
    recordsFeedCalls++;
    const rows=Array.from({length:TOTAL},(_,i)=>({record_id:'r'+i}));
    return {records:rows.slice(offset,offset+limit),view_total:TOTAL};
  }
  if(path.includes('/title-duplicate-reviews')) return {groups:[],view_total:0};
  return {};
};

/* 真实代码（逐字切出）；W/U/M 为原文件 :3 的模块别名 */
const W=WorkflowControls,U=ImportExportWorkspace,M=ImportReviewModel;
${idLine[0]}
${cutOpen}
${cutActivate}

globalThis.__probe={
  I, activate,
  recordsCalls: () => recordsFeedCalls,
  state: () => ({
    phase: loadState && loadState.phase,
    loaded: loadState ? loadState.loaded : null,
    total: loadState ? loadState.total : null,
    error: loadState && loadState.error ? String(loadState.error && loadState.error.message || loadState.error) : null,
    busy, ticket, records: records.length,
  }),
};
`;
}

async function runScenario(failActivation) {
  const sandbox = { setTimeout, clearTimeout };
  vm.createContext(sandbox);
  vm.runInContext(sceneScript(), sandbox, { filename: 'l02-scene.js' });
  const probe = sandbox.__probe;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const waitFor = async (pred, ms) => {
    const deadline = Date.now() + ms;
    while (Date.now() < deadline) {
      if (pred()) return true;
      await sleep(5);
    }
    return pred();
  };
  await probe.I.open('B1'); // 首屏（initial 页）
  if (!await waitFor(() => probe.recordsCalls() >= 3, 4000))
    fail('后台分页未按预期进行（前 3 页 records 请求未出现）',
         { state: probe.state(), calls: probe.recordsCalls() });
  const before = probe.recordsCalls();
  let activationError = null;
  if (failActivation) {
    try { await probe.activate(); } catch (e) { activationError = e.message; }
  }
  await sleep(800); // 复审同款：给后台加载器足够的续拉时间
  return {
    failActivation,
    activationError,
    recordsCallsBeforeActivation: before,
    recordsCallsAfterSettle: probe.recordsCalls(),
    stateAfter: probe.state(),
  };
}

(async () => {
  const failing = await runScenario(true);
  const control = await runScenario(false);

  if (failing.activationError !== 'simulated 503 from POST /activate')
    fail('场景 A：activate() 未按桩预期失败', failing);
  // 核心钉子：启用失败后加载器必须继续拉页直到 complete（修前停在 background）
  if (failing.stateAfter.phase !== 'complete')
    fail('场景 A：启用失败后后台分页停滞（loadState.phase!=="complete"）', failing);
  if (failing.stateAfter.loaded !== 100 || failing.stateAfter.total !== 100)
    fail('场景 A：启用失败后未加载完全部记录', failing);
  if (failing.recordsCallsAfterSettle <= failing.recordsCallsBeforeActivation)
    fail('场景 A：启用失败后没有新的分页请求（加载器未恢复）', failing);
  // 对照组：不激活必须照常 complete
  if (control.stateAfter.phase !== 'complete' || control.stateAfter.loaded !== 100)
    fail('场景 B（对照）：无激活的后台加载未到 complete', control);

  console.log(JSON.stringify({ fail: failing, control,
    assert: 'activation failure no longer stalls background paging' }, null, 2));
})().catch((e) => fail('harness 异常：' + (e && e.stack || e)));
