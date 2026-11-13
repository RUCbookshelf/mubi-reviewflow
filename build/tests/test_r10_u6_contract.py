# -*- coding: utf-8 -*-
"""r10-u6 泳道（UX 批准单 §六/§七）钉子：F04 / F07 / F60 / F63 / F64 / F68。

每项都按「修复前行为 → 修复后行为」成对断言，破坏修复即红；与条目无关的既有
输出（其它 locale 行、既有 409/503 文案、既有 422 拼接）由对照组断言保持逐字
不变。行为面用真实入口驱动：

- F04：从 index.html 同源加载的 research_trace.js 原文抽出 localDay/defaultWindow/
  params，在三个时区（宿主 Asia/Shanghai、UTC、EST5EDT）下逐点核对日界序列化与
  本地 00:00±1h 记录的可见性；并静态钉住两处同步点（明细 params + 导出预览）。
- F07：静态钉住「先清单后校验」的调用次序与面板/高亮接线；文案必须十语言注册。
- F60：真实 preview 端点（MAX_EVENTS 临时压到 -1）返回 413，文案必须逐字等于
  interface_locale.js 已注册的十语言词条；三处 413 不得再有插值英文。
- F63：2.1.5 W1 已删轮次路由（恒 404）；版本冲突的触发端点换成同一 write_guard
  的 PUT /workflow-settings（与浏览器套件 9b9b45a 同口径）。前端从
  workflow_controls.js 抽出真实 run/W.error/saveSettings 原句回放：409
  workflow_version_conflict → W.refresh('conflict') 把新版本落回 draft →
  重试保存必须带新版本。
- F64：保存端点 503 文案逐字对齐 main.py 决策保存口径，撤销端点文案不变。
- F68：从 index.html 抽出真实 apiRequest 跑 422 分支：高频 type 产出中文模板，
  字段名来自 loc；/api/auth/ 的长度特例与未知 type 的原文回退保持不变。
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "custom_frontend"
INDEX = FRONTEND / "index.html"
BACKEND = REPO / "custom_backend"

NODE_CANDIDATES = (
    r"C:\Users\16073\AppData\Local\hermes\node\node.exe",
)
PY = sys.executable


def _node_exe():
    for exe in (*NODE_CANDIDATES,):
        if exe and Path(exe).is_file():
            return exe
    return "node"


def _run_node(script, env_extra=None, timeout=120, argv=()):
    """以临时脚本文件直跑 node（argv 走 process.argv[2..]，供脚本读源码路径）。

    stdin 模式（node -）下 process.argv[1] 是 '-'，脚本无法取得真实文件路径，
    故改为落盘到独立临时目录再执行（探针不落在仓库内）。
    """
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    with tempfile.TemporaryDirectory(prefix="rf_u6_probe_") as tmp:
        path = Path(tmp) / "probe.cjs"
        path.write_text(script, encoding="utf-8", newline="\n")
        proc = subprocess.run([_node_exe(), str(path), *argv], cwd=tmp,
                              capture_output=True, env=env, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError("node 退出码 %d：%s" % (proc.returncode, proc.stderr.decode("utf-8", "replace")[-2000:]))
    return proc.stdout.decode("utf-8", "replace")


_LOCALE_DUMP = r"""
'use strict';
/* 用 RFLang.register 桩回放全部 *_locale.js，取出真实注册的十语言行。
   行可能跨多行书写（interface_locale.js 就是一行一个语言），逐行 JSON 解析会漏。
   桩同时暴露 aliases（历史措辞）——本文件的断言只看十槽本体。 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const frontend = process.argv[2];
const index = process.argv[3];
function matchBracket(src, start) {
  let depth = 0, quote = null;
  for (let k = start; k < src.length; k++) {
    const c = src[k];
    if (quote) { if (c === String.fromCharCode(92)) { k++; continue; } if (c === quote) quote = null; continue; }
    if (c === '"' || c === "'" || c === '`') { quote = c; continue; }
    if (c === '[') depth++;
    else if (c === ']') { depth--; if (!depth) return k; }
  }
  throw new Error('unterminated array');
}
const rows = [];
const html = fs.readFileSync(index, 'utf8');
for (const name of ['rows', 'APP_ROWS', 'PRISMA_ROWS']) {
  const needle = 'const ' + name + ' = [';
  const at = html.indexOf(needle);
  if (at < 0) continue;
  const i = at + needle.length - 1;
  rows.push(...Function('return ' + html.slice(i, matchBracket(html, i) + 1))());
}
const stub = { register(pairs) { for (const row of pairs) rows.push(row); } };
for (const file of fs.readdirSync(frontend).filter(f => f.endsWith('_locale.js'))) {
  const src = fs.readFileSync(path.join(frontend, file), 'utf8');
  const sandbox = { RFLang: stub, window: { RFLang: stub }, console };
  vm.createContext(sandbox);
  try { vm.runInContext(src, sandbox, { timeout: 20000 }); }
  catch (e) { process.stderr.write(file + ': ' + e.message + String.fromCharCode(10)); }
}
process.stdout.write(JSON.stringify(rows));
"""


def _all_rows():
    """index.html 内联词典 + 全部 *_locale.js 注册行的并集（真实加载语义）。"""
    return json.loads(_run_node(_LOCALE_DUMP, argv=[str(FRONTEND), str(INDEX)]))


_ROWS_CACHE = {}


def _rows():
    if "rows" not in _ROWS_CACHE:
        _ROWS_CACHE["rows"] = _all_rows()
    return _ROWS_CACHE["rows"]


def _registered(key):
    """真正的查找语义：RFLang.register 把 zh 与 en 两列都登记为词典键。"""
    for row in _rows():
        if row and key in (row[0], row[1]):
            return row
    return None


def _run_py(script, timeout=180):
    """以独立解释器（-I + 独立临时 CWD）跑真实端点探针。"""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="rf_u6_py_") as tmp:
        path = Path(tmp) / "probe.py"
        path.write_text(script, encoding="utf-8", newline=chr(10))
        proc = subprocess.run([PY, "-I", str(path)], cwd=str(REPO), capture_output=True,
                              env=env, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError("python 探针退出码 %d：%s" % (proc.returncode,
                             proc.stderr.decode("utf-8", "replace")[-2000:]))
    return proc.stdout.decode("utf-8", "replace").strip().splitlines()[-1]


def _line_with(text, needle):
    for line in text.splitlines():
        if needle in line:
            return line
    raise AssertionError("未找到包含 %r 的行" % needle)


# ---------------------------------------------------------------------------
# F04：轨迹默认筛选（全部阶段 + 最近 7 天含今天）与本地时区日界
# ---------------------------------------------------------------------------

_F04_HARNESS = r"""
'use strict';
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const pick = needle => {
  const line = src.split(String.fromCharCode(10)).find(l => l.includes(needle));
  if (!line) throw new Error('missing ' + needle);
  return line.trim();
};
/* 同一函数体内按源码原样声明四个助手，使 defaultWindow/params 仍闭包到
   localDay/localDate/state（Function 构造器不共享外层作用域）。 */
const helpers = Function('state', 'URLSearchParams', [
  pick('function localDay('),
  pick('function localDate('),
  pick('function defaultWindow('),
  pick('function utcDaySpan('),
  pick('function params('),
  'return {localDay, localDate, defaultWindow, utcDaySpan, params};',
].join(String.fromCharCode(10)));
const state = {cursor: null, stage: '', query: '', action: '', since: '2026-10-26', until: '2026-10-26'};
const H = helpers(state, URLSearchParams);
const localMidnight = new Date(2026, 9, 26, 0, 0, 0, 0);
const localEnd = new Date(2026, 9, 26, 23, 59, 59, 999);
process.stdout.write(JSON.stringify({
  tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
  offsetMinutes: localMidnight.getTimezoneOffset(),
  localDay: H.localDay('2026-10-26'),
  localDayEnd: H.localDay('2026-10-26', true),
  at0030: new Date(2026, 9, 26, 0, 30, 0, 0).toISOString(),
  atPrev2330: new Date(2026, 9, 25, 23, 30, 0, 0).toISOString(),
  atLastMs: new Date(2026, 9, 26, 23, 59, 59, 999).toISOString(),
  oldSince: '2026-10-26T00:00:00.000Z',
  query: H.params().toString(),
  window: H.defaultWindow(),
  today: H.localDate(new Date()),
  utcSpan: H.utcDaySpan('2026-10-25'),
}));
"""


class F04TraceLocalDateWindowTests(unittest.TestCase):
    """默认窗口 = 全部阶段 + 最近 7 天（含今天）；日界按本地时区构造。"""

    def _probe(self, tz=None):
        env = {"TZ": tz} if tz else {}
        return json.loads(_run_node(_F04_HARNESS, env_extra=env, argv=[str(FRONTEND / "research_trace.js")]))

    def test_local_midnight_serialization_per_timezone(self):
        """本地 00:00 序列化结果必须随时区变化；UTC 'Z' 裸拼只在零时区正确。"""
        cases = {
            None: "2026-10-25T16:00:00.000Z",       # 宿主 Asia/Shanghai（UTC+8）
            "UTC": "2026-10-26T00:00:00.000Z",
            "EST5EDT": "2026-10-26T04:00:00.000Z",  # 10/26 仍为夏令时（UTC-4）
        }
        for tz, expected in cases.items():
            out = self._probe(tz)
            self.assertEqual(out["localDay"], expected,
                             "localDay('2026-10-26') 在 %r 下应为本地零点：%r" % (tz, out))
            self.assertTrue(out["localDayEnd"].endswith(".999Z"), out)
            self.assertEqual(out["localDayEnd"],
                             expected.replace("T00:00:00.000Z", "T23:59:59.999Z") if expected.endswith("T00:00:00.000Z") else out["localDayEnd"])

    def test_local_hour_window_visibility(self):
        """本地 00:30 必须被 10/26 窗口包含；前一日 23:30 必须排除；当日最后一毫秒包含。

        修复前 since 恒为 '2026-10-26T00:00:00Z'：UTC+8 下本地 00:30 的记录
        （2026-10-25T16:30Z）落在 since 之前 → 明明有记录却 0 行（F04 复现）。
        """
        sh = self._probe(None)
        since, until = sh["localDay"], sh["localDayEnd"]
        self.assertLessEqual(since, sh["at0030"], "本地 00:30 记录必须 ≥ 本地零点序列")
        self.assertGreaterEqual(until, sh["atLastMs"], "本地当日最后一毫秒必须 ≤ until")
        self.assertLess(sh["atPrev2330"], since, "前一日本地 23:30 必须排除")
        # 回归复现：旧口径会漏掉本地 00:30
        self.assertGreater(sh["oldSince"], sh["at0030"],
                           "旧 UTC 裸拼口径本应漏掉本地 00:30（用例失真）")
        utc = self._probe("UTC")
        self.assertEqual(utc["localDay"], utc["oldSince"],
                         "零时区下新旧口径应一致（对照组：UTC 用户行为不变）")

    def test_params_serializes_local_window(self):
        for tz in (None, "UTC"):
            out = self._probe(tz)
            query = unquote(out["query"])
            self.assertIn("since=" + out["localDay"], query,
                          "明细请求未使用本地时区日界：%r" % query)
            self.assertIn("until=" + out["localDayEnd"], query,
                          "明细请求的结束日界必须包含本地当日末毫秒：%r" % query)
            self.assertNotIn("since=2026-10-26T00:00:00Z", query,
                             "明细 params 不得再拼接 UTC 日界：%r" % query)
            self.assertNotIn("T23:59:59Z", query, "结束日界不得是 UTC 裸拼：%r" % query)

    def test_default_window_is_seven_local_days_including_today(self):
        out = self._probe(None)
        since, until = out["window"]["since"], out["window"]["until"]
        self.assertEqual(until, out["today"], "默认窗口结束日必须是本地今天")
        from datetime import date, timedelta
        y, m, d = (int(part) for part in since.split("-"))
        self.assertEqual(date(y, m, d) + timedelta(days=6), date(*map(int, until.split("-"))),
                         "默认窗口应为最近 7 天（含今天）：%r" % (out["window"],))
        # 热力图的列是 UTC 日；点击后换算出的本地区间必须**覆盖该列记录**。
        # UTC+8 下 UTC 10/25 日 = 本地 10/25 08:00 至 10/26 07:59 → since=10/25、until=10/26。
        span = out["utcSpan"]
        from datetime import datetime, timezone
        start = datetime(int(span["since"][:4]), int(span["since"][5:7]), int(span["since"][8:10]),
                         0, 0, 0, tzinfo=timezone.utc)
        col_start = datetime(2026, 10, 25, 0, 0, 0, tzinfo=timezone.utc)
        local_start = col_start.astimezone(timezone(timedelta(minutes=-out["offsetMinutes"])))
        self.assertEqual(span["since"], local_start.strftime("%Y-%m-%d"),
                         "UTC 列起点换算成本地日期有误：%r（时区 %s）" % (span, out["tz"]))
        self.assertNotEqual(span["since"], span["until"],
                            "UTC 日跨本地两天时必须给出跨日区间（否则该列部分记录被排除）")

    def test_two_sync_points_and_no_utc_concat_left(self):
        src = (FRONTEND / "research_trace.js").read_text(encoding="utf-8")
        self.assertNotIn(".since+'T00:00:00Z'", src, "明细 params 仍裸拼 UTC 日界")
        self.assertNotIn(".until+'T23:59:59Z'", src, "明细 params 仍裸拼 UTC 日界")
        self.assertIn("const since=localDay(state.since),until=localDay(state.until,true)", src)
        preview = _line_with(src, "'/exports/preview'")
        self.assertIn("since:localDay(state.since)||null", preview, "导出预览未同步本地时区日界")
        self.assertIn("until:localDay(state.until,true)||null", preview, "导出预览未同步本地时区日界")
        # 明细筛选默认值必须来自同一个默认窗口（全部阶段 = state.stage 初值 ''）
        self.assertIn("Object.assign(state,defaultWindow())", src)

    def test_heatmap_column_click_maps_utc_day_to_local_span(self):
        src = (FRONTEND / "research_trace.js").read_text(encoding="utf-8")
        click = _line_with(src, "const span=utcDaySpan(date)")
        self.assertIn("state.since=span.since", click)
        self.assertIn("state.until=span.until", click)
        self.assertNotIn("state.since=state.until=date", src,
                         "热力图点击仍把 UTC 日当本地日期（UTC+8 下该列记录落到窗口外）")


# ---------------------------------------------------------------------------
# F07：PRISMA 自动填充反馈 + 缺失项清单面板
# ---------------------------------------------------------------------------

class F07PrismaFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.html = INDEX.read_text(encoding="utf-8")

    def test_missing_panel_exists_and_runs_before_validation(self):
        self.assertIn('<div id="prMissing" role="alert" aria-live="assertive" tabindex="-1" hidden></div>',
                      self.html, "缺失项面板容器缺失")
        generate = self.html.split("$('#prGenerate').addEventListener('click', async () => {", 1)[1]
        generate = generate.split("\n});", 1)[0]
        missing_at = generate.find("const missing = prMissingItems();")
        legacy_at = generate.find("PrismaTaskUI.completeForm(form)")
        self.assertGreaterEqual(missing_at, 0, "生成入口未收集缺失项")
        self.assertGreater(legacy_at, missing_at, "先一次性清单、后单项拦截的次序被破坏")
        self.assertIn("if (missing.length) { prShowMissing(missing); return; }", generate)

    def test_autofill_reports_count_and_highlights(self):
        block = self.html.split("$('#prAutoFill').addEventListener('click', () => {", 1)[1].split("\n});", 1)[0]
        self.assertIn("input.value === value", block, "自动填充未统计实际改动字段")
        self.assertIn("pr-filled", block, "自动填充未高亮被改写字段")
        self.assertIn("T('已填充 {0} 个字段（标记「自动」的字段会随上游数字自动更新）', filled)", block)
        self.assertIn("T('没有可填充的字段：请先在上游填写识别来源与数量。')", block,
                      "无可填值时没有说明原因（旧行为：一律声称已填充）")
        for key in ("已填充 {0} 个字段（标记「自动」的字段会随上游数字自动更新）",
                    "没有可填充的字段：请先在上游填写识别来源与数量。",
                    "请先补齐以下内容（{0} 项）",
                    "未填写", "格式不正确",
                    "填写后再点「校验并生成流程图」。",
                    "识别来源（至少一行，含来源名称）",
                    "其他方法的识别来源（至少一行）"):
            row = _registered(key)
            self.assertIsNotNone(row, "F07 文案未注册十语言：%r" % key)
            self.assertEqual(len(row), 10, "F07 文案不是十槽：%r" % (row,))

    def test_prisma_chain_labels_and_required_modes(self):
        block = self.html.split("const PR_REQUIRED =", 1)[1].split("function prMissingItems", 1)[0]
        self.assertIn("updated: ['prPreviousStudies', 'prPreviousReports', 'prNewStudies']", block,
                      "更新综述模式必须列出三个必填计数（原逐项拦截只报第一个）")
        self.assertIn("prIncluded", block)
        # 面板只列识别来源的「名称」门槛（与后端 R1 一致；数量为 0 只是 warning）
        self.assertIn("rows.some(row => row.name)", self.html)


# ---------------------------------------------------------------------------
# F60：轨迹导出预览 413 文案本地化
# ---------------------------------------------------------------------------

class F60Trace413Tests(unittest.TestCase):
    def test_413_constant_is_registered_ten_languages(self):
        from custom_backend import research_trace_api as rt
        row = _registered(rt._PREVIEW_TOO_LARGE)
        self.assertIsNotNone(row, "413 文案必须注册十语言（zh/en 双键）：%r" % rt._PREVIEW_TOO_LARGE)
        self.assertEqual(len(row), 10)
        self.assertIn("请缩小导出范围", row[0])

    def test_all_preview_413_sites_use_the_constant(self):
        src = (BACKEND / "research_trace_api.py").read_text(encoding="utf-8")
        self.assertNotIn("Filtered export exceeds", src,
                         "超限页仍拼英文 f-string（带插值永远命不中词典）")
        self.assertGreaterEqual(src.count("raise HTTPException(413, _PREVIEW_TOO_LARGE)"), 3,
                                "三处预览 413（两处 MAX_EVENTS + 缓存字节）必须统一常量")
        for line in src.splitlines():
            if "413" in line and "raise HTTPException" in line and "MAX_EVENTS" not in line:
                self.assertNotIn("f\"", line, "413 不得使用插值文案：%r" % line)

    def test_frontend_shows_localized_413_instead_of_generic_failure(self):
        src = (FRONTEND / "research_trace.js").read_text(encoding="utf-8")
        catch = _line_with(src, "'/exports/preview'")
        self.assertIsNotNone(catch)
        line = _line_with(src, "downloadCondition(e.status===413&&e.message?e.message:")
        self.assertIn("downloadCondition(e.status===413&&e.message?e.message:", line)
        self.assertIn("导出预览未完成，请重试。", line, "非 413 失败仍沿用原文案（对照组）")

    def test_preview_endpoint_returns_registered_text(self):
        """真实 preview 端点：MAX_EVENTS 临时压到 -1 触发超限页 413。

        用例在**独立子进程**里跑（独立数据目录 + 独立解释器状态）：本仓库其它
        测试模块会在导入期设置 COBOOKSHELF_DATA_DIR，同进程复用 main 会让该用例
        的注册落到别人的数据目录上。
        """
        script = r"""
import json, os, sys, tempfile
sys.path.insert(0, os.getcwd())
os.environ["COBOOKSHELF_DATA_DIR"] = tempfile.mkdtemp(prefix="u6f60-")
os.environ["REVIEWFLOW_MODE"] = "server"
from fastapi.testclient import TestClient
from custom_backend.main import app, DATA_DIR
from custom_backend import research_trace_api as rt
from coscreen import db as trace_db, tasks as tasks_mod
from coscreen.models import Article
out = {}
with TestClient(app) as client:
    reg = client.post("/api/auth/register", json={"username": "u6f60@example.test",
                                                 "password": "Probe-Pass-2026"}).json()
    headers = {"Authorization": "Bearer " + reg["token"]}
    task = client.post("/api/tasks", json={"name": "u6f60"}, headers=headers).json()["task_id"]
    base = "/api/tasks/" + task
    info = tasks_mod.find_task(task, DATA_DIR)
    ctx = app.state.review_workflow.context(task, {"user_id": reg["user"]["id"],
                                                   "username": reg["user"]["username"]})
    trace_db.upsert_articles(ctx.owner_db, [Article(zotero_key="A1", title="One", doi="10.t/1")], [])
    out["decision"] = client.post(base + "/decisions", json={"zotero_key": "A1",
                                                             "decision": "include"}, headers=headers).status_code
    original = rt.MAX_EVENTS
    rt.MAX_EVENTS = -1
    try:
        resp = client.post(base + "/trace/exports/preview", json={
            "preset": "public", "language": "zh-CN", "included_private_fields": [], "filters": {}},
            headers=headers)
    finally:
        rt.MAX_EVENTS = original
    out["status"] = resp.status_code
    out["detail"] = resp.json().get("detail")
    out["constant"] = rt._PREVIEW_TOO_LARGE
print(json.dumps(out))
"""
        result = json.loads(_run_py(script))
        self.assertEqual(result["decision"], 200)
        self.assertEqual(result["status"], 413)
        self.assertEqual(result["detail"], result["constant"])
        self.assertNotIn("exceeds", result["detail"], "413 仍是带插值的英文原串")

# ---------------------------------------------------------------------------
# F63：screening-rounds 版本冲突载荷 + 前端等待刷新
# ---------------------------------------------------------------------------

_F63_HARNESS = r"""
'use strict';
/* 2.1.5 W1 删除轮次路由后，F63「冲突后等刷新拿到新版本」的载体从 screening_rounds.js
   （已随之删除）换成 WorkflowControls：PUT /workflow-settings 发过期 expected_version
   （与浏览器套件 9b9b45a 同一触发口径）→ 409 workflow_version_conflict → W.error 触发
   run(W.refresh('conflict')) → refreshPolicy 把新版本落回 draft.version → 重试保存
   带新版本。这里从 workflow_controls.js 原文抽出真实语句（run / W.error /
   W.saveSettings / 冲突版本落 draft），在桩环境回放整条链；任一句被删改 → 抽取
   失败即红。 */
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
function need(needle) {
  const at = src.indexOf(needle);
  if (at < 0) throw new Error('源码字面量缺失：' + needle);
  return at;
}
function matchBraces(start) {
  let depth = 0;
  for (let k = start; k < src.length; k++) {
    if (src[k] === '{') depth++;
    else if (src[k] === '}') { depth--; if (!depth) return k; }
  }
  throw new Error('unterminated block');
}
const runCode = "const run = fn => Promise.resolve().then(fn).catch(error => W.error(error));";
need(runCode);
const errorAt = need("W.error = error => {");
const errorBlock = src.slice(errorAt, matchBraces(src.indexOf('{', errorAt)) + 1) + ';';
const saveAt = need("W.saveSettings=async()=>{");
const saveBlock = src.slice(saveAt, matchBraces(src.indexOf('{', saveAt)) + 1) + ';';
const landing = "if(reason==='conflict'&&draft&&!settingsDirty)draft.version=result.version;";
need(landing);
const landConflict = new Function('reason', 'draft', 'settingsDirty', 'result', landing);
const order = [];
let fail = true;
const assert = require('node:assert/strict');
const saveButton = {disabled: false};
const unavailableButton = {disabled: true};
const notice = {textContent: ''};
let finishConflict;
const conflictFinished = new Promise(resolve => { finishConflict = resolve; });
let finishSaveRefresh;
let startSaveRefresh;
const saveRefresh = new Promise(resolve => { finishSaveRefresh = resolve; });
const saveRefreshStarted = new Promise(resolve => { startSaveRefresh = resolve; });
const context = {
  console, setTimeout, Promise, structuredClone,
  W: {},
  stages: ['screen', 'fulltext', 'coding'],
  T: value => value,
  toast: message => order.push(message),
  scope: () => 't1|1|s',
  taskApi: () => '/api/tasks/t1',
  draftSignature: value => JSON.stringify(value),
  policyEditor: () => {},
  draft: { version: 1,
           stages: { screen: {mode: 'paused', source_ids: []},
                     fulltext: {mode: 'paused', source_ids: []},
                     coding: {mode: 'paused', source_ids: []} },
           evidence_required_field_ids: [] },
  settings: { can_manage: true, version: 1 },
  settingsDirty: false,
  savingSettings: false,
  document: {
    querySelectorAll: selector => {
      assert.equal(selector, '.wf-settings-save button');
      return [saveButton, unavailableButton];
    },
  },
  $: selector => {
    assert.equal(selector, '#wf-settings-notice');
    return notice;
  },
  draftBaseline: null,
  api: async (path, opts) => {
    assert.equal(saveButton.disabled, true, 'save must be disabled during PUT');
    order.push('api:' + opts.method + ':' + path.slice(path.lastIndexOf('/')) + ':' + opts.body.expected_version);
    if (fail) {
      const error = new Error('stale');
      error.status = 409;
      error.code = 'workflow_version_conflict';
      error.workflowSettings = { version: 2 };
      throw error;
    }
    return { version: 2 };
  },
  __exports: {},
};
context.W.errorText = error => 'error:' + (error.code || error.status);
context.W.refresh = async reason => {
  order.push('refresh:' + reason);
  if (reason === 'saved') {
    startSaveRefresh();
    await saveRefresh;
    return;
  }
  assert.equal(reason, 'conflict');
  const result = { can_manage: true, version: 2 };  /* GET /workflow-settings 拿回的最新设置 */
  landConflict(reason, context.draft, context.settingsDirty, result);
  context.settings = result;
  finishConflict();
};
vm.createContext(context);
vm.runInContext([runCode, errorBlock, saveBlock, ';__exports.run = run;'].join('\n'), context);
const save = context.W.saveSettings;
(async () => {
  await context.__exports.run(save);
  assert.ok(order.includes('refresh:conflict'), '409 must schedule a refresh');
  await conflictFinished;
  assert.equal(saveButton.disabled, false, 'conflict must restore the save control');
  assert.equal(notice.textContent, 'error:workflow_version_conflict');
  const afterFail = { order: order.slice(), version: context.draft.version };
  fail = false;
  order.length = 0;
  const retry = context.__exports.run(save);
  await Promise.race([
    saveRefreshStarted,
    retry.then(() => { throw Error('save finished before refresh'); }),
  ]);
  assert.equal(saveButton.disabled, true, 'save must stay disabled until refresh completes');
  assert.equal(await save(), false, 'a second save must not submit during refresh');
  finishSaveRefresh();
  assert.equal(await retry, true);
  assert.equal(saveButton.disabled, false);
  assert.equal(unavailableButton.disabled, true, 'restore original disabled state');
  process.stdout.write(JSON.stringify({
    afterFail,
    afterRetry: { order: order.slice(), version: context.draft.version },
  }));
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
"""


class F63ScreeningRoundConflictTests(unittest.TestCase):
    CONFLICT_TEXT = "Workflow policy changed. Refresh and retry."

    def test_backend_returns_structured_conflict(self):
        """真实端点：过期 expected_policy_version → 结构化 409；其余 409 仍是纯串。

        同样跑在独立子进程（见 F60 用例的说明）。
        """
        script = r"""
import json, os, sys, tempfile
sys.path.insert(0, os.getcwd())
os.environ["COBOOKSHELF_DATA_DIR"] = tempfile.mkdtemp(prefix="u6f63-")
os.environ["REVIEWFLOW_MODE"] = "server"
from fastapi.testclient import TestClient
from custom_backend.main import app
out = {}
with TestClient(app) as client:
    reg = client.post("/api/auth/register", json={"username": "u6f63@example.test",
                                                 "password": "Probe-Pass-2026"}).json()
    headers = {"Authorization": "Bearer " + reg["token"]}
    task = client.post("/api/tasks", json={"name": "u6f63"}, headers=headers).json()["task_id"]
    base = "/api/tasks/" + task
    settings = client.get(base + "/workflow-settings", headers=headers).json()
    stale = client.post(base + "/screening-rounds", json={
        "action": "start", "expected_policy_version": settings["version"] + 1}, headers=headers)
    out["stale"] = {"status": stale.status_code, "detail": stale.json().get("detail"),
                    "live_version": settings["version"]}
    control = client.post(base + "/screening-rounds", json={
        "action": "start", "expected_policy_version": settings["version"]}, headers=headers)
    out["control"] = {"status": control.status_code, "detail": control.json().get("detail")}
print(json.dumps(out))
"""
        result = json.loads(_run_py(script))
        # 2.1.5 W1 换钉：轮次路由已删除，旧前端（F63 版本冲突等待刷新）不再
        # 有可调用的后端；无论版本是否过期一律 404（未注册），绝不 500/409。
        self.assertEqual(result["stale"]["status"], 404)
        self.assertEqual(result["control"]["status"], 404)

    def test_round_route_removed_and_binary_maybe_contract_present(self):
        src = (BACKEND / "review_workflow_api.py").read_text(encoding="utf-8")
        self.assertNotIn('@app.post("/api/tasks/{task_id}/screening-rounds")', src,
                         "轮次路由必须删除（2.1.5 W1）")
        self.assertIn('"message_key": "workflow.maybe_disabled"', src,
                      "两阶段 maybe 拒绝必须带新 message_key")
        self.assertIn('"code": "maybe_disabled"', src)

    def test_frontend_awaits_refresh_before_releasing_busy(self):
        """2.1.5 W1 换钉（screening_rounds.js 已删）：workflow_controls.js 的真实
        run/W.error/saveSettings 原句在桩环境回放——PUT /workflow-settings 发
        过期 expected_version → 409 workflow_version_conflict → W.refresh
        ('conflict') 把新版本落回 draft.version → 重试保存必须带新版本。
        """
        out = json.loads(_run_node(_F63_HARNESS, argv=[str(FRONTEND / "workflow_controls.js")]))
        fail = out["afterFail"]
        self.assertEqual(fail["order"],
                         ["api:PUT:/workflow-settings:1", "error:workflow_version_conflict", "refresh:conflict"],
                         "冲突后必须触发 W.refresh('conflict') 拿到新版本（否则重试仍带旧 version）：%r" % fail)
        self.assertEqual(fail["version"], 2,
                         "冲突刷新必须把新版本落回 draft.version（refreshPolicy 的 conflict 分支）：%r" % fail)
        retry = out["afterRetry"]
        self.assertEqual(retry["order"][0], "api:PUT:/workflow-settings:2",
                         "刷新后重试必须带新版本（修复前恒为旧值 1）：%r" % retry)
        self.assertIn("refresh:saved", retry["order"])


# ---------------------------------------------------------------------------
# F64：保存端点 503 文案对齐 main.py 决策保存口径
# ---------------------------------------------------------------------------

class F64SaveUnavailableCopyTests(unittest.TestCase):
    def test_save_and_submission_save_use_decision_save_wording(self):
        from custom_backend import workspace_undo_api as undo_api
        main_src = (BACKEND / "main.py").read_text(encoding="utf-8")
        expected = "本次修改尚未保存。轨迹记录不可用，请重试或暂停记录后重新保存。"
        self.assertIn('raise HTTPException(503, "%s")' % expected, main_src,
                      "main.py 决策保存的 503 口径漂移（对照基准）")
        self.assertEqual(undo_api.SAVE_NOT_SAVED_503, expected,
                         "保存端点 503 文案必须逐字对齐 main.py 决策保存口径")
        src = (BACKEND / "workspace_undo_api.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(src.count("raise HTTPException(503, SAVE_NOT_SAVED_503)"), 2,
                                "编码表单保存与提交证据保存都必须使用保存口径")
        # 撤销端点保留撤销专属文案（对照组：不得被一并改写）
        self.assertIn("raise HTTPException(503, \"Undo is unavailable. Please try again.\")", src)
        self.assertNotIn("""
                context = trace_context(ctx.info, user)
                if context is None:
                    raise HTTPException(503, 'Undo is unavailable. Please try again.')""", src)
        row = _registered(expected)
        self.assertIsNotNone(row, "F64 文案必须注册十语言（errOf 的 detail→T() 命中）")
        self.assertEqual(len(row), 10)


# ---------------------------------------------------------------------------
# F68：高频 422 校验类型中文模板（随 message_key/词典收编，不做独立映射表）
# ---------------------------------------------------------------------------

_F68_HARNESS = r"""
/* 非严格模式：直连 eval 的 function 声明会进入外层作用域，才能在这里调用
   从 index.html 原文抽出的真实 apiRequest。 */
const fs = require('fs');
const path = require('path');
const html = fs.readFileSync(process.argv[2], 'utf8');
const frontend = path.dirname(process.argv[2]);
function matchBracket(src, start) {
  let depth = 0, quote = null;
  for (let k = start; k < src.length; k++) {
    const c = src[k];
    if (quote) { if (c === '\\') { k++; continue; } if (c === quote) quote = null; continue; }
    if (c === '"' || c === "'" || c === '`') { quote = c; continue; }
    if (c === '[') depth++;
    else if (c === ']') { depth--; if (!depth) return k; }
  }
  throw new Error('unterminated array');
}
const rows = [];
for (const name of ['rows', 'APP_ROWS', 'PRISMA_ROWS']) {
  const needle = 'const ' + name + ' = [';
  const at = html.indexOf(needle);
  if (at < 0) continue;
  const i = at + needle.length - 1;
  rows.push(...Function('return ' + html.slice(i, matchBracket(html, i) + 1))());
}
for (const file of fs.readdirSync(frontend).filter(f => f.endsWith('_locale.js'))) {
  try {
    const src = fs.readFileSync(path.join(frontend, file), 'utf8');
    for (const m of src.matchAll(/^\s*(\[[^\n]*\])\s*,?\s*$/gm)) {
      try { const row = JSON.parse(m[1]); if (Array.isArray(row)) rows.push(row); } catch (e) {}
    }
  } catch (e) {}
}
const dictionary = new Map();
for (const row of rows) { if (row[0]) dictionary.set(row[0], row); if (row[1]) dictionary.set(row[1], row); }
let langIndex = 0;
function T(tpl, ...args) {
  const key = String(tpl);
  const row = dictionary.get(key) || dictionary.get(key.trim());
  let s = row ? row[langIndex] : key;
  if (args.length) s = s.replace(/\{(\d+)\}/g, (m, d) => args[+d] === undefined ? m : String(args[+d]));
  return s;
}
/* apiRequest 依赖的两个模块级变量：令牌与基址（本用例恒为空令牌/同源）。 */
var AUTH_TOKEN = null, API_BASE = '';
const extracted = html.match(/\nclass ApiError extends Error \{[\s\S]*?\n\}/)[0]
  + html.match(/\nasync function apiRequest\(path, opts = \{\}\) \{[\s\S]*?\n\}/)[0];
const responses = [];
let nextResponse = null;
const fetch = async () => nextResponse;
eval(extracted);
async function call(pathname, detail) {
  nextResponse = { status: 422, ok: false, json: async () => ({ detail }) };
  let out = null;
  try { await apiRequest(pathname, { method: 'POST', body: {} }); }
  catch (e) { out = { status: e.status, message: e.message }; }
  return out;
}
(async () => {
  const cases = {
    zh_short: await call('/api/tasks/t/decisions', [{ type: 'string_too_short', loc: ['body', 'zotero_key'], msg: 'String should have at least 1 character', ctx: { min_length: 1 } }]),
    zh_ge: await call('/api/tasks/t/screening-rounds', [{ type: 'greater_than_equal', loc: ['body', 'expected_policy_version'], msg: 'Input should be greater than or equal to 1', ctx: { ge: 1 } }]),
    zh_missing: await call('/api/tasks/t/x', [{ type: 'missing', loc: ['body', 'state'], msg: 'Field required' }]),
    zh_unknown: await call('/api/tasks/t/x', [{ type: 'custom_mystery', loc: ['body', 'x'], msg: 'Some technical words' }]),
    auth_short: await call('/api/auth/register', [{ type: 'string_too_short', loc: ['body', 'username'], msg: 'String should have at least 3 characters', ctx: { min_length: 3 } }]),
  };
  langIndex = 1;
  cases.en_short = await call('/api/tasks/t/decisions', [{ type: 'string_too_short', loc: ['body', 'zotero_key'], msg: 'String should have at least 1 character', ctx: { min_length: 1 } }]);
  const templates = (html.match(/const VALIDATION_TEMPLATES = \{[\s\S]*?\n        \};/) || [''])[0];
  process.stdout.write(JSON.stringify({ cases, templates }));
})();
"""


class F68ValidationTemplateTests(unittest.TestCase):
    def test_high_frequency_types_localized(self):
        out = json.loads(_run_node(_F68_HARNESS, argv=[str(INDEX)]))
        cases = out["cases"]
        self.assertEqual(cases["zh_short"]["status"], 422)
        self.assertEqual(cases["zh_short"]["message"], "zotero_key 太短：至少 1 个字符",
                         "高频 string_too_short 未走中文模板（仍出 Pydantic 英文原串）")
        self.assertEqual(cases["zh_ge"]["message"], "expected_policy_version 不能小于 1。")
        self.assertEqual(cases["zh_missing"]["message"], "state 为必填项。")
        self.assertEqual(cases["zh_unknown"]["message"], "x：Some technical words",
                         "未收录 type 的原文回退被破坏（对照组）")
        self.assertEqual(cases["auth_short"]["message"],
                         "用户名须为 3~64 个字符，且只含字母、数字、汉字与 . _ - @。",
                         "/api/auth/ 的长度特例被模板覆盖（对照组：既有行为不变）")
        self.assertEqual(cases["en_short"]["message"], "zotero_key is too short: at least 1 characters",
                         "英文界面必须命中同一模板的 en 槽")

    def test_templates_are_dictionary_keys_not_a_field_table(self):
        out = json.loads(_run_node(_F68_HARNESS, argv=[str(INDEX)]))
        templates = out["templates"]
        self.assertIn("VALIDATION_TEMPLATES", templates)
        keys = set(re.findall(r"'([^']*\{\d\}[^']*)'", templates))
        self.assertGreaterEqual(len(keys), 10)
        for key in keys:
            self.assertIsNotNone(_registered(key), "422 模板未进词典（会退回英文）：%r" % key)
        # 不做字段级映射表：模板按 type 索引，字段名来自 loc
        self.assertNotIn("zotero_key", templates)


if __name__ == "__main__":
    unittest.main()
