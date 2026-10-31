# -*- coding: utf-8 -*-
"""r8-fixpool · FL5 前端组红钉子（每条修复配破坏即红钉，R4）。

钉子 ↔ 修复项对照（锚定缓修池 inventory_缓修池72条.md）：
  - R3-04    analysis_grade.js            全局票据跨行丢响应 → 行×动作票据隔离（Node vm 真模块回放）
  - LUNA4-03 protocol_designer.js         保存期间可编辑→成功反馈引用旧快照 → formRevision 比对+未保存标记
  - B15-01   protocol_designer.js         CSS 缺 } 吞 10 条规则 → 每条字面量大括号自平衡
  - B08-N5   protocol_designer.py/.js     日期窗只进 PubMed 无提示 → 导出 Markdown + 前端条件披露
  - MM11-06  protocol_designer.py         日期不校验→URL 注入+链接闭合 → YYYY-MM-DD 校验 + 非法整体转义
  - LUNA4-02 evidence_mapper.js           零计数格键盘/读屏不可达 → gapcell 补 tabindex/role/Enter/Space
  - GLM14-01 evidence_mapper.js           头注声称监听不存在的事件 → 头注事件与 addEventListener 一致
  - MM9-17   index.html RFLang.register   词典守卫 zh 门控 → 任一语种槽位查重（仍仅 console.warn）
  - MM11-11  import_export_workspace.js   猴补丁隐式顺序无守卫 → 静态顺序断言 + typeof 显式守卫
  - 批13-1   analysis_subgroup_random.js  尺度三元缺 fisher_z/logit 分支（词条键已存在，无新增词典内容）
  - 批13-2   analysis_network_pscores.js  catch 无条件 lang:'en' → known 串守卫
  - 批13-2b  analysis_network_ranks.js    同根因 → known 串守卫
  - ?v=      index.html                   本组改动资产的缓存令牌 bump

JS 钉子用 Node vm 加载真实模块文件（DOM/api 受控桩），Python 钉子直接 import 真实模块。
"""

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "custom_frontend"
INDEX = FRONTEND / "index.html"

NODE_CANDIDATES = [
    os.environ.get("RF214_NODE", ""),
    shutil.which("node") or "",
    r"C:\Users\16073\AppData\Local\hermes\node\node.exe",
]


def _node_exe():
    """优先环境变量/PATH，最后才退回本机已知安装（避免 MM11-09 式硬编码）。"""
    for candidate in NODE_CANDIDATES:
        if candidate and pathlib.Path(candidate).is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Node 侧共享 DOM 桩（Element/选择器/事件冒泡/受控 api）
# ---------------------------------------------------------------------------

DOM_STUBS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
function mkText(t) { return { nodeType: 3, textContent: String(t), children: [], parentNode: null }; }
class El {
  constructor(tag) {
    this.tagName = String(tag).toLowerCase(); this.nodeType = 1;
    this.attrs = {}; this.children = []; this.parentNode = null;
    this._listeners = {}; this.dataset = {}; this.style = {};
    this.value = ''; this.textContent = ''; this.hidden = false; this.disabled = false;
    const self = this;
    this.classList = {
      toggle(c, f) {
        const set = new Set((self.attrs['class'] || '').split(/\s+/).filter(Boolean));
        const want = f === undefined ? !set.has(c) : !!f;
        if (want) set.add(c); else set.delete(c);
        self.attrs['class'] = Array.from(set).join(' '); return want;
      },
      add(c) { this.toggle(c, true); },
      remove(c) { this.toggle(c, false); },
      contains(c) { return (self.attrs['class'] || '').split(/\s+/).filter(Boolean).includes(c); },
    };
  }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'id') this.id = String(v); }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  get options() { return this.children.filter(c => c.nodeType === 1 && c.tagName === 'option'); }
  get firstElementChild() { return this.children.find(c => c.nodeType === 1) || null; }
  _flat(ns) { const out = []; for (const n of ns) { if (n == null) continue; if (Array.isArray(n)) out.push(...this._flat(n)); else out.push(n); } return out; }
  _ins(n) { if (n && n.nodeType === 3) { n.parentNode = this; this.children.push(n); return; } if (n.parentNode) n.parentNode._detach(n); n.parentNode = this; this.children.push(n); }
  _detach(n) { const i = this.children.indexOf(n); if (i >= 0) this.children.splice(i, 1); }
  appendChild(n) { this._ins(n); return n; }
  append(...ns) { for (const n of this._flat(ns)) this._ins(n); }
  prepend(...ns) { for (const n of this._flat(ns)) { if (n.parentNode) n.parentNode._detach(n); n.parentNode = this; this.children.unshift(n); } }
  remove() { if (this.parentNode) this.parentNode._detach(this); }
  replaceChildren(...ns) { this.children = []; for (const n of this._flat(ns)) this._ins(n); }
  after(...ns) { this._afterNodes = this._afterNodes || []; const fl = this._flat(ns); this._afterNodes.push(...fl); if (this.parentNode) { let at = this.parentNode.children.indexOf(this); for (const n of fl) { n.parentNode = this.parentNode; this.parentNode.children.splice(++at, 0, n); } } }
  closest(sel) { let cur = this; const cls = sel.startsWith('.') ? sel.slice(1) : null; while (cur) { if (cls && cur.nodeType === 1 && cur.classList.contains(cls)) return cur; cur = cur.parentNode; } return null; }
  matchesOne(part) { part = part.trim(); if (part.startsWith('#')) return this.attrs['id'] === part.slice(1); if (part.startsWith('.')) return this.classList.contains(part.slice(1)); return this.tagName === part.toLowerCase(); }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  removeEventListener(type, fn) { const l = this._listeners[type] || []; const i = l.indexOf(fn); if (i >= 0) l.splice(i, 1); }
  dispatch(type, ev) { ev = ev || {}; ev.type = type; if (!ev.preventDefault) ev.preventDefault = () => {}; if (!ev.target) ev.target = this; (this._listeners[type] || []).slice().forEach(fn => fn.call(this, ev)); if (ev.bubbles !== false) { let p = this.parentNode; while (p) { (p._listeners && p._listeners[type] || []).slice().forEach(fn => fn.call(p, ev)); p = p.parentNode; } } }
  showModal() { this._modalShown = true; }
  close() { this._modalShown = false; }
  focus() { globalThis.__activeElement = this; }
  click() { this.dispatch('click'); }
  _all() { const out = []; const walk = n => { for (const c of n.children || []) { out.push(c); walk(c); } }; walk(this); return out; }
  _match(sel) { return sel.split(',').some(part => this.matchesOne(part)); }
  querySelector(sel) { return this._all().find(n => n.nodeType === 1 && n._match(sel)) || null; }
  querySelectorAll(sel) { return this._all().filter(n => n.nodeType === 1 && n._match(sel)); }
}
function textOf(root) { let s = root.textContent || ''; for (const c of root.children || []) s += ' ' + textOf(c); return s; }
globalThis.__toasts = [];
globalThis.toast = (text, kind) => { globalThis.__toasts.push({ text: String(text), kind: kind || '' }); };
globalThis.confirm = () => true;
globalThis.T = (tpl, ...args) => { let s = String(tpl); if (args.length) s = s.replace(/\{(\d+)\}/g, (m, d) => args[+d] === undefined ? m : String(args[+d])); return s; };
globalThis.el = (tag, attrs, ...children) => {
  const n = new El(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null) continue;
    if (k === 'text') n.textContent = String(v);
    else if (k === 'value') n.value = String(v);
    else if (k === 'hidden') n.hidden = !!v;
    else n.setAttribute(k, String(v));
  }
  n.append(...children);
  return n;
};
globalThis.__docListeners = {};
globalThis.__docQ = {};
globalThis.__byId = {};
globalThis.document = {
  nodeType: 9,
  createElement: t => new El(t),
  createElementNS: (ns, t) => new El(t),
  createTextNode: t => mkText(t),
  getElementById: id => globalThis.__byId[id] || null,
  querySelector: sel => (Object.prototype.hasOwnProperty.call(globalThis.__docQ, sel) ? globalThis.__docQ[sel] : null),
  querySelectorAll: () => [],
  addEventListener: (t, f) => { (globalThis.__docListeners[t] = globalThis.__docListeners[t] || []).push(f); },
  body: new El('body'),
  head: new El('head'),
  documentElement: new El('html'),
};
globalThis.__docDispatch = (type, detail) => { (globalThis.__docListeners[type] || []).slice().forEach(f => f({ type, detail })); };
globalThis.__requests = [];      // {url, opts, resolve, reject}
globalThis.api = (url, opts) => new Promise((resolve, reject) => { globalThis.__requests.push({ url: String(url), opts: opts || {}, resolve, reject }); });
globalThis.taskApi = () => '/api/tasks/t1';
globalThis.go = async () => true;
globalThis.loadTasks = async () => {};
globalThis.pickTask = () => {};
globalThis.S = { task: { task_id: 't1', name: 'T1' }, taskVersion: 1, tasks: [{ task_id: 't1', name: 'T1' }], user: { username: 'u' }, page: 'analysis' };
globalThis.__rfRegister = [];
globalThis.RFLang = { register: pairs => { globalThis.__rfRegister.push(...pairs); } };
const drain = async (n) => { for (let i = 0; i < (n || 6); i++) await new Promise(r => setTimeout(r, 0)); };
/* 受控应答：按 URL 自动/手动 resolve */
globalThis.__autoResolve = [];
function autoAnswer(req) {
  const u = req.url;
  if (u.endsWith('/analysis/grade/export?format=sof_table')) { req.resolve({ assessments: [], columns: [], rows: [] }); return true; }
  if (u.indexOf('/evidence-map?') >= 0) {
    req.resolve({ matrix: [[1, 0], [0, 2]], x_labels: ['0', '1'], y_labels: ['rct', 'cohort'], clusters: [], gaps: [{ x: '0', y: 'cohort' }, { x: '1', y: 'rct' }] });
    return true;
  }
  return false;
}
async function pump() { for (let i = 0; i < globalThis.__requests.length; i++) { const r = globalThis.__requests[i]; if (!r._done && autoAnswer(r)) { r._done = true; } } await drain(); }
function pending(urlPart) { return globalThis.__requests.filter(r => !r._done && r.url.indexOf(urlPart) >= 0); }
function finish(req, data) { req._done = true; req.resolve(data); }
function loadModule(rel) { vm.runInThisContext(fs.readFileSync(require('path').join(process.argv[2], rel), 'utf8'), { filename: rel }); }
"""


def _run_node_js(driver_body, module_dir):
    """把 DOM_STUBS + driver 写入临时 .cjs，用 node 执行，返回 stdout 的 JSON。"""
    exe = _node_exe()
    if exe is None:
        raise unittest.SkipTest("本机无 node 可执行文件（前端钉子不可用）")
    with tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False, encoding="utf-8", dir=str(pathlib.Path(tempfile.gettempdir()))) as handle:
        handle.write(DOM_STUBS + "\n(async () => {\n" + driver_body + "\n})();\n")
        script = handle.name
    try:
        proc = subprocess.run([exe, script, str(module_dir)], capture_output=True, text=True,
                              encoding="utf-8", timeout=120, cwd=str(module_dir))
    finally:
        pathlib.Path(script).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise AssertionError("node 钉子执行失败：%s" % proc.stderr[-2000:])
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    if not lines:
        raise AssertionError("node 钉子无输出；stderr=%s" % proc.stderr[-800:])
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# R3-04：analysis_grade.js 票据按行隔离（跨行动作互不丢响应；同行同动作仍后发抑制先发）
# ---------------------------------------------------------------------------

GRADE_DRIVER_FULL = r"""
globalThis.anEffects = [
  { comparison: 'A vs B', outcome: 'Outcome A', timepoint: 'tp', measure: 'MD', study_id: 's1' },
  { comparison: 'A vs B', outcome: 'Outcome B', timepoint: 'tp', measure: 'MD', study_id: 's2' },
];
__byId['anJumpEffects'] = new El('div');
__docQ['.page[data-p="analysis"]'] = new El('div');
loadModule('analysis_grade.js');
const panel = __byId['anJumpEffects']._afterNodes[0];
function rowsNow() { return panel.querySelector('#rfGradeRows').children; }
function rowUi(row) {
  const buttons = row.querySelectorAll('button');
  return { row, auto: buttons[0], save: buttons[1], status: row.children[2], rationale: row.children[3] };
}
await pump();  // 模块初始 renderRows（控件禁用）
__docDispatch('reviewflow:analysis-loaded');
await pump();  // restoreAssessments 完成 → 控件可用
const out = {};
{
  const [ra, rb] = rowsNow().map(rowUi);
  ra.auto.click();                       // 行 A：自动建议（在途）
  out.aStatusPending = ra.status.textContent;
  rb.save.click();                       // 行 B：保存（在途）——修复前会推进全局票据
  await drain();
  const saves = pending('/analysis/grade/save'); const autos = pending('/analysis/grade/auto-suggest');
  finish(saves[0], { final_quality: 'moderate' });
  await drain();
  finish(autos[0], { downgrades: { rob: 'serious' }, initial_quality: 'high', final_quality: 'moderate', signal_sources: {} });
  await drain();
  out.aStatusAfter = ra.status.textContent;
  out.aRationaleHidden = ra.rationale.hidden;
  out.bStatusAfter = rb.status.textContent;
}
{
  const [ra, rb] = rowsNow().map(rowUi);
  ra.save.click();                       // 反向：行 A 保存先发
  await drain();
  rb.auto.click();                       // 行 B 自动建议后发
  await drain();
  const saves = pending('/analysis/grade/save'); const autos = pending('/analysis/grade/auto-suggest');
  if (autos.length) finish(autos[0], { downgrades: {}, initial_quality: 'high', final_quality: 'high', signal_sources: {} });
  await drain();
  finish(saves[0], { final_quality: 'moderate' });
  await drain();
  out.aSaveStatusAfter = ra.status.textContent;
  out.toasts = __toasts.map(t => t.text);
}
{
  /* 同行同动作：后发抑制先发（修复保留了同域过期抑制） */
  const [ra] = rowsNow().map(rowUi);
  ra.auto.click();
  await drain();
  __docDispatch('reviewflow:analysis-core-loaded');   // 重渲染出可用新按钮，同 st 域
  const [ra2] = rowsNow().map(rowUi);
  ra2.auto.click();
  await drain();
  const autos = pending('/analysis/grade/auto-suggest');
  finish(autos[0], { downgrades: { rob: 'serious' }, initial_quality: 'high', final_quality: 'moderate', signal_sources: {} });
  await drain();
  out.sameRowSuppressedStatus = ra2.status.textContent;   // 先发响应必须被抑制
  finish(autos[1], { downgrades: { rob: 'serious' }, initial_quality: 'high', final_quality: 'moderate', signal_sources: {} });
  await drain();
  out.sameRowLatestStatus = rowsNow().map(rowUi)[0].status.textContent;
}
process.stdout.write('\n' + JSON.stringify(out));
"""


class TestR304GradeRowTicketIsolation(unittest.TestCase):
    """R3-04 钉子：A 行建议不被 B 行保存推进的票据丢弃；反向保存确认不丢；同域抑制保留。"""

    def test_cross_row_responses_survive(self):
        out = _run_node_js(GRADE_DRIVER_FULL, FRONTEND)
        self.assertIn("正在推导自动建议", out["aStatusPending"])
        # 修复前：A 行响应被 B 行保存推进的全局票据丢弃，状态永远停在“正在推导…”
        self.assertEqual(out["aStatusAfter"], "已按自动建议预填（初始质量：高，建议最终质量：中）。",
                         "R3-04：A 行自动建议响应被跨行动作丢弃：%r" % out["aStatusAfter"])
        self.assertFalse(out["aRationaleHidden"], "R3-04：推导依据框应随有效建议展开")
        self.assertIn("✓ 已保存", out["bStatusAfter"])
        # 反向：保存先发、他行自动建议后发 → 保存成功状态/toast 必须保留
        self.assertIn("✓ 已保存（最终质量已重新计算：中）。", out["aSaveStatusAfter"],
                      "R3-04：保存成功确认被跨行动作丢弃：%r" % out["aSaveStatusAfter"])
        self.assertTrue(any("Outcome A" in t for t in out["toasts"]), "R3-04：保存 toast 丢失：%r" % out["toasts"])

    def test_same_row_stale_response_still_suppressed(self):
        out = _run_node_js(GRADE_DRIVER_FULL, FRONTEND)
        self.assertIn("正在推导", out["sameRowSuppressedStatus"],
                      "R3-04 修复不得移除同域过期抑制（后发请求应抑制先发响应）")
        self.assertIn("已按自动建议预填", out["sameRowLatestStatus"])


# ---------------------------------------------------------------------------
# R3-04 补钉（评审 r2 阻断）：失败路径不得 ReferenceError；任务切换仍作废在途响应
# ---------------------------------------------------------------------------

GRADE_REJECT_DRIVER = r"""
globalThis.anEffects = [
  { comparison: 'A vs B', outcome: 'Outcome A', timepoint: 'tp', measure: 'MD', study_id: 's1' },
  { comparison: 'A vs B', outcome: 'Outcome B', timepoint: 'tp', measure: 'MD', study_id: 's2' },
];
__byId['anJumpEffects'] = new El('div');
__docQ['.page[data-p="analysis"]'] = new El('div');
globalThis.__unhandled = [];
process.on('unhandledRejection', er => { globalThis.__unhandled.push(String(er && er.stack || er)); });
loadModule('analysis_grade.js');
await pump();
__docDispatch('reviewflow:analysis-loaded');
await pump();
const rows = () => __byId['anJumpEffects']._afterNodes[0].querySelector('#rfGradeRows').children;
const ui = row => { const b = row.querySelectorAll('button'); return { row, auto: b[0], save: b[1], status: row.children[2] }; };
const out = {};
/* (1) 保存 + 导出 reject：状态必须落到失败文案，toast 必须出现，不得有未处理 rejection */
{
  const [ra] = rows().map(ui);
  ra.save.click(); await drain();
  const sof = __byId['anJumpEffects']._afterNodes[0].querySelectorAll('button').find(b => b.textContent === '导出 SoF 表');
  sof.click(); await drain();
  const saves = pending('/analysis/grade/save');
  const exps = pending('/analysis/grade/export');
  saves[0]._done = true; saves[0].reject(new Error('save 500'));
  exps[0]._done = true; exps[0].reject(new Error('export 500'));
  await drain();
  out.saveStatus = ra.status.textContent;
  out.toasts = __toasts.map(t => t.text);
  out.unhandled = globalThis.__unhandled.slice();
}
/* (2) 自动建议 reject */
{
  __toasts.length = 0;
  const [ra] = rows().map(ui);
  ra.auto.click(); await drain();
  const reqs = pending('/analysis/grade/auto-suggest');
  reqs[0]._done = true; reqs[0].reject(new Error('auto 500'));
  await drain();
  out.autoStatus = ra.status.textContent;
  out.unhandled2 = globalThis.__unhandled.slice();
}
/* (3) 任务切换后迟到的保存响应必须被作废（epoch 守卫；修复前 6 处 epoch 被删则复活） */
{
  __toasts.length = 0;
  const [ra] = rows().map(ui);
  ra.save.click(); await drain();
  const saves = pending('/analysis/grade/save');
  __docDispatch('reviewflow:analysis-task-changed');       // version++ → 该响应当前已失效
  await drain();
  saves[0]._done = true; saves[0].resolve({ final_quality: 'moderate' });
  await drain();
  out.toastsAfterTaskSwitch = __toasts.map(t => t.text);
  out.statusAfterTaskSwitch = ra.status.textContent;
}
process.stdout.write('\n' + JSON.stringify(out));
"""


class TestR304FailurePathsAndEpoch(unittest.TestCase):
    """R3-04 补钉：
    (a) 网络失败/500 时状态行落到失败文案且 toast 出现（守卫 epoch 的 catch 分支不得 ReferenceError）；
    (b) 任务切换后迟到的成功响应不得写成功态/toast（epoch 守卫被删即红）。"""

    def test_rejections_reach_failure_state(self):
        out = _run_node_js(GRADE_REJECT_DRIVER, FRONTEND)
        self.assertEqual(out["unhandled"], [], "R3-04：catch 分支抛未处理 rejection（epoch 作用域错误）：%r" % out["unhandled"])
        self.assertIn("保存失败：save 500", out["saveStatus"],
                      "R3-04：保存 500 后状态行未落到失败文案：%r" % out["saveStatus"])
        self.assertTrue(any("保存失败" in t for t in out["toasts"]), "R3-04：保存失败 toast 丢失：%r" % out["toasts"])
        self.assertTrue(any("导出失败" in t for t in out["toasts"]), "R3-04：导出失败 toast 丢失：%r" % out["toasts"])
        self.assertIn("自动建议失败：auto 500", out["autoStatus"])
        self.assertEqual(out["unhandled2"], [], "R3-04：自动建议 catch 抛未处理 rejection：%r" % out["unhandled2"])

    def test_task_switch_invalidates_late_response(self):
        out = _run_node_js(GRADE_REJECT_DRIVER, FRONTEND)
        self.assertEqual(out["toastsAfterTaskSwitch"], [],
                         "R3-04：任务切换后迟到保存响应仍写成功 toast（epoch 守卫失效）：%r" % out["toastsAfterTaskSwitch"])
        self.assertNotIn("✓ 已保存", out["statusAfterTaskSwitch"],
                         "R3-04：任务切换后迟到响应仍写成功状态：%r" % out["statusAfterTaskSwitch"])


# ---------------------------------------------------------------------------
# LUNA4-03 + B08-N5(UI) + 十语词条自注册：protocol_designer.js
# ---------------------------------------------------------------------------

PROTOCOL_DRIVER = r"""
__byId['newTaskBtn'] = new El('button'); __byId['newTaskBtn'].classList.add('card'); /* closest('.card') 命中 */
__byId['newTaskBtn']._card = new El('div');
__byId['newTaskBtn'].closest = function (sel) { return this._card; };
__docQ['.page[data-p="home"]'] = new El('div');
loadModule('protocol_designer.js');
const out = {};
out.registered = __rfRegister.map(row => ({ key: row[0], slots: row.length, filled: row.filter(Boolean).length }));
const panel = __byId['newTaskBtn']._card._afterNodes[0];
const title = panel.querySelector('#rfProtocolTitle');
const status = panel.querySelector('.rf-status');
const strategyBox = panel.querySelector('#rfProtocolStrategies');
title.value = '标题A';
const saveBtn = panel.querySelectorAll('button').find(b => b.textContent === '保存并创建任务');
saveBtn.click();                                   // 保存（创建新任务 → POST /api/tasks → POST /protocol 在途）
await drain();
const taskReq = pending('/api/tasks')[0];
finish(taskReq, { task_id: 't1', name: '标题A' });
await drain();
const protoReq = pending('/protocol')[0];
out.postedTitle = protoReq.opts.body.title;
title.value = '标题B';
title.dispatch('input', { bubbles: true });        // 保存期间继续编辑（formRevision++）
await drain();
finish(protoReq, { protocol: { title: '标题A', search_strategies: [
  { database: 'pubmed', query_string: 'q', url: 'https://pubmed.ncbi.nlm.nih.gov/?term=q&filter=dates.2020-01-01-2024-12-31', line_count: 1 },
  { database: 'embase', query_string: 'q', url: 'https://www.embase.com/search#?q=q', line_count: 1 },
] } });
await drain();
out.statusAfter = status.textContent;
out.hasDateHint = strategyBox.querySelectorAll('.hint').some(p => textOf(p).indexOf('日期窗仅适用于 PubMed') >= 0);
out.toasts = __toasts.map(t => t.text);
process.stdout.write('\n' + JSON.stringify(out));
"""


class TestLuna403ProtocolSaveSnapshot(unittest.TestCase):
    """LUNA4-03 钉子：在途编辑后，成功反馈必须附带“最新内容尚未保存”标记；发送体仍是提交快照。"""

    def test_unsaved_marker_after_inflight_edit(self):
        out = _run_node_js(PROTOCOL_DRIVER, FRONTEND)
        self.assertEqual(out["postedTitle"], "标题A", "POST 体必须是提交时快照")
        self.assertIn("方案已保存到任务 t1", out["statusAfter"])
        self.assertIn("保存期间表单又有修改，界面最新内容尚未保存。", out["statusAfter"],
                      "LUNA4-03：在途编辑未被披露，成功反馈与可见内容不一致：%r" % out["statusAfter"])
        self.assertTrue(any("标题A" in t for t in out["toasts"]))

    def test_button_driven_edit_also_marks_unsaved(self):
        """评审 r2：MeSH/同义词/芯片/标准等按钮编辑不派发 input——保存期间经按钮
        加入内容后，同样必须提示「最新内容尚未保存」，否则可见内容≠快照而无提示。"""
        driver = PROTOCOL_DRIVER.replace(
            """title.value = '标题B';
title.dispatch('input', { bubbles: true });        // 保存期间继续编辑（formRevision++）""",
            """/* 保存期间用【按钮】加入 MeSH：不派发 input，只有 touchForm 能记账 */
const meshInput = panel.querySelectorAll('input').find(i => i.getAttribute('list'));
meshInput.value = 'Humans';
const addMeshBtn = panel.querySelectorAll('button').find(b => b.textContent === '添加 MeSH');
addMeshBtn.click();
out.postedMesh = JSON.stringify(protoReq.opts.body.pico[0].mesh_terms);""")
        self.assertIn("addMeshBtn", driver, "驱动改写失败：未找到 MeSH 添加按钮注入点")
        out = _run_node_js(driver, FRONTEND)
        self.assertEqual(out["postedMesh"], "[]", "POST 体应仍是提交快照（不含保存期间加入的 MeSH）")
        self.assertIn("保存期间表单又有修改，界面最新内容尚未保存。", out["statusAfter"],
                      "LUNA4-03：按钮驱动编辑未计入修订号，提示缺失：%r" % out["statusAfter"])


class TestB08N5DateWindowDisclosureUi(unittest.TestCase):
    """B08-N5（前端面）钉子：检测到日期过滤时必须在检索式区披露“日期窗仅适用于 PubMed”。"""

    def test_conditional_disclosure_and_dictionary_rows(self):
        out = _run_node_js(PROTOCOL_DRIVER, FRONTEND)
        self.assertTrue(out["hasDateHint"],
                        "B08-N5：带 filter=dates. 的方案渲染必须出现日期窗披露（此前四库同窗零提示）")
        # 自注册词条：键唯一（在文件内）、十槽完整、无空槽（回退消除）
        keys = [r["key"] for r in out["registered"]]
        self.assertEqual(len(keys), len(set(keys)), "自注册词条键必须唯一")
        for row in out["registered"]:
            self.assertEqual(row["slots"], 10, "十语言槽位完整：%r" % row)
            self.assertEqual(row["filled"], 10, "词条不得有空槽（回退中文）：%r" % row)
        self.assertIn("日期窗仅适用于 PubMed 检索式；Embase 与 Cochrane 链接不含日期过滤。", keys)
        self.assertIn("保存期间表单又有修改，界面最新内容尚未保存。", keys)


# ---------------------------------------------------------------------------
# B15-01：protocol_designer.js 注入样式每条字面量大括号自平衡
# ---------------------------------------------------------------------------

class TestB1501ProtocolCssBraces(unittest.TestCase):
    """B15-01 钉子：任一样式字面量缺 } 会让后续 10 条规则坠入嵌套块（非顶层）。"""

    def _literals(self):
        src = (FRONTEND / "protocol_designer.js").read_text(encoding="utf-8")
        m = re.search(r"style\.textContent\s*=\s*\[(.*?)\]\.join", src, re.S)
        self.assertIsNotNone(m, "找不到注入样式数组")
        return re.findall(r"'((?:[^'\\]|\\.)*)'", m.group(1))

    def test_every_style_literal_brace_balanced(self):
        lits = self._literals()
        self.assertGreaterEqual(len(lits), 13, "样式字面量数量异常：%d" % len(lits))
        for lit in lits:
            opens, closes = lit.count("{"), lit.count("}")
            self.assertEqual(opens, closes,
                             "B15-01：样式字面量大括号不自平衡（{=%d }=%d）：%s" % (opens, closes, lit[:70]))

    def test_all_rules_top_level(self):
        css = "\n".join(self._literals())
        depth, top_rules = 0, 0
        for ch in css:
            if ch == "{":
                if depth == 0:
                    top_rules += 1
                depth += 1
            elif ch == "}":
                depth -= 1
        self.assertEqual(depth, 0, "整段样式大括号不平衡")
        self.assertEqual(top_rules, len(self._literals()),
                         "B15-01：顶层规则数(%d)≠字面量数(%d)——有规则被吞进嵌套块" % (top_rules, len(self._literals())))


# ---------------------------------------------------------------------------
# MM11-06 + B08-N5(后端面)：protocol_designer.py
# ---------------------------------------------------------------------------

def _import_pd():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from coscreen import protocol_designer
    return protocol_designer


class TestMM1106PubMedDateValidation(unittest.TestCase):
    """MM11-06 钉子：日期窗非法输入不再原样拼 URL（参数注入/锚点截断/Markdown 链接闭合）。"""

    def test_injection_encoded_and_iso_unchanged(self):
        pd = _import_pd()
        url = pd.generate_pubmed_url("cancer trial", "2020-01-01&evil=1", "2021-01-01")
        self.assertIn("dates.2020-01-01%26evil%3D1-2021-01-01", url)
        self.assertNotIn("&evil=1", url, "MM11-06：& 注入额外 PubMed 查询参数")
        url = pd.generate_pubmed_url("cancer trial", "2020-01-01#frag", "")
        self.assertNotIn("#frag", url, "MM11-06：# 截断日期过滤")
        self.assertIn("%23frag", url)
        url = pd.generate_pubmed_url("cancer trial", "2020-01-01](evil)", "")
        self.assertNotIn("]", url, "MM11-06：] ) 可提前闭合导出 Markdown 链接")
        # 合法 YYYY-MM-DD 逐字节保持（不新增拒绝合法输入的严格性，R2）
        self.assertEqual(pd.generate_pubmed_url("cancer trial", "2020-01-01", "2024-12-31"),
                         "https://pubmed.ncbi.nlm.nih.gov/?term=cancer%20trial&filter=dates.2020-01-01-2024-12-31")

    def test_markdown_link_not_breakable(self):
        pd = _import_pd()
        strategies = pd.build_search_strategies(
            [pd.PICOElement(key="population", value="adult")], "2020-01-01](evil)", "2021-01-01")
        md = pd.protocol_to_markdown({"title": "T", "research_question": "", "pico": [],
                                      "inclusion_criteria": [], "exclusion_criteria": [],
                                      "search_strategies": [{"database": s.database, "query_string": s.query_string,
                                                             "url": s.url, "line_count": s.line_count} for s in strategies],
                                      "created_at": ""})
        link = [ln for ln in md.splitlines() if ln.startswith("[在 Pubmed 中打开]")][0]
        self.assertNotIn("](", link[len("[在 Pubmed 中打开]("):], "MM11-06：链接目标含未转义 ]( 可被提前闭合")

    def test_only_ascii_iso_dates_kept_verbatim(self):
        """评审 r2：`^\\d{4}-\\d{2}-\\d{2}$` 在 Python 下 $ 允许尾随换行、\\d 匹配
        Unicode 数字，二者都会让非 ISO 输入走「原样」分支。合法 ASCII ISO 日期
        仍须逐字节保持（R2），其余一律转义。"""
        pd = _import_pd()
        for good in ("2020-01-01", "1999-12-31", "0000-01-01", "9999-12-31"):
            self.assertEqual(pd.generate_pubmed_url("q", good, good),
                             "https://pubmed.ncbi.nlm.nih.gov/?term=q&filter=dates.%s-%s" % (good, good),
                             "合法 ISO 日期 %r 必须逐字节保持（R2）" % good)
        # 尾随换行：不得原样带出（否则导出 Markdown 链接被断成两行）
        url = pd.generate_pubmed_url("q", "2020-01-01\n", "")
        self.assertNotIn("\n", url, "MM11-06：尾随换行被原样带出，Markdown 链接会被截断")
        self.assertIn("%0A", url)
        # Unicode 数字（阿拉伯-印度数字）不得冒充合法日期
        url = pd.generate_pubmed_url("q", "٢٠٢٠-٠١-٠١", "")
        self.assertNotIn("٢", url, "MM11-06：Unicode 数字被当作合法日期原样拼入")
        self.assertIn("%D9%A2", url)


class TestB08N5DateWindowDisclosureBackend(unittest.TestCase):
    """B08-N5（后端面）钉子：日期窗只进 PubMed 链接这一事实必须在导出 Markdown 中披露。"""

    def _strategies(self, date_from="", date_to=""):
        pd = _import_pd()
        pico = [pd.PICOElement(key="population", value="adult"), pd.PICOElement(key="intervention", value="drug")]
        return pd, pd.build_search_strategies(pico, date_from, date_to)

    def test_only_pubmed_carries_dates(self):
        pd, strategies = self._strategies("2020-01-01", "2024-12-31")
        by_db = {s.database: s.url for s in strategies}
        self.assertIn("filter=dates.2020-01-01-2024-12-31", by_db["pubmed"])
        self.assertNotIn("2020-01-01", by_db["embase"], "B08-N5 前提核对：Embase 不带日期")
        self.assertNotIn("2020-01-01", by_db["cochrane"], "B08-N5 前提核对：Cochrane 不带日期")

    def _markdown(self, strategies):
        pd = _import_pd()
        return pd.protocol_to_markdown({"title": "T", "research_question": "", "pico": [],
                                        "inclusion_criteria": [], "exclusion_criteria": [],
                                        "search_strategies": [{"database": s.database, "query_string": s.query_string,
                                                               "url": s.url, "line_count": s.line_count} for s in strategies],
                                        "created_at": ""})

    def test_markdown_discloses_only_with_dates(self):
        _, with_dates = self._strategies("2020-01-01", "2024-12-31")
        self.assertIn("注：日期窗仅适用于 PubMed 检索式；Embase 与 Cochrane 链接不含日期过滤。",
                      self._markdown(with_dates), "B08-N5：导出文档未披露日期窗仅适用 PubMed")
        _, without = self._strategies()
        self.assertNotIn("日期窗仅适用于", self._markdown(without), "无日期窗时不应出现披露")


# ---------------------------------------------------------------------------
# LUNA4-02 + GLM14-01：evidence_mapper.js
# ---------------------------------------------------------------------------

EMAPPER_DRIVER = r"""
__byId['rfLivingReviewPanel'] = new El('section');
__docQ['.page[data-p="analysis"] .an-workspace'] = new El('div');
loadModule('evidence_mapper.js');
const out = {};
const panel = __byId['rfLivingReviewPanel']._afterNodes[0];
const runBtn = panel.querySelectorAll('button').find(b => b.textContent === '生成地图');
runBtn.click();
await pump();
const heat = panel.querySelector('.rf-em-heat');
const svg = heat.children.find(c => c.nodeType === 1 && c.tagName === 'svg');
const rects = svg.children.filter(c => c.nodeType === 1 && c.tagName === 'rect');
const gapData = rects.filter(r => r.classList.contains('gapcell') && r.children.length > 0);   // 有 <title> 的数据格
const gapLegend = rects.filter(r => r.classList.contains('gapcell') && r.children.length === 0);
const countData = rects.filter(r => r.classList.contains('countcell'));
out.gapDataCells = gapData.length;
out.gapLegendCells = gapLegend.length;
out.countCells = countData.length;
out.gapAttrs = gapData.map(r => ({ tabindex: r.getAttribute('tabindex'), role: r.getAttribute('role') }));
out.countAttrs = countData.map(r => ({ tabindex: r.getAttribute('tabindex'), role: r.getAttribute('role') }));
/* 键盘可达：Enter 直接在零计数格上开弹层（修复前 focus 不可达、Enter 无响应） */
gapData[0].dispatch('keydown', { key: 'Enter' });
await drain();
const dialog = document.body.children.filter(c => c.nodeType === 1 && c.classList.contains('rf-em-pop')).pop() || null;
out.enterOpensDialog = !!dialog;
out.dialogChip = dialog ? textOf(dialog.querySelector('.chip')) : null;
if (dialog) { dialog.remove(); }
gapData[0].dispatch('keydown', { key: ' ' });
await drain();
const dialog2 = document.body.children.filter(c => c.nodeType === 1 && c.classList.contains('rf-em-pop')).pop() || null;
out.spaceOpensDialog = !!dialog2;
process.stdout.write('\n' + JSON.stringify(out));
"""


class TestLuna402GapCellKeyboard(unittest.TestCase):
    """LUNA4-02 钉子：零计数缺口格 tabindex=0 + role=button，Enter/Space 可开弹层。"""

    def test_gap_cells_focusable_and_operable(self):
        out = _run_node_js(EMAPPER_DRIVER, FRONTEND)
        self.assertEqual(out["gapDataCells"], 2, "夹具 matrix=[[1,0],[0,2]] 应有 2 个零计数数据格")
        self.assertEqual(out["countCells"], 2)
        for attrs in out["gapAttrs"]:
            self.assertEqual(attrs["tabindex"], "0",
                             "LUNA4-02：零计数格不可聚焦（tabindex 缺失）：%r" % attrs)
            self.assertEqual(attrs["role"], "button",
                             "LUNA4-02：零计数格对读屏不是按钮（role 缺失）：%r" % attrs)
        for attrs in out["countAttrs"]:   # 正计数格回归：同等可达不得回退
            self.assertEqual(attrs["tabindex"], "0")
            self.assertEqual(attrs["role"], "button")
        self.assertTrue(out["enterOpensDialog"], "LUNA4-02：Enter 无法打开缺口格弹层")
        self.assertTrue(out["spaceOpensDialog"], "LUNA4-02：Space 无法打开缺口格弹层")
        self.assertEqual(out["dialogChip"], "研究缺口")

    def test_svg_container_does_not_clip_cells(self):
        """评审 r2：role="img" 会把后代从可访问性树裁掉，单元格的 role=button
        对读屏无效。容器必须是分组角色。"""
        src = (FRONTEND / "evidence_mapper.js").read_text(encoding="utf-8")
        self.assertIn("svg.setAttribute('role', 'group')", src,
                      "LUNA4-02：热力图 svg 仍是 role=img，单元格对读屏不可达")
        self.assertNotIn("svg.setAttribute('role', 'img')", src)


class TestGLM1401HeaderMatchesListeners(unittest.TestCase):
    """GLM14-01 钉子：头注声称的每个 reviewflow:* 事件都必须真实注册。"""

    def test_comment_events_all_registered(self):
        src = (FRONTEND / "evidence_mapper.js").read_text(encoding="utf-8")
        header = src.split("(function")[0]
        mentioned = set(re.findall(r"reviewflow:[a-z0-9-]+", header))
        registered = set(re.findall(r"addEventListener\('(reviewflow:[a-z0-9-]+)'", src))
        self.assertTrue(mentioned, "头注应列举事件接入")
        unregistered = mentioned - registered
        self.assertEqual(unregistered, set(),
                         "GLM14-01：头注声称监听但未注册的事件：%r" % unregistered)
        self.assertIn("reviewflow:analysis-task-changed", registered)
        self.assertIn("reviewflow:language-changed", registered)


# ---------------------------------------------------------------------------
# MM9-17：index.html RFLang.register 任一槽位查重（zh 门 → 全槽位门）
# ---------------------------------------------------------------------------

MM917_DRIVER = r"""
const html = fs.readFileSync(require('path').join(process.argv[2], 'index.html'), 'utf8');
const start = html.indexOf('register(pairs) {');
if (start < 0) { process.stdout.write('\n' + JSON.stringify({ error: 'register not found' })); return; }
const open = html.indexOf('{', start);
let depth = 0, i = open;
for (; i < html.length; i++) { const c = html[i]; if (c === '{') depth++; else if (c === '}') { depth--; if (depth === 0) break; } }
const body = html.slice(open + 1, i);
const languages = [["zh-CN","中文"],["en","English"],["fr","Français"],["ru","Русский"],["es","Español"],["ja","日本語"],["pt","Português"],["de","Deutsch"],["sr","Српски"],["ko","한국어"]];
const dictionary = new Map();
let reverseLanguage = 0;
const observer = { disconnect() {} };
const translateTree = () => {};
const observe = () => {};
const warns = [];
const console = { warn: (...a) => warns.push(a.join(' ')) };
const register = new Function('pairs', 'languages', 'dictionary', 'reverseLanguage', 'observer', 'translateTree', 'observe', 'console',
  body + '\n');
const out = {};
register([['概念一', 'EN1', 'FR SAME', 'RU1', 'ES1', 'JA1', 'PT1', 'DE1', 'SR1', 'KO1']], languages, dictionary, reverseLanguage, observer, translateTree, observe, console);
register([['概念二', 'EN2', 'FR SAME', 'RU2', 'ES2', 'JA2', 'PT2', 'DE2', 'SR2', 'KO2']], languages, dictionary, reverseLanguage, observer, translateTree, observe, console);
out.slotWarnFired = warns.some(w => w.indexOf('词典槽位冲突') >= 0 && w.indexOf('FR SAME') >= 0 && w.indexOf('概念') >= 0);
out.slotWarnCount = warns.filter(w => w.indexOf('词典槽位冲突') >= 0).length;
out.winner = (dictionary.get('FR SAME') || [null])[0];   // 覆盖行为必须保持（R3：仅告警不改行为）
const before = warns.length;
register([['概念二', 'EN2', 'FR SAME', 'RU2', 'ES2', 'JA2', 'PT2', 'DE2', 'SR2', 'KO2']], languages, dictionary, reverseLanguage, observer, translateTree, observe, console);
out.identicalReregisterQuiet = warns.length === before;   // 内容一致的重复注册不打扰
/* 评审 r2：措辞孪生（en 相同、外语槽恰好写法一致）不是 harmful 冲突，不得告警 */
const beforeTwin = warns.length;
register([['孪生甲', 'Save assessment', 'SS', 'RU', 'ES', 'JA', 'PT', 'DE', 'SR', 'KO']], languages, dictionary, reverseLanguage, observer, translateTree, observe, console);
register([['孪生乙', 'Save assessment', 'SS', 'RU', 'ES', 'JA', 'PT', 'DE', 'SR', 'KO']], languages, dictionary, reverseLanguage, observer, translateTree, observe, console);
out.twinQuiet = warns.filter(w => w.indexOf('词典槽位冲突') >= 0).length ===
  warns.slice(0, beforeTwin).filter(w => w.indexOf('词典槽位冲突') >= 0).length;
register([['概念一', 'EN1-DIFF', 'FR1B', 'RU1', 'ES1', 'JA1', 'PT1', 'DE1', 'SR1', 'KO1']], languages, dictionary, reverseLanguage, observer, translateTree, observe, console);
out.zhKeyWarnStillFires = warns.some(w => w.indexOf('词典键冲突') >= 0 && w.indexOf('概念一') >= 0);
out.winnerZh = (dictionary.get('概念一') || [])[0];
process.stdout.write('\n' + JSON.stringify(out));
"""


class TestMM917AnySlotDictionaryGuard(unittest.TestCase):
    """MM9-17 钉子：不同 zh 共享非 zh 槽位时运行时守卫必须告警；zh 键守卫与覆盖行为保持。"""

    def test_any_slot_collision_warns(self):
        out = _run_node_js(MM917_DRIVER, FRONTEND)
        self.assertNotIn("error", out)
        self.assertTrue(out["slotWarnFired"],
                        "MM9-17：非 zh 槽位碰撞（概念一/概念二共用 fr 串）未触发任一槽位守卫——原 zh 门控盲区仍在")
        self.assertEqual(out["winner"], "概念二", "守卫只告警，不得改变后注册者胜的覆盖行为（R3）")
        self.assertTrue(out["identicalReregisterQuiet"], "内容一致的重复注册不应打扰")
        self.assertTrue(out["zhKeyWarnStillFires"], "MM5-03 的 zh 键冲突守卫必须保留")
        self.assertEqual(out["winnerZh"], "概念一")
        self.assertTrue(out["twinQuiet"],
                        "MM9-17：en 相同的措辞孪生仍报槽位冲突——与 harmful 定义不符且淹没真冲突")


# ---------------------------------------------------------------------------
# MM11-11：猴补丁加载顺序守卫
# ---------------------------------------------------------------------------

class TestMM1111LoadOrderGuard(unittest.TestCase):
    """MM11-11 钉子：内联 renderImportReport 定义必须先于 import_export_workspace.js 外链。"""

    def test_inline_definition_precedes_external_link(self):
        html = INDEX.read_text(encoding="utf-8")
        def_at = html.find("function renderImportReport")
        link_at = html.find('./import_export_workspace.js?v=')
        self.assertGreaterEqual(def_at, 0, "index.html 内联 renderImportReport 定义缺失")
        self.assertGreaterEqual(link_at, 0, "index.html import_export_workspace.js 外链缺失")
        self.assertLess(def_at, link_at,
                        "MM11-11：外链被移到内联定义之前会让猴补丁抛 ReferenceError（重排即红）")
        close_at = html.find("</script>", def_at)
        self.assertGreater(close_at, 0)
        self.assertLess(close_at, link_at, "renderImportReport 必须定义于内联脚本块内，且该块先于外链结束")

    def test_monkeypatch_has_explicit_guard(self):
        """语义钉：真正加载模块并在 renderImportReport 缺失时执行——
        块注释/字符串化等「保留字面量但取消执行」的破坏必须红。"""
        driver = r"""
globalThis.window = globalThis;   // import_export_workspace.js 在 window 上挂 UI
let thrown = null;
try { loadModule('import_export_workspace.js'); }
catch (e) { thrown = { name: String(e && e.name), message: String(e && e.message) }; }
process.stdout.write('\n' + JSON.stringify({ thrown }));
"""
        out = _run_node_js(driver, FRONTEND)
        self.assertIsNotNone(out["thrown"],
                             "MM11-11：renderImportReport 未定义时模块应显式报错，实际静默/裸 ReferenceError 前无守卫")
        self.assertEqual(out["thrown"]["name"], "Error",
                         "MM11-11：应抛具名 Error 而非裸 ReferenceError：%r" % out["thrown"])
        self.assertIn("renderImportReport", out["thrown"]["message"],
                      "MM11-11：守卫报错信息应指出缺失的 renderImportReport：%r" % out["thrown"])


# ---------------------------------------------------------------------------
# 批13-1：analysis_subgroup_random.js 尺度分支
# ---------------------------------------------------------------------------

PI13_1_DRIVER = r"""
const src = fs.readFileSync(require('path').join(process.argv[2], 'analysis_subgroup_random.js'), 'utf8');
const m = src.match(/const scale = (data\.analysis_scale[^;]+);/);
const out = { found: !!m };
if (m) {
  const f = new Function('T', 'data', 'return ' + m[1] + ';');
  const T = x => x;
  out.map = {};
  for (const v of ['log', 'fisher_z', 'logit', 'natural']) out.map[v] = f(T, { analysis_scale: v });
}
process.stdout.write('\n' + JSON.stringify(out));
"""


class TestPi131SubgroupScaleBranches(unittest.TestCase):
    """批13-1 钉子：fisher_z/logit 不得再显示为“自然尺度”；两词条键在词典中十槽齐全。"""

    def test_scale_ternary_covers_registry_scales(self):
        out = _run_node_js(PI13_1_DRIVER, FRONTEND)
        self.assertTrue(out["found"], "找不到尺度三元表达式")
        self.assertEqual(out["map"]["log"], "对数尺度")
        self.assertEqual(out["map"]["fisher_z"], "Fisher z 尺度",
                         "批13-1：fisher_z 被误标（修复前落入“自然尺度”）：%r" % out["map"]["fisher_z"])
        self.assertEqual(out["map"]["logit"], "logit 尺度",
                         "批13-1：logit 被误标（修复前落入“自然尺度”）：%r" % out["map"]["logit"])
        self.assertEqual(out["map"]["natural"], "自然尺度")

    def test_scale_label_keys_registered(self):
        """词条键复用既有注册（与 analysis_meta_regression.js:132 同键）：
        zh 键 + 非空 en 槽（非中文界面显示尺度代码，不回退中文）。"""
        locale = (FRONTEND / "analysis_locale.js").read_text(encoding="utf-8")
        for key in ("Fisher z 尺度", "logit 尺度"):
            at = locale.find('"%s"' % key)
            self.assertGreaterEqual(at, 0, "词条 %s 缺失" % key)
            # 行可能跨多行：从键起扫描到本行数组的闭合 ]，收集全部字符串槽
            depth, end = 0, None
            i = at
            while i < len(locale):
                c = locale[i]
                if c == "[":
                    depth += 1
                elif c == "]":
                    if depth == 0:
                        end = i
                        break
                    depth -= 1
                i += 1
            self.assertIsNotNone(end, "词条 %s 行未闭合" % key)
            fields = re.findall(r'"(?:[^"\\]|\\.)*"', locale[at:end])
            self.assertGreaterEqual(len(fields), 2, "词条 %s 至少含 zh+en 槽" % key)
            self.assertEqual(fields[0], '"%s"' % key)
            self.assertTrue(len(fields[1]) > 2, "词条 %s 的 en 槽非空（非中文界面不得回退中文）" % key)


# ---------------------------------------------------------------------------
# 批13-2 / 批13-2b：pscores / ranks catch 的 known 串守卫
# ---------------------------------------------------------------------------

class TestPi132KnownMessageGuards(unittest.TestCase):
    """批13-2/2b 钉子：catch 不得把本模块自抛的 T() 中文校验串标成 lang:'en'。"""

    def _catch_line(self, fname):
        src = (FRONTEND / fname).read_text(encoding="utf-8")
        lines = [ln for ln in src.splitlines() if "catch (error)" in ln and "replaceChildren" in ln]
        self.assertEqual(len(lines), 1, "%s 应恰有一行 catch 渲染" % fname)
        return lines[0]

    def test_pscores_known_guard(self):
        line = self._catch_line("analysis_network_pscores.js")
        self.assertIn(".some(key=>T(key)===error.message)", line, "批13-2：缺 known 串守卫")
        for key in ("请选择上方的随机效应 REML 网络模型。", "请选择有利方向。"):
            self.assertIn(key, line, "批13-2：自抛校验串 %r 必须在 known 清单中" % key)
        self.assertNotIn("{lang:'en',text:error.message}", line,
                         "批13-2：catch 不得无条件 lang:'en'（中文校验串会被标英文）")

    def test_ranks_known_guard(self):
        line = self._catch_line("analysis_network_ranks.js")
        self.assertIn(".some(key=>T(key)===error.message)", line, "批13-2b：缺 known 串守卫")
        for key in ("请选择上方的随机效应 REML 网络模型。", "请选择有利方向。",
                    "模拟次数须为 2–100000 的整数，随机种子须为 0–4,294,967,295 的整数。"):
            self.assertIn(key, line, "批13-2b：自抛校验串 %r 必须在 known 清单中" % key)
        self.assertNotIn("{lang:'en',text:error.message}", line,
                         "批13-2b：catch 不得无条件 lang:'en'")


# ---------------------------------------------------------------------------
# 缓存令牌：本组改动资产 ?v= bump
# ---------------------------------------------------------------------------

class TestVersionTokenBump(unittest.TestCase):
    """改动资产的缓存令牌必须随本组修复 bump（防旧缓存延续缺陷）。"""

    ASSETS = [
        "analysis_grade.js", "protocol_designer.js", "evidence_mapper.js",
        "import_export_workspace.js", "analysis_network_pscores.js",
        "analysis_network_ranks.js", "analysis_subgroup_random.js",
    ]

    def test_all_touched_assets_bumped(self):
        html = INDEX.read_text(encoding="utf-8")
        for asset in self.ASSETS:
            self.assertIn('./%s?v=20261030-r8fl5' % asset, html,
                          "%s 的 ?v= 令牌未随 r8-fl5 修复 bump" % asset)


if __name__ == "__main__":
    unittest.main(verbosity=2)
