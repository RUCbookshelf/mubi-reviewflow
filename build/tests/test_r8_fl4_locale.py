# -*- coding: utf-8 -*-
"""r8-FL4 词典泳道钉子（MM9-18 / MM10-07+MM11-12 / MM9-06 / 批13-1 / 批13-2 / 批13-2b /
LUNA4-04 / LUNA4-05 / FS-01 / MM9-06 前端侧）。

机检口径（与评审员 deepseek 的机检对齐）：
- 十槽完整：本组登记/补译的键，10 个语言槽全部非空；
- 占位符一致：{n} 记号多重集在 10 个槽上逐槽相等；
- 键唯一：本组新增键跨注册的 en 集合必须恰为 1 个（不制造新冲突键）；
- 英文回退 = 0：全词典不存在"任何来源都未显式提供、且值 == en"的 i=2..9 槽
  （MM9-18 复审口径：基线 35 键 / 280 槽，修复后必须为 0）；
- 已知白名单不回归：由既有 test_r7_frontsec_fixes 词典守卫承担（另行全绿运行）。

破坏即红（每条修复 ↔ 钉子）：
- 把任一补译行删回 2 槽 → test_english_fallback_eliminated 红；
- 改坏任一新键占位符/漏槽 → test_new_entries_complete_and_placeholder_consistent 红；
- MM9-06 的 5 个合成页长串 en 字面任一侧（前端词典 / 后端词表）漂移 →
  test_mm9_06_synthesis_scale_labels_localizable 红；
- 批13-1 三元回退到 log/其他 两分支 → test_batch13_1_subgroup_scale_branches 红；
- 批13-2/2b catch 回退到无条件 lang:'en' → test_batch13_2_known_message_guards 红；
- LUNA4-04 ja 槽回退到简体"协"(U+534F) → test_luna4_04_ja_glyph / 全词典 U+534F 扫描红；
- LUNA4-05 sr 槽回退到"Учитање"名词化 → test_luna4_05_sr_grammar 红；
- FS-01 新文案行被删/占位符丢失/旧死键复活 → test_fs_01_stop_certificate_copy 红；
- MM10-07/MM11-12 的 T() 包裹被撤 → test_mm10_07_call_sites_wrapped_in_T 红。
"""

import json
import pathlib
import re
import shutil
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
FRONTEND = REPO / "custom_frontend"
INDEX = FRONTEND / "index.html"

NODE_CANDIDATES = (
    r"C:\Users\16073\AppData\Local\hermes\node\node.exe",
)

# ---------------------------------------------------------------- 词典键清单

# MM9-18：基线（a080fde）实测 35 个英文回退键（34 个"只登记一次且不在种子" +
# 1 个双文件登记键）。修复后每个键 2..9 槽都必须有显式来源。
MM9_18_FALLBACK_KEYS = (
    "Cochrane Handbook 第 6 章，第 6.3.1–6.3.2 节",
    "Tierney 等（2007）公式 7",
    "Cochrane Handbook 第 6 章，第 6.8.2 节",
    "，第 2 版第 4 章，第 23–29 页；Cooper、Hedges 与 Valentine 主编（2019）",
    "，第 2 版第 4 章，第 24、26–28 页，以及",
    "Cochrane Handbook 第 23 章，第 23.1.4–23.1.5 节",
    "Cochrane Handbook 第 23.3.4 节",
    "Cochrane Handbook 第 10.14 节；Harrer 等（2022）",
    "，第 11–12 章及第 17 章。",
    "，第 4.1 章及第 4.2.1、4.2.3、4.4.4 节（本地 PDF 第 214–215、224–225、228–232、251–253 页）。",
    "，第 3.2.2、4.2.6 节，第 60–61、132–134 页（logit 比例与二项 logit 广义线性混合模型）；Borenstein 等（2021）",
    "，第 2 版第 50 章，第 465–468 页（患病率示例）；",
    "，第 2 版第 42 章，第 369–376 页（MH OR、Peto）；Harrer 等（2022）",
    "，第 4 章，第 115–119 页。Mantel 与 Haenszel（1959），DOI 10.1093/jnci/22.4.719。RR/RD 公式见 Cochrane RevMan 5 统计算法。",
    "，第 2 版第 22 章。Knapp 与 Hartung（2003），DOI 10.1002/sim.1482。",
    "Cochrane Handbook 第 11 章，第 11.4.3 节；Harrer 等（2022）",
    "，第 12 章，第 12.2.1 节，第 336–338 页（对比网络模型及随机效应扩展），342–344 页（设计间 Q）；Hedges（2019），载于",
    "，第 4.1.2.1–2 节，第 102–104 页（方差估计与 HKSJ 区间）。",
    "{0}：{1}",
    "不同区间对是否跨越无效值的结论不一致。",
    "DerSimonian 与 Laird（1986），DOI 10.1016/0197-2456(86)90046-2。",
    "Paule 与 Mandel（1982），J. Res. Natl. Bur. Stand.，87：377–385。",
    "例如：95",
    "来源页码、表格或图号",
    "，第 4 章；Borenstein 等（2021），",
    "，第 3–5 章；Borenstein 等（2021），",
    "，第 5 章，第 121–133 页；",
    "，第 6 章，第 183–184 页，公式 6.1–6.7。",
    "，第 8 章，第 199–215 页；Borenstein 等（2021），",
    "，第 11 章，第 271–275 页；Harrer 等（2022），",
    "，第 12 章，第 12.2.2.2 节，第 340–342 页。",
    "，第 2 版，第 39 章，第 351、354–355 页；",
    "{0} · {1}% {2}",
    "{0} {1}：{2}。{3}。",
    "／{0}",
)

# MM10-07（10 条）+ final_verify_v3 补扫（9 条）+ MM11-12（12 条）去重后的新增键。
NEW_DICTIONARY_KEYS = (
    "SE（分析尺度）",
    "边际 Q（抽样方差）",
    "协方差来源或假设（必填）",
    "例如：原文表 3 的相关系数，或预设假设及理由",
    "比较、时间点或效应指标已变化，请重新载入结果",
    "查看协方差与输入快照",
    "请先填写比较和时间点",
    "此层至少需要两个已选用的结局",
    "请先载入当前层的结果",
    "协方差必须是有限数值",
    "至少需要两项独立研究",
    "每个结局至少需要两项独立研究",
    "请选择相关结局联合合并模型",
    "随机效应 REML 模型要求 2 至 8 个结局",
    "拟合方法：{0}；结点：{1}；参照剂量：{2}",
    "方法来源：",
    "比值尺度",
    "森林图数据不含有限的效应值或区间端点，无法建立坐标系；请检查效应值与 SE。",
    "τ² 非中心卡方近似区间",
    "τ² Q-profile 区间",
    "Higgins–Thompson H 转换区间",
    "导出 {0} 审计",
    "HSROC 参数",
    "已归档（可在“显示已归档任务”中查看）",
    "界面密度：{0}（已生效）",
    "工作范围",
)

# MM11-12 的英文字面量键（源串即英文，经 en 列建键）。
NEW_ENGLISH_KEYS = (
    "Export {0} audit",
    "I² = max(0, (Q − df) / Q) × 100% from fixed inverse-variance Q; I² = 0 when Q = 0.",
    "HSROC parameter",
    "Accuracy and threshold variances are on their latent HSROC parameter scales. "
    "Descriptive HSROC curve and parameters only: no HSROC parameter CI or shape "
    "significance test. The separate bivariate prediction region is shown on the "
    "plot when available.",
    "Work scope",
    "DOI {0}",
    "κ = {0}",
)

# MM9-06：合成路径 effect_scale 长串（en 列必须与后端 _synthesis_effect_scale_label
# 的产出逐字一致；zh 列为源键）。term() 反查用的就是 en → zh。
MM9_06_SYNTHESIS_LABELS = {
    "Fisher z; results shown as r": "Fisher z；结果显示为 r",
    "logit; results shown as proportion": "logit；结果显示为比例",
    "log; results shown as rate": "对数尺度；结果显示为发生率",
    "log; results shown as ratio of means": "对数尺度；结果显示为均数比",
    "log; results shown as matched-pair odds ratio": "对数尺度；结果显示为配对比值比",
}

LANG_ORDER = ("zh", "en", "fr", "ru", "es", "ja", "pt", "de", "sr", "ko")

MM9_06_SYNTHESIS_KEYS = tuple(MM9_06_SYNTHESIS_LABELS)

# ---------------------------------------------------------------- 回放引擎

_REPLAY_ENGINE = r"""
const fs = require('fs');
const path = require('path');
const repo = process.argv[1];
const input = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const frontend = path.join(repo, 'custom_frontend');
const html = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
function bracketMatch(s, i) {
  let depth = 0, j = i, instr = null;
  while (j < s.length) {
    const c = s[j];
    if (instr) { if (c === '\\') { j += 2; continue; } if (c === instr) instr = null; }
    else {
      if (c === '"' || c === "'" || c === '`') instr = c;
      else if (c === '[') depth += 1;
      else if (c === ']') { depth -= 1; if (depth === 0) return j; }
    }
    j += 1;
  }
  throw new Error('bracket mismatch @' + i);
}
function commentRanges(s) {
  const ranges = []; let i = 0, instr = null;
  while (i < s.length) {
    const c = s[i];
    if (instr) { if (c === '\\') { i += 2; continue; } if (c === instr) instr = null; i += 1; continue; }
    if (c === '"' || c === "'" || c === '`') { instr = c; i += 1; continue; }
    if (c === '/' && i + 1 < s.length) {
      if (s[i + 1] === '/') { let j = s.indexOf('\n', i); if (j < 0) j = s.length; ranges.push([i, j]); i = j; continue; }
      if (s[i + 1] === '*') { let j = s.indexOf('*/', i + 2); if (j < 0) j = s.length; else j += 2; ranges.push([i, j]); i = j; continue; }
    }
    i += 1;
  }
  return ranges;
}
function inRanges(ranges, pos) { return ranges.some(([a, b]) => a <= pos && pos < b); }
function extractArray(src, name) {
  const m = new RegExp('\\b(?:const|let|var)\\s+' + name + '\\s*=\\s*\\[').exec(src);
  if (!m) throw new Error('array not found: ' + name);
  const start = src.indexOf('[', m.index);
  return src.slice(start, bracketMatch(src, start) + 1);
}
const LANGS = ['zh-CN','en','fr','ru','es','ja','pt','de','sr','ko'];
const dictionary = new Map();
const provided = new Map();   // zh -> Set(显式提供过值的槽下标)
const regs = new Map();       // zh -> [en, ...]（脚本注册；种子不计）
function mark(zh, entry) {
  if (!provided.has(zh)) provided.set(zh, new Set());
  for (let i = 0; i < entry.length; i += 1) if (entry[i]) provided.get(zh).add(i);
}
function register(pairs, source) {
  for (const entry of pairs) {
    const zh = entry[0], en = entry[1];
    const previous = dictionary.get(zh) || dictionary.get(en);
    const row = LANGS.map((_, i) => entry[i] || (previous ? previous[i] : undefined) || en);
    mark(zh, entry);
    if (source !== 'seed') { if (!regs.has(zh)) regs.set(zh, []); regs.get(zh).push(en); }
    for (const value of row) if (value) dictionary.set(value, row);
    dictionary.set(zh, row); dictionary.set(en, row);
    for (const alias of entry.aliases || []) dictionary.set(alias, row);
  }
}
const errors = [];
const seedRows = [];
for (const name of ['rows', 'APP_ROWS', 'PRISMA_ROWS']) {
  try { seedRows.push(...Function('return ' + extractArray(html, name))()); }
  catch (e) { errors.push('seed ' + name + ': ' + String(e)); }
}
const seedMap = new Map();
for (const row0 of seedRows) { seedMap.set(row0[0], row0); mark(row0[0], row0); }
for (const [k, v] of seedMap) dictionary.set(k, v);
const scripts = [...html.matchAll(/<script src="\.\/([^"?]+)\?v=/g)].map(m => m[1]);
for (const fname of scripts) {
  const p = path.join(frontend, fname);
  if (!fs.existsSync(p)) continue;
  const js = fs.readFileSync(p, 'utf8');
  const cranges = commentRanges(js);
  for (const m of js.matchAll(/RFLang\??\.register\s*\(/g)) {
    if (inRanges(cranges, m.index)) continue;
    let k = m.index + m[0].length;
    while (js[k] === ' ' || js[k] === '\t' || js[k] === '\r' || js[k] === '\n') k += 1;
    if (js[k] === '[') {
      try { register(Function('return ' + js.slice(k, bracketMatch(js, k) + 1))(), fname); }
      catch (e) { errors.push(fname + ': ' + String(e)); }
    } else {
      const end = js.indexOf(')', k);
      globalThis.window = globalThis;
      globalThis.RFLang = { register: pairs => register(pairs, fname) };
      try { Function(js.slice(0, end + 1) + '\n})();')(); }
      catch (e) { errors.push(fname + ' prefix: ' + String(e)); }
    }
  }
}
/* 英文回退槽：最终行 i=2..9 没有任何来源显式提供过 且 值 == en */
const fallback = [];
for (const [zh, row] of dictionary) {
  if (typeof zh !== 'string' || zh !== row[0]) continue;
  const prov = provided.get(zh) || new Set();
  for (let i = 2; i < 10; i += 1) if (!prov.has(i) && row[i] === row[1]) fallback.push([zh, LANGS[i]]);
}
/* ja 槽简体"协"(U+534F) 扫描（LUNA4-04 口径：基线仅 ai_suggest.js:150 一处） */
const u534f = [];
/* 第 4 轮扩展：避免只盯单字。下面这组字在日文新字体里一律写作另一个字形，
   出现在 ja 槽即为简体中文串泄漏（同一个 LUNA4-04 病灶的其它实例：
   「个/为/变/权/选」见于 Peese 说明、「筛」见于标题候选提示）。 */
const SIMPLIFIED_ONLY = '个为变权选筛协设认证术样关实级质评审录导择确页图发经验构标题义书读间时险遗范围处输规则计误转换缩扩边际检测杂优调务员团队归历简详细总览对错隐藏启闭键词项侧头码试备贝贴态补偿预拟伪纳库储辑销账访问请响类汇统联链续继暂临说帮馈沟环默签';
const jaSimp = [];
for (const [zh, row] of dictionary) {
  if (typeof zh !== 'string' || zh !== row[0]) continue;
  const ja = String(row[5]);
  if (ja.includes('协')) u534f.push(zh);
  const bad = [...new Set([...ja].filter(c => SIMPLIFIED_ONLY.includes(c)))];
  if (bad.length) jaSimp.push([zh, bad.join('')]);
}
const keys = {};
for (const key of input.keys) {
  const row = dictionary.get(key);
  keys[key] = row ? row.map(String) : null;
}
const newKeyRegs = {};
for (const key of input.newKeys) newKeyRegs[key] = [...new Set(regs.get(key) || [])];
/* 全词典显示口径：任一 zh 源键中 i=2..9 槽逐字等于该行 en 的键（含槽清单）。
   这是"用户仍看到英文"的真实面，与"来源口径回退"（无来源显式提供）不同。 */
const displayFallback = [];
for (const [zh, row] of dictionary) {
  if (typeof zh !== 'string' || zh !== row[0]) continue;
  const same = [];
  for (let i = 2; i < 10; i += 1) if (row[i] === row[1]) same.push(LANGS[i]);
  if (same.length) displayFallback.push([zh, same]);
}
process.stdout.write(JSON.stringify({
  errors,
  registrations: regs.size,
  fallbackKeys: [...new Set(fallback.map(f => f[0]))],
  fallbackSlots: fallback.length,
  jaU534F: u534f,
  jaSimplified: jaSimp,
  keys,
  newKeyRegs,
  displayFallback,
}));
"""


def _node_exe():
    for exe in (*NODE_CANDIDATES, shutil.which("node")):
        if exe and pathlib.Path(exe).is_file():
            return str(exe)
    return None


def _replay(keys, new_keys):
    """按 index.html 真实加载顺序回放全部词典注册（含逐槽来源标记）。"""
    import tempfile
    exe = _node_exe()
    if exe is None:
        raise unittest.SkipTest("本机无 node 可执行文件（词典回放不可用）")
    payload = json.dumps({"keys": list(keys), "newKeys": list(new_keys)}, ensure_ascii=False)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        handle.write(payload)
        path = handle.name
    try:
        proc = subprocess.run([exe, "-e", _REPLAY_ENGINE, str(REPO), path],
                              capture_output=True, text=True, encoding="utf-8", timeout=180)
    finally:
        pathlib.Path(path).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise AssertionError("词典回放失败：%s" % proc.stderr[-1500:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


_REPLAY_CACHE = {}


def _cached_replay():
    if "result" not in _REPLAY_CACHE:
        _REPLAY_CACHE["result"] = _replay(
            tuple(MM9_18_FALLBACK_KEYS)
            + tuple(NEW_DICTIONARY_KEYS)
            + tuple(NEW_ENGLISH_KEYS)
            + MM9_06_SYNTHESIS_KEYS
            + (
                "可信", "未提供", "人群（P）", "混合探索 (mixed)",
                "已加载的记录仍可核验",
                "证书通过 ✅ 抽验未见新相关，总召回 ≥95%（剩余相关 ≤ {0} 条；置信度按超几何随已发现相关数与抽验量变化，并非固定 95%）",
                "证书通过 ✅ 以 ≥95% 置信度，总召回 ≥95%（剩余相关 ≤ {0} 条）",
                "此任务为升级前创建（无负责人）。当前版本暂无自助认领入口：可在桌面版重建任务协作，或联系服务器管理员处理。",
                "Fisher z 尺度", "logit 尺度",
            ),
            tuple(NEW_DICTIONARY_KEYS) + tuple(NEW_ENGLISH_KEYS) + MM9_06_SYNTHESIS_KEYS,
        )
    return _REPLAY_CACHE["result"]


def _placeholders(value):
    return sorted(re.findall(r"\{\d+\}", value or ""))


class DictionaryReplayHealth(unittest.TestCase):
    """回放本身不得出错（引擎/语法回归即红）。"""

    def test_replay_has_no_errors(self):
        result = _cached_replay()
        self.assertEqual(result["errors"], [], "词典回放报错：%r" % (result["errors"][:5],))
        self.assertGreater(result["registrations"], 0)


class MM9_18FallbackEliminationTests(unittest.TestCase):
    """MM9-18：35 键 280 槽英文回退必须清零（破坏任一补译行即红）。"""

    def test_english_fallback_eliminated(self):
        result = _cached_replay()
        self.assertEqual(
            result["fallbackKeys"], [],
            "MM9-18：词典仍存在英文回退键（i=2..9 无显式来源且 == en）：%r" % (result["fallbackKeys"][:10],),
        )
        self.assertEqual(result["fallbackSlots"], 0)

    def test_mm9_18_keys_have_ten_explicit_slots(self):
        result = _cached_replay()
        missing = [k for k in MM9_18_FALLBACK_KEYS if not result["keys"].get(k)]
        self.assertEqual(missing, [], "MM9-18 键在词典中失踪：%r" % missing)
        short = [k for k in MM9_18_FALLBACK_KEYS
                 if any(not slot for slot in result["keys"][k])]
        self.assertEqual(short, [], "MM9-18 键存在空槽：%r" % short)


class NewEntryIntegrityTests(unittest.TestCase):
    """新增词条：十槽完整 + 占位符一致 + 键唯一（不制造新冲突键）。"""

    def test_new_entries_complete_and_placeholder_consistent(self):
        result = _cached_replay()
        for key in tuple(NEW_DICTIONARY_KEYS) + tuple(NEW_ENGLISH_KEYS):
            row = result["keys"].get(key)
            self.assertIsNotNone(row, "新增词条未进词典：%r" % key)
            self.assertEqual(len(row), 10, "新增词条不是 10 槽：%r -> %r" % (key, row))
            self.assertTrue(all(isinstance(s, str) and s for s in row),
                            "新增词条存在空槽：%r -> %r" % (key, row))
            stamps = [_placeholders(slot) for slot in row]
            self.assertEqual(stamps, [stamps[0]] * 10,
                             "新增词条占位符在 10 槽间不一致：%r -> %r" % (key, stamps))

    def test_new_keys_are_unique_concepts(self):
        result = _cached_replay()
        for key, ens in result["newKeyRegs"].items():
            # 种子行登记不计入脚本注册（ens 为空合法）；一旦有脚本注册，
            # 互异 en 不得超过 1 个，否则就是新造的概念级冲突键。
            self.assertLessEqual(len(ens), 1,
                                 "新增键 %r 出现多个互异 en（概念级冲突）：%r" % (key, ens))


class MM9_06SynthesisScaleLabelTests(unittest.TestCase):
    """MM9-06：合成页 effect_scale 长串经 term()/T() 本地化。

    en 列字面必须与后端 coscreen/review_analysis.py 的
    _SYNTHESIS_SHOWN_AS_SUFFIX + _SYNTHESIS_SCALE_DISPLAY 产出逐字一致——
    任一侧漂移都会让非中文界面重新直出英文长串。
    """

    def test_mm9_06_synthesis_scale_labels_localizable(self):
        result = _cached_replay()
        for en, zh in MM9_06_SYNTHESIS_LABELS.items():
            row = result["keys"].get(en)
            self.assertIsNotNone(row, "MM9-06：合成页长串 %r 未登记为词典键" % en)
            self.assertEqual(row[0], zh, "MM9-06：%r 的 zh 源键应为 %r" % (en, zh))
            # 非英文槽不得仍是该英文长串本身（回退即"直出"回归）
            for i, lang in enumerate(LANG_ORDER):
                if lang in ("zh", "en"):
                    continue
                self.assertNotEqual(row[i], en,
                                    "MM9-06：%r 的 %s 槽仍直出英文长串" % (en, lang))

    def test_backend_suffix_literals_pinned(self):
        """两侧字面都钉：直接导入真实 _synthesis_effect_scale_label 求值，
        断言五个 measure 的产出逐字等于前端词典的 en 键。

        该断言同时覆盖 _SYNTHESIS_SHOWN_AS_SUFFIX 与 _SYNTHESIS_SCALE_DISPLAY
        两张表——只 grep 后缀表时，把 {"fisher_z": "Fisher z"} 改成 "Fisher-Z"
        仍会绿，但前端词典无该键，德语界面会整串直出英文（MM9-06 回归）。
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_r8fl4_review_analysis", REPO / "coscreen" / "review_analysis.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        produced = {
            measure: module._synthesis_effect_scale_label(measure)
            for measure in ("FISHER_Z", "LOGIT_PROP", "LOG_RATE", "LOG_ROM", "PAIRED_OR")
        }
        self.assertEqual(
            sorted(produced.values()), sorted(MM9_06_SYNTHESIS_LABELS),
            "MM9-06：后端合成标签产出与前端词典 en 键脱钩（任一侧漂移都会让"
            "非中文界面直出英文长串）：%r" % (produced,))
        # 反向：词典里每个 en 键都能被 term() 反查到（en→zh）——靠 zh 槽存在保证
        result = _cached_replay()
        for en, zh in MM9_06_SYNTHESIS_LABELS.items():
            self.assertEqual(result["keys"][en][0], zh,
                             "MM9-06：%r 的 zh 源键漂移" % en)


class Batch13ScaleAndGuardTests(unittest.TestCase):
    """批13-1 / 批13-2 / 批13-2b 的源级钉子（回退即红）。"""

    def test_batch13_1_subgroup_scale_branches(self):
        src = (FRONTEND / "analysis_subgroup_random.js").read_text(encoding="utf-8")
        self.assertIn("data.analysis_scale === 'fisher_z' ? T('Fisher z 尺度')", src,
                      "批13-1：subgroup 尺度三元缺 fisher_z 分支")
        self.assertIn("data.analysis_scale === 'logit' ? T('logit 尺度')", src,
                      "批13-1：subgroup 尺度三元缺 logit 分支")
        result = _cached_replay()
        for label in ("Fisher z 尺度", "logit 尺度"):
            row = result["keys"][label]
            self.assertEqual(len(row), 10, "批13-1 标签 %r 不是 10 槽" % label)
            self.assertTrue(all(row), "批13-1 标签 %r 存在空槽" % label)

    def test_batch13_2_known_message_guards(self):
        pscores = (FRONTEND / "analysis_network_pscores.js").read_text(encoding="utf-8")
        self.assertIn(
            "['请选择上方的随机效应 REML 网络模型。','请选择有利方向。'].some(key=>T(key)===error.message)",
            pscores, "批13-2：pscores catch 的 known 守卫缺失（中文串被标 lang:'en'）")
        self.assertIn("...(known?{}:{lang:'en'})})", pscores,
                      "批13-2：pscores catch 未按守卫省略 lang:'en'")
        ranks = (FRONTEND / "analysis_network_ranks.js").read_text(encoding="utf-8")
        self.assertIn(
            "['请选择上方的随机效应 REML 网络模型。','请选择有利方向。',"
            "'模拟次数须为 2–100000 的整数，随机种子须为 0–4,294,967,295 的整数。'].some(key=>T(key)===error.message)",
            ranks, "批13-2b：ranks catch 的 known 守卫缺失")


class LUNA4TranslationFixTests(unittest.TestCase):
    """LUNA4-04（ja 简体"协"）与 LUNA4-05（sr 名词化语法）。"""

    def test_luna4_04_ja_glyph(self):
        result = _cached_replay()
        self.assertEqual(result["jaU534F"], [],
                         "LUNA4-04：ja 槽仍含简体「协」(U+534F) 的键：%r" % (result["jaU534F"][:5],))
        # 第 4 轮扩展：只扫「协」一个字符抓不住「个/为/变/权/选/筛」等同类泄漏
        # （实测 A 波基线里另有两行 ja 槽含简体字：Peese 拟合说明与标题候选提示）。
        self.assertEqual(result["jaSimplified"], [],
                         "LUNA4-04：ja 槽仍含简体专用字形（日文新字体写作另一字）的键：%r"
                         % (result["jaSimplified"][:5],))
        row = result["keys"]["此任务为升级前创建（无负责人）。当前版本暂无自助认领入口：可在桌面版重建任务协作，或联系服务器管理员处理。"]
        self.assertIn("タスク協作", row[5], "LUNA4-04：ja 译文应使用「協作」（U+5354）：%r" % row[5])
        src = (FRONTEND / "ai_suggest.js").read_text(encoding="utf-8")
        self.assertNotIn("タスク协作", src, "LUNA4-04：ai_suggest.js 仍含简体「协作」")

    def test_luna4_05_sr_grammar(self):
        result = _cached_replay()
        sr = result["keys"]["已加载的记录仍可核验"][8]
        self.assertTrue(sr.startswith("Учитане записе"),
                        "LUNA4-05：sr 槽应使用分词形容用法「Учитане записе…」：%r" % sr)
        self.assertFalse(sr.startswith("Учитање"),
                         "LUNA4-05：sr 槽仍是名词化误形「Учитање…」：%r" % sr)

    def test_mm9_18_ja_leftovers_retranslated(self):
        result = _cached_replay()
        fixed = {
            "可信": "信頼できる",
            "未提供": "提供されていません",
            "人群（P）": "集団（P）",
            "混合探索 (mixed)": "混合 (mixed)",
        }
        for key, expected in fixed.items():
            self.assertEqual(result["keys"][key][5], expected,
                             "MM9-18 ja 残留：%r 的 ja 槽应为 %r" % (key, expected))


class FS01StopCertificateCopyTests(unittest.TestCase):
    """FS-01：al_stop_panel.js:237 的新文案必须有 10 语词条（替换旧死键）。"""

    NEW_KEY = ("证书通过 ✅ 抽验未见新相关，总召回 ≥95%（剩余相关 ≤ {0} 条；"
               "置信度按超几何随已发现相关数与抽验量变化，并非固定 95%）")
    OLD_KEY = "证书通过 ✅ 以 ≥95% 置信度，总召回 ≥95%（剩余相关 ≤ {0} 条）"

    def test_fs_01_stop_certificate_copy(self):
        result = _cached_replay()
        row = result["keys"][self.NEW_KEY]
        self.assertIsNotNone(row, "FS-01：新文案未登记 10 语词条")
        self.assertEqual(len(row), 10, "FS-01：新文案词条不是 10 槽")
        self.assertTrue(all(row), "FS-01：新文案词条存在空槽")
        stamps = [_placeholders(slot) for slot in row]
        self.assertEqual(stamps, [["{0}"]] * 10, "FS-01：新文案占位符不一致：%r" % stamps)
        self.assertIsNone(result["keys"][self.OLD_KEY],
                          "FS-01：旧死词条应随替换移除（避免守卫口径漂移）")
        panel = (FRONTEND / "al_stop_panel.js").read_text(encoding="utf-8")
        self.assertIn("T('证书通过 ✅ 抽验未见新相关", panel,
                      "FS-01：面板调用点文案与词典键脱钩")


class MM10_07CallSiteTests(unittest.TestCase):
    """MM10-07（含 MM11-12 增量）：显示路径必须经 T()（撤包裹即红）。"""

    def test_mm10_07_call_sites_wrapped_in_T(self):
        dependent = (FRONTEND / "analysis_dependent_synthesis.js").read_text(encoding="utf-8")
        # 表头两列走数组 + map(text:T(label))；其余为直接 T('…') 包裹
        self.assertIn("'SE（分析尺度）', '95% CI', '边际 Q（抽样方差）'].map(label => el('th', { text:T(label) }))",
                      dependent, "MM10-07：dependent_synthesis 表头缺 T() 包裹")
        for literal in (
            "T('协方差来源或假设（必填）')", "T('例如：原文表 3 的相关系数，或预设假设及理由')",
            "T('比较、时间点或效应指标已变化，请重新载入结果')", "T('查看协方差与输入快照')",
            "T('请先填写比较和时间点')", "T('此层至少需要两个已选用的结局')",
            "T('请先载入当前层的结果')", "T('协方差必须是有限数值')",
            "T('至少需要两项独立研究')", "T('每个结局至少需要两项独立研究')",
            "T('请选择相关结局联合合并模型')",
            # r8.1 FL4：这两条此前是裸字面量（REML 结局数上限是英文原样，
            # 「请先选择任务」走 toast 时未过 T()），非中文界面无译文可用。
            "T('随机效应 REML 模型要求 2 至 8 个结局')",
            "T('请先选择任务')",
        ):
            self.assertIn(literal, dependent,
                          "MM10-07：analysis_dependent_synthesis.js 缺 T() 包裹 %r" % literal)
        dose = (FRONTEND / "analysis_dose_nonlinear.js").read_text(encoding="utf-8")
        self.assertIn("T('比值尺度')", dose, "MM10-07：dose_nonlinear 比值尺度缺 T()")
        self.assertIn("T('方法来源：')", dose, "MM10-07：dose_nonlinear 方法来源缺 T()")
        forest = (FRONTEND / "analysis_forest.js").read_text(encoding="utf-8")
        self.assertIn("T('森林图数据不含有限的效应值或区间端点", forest,
                      "MM10-07：forest 校验串缺 T()")
        audit = (FRONTEND / "analysis_audit_download.js").read_text(encoding="utf-8")
        self.assertIn("T('Export {0} audit'", audit, "MM11-12：audit 按钮缺 T() 模板")
        dta = (FRONTEND / "analysis_dta_synthesis.js").read_text(encoding="utf-8")
        self.assertIn("T('HSROC parameter')", dta, "MM11-12：HSROC 表头缺 T()")
        self.assertIn("T('Accuracy and threshold variances are on their latent HSROC parameter scales.", dta,
                      "MM11-12：HSROC 长说明缺 T()")
        lr = (FRONTEND / "analysis_dta_exploratory_lr.js").read_text(encoding="utf-8")
        self.assertIn("T('I² = max(0, (Q − df) / Q) × 100% from fixed inverse-variance Q", lr,
                      "MM11-12：I² 公式说明缺 T()")
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn("T('DOI {0}', nz(r.doi))", html, "MM11-12：DOI chip 缺 T()")
        self.assertIn("T('κ = {0}'", html, "MM11-12：κ 指标缺 T()")
        self.assertIn("T('已归档（可在“显示已归档任务”中查看）')", html,
                      "MM11-12：归档 toast 键与种子行脱钩")
        self.assertIn("T('界面密度：{0}（已生效）'", html, "MM11-12：密度 toast 键与种子行脱钩")


class MM9_18DisplayCaliberTests(unittest.TestCase):
    """MM9-18 显示口径（第 2 轮评审意见 3 的口径修正）。

    「来源口径回退 0」只说明每个槽都有来源显式提供过值；它不等于「用户看到
    本地化文本」。补一行与 en 逐字相同的译文就能让来源钉变绿而用户仍读英文。
    本钉把两类分开登记：

    (a) LATIN_CITATION_KEYS —— 拉丁书目串（人名/期刊/DOI），产品口径是
        **有意保留原文**（评审确认 Tierney 等处真译说明其余为有意保留）。
        它们允许全部非英文槽等于 en，但必须在册，且不得被误算作"已本地化"。
    (b) 其余不在册的槽值==en 必须为 0——除非是语言中性的 ASCII 模板
        （纯 {n} 占位符 + ASCII 分隔符，无任何可译词），这些单列断言。

    破坏即红：把某条真译文改回英文（如在 (b) 段新增一条与 en 相同的槽），
    或把本条引用串从在册集合里删掉，对应断言失败。
    """

    # 拉丁书目串：人名 + 期刊 + DOI + §节号，十语言均按原文引用（产品口径）
    LATIN_CITATION_KEYS = (
        "DerSimonian 与 Laird（1986），DOI 10.1016/0197-2456(86)90046-2。",
        "Paule 与 Mandel（1982），J. Res. Natl. Bur. Stand.，87：377–385。",
        "Cochrane Handbook 第 23.3.4 节",
        "Cochrane Handbook 第 10.14 节；Harrer 等（2022）",
    )
    # 纯 ASCII 结构模板：整串只有 {n} 占位符 + ASCII 分隔符/标点，无任何字母词。
    LANGUAGE_NEUTRAL_TEMPLATES = (
        "{0}：{1}",
        "{0} · {1}% {2}",
        "{0} {1}：{2}。{3}。",
    )
    # 技术记法：标签本身是国际通用缩写/符号（DOI、Cohen κ），各语言均按原文
    # 书写，只需本地化标点与数值格式。不得含可翻译的自然语言词。
    TECHNICAL_NOTATION_KEYS = (
        "DOI {0}",
        "κ = {0}",
    )

    def test_neutral_templates_contain_no_letters(self):
        """豁免护栏 1：neutral 模板不得写进含字母词的串（否则会变成任意英文豁免）。"""
        for zh in self.LANGUAGE_NEUTRAL_TEMPLATES:
            self.assertNotRegex(
                zh, r"[A-Za-z]",
                "语言中性模板含字母词（不是纯结构模板，应归入引用串/技术记法或不得豁免）：%r" % zh)

    def test_technical_notation_is_notation_only(self):
        """豁免护栏 2：技术记法只允许国际通用缩写/符号 + 占位符，不得夹带自然语言。"""
        for zh in self.TECHNICAL_NOTATION_KEYS:
            stripped = re.sub(r"\{\d+\}", "", zh)
            stripped = re.sub(r"DOI|\u03ba|[=\s·]", "", stripped)
            self.assertEqual(
                stripped, "",
                "技术记法豁免里出现了非通用记法内容（应改为真翻译）：%r" % zh)

    def test_display_caliber_declared(self):
        """本组责任范围内（35 个 MM9-18 键 + 本组新增键）的显示口径申报。

        边界（第 3 轮评审后如实收窄）：全词典还有大量历史存量键的非英文槽
        等于英文（如 'Efron 法' 等），它们既不属 MM9-18 的 35 键、也不属
        MM10-07/MM11-12 的新增条目，不在本组修复范围内——本钉不为其背书，
        也不假装覆盖全词典（原 docstring 声称的"其余不在册必须为 0"已撤回）。
        新增键（NEW_DICTIONARY_KEYS + NEW_ENGLISH_KEYS）本组自产，同样纳入。
        """
        result = _cached_replay()
        allowed = (set(self.LATIN_CITATION_KEYS) | set(self.LANGUAGE_NEUTRAL_TEMPLATES)
                   | set(self.TECHNICAL_NOTATION_KEYS))
        in_scope = set(MM9_18_FALLBACK_KEYS) | set(NEW_DICTIONARY_KEYS) | set(NEW_ENGLISH_KEYS)
        undeclared = [(zh, same) for zh, same in result["displayFallback"]
                      if zh in in_scope and zh not in allowed]
        self.assertEqual(
            undeclared, [],
            "本组范围内这些键的非英文槽逐字等于英文，且未登记在"
            "「有意保留原文 / 语言中性模板」两类中：%r" % (undeclared[:20],))

    def test_full_dictionary_display_residue_is_measured(self):
        """把全词典残留在报告里显式登记（不设门槛，只防"假装已清零"）。"""
        result = _cached_replay()
        total = sum(len(same) for _, same in result["displayFallback"])
        print("[MM9-18 显示口径] 全词典仍有 %d 键 / %d 槽的槽值等于英文"
              "（含历史存量与有意保留原文；本组只对 35 键 + 新增键负责）"
              % (len(result["displayFallback"]), total))

    def test_new_entries_are_not_english_padding(self):
        """本组新增键不得靠「逐槽抄 en」充数（第 3 轮评审反例的反向护栏）。

        NEW_DICTIONARY_KEYS 是本组自产的界面文案（不是书目引用、不是技术记法），
        除 zh/en 外至少多数槽必须是真译文：若某键 7 个外语槽里有 5 个以上
        仍是英文，说明是补行凑数而非翻译。
        """
        result = _cached_replay()
        padding = []
        for zh in NEW_DICTIONARY_KEYS:
            row = result["keys"][zh]
            same = [LANG_ORDER[i] for i in range(2, 10) if row[i] == row[1]]
            if len(same) >= 5:
                padding.append((zh, len(same), same))
        self.assertEqual(padding, [],
                         "本组新增条目有 >4 个外语槽仍抄英文（补行充数）：%r" % (padding,))

    def test_declared_citation_keys_still_real_citations(self):
        """在册的引用串必须真的是拉丁书目串（防止把未译中文串混进豁免）。"""
        for zh in self.LATIN_CITATION_KEYS:
            self.assertRegex(zh, r"DOI|J\. Res\.|Natl\.|Cochrane",
                             "在册引用串不是拉丁书目：%r" % zh)


class LocalizedThrowMarkerTests(unittest.TestCase):
    """读屏/外壳回归钉（第 2 轮评审阻断项 1）。

    message() 的启发式外壳条件是 analysis_locale.js：`translated === raw`、
    无 CJK、且含 ≥2 个拉丁词。把校验串从「原样中文」改成 T() 之后，非中文
    界面的 raw 已是法语/西语/葡语/德语 → 三条短路全部失效 → 正文被套进
    「此消息暂未翻译」外壳（比修前更差）。唯一正确出路是给这些本地抛出的
    Error 置 rfLocalized=true（仓库既有写法：index.html:8753/8756/8769/8776）。

    破坏即红：删掉任一处的 {rfLocalized:true}、或把 true 翻成 false、
    或改成条件表达式使值为假，该用例都会失败（第 3 轮评审：裸子串匹配
    assertIn("rfLocalized", …) 抓不住 {rfLocalized:false}，已改为解析
    rfLocalized:true）。
    """

    # 自动发现：任何把 error.rfLocalized 传给 RFAnalysisLocale.message() 的模块，
    # 或把 e.rfLocalized 打包进 analysis-synthesis-failed detail 的宿主页，
    # 其 T() 抛出的 Error 都必须带 rfLocalized:true。
    # analysis_forest.js 属后者：它的 throw 由 index.html:8730 的 catch 消费
    # （detail:{localized:e.rfLocalized === true}），再经 analysis_navigation.js
    # 的 message() 渲染，因此不能只按 "RFAnalysisLocale.message(" 字面发现。
    FOREST_CONSUMER_HOST = "index.html"

    def _message_consumer_files(self):
        files = []
        for path in sorted(FRONTEND.glob("*.js")):
            text = path.read_text(encoding="utf-8")
            if "RFAnalysisLocale.message(" in text and "rfLocalized" in text:
                files.append(path.name)
        self.assertTrue(files, "未发现任何 message() 消费方（发现逻辑失效）")
        return tuple(files)

    def _forest_throws_are_consumed_by_host(self):
        """确认 forest 的标记确有宿主消费，否则该文件不该算进网。"""
        html = INDEX.read_text(encoding="utf-8")
        return "localized:e.rfLocalized === true" in html

    def test_localized_throws_carry_rflocalized(self):
        files = list(self._message_consumer_files())
        if self._forest_throws_are_consumed_by_host():
            files.append("analysis_forest.js")
        for name in files:
            src = (FRONTEND / name).read_text(encoding="utf-8")
            body = re.sub(r"/\*.*?\*/", "", src, flags=re.S)  # 去掉注释，避免注释里的 T('…') 干扰
            for match in re.finditer(r"new Error\(\s*T\(", body):
                # 取该 Error 构造所在的语句范围（到分号或行尾换行）
                start = match.start()
                stop = body.find(";", match.end())
                newline = body.find("\n", match.end())
                stop = len(body) if stop < 0 else stop
                if newline >= 0:
                    stop = min(stop, newline + 1) if stop > newline else stop
                statement = body[start:stop]
                self.assertRegex(
                    statement, r"rfLocalized\s*:\s*true",
                    "%s：本地 T() 校验串缺 rfLocalized:true（缺标记、或值被翻成 false/"
                    "条件表达式），非中文界面会被 message() 套进「暂无译文」外壳：%r"
                    % (name, statement[:160]))

    def test_known_markers_present(self):
        """回归保护：两处已修文件必须仍在覆盖集合内（发现逻辑被削弱即红）。"""
        files = list(self._message_consumer_files())
        if self._forest_throws_are_consumed_by_host():
            files.append("analysis_forest.js")
        for name in ("analysis_dependent_synthesis.js", "analysis_forest.js"):
            self.assertIn(name, files,
                          "%s 不在 T() 抛出检查范围内，钉子覆盖出现盲区" % name)

    def test_message_shell_predicate_unchanged(self):
        """外壳谓词本身不得放宽（否则会用改 message() 掩盖缺标记）。"""
        src = (FRONTEND / "analysis_locale.js").read_text(encoding="utf-8")
        self.assertIn("translated !== raw", src,
                      "message() 的 translated/raw 短路被移除（改判据而非补标记）")
        self.assertIn(r"/[\u3400-\u9fff]/.test(raw)", src,
                      "message() 的 CJK 短路被移除")


class STOPREGClaimCopyTests(unittest.TestCase):
    """STOP-REG（经升级授权的跨域一处）：stop_registry.py:29 的 certificate
    description 不得再作「以 ≥95% 置信度保证」的超 claim（F-04-01 漏掉的第 4 处），
    口径须与 main.py:6273-6274 / al_stop_panel.js:237 已修的三处一致。"""

    def test_stop_registry_description_honest(self):
        src = (REPO / "coscreen" / "al" / "stop_registry.py").read_text(encoding="utf-8")
        line = next((l for l in src.splitlines() if '"certificate"' in l), "")
        self.assertTrue(line, "STOP-REG：stop_registry.py 中找不到 certificate 条目")
        self.assertNotIn("置信度保证总召回", line,
                         "STOP-REG：certificate description 仍是「以 ≥95% 置信度保证总召回」超 claim")
        self.assertNotIn("保证总召回 ≥95%", line,
                         "STOP-REG：certificate description 仍声称保证总召回 ≥95%")
        self.assertIn("并非固定 95%", line,
                      "STOP-REG：certificate description 须声明置信度并非固定 95%（与另三处口径一致）")


if __name__ == "__main__":
    unittest.main()
