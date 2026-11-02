# -*- coding: utf-8 -*-
"""R11 · ERIC 前端模块钉子（node 真实加载 custom_frontend/eric_search.js）。

覆盖（钉子=破坏即红）：
- 真实 ``require`` 模块（node 下 IIFE 无 DOM/RFLang 副作用、导出纯逻辑 API）；
- 十语言词条：每条恰 10 列、非空、``{N}`` 占位符跨语言一致；
- 无死词条：表内每个中文源串都被代码真实引用，且每个 ``T()`` 键都已注册
  （双向闭包；剥掉注册表本身再扫，防恒真）；
- 渲染红线：无 ``innerHTML``/``outerHTML``/``insertAdjacentHTML``/``document.write``；
- 出站红线：前端不直连 ERIC，只调本应用 ``/eric/search`` 与 ``/eric/import``；
- 错误分流：五类后端错误 code 在英文界面不泄漏中文；超时/不可达/超限/零结果
  各自给已注册的可读文案；
- 预览/导入摘要与占位填充；
- index.html 挂载锚点与 ``?v=20261102-r11eric1`` 令牌。

node 脚本以 --e 内嵌（不落临时文件），模块路径经 argv 传入；真实加载与断言
都在 node 内完成，Python 只负责断言结构化输出。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "custom_frontend"
MODULE = FRONTEND / "eric_search.js"
INDEX = FRONTEND / "index.html"
NODE = (
    os.environ.get("RF_NODE")
    or shutil.which("node")
    or r"C:\Users\16073\AppData\Local\hermes\node\node.exe"
)
pytestmark = pytest.mark.skipif(not os.path.exists(NODE), reason="node runtime not found")

HARNESS = r"""
'use strict';
const fs = require('fs');
const modulePath = process.argv[1];
const U = require(modulePath);
const src = fs.readFileSync(modulePath, 'utf8');

/* 与词典回放工具同口径：抽取 RFLang.register([...]) 的字面量数组。 */
function extractRows(text) {
  const m = /RFLang\s*\.\s*register\s*\(\s*\[/.exec(text);
  if (!m) throw new Error('RFLang.register([ 字面量必须存在');
  const start = m.index + m[0].length - 1;
  let depth = 0;
  for (let i = start; i < text.length; i += 1) {
    if (text[i] === '[') depth += 1;
    else if (text[i] === ']') {
      depth -= 1;
      if (depth === 0) return Function('return ' + text.slice(start, i + 1))();
    }
  }
  throw new Error('literal array is not balanced');
}
const rows = extractRows(src);

/* 剥掉注册表本体，只留下代码正文（死词条检测不能把表里的键当引用）。 */
let regStart = src.indexOf('RFLang.register([');
let depth = 0, regEnd = -1;
for (let i = src.indexOf('[', regStart); i < src.length; i += 1) {
  if (src[i] === '[') depth += 1;
  else if (src[i] === ']') {
    depth -= 1;
    if (depth === 0) { regEnd = i + 1; break; }
  }
}
const body = src.slice(0, regStart) + src.slice(regEnd);
const keys = new Set(rows.map((row) => row[0]));
const dead = [...keys].filter((key) => !body.includes(key));
const tCalls = [...body.matchAll(/\bT\('([^']*)'/g)].map((m) => m[1]);
const missing = [...new Set(tCalls)].filter((key) => !keys.has(key));

const dict = new Map(rows.map((row) => [row[0], row[1]]));
function T(tpl, ...args) {
  const out = dict.has(tpl) ? dict.get(tpl) : tpl;
  return String(out).replace(/\{(\d+)\}/g, (_, i) =>
    args[Number(i)] === undefined || args[Number(i)] === null ? '' : String(args[Number(i)]));
}
const CJK = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;
const BACKEND_CODES = ['eric_unreachable', 'eric_timeout', 'eric_redirect',
  'eric_http_error', 'eric_bad_response', 'eric_response_too_large',
  'eric_incomplete', 'eric_query_error', 'eric_bad_query', 'eric_too_many_hits',
  'eric_no_results', 'eric_unavailable', 'eric_totally_new'];
const errors = {};
for (const code of BACKEND_CODES) {
  const raw = U.errorText({ code, message: '后端中文技术串' });
  errors[code] = { raw: raw, en: T(raw) };
}
const unknownEn = T(U.errorText({ code: 'eric_totally_new', message: '服务端中文' }));
const nullEn = T(U.errorText(null));

process.stdout.write(JSON.stringify({
  exports: Object.keys(U).sort(),
  rowCount: rows.length,
  rows: rows,
  dead: dead,
  missing: missing,
  tCalls: [...new Set(tCalls)],
  dangerous: /\.innerHTML\s*=/.test(src) || /\.outerHTML\s*=/.test(src)
    || /insertAdjacentHTML\s*\(/.test(src) || /document\.write\s*\(/.test(src),
  directNetwork: /\bfetch\s*\(\s*['"]https?:/.test(src)
    || /XMLHttpRequest/.test(src) || /WebSocket/.test(src)
    || /sendBeacon/.test(src),
  endpoints: {
    search: src.includes("'/eric/search'"),
    import: src.includes("'/eric/import'"),
    ericHost: src.includes('api.ies.ed.gov')
  },
  errors: errors,
  unknownEn: unknownEn,
  nullEn: nullEn,
  previewZero: U.previewSummary({ total_hits: 0 }),
  previewHits: U.previewSummary({ total_hits: 123, returned: 20 }),
  previewEmpty: U.previewSummary(null),
  importStaged: U.importSummary({ staged: true, total_parsed: 12 }),
  importDirect: U.importSummary({ staged: false, total_parsed: 10,
    exact_duplicate_excess: 2, imported: 8 }),
  importSingle: U.importSummary({ staged: false, n_parsed: 10,
    exact_duplicate_excess: 2, n_imported: 8 }),
  importStagedSingle: U.importSummary({ staged: true, n_parsed: 2 }),
  importEmpty: U.importSummary({}),
  filled: U.fill('a {0} b {1}', ['x', 'y']),
  fillMissing: U.fill('{0}-{1}', ['only']),
}));
"""


@pytest.fixture(scope="module")
def probe():
    proc = subprocess.run(
        [NODE, "-e", HARNESS, str(MODULE)],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


class TestModuleSurface:
    def test_real_require_exports_pure_api(self, probe):
        for name in ("errorText", "isNetworkFailure", "previewSummary",
                     "importSummary", "fill"):
            assert name in probe["exports"], f"{name} must be exported"

    def test_ten_language_rows_are_complete_and_consistent(self, probe):
        assert probe["rowCount"] >= 18, probe["rowCount"]
        for row in probe["rows"]:
            assert len(row) == 10, f"row must have 10 languages: {row[0]}"
            for value in row:
                assert isinstance(value, str) and value.strip(), "no empty translation"
            base = sorted(re.findall(r"\{\d+\}", row[1]))
            for value in row:
                assert sorted(re.findall(r"\{\d+\}", value)) == base, \
                    f"placeholder mismatch in: {value}"

    def test_no_dead_rows_and_every_t_key_registered(self, probe):
        assert probe["dead"] == [], f"未使用的词条必须删除：{probe['dead']}"
        assert probe["missing"] == [], f"T() 使用了未注册的键：{probe['missing']}"
        assert probe["tCalls"], "代码正文必须真实引用词条"

    def test_rendering_red_line_textcontent_only(self, probe):
        assert probe["dangerous"] is False, "不得使用 innerHTML/outerHTML/insertAdjacentHTML/document.write"

    def test_frontend_never_contacts_eric_directly(self, probe):
        assert probe["directNetwork"] is False, "前端不得直连外网（只经本应用 API）"
        assert probe["endpoints"] == {
            "search": True, "import": True, "ericHost": True,
        }, probe["endpoints"]


class TestErrorMapping:
    def test_every_backend_code_renders_non_cjk_english(self, probe):
        for code in ("eric_unreachable", "eric_timeout", "eric_redirect",
                     "eric_http_error", "eric_bad_response",
                     "eric_response_too_large", "eric_incomplete",
                     "eric_query_error", "eric_bad_query", "eric_too_many_hits",
                     "eric_no_results", "eric_unavailable", "eric_totally_new"):
            en = probe["errors"][code]["en"]
            assert en.strip(), f"{code} must render something"
            assert not CJK.search(en), f"{code} 在英文界面泄漏中文：{en}"
        assert not CJK.search(probe["unknownEn"])
        assert not CJK.search(probe["nullEn"])

    def test_error_texts_are_actionable(self, probe):
        assert "api.ies.ed.gov" in probe["errors"]["eric_unreachable"]["en"]
        assert "timed out" in probe["errors"]["eric_timeout"]["en"]
        assert "500" in probe["errors"]["eric_too_many_hits"]["en"]
        assert "0 hits" in probe["errors"]["eric_no_results"]["en"]
        assert "incomplete" in probe["errors"]["eric_incomplete"]["en"]
        # 未登记 code 回落通用外壳，绝不拼接后端中文原文
        assert "后端中文技术串" not in probe["unknownEn"]


class TestSummaries:
    def test_preview_summary_zero_and_hits(self, probe):
        assert probe["previewZero"]["template"] == "ERIC 检索命中 0 条。请调整检索式后重试。"
        assert probe["previewZero"]["args"] == []
        assert probe["previewHits"]["template"] == "命中 {0} 条；以下为前 {1} 条题录。"
        assert probe["previewHits"]["args"] == [123, 20]
        assert probe["previewEmpty"]["args"] == []

    def test_import_summary_staged_and_direct(self, probe):
        assert probe["importStaged"]["template"] == "导入已暂存：解析 {0} 条，待核验后激活。"
        assert probe["importStaged"]["args"] == [12]
        assert probe["importDirect"]["template"] == "导入完成：解析 {0} 条，精确去重 {1} 条，导入 {2} 条。"
        assert probe["importDirect"]["args"] == [10, 2, 8]
        # 受保护导入 batch=false 的真实键是 n_parsed/n_imported（不是 imported）
        assert probe["importSingle"]["args"] == [10, 2, 8]
        assert probe["importStagedSingle"]["args"] == [2]
        assert probe["importEmpty"]["args"] == [0, 0, 0]

    def test_fill_matches_app_t_semantics(self, probe):
        assert probe["filled"] == "a x b y"
        assert probe["fillMissing"] == "only-"


class TestMountAnchor:
    def test_index_loads_module_with_r11eric1_token(self):
        html = INDEX.read_text(encoding="utf-8")
        assert './eric_search.js?v=20261102-r11eric1' in html, \
            "index.html 必须挂载 eric_search.js 且带 r11eric1 令牌"
        # 挂载顺序：Zotero 之后、PDF 获取之前（追加式锚点）
        eric_at = html.index('./eric_search.js?v=20261102-r11eric1')
        zotero_at = html.index('./zotero_import.js?v=')
        pdf_at = html.index('./pdf_fetch.js?v=')
        assert zotero_at < eric_at < pdf_at

    def test_mount_code_is_additive_and_guarded(self):
        source = MODULE.read_text(encoding="utf-8")
        assert "section.rf-import-page" in source
        assert "rfEricEntry" in source
        assert "try { mountBrowser(); } catch (error)" in source, \
            "单点故障：初始化异常绝不影响导入页"
        assert "typeof RFLang === 'undefined'" in source

    def test_error_status_paths_pass_through_T(self):
        """errorText 返回的是 zh 源串：写入状态栏前必须经 T() 取当前语言。

        node 钉只能验证 errorText 的源串已在词典里（TestErrorMapping）；
        真正落到 DOM 的调用点若漏了 T()，非中文界面仍会显示中文——这里对
        两个显式触发入口的调用形态做静态闭环。
        """
        source = MODULE.read_text(encoding="utf-8")
        assert source.count("setStatus(T(errorText(error)))") == 2, \
            "预览/导入两个 catch 都必须经 T() 翻译错误源串"
        assert "setStatus(errorText(" not in source
