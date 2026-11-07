# -*- coding: utf-8 -*-
"""r7-lastfix · LUNA4-01 钉子（extraction_workbench.js 模板拉取失败后的重试）。

缺陷（mm11_l4_verify_l4.md §2，confirmed / 应修（同批））：loadTemplates() 的
catch 把 templates 固化为空数组（truthy），入口 :152 的 truthiness 判断因此
在本次页面会话内永久抑制重试；恢复网络也无法自愈，只有整页重载才能恢复。

修复：失败态保留未加载信号（catch 里 templates = null），再次进入编码页
（reviewflow:coding-loaded / workspace-changed → waitForCoding）即重拉；
成功路径行为逐位不变（成功后仍会话内缓存，不再发请求）。

本文件两层钉子：
  1. 静态断言：catch 分支必须是 templates = null；旧缺陷字面量
     ``templates = [];`` 不得再出现；try 分支（成功路径）与 :152 入口守卫
     逐字保留——撤掉修复（还原 catch）即红。
  2. 行为钉子（node 真实加载模块 + 失败/恢复桩，复审复现手法的 node 版）：
     fail-recover 模式断言「失败 → 再进入仍失败（应重发 API）→ 恢复后再进入
     （应重发且下拉出现模板）」；ok 模式断言成功对照不变（多次再进入不发
     请求、下拉仍有模板）。另设一条「撤修复必红」自检：把 catch 还原为
     ``templates = [];`` 写入临时副本，钉子必须对副本变红。
"""
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
MODULE = REPO / "custom_frontend" / "extraction_workbench.js"

NODE_CANDIDATES = (
    r"C:\Users\16073\AppData\Local\hermes\node\node.exe",
)

ORIGINAL_CATCH = "} catch (e) { templates = []; /* 拉取失败：占位提示，不影响编码页 */ }"
FIXED_CATCH = "} catch (e) { templates = null; /* 拉取失败：保留未加载态（LUNA4-01），再次进入编码页时重拉，不固化为空数组 */ }"
SUCCESS_ASSIGN = "templates = (d && Array.isArray(d.templates)) ? d.templates : [];"
ENTRY_GUARD = "if (templates || typeof api !== 'function' || typeof taskApi !== 'function') return;"


def _node_exe():
    for exe in (*NODE_CANDIDATES, shutil.which("node")):
        if exe and pathlib.Path(exe).is_file():
            return str(exe)
    return None


class Luna401StaticTests(unittest.TestCase):
    """静态断言：撤修复（还原 catch 为空数组）必红。"""

    def setUp(self):
        self.src = MODULE.read_text(encoding="utf-8")

    def test_catch_keeps_unloaded_state_null(self):
        self.assertRegex(
            self.src, r"catch\s*\(\s*e\s*\)\s*\{\s*templates\s*=\s*null\s*;",
            "loadTemplates 的 catch 必须把 templates 恢复为 null（失败态保留可重试信号，LUNA4-01）")

    def test_bug_literal_templates_empty_array_gone(self):
        self.assertIsNone(
            re.search(r"templates\s*=\s*\[\]", self.src),
            "旧缺陷字面量 `templates = [];` 不得再出现（空数组 truthy 会永久抑制重试）")

    def test_success_path_assignment_unchanged(self):
        self.assertIn(SUCCESS_ASSIGN, self.src,
                      "成功路径（try 分支）必须逐字保留：响应缺 templates 时仍按空数组缓存、不重试")

    def test_entry_guard_unchanged(self):
        self.assertIn(ENTRY_GUARD, self.src,
                      "loadTemplates 入口守卫必须逐字保留（成功后仍会话内缓存，不再发请求）")


class Luna401BehaviorTests(unittest.TestCase):
    """行为钉子：node 真实加载 custom_frontend/extraction_workbench.js + 受控 api 失败/恢复桩。"""

    def _run_nail(self, mode, module_path=None):
        exe = _node_exe()
        if exe is None:
            raise unittest.SkipTest("本机无 node 可执行文件（LUNA4-01 行为级钉子不可运行）")
        module_path = pathlib.Path(module_path or MODULE)
        with tempfile.TemporaryDirectory(prefix="rf214_luna401_") as td:
            harness = pathlib.Path(td) / "luna4_01_nail.cjs"
            harness.write_text(HARNESS_JS, encoding="utf-8")
            proc = subprocess.run(
                [exe, str(harness), str(module_path), mode],
                cwd=str(REPO), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120)
        return proc

    def test_failure_keeps_retry_and_recovery_shows_templates(self):
        proc = self._run_nail("fail-recover")
        self.assertEqual(
            proc.returncode, 0,
            "LUNA4-01 钉子失败（失败后不再重试 / 恢复后下拉无模板）：\n"
            + (proc.stdout + proc.stderr)[-4000:])
        self.assertIn("LUNA4-01 nail: PASS (fail-recover)", proc.stdout)

    def test_success_control_single_fetch_and_cache_kept(self):
        proc = self._run_nail("ok")
        self.assertEqual(
            proc.returncode, 0,
            "LUNA4-01 成功对照失败（成功路径行为被改动）：\n"
            + (proc.stdout + proc.stderr)[-4000:])
        self.assertIn("LUNA4-01 nail: PASS (ok)", proc.stdout)

    def test_nail_goes_red_when_fix_reverted(self):
        """撤修复必红自检：catch 还原为 templates = []; 后，行为钉子必须失败。"""
        reverted = self.src_with_fix_reverted()
        with tempfile.TemporaryDirectory(prefix="rf214_luna401_rev_") as td:
            rev_module = pathlib.Path(td) / "extraction_workbench.reverted.js"
            rev_module.write_text(reverted, encoding="utf-8")
            proc = self._run_nail("fail-recover", module_path=rev_module)
        self.assertNotEqual(
            proc.returncode, 0,
            "撤掉修复（catch 还原为 templates = [];）后钉子仍绿——钉子失效：\n"
            + (proc.stdout + proc.stderr)[-4000:])
        self.assertIn("LUNA4-01 nail: FAIL (fail-recover)", proc.stdout)

    def src_with_fix_reverted(self):
        src = MODULE.read_text(encoding="utf-8")
        self.assertIn(FIXED_CATCH, src, "修复 catch 字面量不匹配——请同步更新本测试的 FIXED_CATCH/ORIGINAL_CATCH")
        reverted = src.replace(FIXED_CATCH, ORIGINAL_CATCH)
        self.assertNotEqual(reverted, src)
        return reverted


HARNESS_JS = r"""'use strict';
/* LUNA4-01 行为钉子（node 版）：vm 沙箱真实执行模块源码，注入受控桩
 * （T/el/S/api/taskApi/toast/document），复现复审的失败/恢复探针。
 * 用法: node luna4_01_nail.cjs <module.js> <fail-recover|ok>
 * 证据以 {"k":..,"v":..} 行输出；断言失败打印 FAIL 并以非零退出。 */
const fs = require('node:fs');
const vm = require('node:vm');

const modulePath = process.argv[2];
const mode = process.argv[3] || 'fail-recover';
const src = fs.readFileSync(modulePath, 'utf8');

const BENCH_FORM = { stub: 'bench-form' };
const BENCH_PAGE = { stub: 'bench-page' };

function makeNode(tag) {
  const node = {
    tagName: String(tag).toUpperCase(),
    children: [],
    options: [],
    listeners: {},
    dataset: {},
    classList: { add() {}, remove() {} },
    value: '', hidden: false, disabled: false,
    append(...nodes) {
      for (const n of nodes) {
        if (n == null) continue;
        this.children.push(n);
        if (this.tagName === 'SELECT') this.options.push(n);
      }
    },
    appendChild(n) { this.children.push(n); return n; },
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    setAttribute(k, v) { this['attr:' + k] = String(v); },
    getAttribute(k) { const v = this['attr:' + k]; return v == null ? null : v; },
    removeAttribute(k) { delete this['attr:' + k]; },
    replaceChildren(...nodes) { this.children = []; this.options = []; this.append(...nodes); },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    after(n) { this._afterNode = n; },
    closest(sel) { return sel === '.bench-form' ? BENCH_FORM : null; },
    focus() {},
  };
  let text = '';
  Object.defineProperty(node, 'textContent', {
    configurable: true,
    get() { return text; },
    set(v) {
      text = v == null ? '' : String(v);
      if (node.tagName === 'SELECT') { node.children = []; node.options = []; }
    },
  });
  return node;
}

const byId = {};
function el(tag, attrs, ...children) {
  const node = makeNode(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null) continue;
      if (k === 'class') node.className = v;
      else if (k === 'hidden') node.hidden = (v === '' ? true : !!v);
      else if (k === 'text') node.textContent = v;
      else node[k] = v;
    }
  }
  for (const c of children) if (c != null) node.append(c);
  if (attrs && attrs.id) byId[attrs.id] = node;
  return node;
}

function T(s, ...args) {
  let r = String(s);
  args.forEach((a, i) => { r = r.split('{' + i + '}').join(String(a)); });
  return r;
}

const apiCalls = [];
const apiPlan = mode === 'ok' ? ['ok'] : ['fail', 'fail', 'ok'];
function api(url, opts) {
  apiCalls.push({ url: url, method: (opts && opts.method) || 'GET' });
  const step = apiPlan.length > 1 ? apiPlan.shift() : apiPlan[0];
  if (step === 'fail') return Promise.reject(new Error('network down (stub)'));
  return Promise.resolve({
    templates: [{
      template_id: 'rct_binary',
      study_type: 'rct',
      outcome_type: 'binary',
      fields: [{ key: 'n_e', label: '试验组事件数', input_type: 'number', required: true }],
      effect_measure: 'RR',
    }],
  });
}

const S = {
  task: { task_id: 't1' },
  user: { user_id: 'u1' },
  screener: 'sc',
  page: 'bench',
  cbQueue: [{ zotero_key: 'K1', title: 'Article One' }],
  cbIdx: 0,
};

const formsStub = makeNode('div');
const docListeners = {};
const documentStub = {
  head: makeNode('head'),
  documentElement: makeNode('html'),
  getElementById(id) { return id === 'cbForms' ? formsStub : null; },
  querySelector(sel) { return sel === '.page[data-p="bench"]' ? BENCH_PAGE : null; },
  createElement(tag) { return makeNode(tag); },
  addEventListener(type, fn) { (docListeners[type] = docListeners[type] || []).push(fn); },
};
function dispatchDoc(type, detail) {
  for (const fn of (docListeners[type] || []).slice()) fn({ type: type, detail: detail });
}

const sandbox = {
  document: documentStub,
  T: T, el: el, S: S, api: api,
  taskApi: function () { return '/api/tasks/t1'; },
  toast: function () {},
  window: {},
  console: console,
  setTimeout: function (fn, ms) { return setTimeout(fn, ms); },
  clearTimeout: clearTimeout,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

async function flush() { await new Promise((r) => setTimeout(r, 10)); }

function optionsSnapshot(tpl) {
  return tpl.options.map((o) => ({ value: o.value, text: o.textContent }));
}

(async function main() {
  vm.runInContext(src, sandbox, { filename: modulePath });
  await flush();            /* S.page='bench' → waitForCoding → loadTemplates 第 1 次 */
  const tpl = byId['rfXTpl'];
  const out = [];
  const put = (k, v) => { out.push({ k: k, v: v }); };

  if (!tpl) {
    console.log(JSON.stringify({ k: 'fatal', v: 'rfXTpl not created (module bailed out?)' }));
    console.log('LUNA4-01 nail: FAIL (' + mode + ')');
    process.exit(1);
  }

  const bad = [];
  if (mode === 'fail-recover') {
    put('calls_after_first_load', apiCalls.length);
    put('options_after_first_load', optionsSnapshot(tpl));
    if (apiCalls.length !== 1) bad.push(['calls_after_first_load', apiCalls.length, '应为 1（初始失败一次）']);
    if (!tpl.options.length || tpl.options[0].value !== '' ||
        tpl.options.some((o) => o.value === 'rct_binary'))
      bad.push(['options_after_first_load', optionsSnapshot(tpl), '失败后下拉应只剩占位“选择研究设计”']);

    dispatchDoc('reviewflow:coding-loaded');      /* 再进入 1：仍失败，但必须重发请求 */
    await flush();
    put('after_workspace_reentry_calls', apiCalls.length);
    put('after_workspace_reentry_options', optionsSnapshot(tpl));
    if (apiCalls.length !== 2)
      bad.push(['after_workspace_reentry_calls', apiCalls.length, '失败后再进入应重新发起 API 调用（应为 2）']);

    dispatchDoc('reviewflow:coding-loaded');      /* 再进入 2：网络恢复 → 重拉成功 */
    await flush();
    put('after_recovery_calls', apiCalls.length);
    put('after_recovery_options', optionsSnapshot(tpl));
    if (apiCalls.length !== 3)
      bad.push(['after_recovery_calls', apiCalls.length, '恢复后再进入应再次发起 API 调用（应为 3）']);
    if (!tpl.options.some((o) => o.value === 'rct_binary' && o.textContent === 'RCT · 二分类结局'))
      bad.push(['after_recovery_options', optionsSnapshot(tpl), '恢复后下拉应出现 rct_binary 模板']);
    put('all_api_calls', apiCalls.map((c) => c.url));
    if (!apiCalls.every((c) => c.url === '/api/tasks/t1/extraction/templates'))
      bad.push(['all_api_calls', apiCalls.map((c) => c.url), '应全部请求 /extraction/templates']);
  } else {
    put('calls_after_first_load', apiCalls.length);
    put('options_after_first_load', optionsSnapshot(tpl));
    if (apiCalls.length !== 1) bad.push(['calls_after_first_load', apiCalls.length, '成功对照：初始只应 1 次调用']);
    if (!tpl.options.some((o) => o.value === 'rct_binary'))
      bad.push(['options_after_first_load', optionsSnapshot(tpl), '成功对照：下拉应出现 rct_binary']);

    dispatchDoc('reviewflow:coding-loaded');
    await flush();
    put('after_coding_loaded_calls', apiCalls.length);
    if (apiCalls.length !== 1)
      bad.push(['after_coding_loaded_calls', apiCalls.length, '成功后再进入不得重拉（会话内缓存）']);

    dispatchDoc('reviewflow:workspace-changed', 'bench');
    await flush();
    put('after_workspace_changed_calls', apiCalls.length);
    if (apiCalls.length !== 1)
      bad.push(['after_workspace_changed_calls', apiCalls.length, '成功后 workspace-changed 不得重拉']);

    dispatchDoc('reviewflow:language-changed');
    await flush();
    put('after_language_changed_calls', apiCalls.length);
    put('after_language_changed_options', optionsSnapshot(tpl));
    if (apiCalls.length !== 1)
      bad.push(['after_language_changed_calls', apiCalls.length, '成功后 language-changed 不得重拉']);
    if (!tpl.options.some((o) => o.value === 'rct_binary'))
      bad.push(['after_language_changed_options', optionsSnapshot(tpl), 'language-changed 重填后模板仍应在下拉中']);
  }

  for (const line of out) console.log(JSON.stringify(line));
  if (bad.length) {
    for (const b of bad) console.log('FAIL ' + b[0] + ' = ' + JSON.stringify(b[1]) + ' 期望: ' + b[2]);
    console.log('LUNA4-01 nail: FAIL (' + mode + ')');
    process.exit(1);
  }
  console.log('LUNA4-01 nail: PASS (' + mode + ')');
  process.exit(0);
})().catch((e) => {
  console.log('HARNESS ERROR: ' + ((e && e.stack) || e));
  console.log('LUNA4-01 nail: FAIL (' + mode + ')');
  process.exit(1);
});
"""


if __name__ == "__main__":
    unittest.main()
