# -*- coding: utf-8 -*-
"""r10-U5 文案泳道静态钉子（批准单 §五 F23/F47/F48/F49/F50/F51/F52）。

本文件只钉「无法用一次点击复现」的层：词典结构、渲染路径接线、范围与措辞。
交互面（F52 对话框的焦点陷阱/ESC/危险色）在
``build/tests/test_r10_u5_confirm_browser.cjs``，由本文件的
``test_confirm_dialog_browser_nail`` 以 subprocess 门控执行。

钉 ↔ 破坏对照（每条至少一个）：
- F23 ``test_decision_label_render_sites_go_through_T``：把 :7473/:7484 的 T() 去掉 → 红；
- F23 ``test_no_stale_maybe_wording_reaches_the_screen``：把任意 strings 后端（
  alias 去掉）→ node 探针解析出「待定」→ 红；
- F47 ``test_role_labels_come_from_the_dictionary``：把 ``T(label)`` 改回字面量
  或把 option 写死英文 → 红；
- F48 ``test_evidence_locale_validator_gate``：往证据模块塞一条未注册中文 →
  校验器必须非零退出；删掉校验器的「一字之差」检测 → 红；
- F49 ``test_same_screen_uses_one_noun``：把筛选语境行改回 article → 红；
- F50 ``test_destination_and_range_wording``：``.trash`` 或 ``2~9`` 回到用户可见面 → 红；
- F51 ``test_login_constraints_are_stated_before_submitting``：占位符退回只给长度 → 红；
- F52 ``test_confirm_dialog_replaces_native_confirm``：任一处 ``confirm(T(`` 回归、删除任务
  退出 2.1.5 两选项删除对话框（task-delete 契约 §一）→ 红；
- 十语言：本轮新增/改写的每一行 10 槽非空、占位符一致、非中文槽不得回退成中文。
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "custom_frontend"
INDEX = FRONTEND / "index.html"
NODE = (
    os.environ.get("RF_NODE")
    or shutil.which("node")
    or r"C:\Users\16073\AppData\Local\hermes\node\node.exe"
)
LANGS = 10

pytestmark = pytest.mark.skipif(not os.path.exists(NODE), reason="node runtime not found")


def _read(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 词典求值：node 侧逐字复刻 index.html 的加载语义
# ---------------------------------------------------------------------------
_RESOLVE = r"""
const fs=require('fs'), path=require('path'), vm=require('vm');
const fe=process.argv[2];
const html=fs.readFileSync(path.join(fe,'index.html'),'utf8');
function extractArray(src,name){
  const re=new RegExp('const\\s+'+name+'\\s*=\\s*\\[');
  const m=re.exec(src);
  const open=m.index+m[0].length-1;
  let depth=0, quote=null;
  for(let i=open;i<src.length;i+=1){
    const c=src[i];
    if(quote){ if(c==='\\'){i+=1;continue;} if(c===quote)quote=null; continue; }
    if(c==='\''||c==='\"'||c==='`'){quote=c;continue;}
    if(c==='[')depth+=1; else if(c===']'){depth-=1; if(!depth)return src.slice(open,i+1);}
  }
  throw new Error('unclosed '+name);
}
/* 种子行只有 row[0] 作键（与 index.html:4922 的 Map 构造一致） */
const seedRows=[];
for(const n of ['rows','APP_ROWS','PRISMA_ROWS']) seedRows.push(...vm.runInNewContext('('+extractArray(html,n)+')',{}));
const dictionary=new Map(seedRows.filter(Boolean).map(row=>[row[0],row]));
function register(pairs){
  for(const entry of pairs){
    if(!Array.isArray(entry)) continue;
    const [zh,en]=entry;
    const previous=dictionary.get(zh)||dictionary.get(en);
    const row=[];
    for(let i=0;i<10;i+=1) row.push(entry[i]||(previous&&previous[i])||en);
    for(const value of row) if(value) dictionary.set(value,row);
    dictionary.set(zh,row); dictionary.set(en,row);
    for(const alias of entry.aliases||[]) dictionary.set(alias,row);
  }
}
const RFLang={register, get:()=>'zh', index:()=>0, capture:()=>null, render:v=>v,
  t(v){const r=dictionary.get(String(v))||dictionary.get(String(v).trim()); return r?r[0]:String(v);}};
const sandbox={console:{warn(){},log(){},error(){}}, RFLang,
  document:{querySelector:()=>null,querySelectorAll:()=>[],addEventListener(){},removeEventListener(){},getElementById:()=>null,documentElement:{lang:''},body:{appendChild(){},addEventListener(){}}},
  navigator:{language:'zh-CN'}, localStorage:{getItem:()=>null,setItem(){}}};
sandbox.window=sandbox; sandbox.globalThis=sandbox;
/* The parser runs ordinary scripts before RFAppModules replays workspace
   markers. Keep document order within each phase. */
const eager=[], workspace=[];
for(const [,attrs] of html.matchAll(/<script\b([^>]*)>/gi)){
  const src=/(?:^|\s)src\s*=\s*["']\.\/([^"'?]+)\?v=[^"']*["']/i.exec(attrs);
  if(!src) continue;
  const queue=/(?:^|\s)data-rf-app(?:\s|=|$)/i.test(attrs) ? workspace : eager;
  queue.push(src[1]);
}
for(const file of [...eager,...workspace]){
  const full=path.join(fe,file);
  if(!fs.existsSync(full)) continue;
  const src=fs.readFileSync(full,'utf8');
  if(!/RFLang\s*\??\.register/.test(src)) continue;
  try{ vm.runInNewContext(src,sandbox,{filename:file,timeout:20000}); }catch(e){}
}
const probes=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const out={};
for(const probe of probes){
  const row=dictionary.get(probe)||dictionary.get(String(probe).trim());
  if(!row){ out[probe]=null; continue; }
  out[probe]={zh:row[0], slots:Array.from({length:10},(_,i)=>row[i]??null), width:row.length};
}
process.stdout.write(JSON.stringify(out));
"""


def _resolve(probes: list[str]) -> dict:
    """用真引擎语义解析一批串：返回 {probe: {zh, slots[10], width} | None}。"""
    script = REPO / "build" / "tests" / "_r10_u5_resolve.tmp.cjs"
    payload = REPO / "build" / "tests" / "_r10_u5_resolve.tmp.json"
    script.write_text(_RESOLVE, encoding="utf-8")
    payload.write_text(json.dumps(probes, ensure_ascii=False), encoding="utf-8")
    try:
        proc = subprocess.run(
            [NODE, str(script), str(FRONTEND), str(payload)],
            capture_output=True, text=True, encoding="utf-8", timeout=180,
        )
    finally:
        script.unlink(missing_ok=True)
        payload.unlink(missing_ok=True)
    assert proc.returncode == 0, f"词典求值失败：{proc.stderr[-1500:]}"
    return json.loads(proc.stdout)


def _locale_rows(name: str) -> list[list[str]]:
    """取一个 locale 文件里所有「整行是数组字面量」的词条（含单引号风格）。

    用 ast.literal_eval 而不是 json：仓库里两种引号风格并存
    （``["中文", ...]`` 与 ``['中文', ...]``），json 会静默跳过后者。
    """
    rows: list[list[str]] = []
    for line in _read(name).splitlines():
        text = line.strip()
        if not text.startswith("["):
            continue
        text = text.rstrip(",").rstrip(";").strip()
        try:
            row = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            continue
        if isinstance(row, list) and row and all(isinstance(slot, str) for slot in row):
            rows.append(row)
    return rows


def _placeholders(text: str) -> list[str]:
    return sorted(re.findall(r"\{(\d+)\}", text))


def _assert_ten_language(row: list[str], label: str) -> None:
    assert len(row) == LANGS, f"{label}：必须恰为十语言，实为 {len(row)} 槽"
    assert all(slot.strip() for slot in row), f"{label}：存在空槽"
    reference = _placeholders(row[1])
    for slot in row:
        assert _placeholders(slot) == reference, f"{label}：占位符跨槽不一致（{slot!r}）"
    for index, slot in enumerate(row):
        if index in (0, 5):      # zh 与 ja 合法含汉字
            continue
        assert not re.search(r"[\u4e00-\u9fff]", slot), \
            f"{label}：第 {index} 槽仍是中文（未翻译）：{slot!r}"


# ---------------------------------------------------------------------------
# F23：决策标签的渲染路径
# ---------------------------------------------------------------------------
class TestDecisionLabelRendering:
    def test_decision_label_render_sites_go_through_T(self):
        """轨迹明细表与仲裁下拉必须经 T() 取词条。

        破坏：把 ``T(DEC_LABEL[...])`` 改回 ``DEC_LABEL[...]`` 即红——那正是
        批准单点名的「不经 T() 直接拼接」旁路。
        """
        html = INDEX.read_text(encoding="utf-8")
        assert "T(DEC_LABEL[r['decision_' + n]])" in html, \
            "F23：轨迹/决策表的 DEC_LABEL 必须经 T() 输出"
        assert re.search(r"el\('option', \{value, text:T\(DEC_LABEL\[value\]\)\}\)", html), \
            "F23：仲裁下拉的选项文案必须经 T() 输出"
        bare = re.findall(r"text:\s*DEC_LABEL\[(?!\s*r\[)", html)
        assert bare == [], f"F23：仍有裸 DEC_LABEL 拼接（不经 T()）：{bare}"

    def test_no_stale_maybe_wording_reaches_the_screen(self):
        """旧词「待定」不得成为任何语言里的**可见**结果。

        做法：取 DEC_LABEL 的源串与三处被改写的源串，用真引擎语义解析；中文槽
        必须是规范词，英语槽必须是 Not Sure Now（与同屏「2 Not Sure Now」一致）。
        """
        result = _resolve([
            "待定", "2 待定", "待定 {0}", "◐ 待定《{0}》",
            "◐ 复筛待定《{0}》", "没有可用于新一轮的待定文献。",
            "理由需随「纳入 / 待定 / 排除」决策一起保存：请先做决策，或清空草稿",
        ])
        for probe, row in result.items():
            assert row is not None, f"F23：{probe!r} 未注册（旧串必须作为别名命中规范词）"
            assert "待定" not in row["slots"][0], \
                f"F23：{probe!r} 的中文槽仍含旧词「待定」：{row['slots'][0]!r}"
            assert "Maybe" not in (row["slots"][1] or ""), \
                f"F23：{probe!r} 的英文槽仍是旧措辞 Maybe：{row['slots'][1]!r}"
        # 决策标签本身：中文「暂不确定」、英文 Not Sure Now
        assert result["待定"]["slots"][0] == "暂不确定"
        assert result["待定"]["slots"][1] == "Not Sure Now"
        assert result["2 待定"]["slots"][0] == "2 暂不确定"

    def test_rewritten_rows_are_ten_language_complete(self):
        """三处改写行的十语言完整性——经**真引擎**取行，不经源码行解析。

        「◐ 复筛暂不确定《{0}》」等行以 ``Object.assign([...], {aliases})`` 形式
        注册，源码行首不是 ``[``，Python 行解析取不到；必须走与 index.html
        同构的求值，否则测试会对着空集「通过」。
        """
        keys = ("◐ 复筛暂不确定《{0}》", "没有可用于新一轮的暂不确定文献。",
                "理由需随「纳入 / 暂不确定 / 排除」决策一起保存：请先做决策，或清空草稿")
        result = _resolve(list(keys))
        for key in keys:
            row = result[key]
            assert row is not None, f"F23：改写行未进十语言词典：{key}"
            assert row["width"] == LANGS, f"F23：{key} 必须恰为十语言，实为 {row['width']} 槽"
            _assert_ten_language(row["slots"], key)


# ---------------------------------------------------------------------------
# F47：角色术语
# ---------------------------------------------------------------------------
class TestRoleTerminology:
    def test_role_labels_come_from_the_dictionary(self):
        """邀请下拉与成员菜单的角色文案必须经 T() 取词条，不得硬编码英文。

        破坏：把 ``text:T(label)`` 改回 ``text:label``，或把 option 写回
        ``Coder``/``Adjudicator`` 字面量 → 红。
        """
        html = INDEX.read_text(encoding="utf-8")
        assert 'text:T(label)' in html, "F47：成员菜单角色项必须经 T() 取词条"
        assert re.search(r"<option value=\"coder\">筛选与编码员（Coder）</option>", html), \
            "F47：邀请下拉必须显示「译名（英文原词）」"
        assert re.search(r"<option value=\"reconciler\">裁决员（Adjudicator）</option>", html), \
            "F47：邀请下拉必须显示「译名（英文原词）」"
        assert "<option value=\"coder\">Coder</option>" not in html, \
            "F47：邀请下拉不得退回硬编码英文"
        assert "T(role === 'coder' ? '筛选与编码员（Coder）' : '裁决员（Adjudicator）')" in html, \
            "F47：改角 toast 的角色名必须经 T() 输出"

    def test_role_rows_resolve_to_translated_names(self):
        result = _resolve(["筛选与编码员（Coder）", "裁决员（Adjudicator）", "Coder", "Adjudicator"])
        coder, adjudicator = result["筛选与编码员（Coder）"], result["裁决员（Adjudicator）"]
        assert coder and adjudicator, "F47：角色词条未注册"
        assert coder["slots"][0] == "筛选与编码员（Coder）"
        assert coder["slots"][1] == "Coder", "F47：英语槽必须是原词本身"
        assert adjudicator["slots"][1] == "Adjudicator"
        # 团队徽标经 T('Coder') 取词：注册集里另有短行「筛选与编码员」（team_prisma
        # 后加载，胜出），两种形态都算对齐——要抓的是「仍是英文」。
        badge = result["Coder"]["slots"][0]
        assert badge.startswith("筛选与编码员"), \
            f"F47：T('Coder') 在中文界面必须给出译名，实为 {badge!r}"
        assert result["Adjudicator"]["slots"][0].startswith("裁决员"), \
            f"F47：T('Adjudicator') 在中文界面必须给出译名，实为 {result['Adjudicator']['slots'][0]!r}"
        _assert_ten_language(coder["slots"], "筛选与编码员（Coder）")
        _assert_ten_language(adjudicator["slots"], "裁决员（Adjudicator）")

    def test_terminology_doc_records_the_pair(self):
        doc = (REPO / "docs" / "UI_TERMINOLOGY.md").read_text(encoding="utf-8")
        assert "Role names in controls" in doc, "F47：术语文档必须记录控件层角色命名"
        assert "筛选与编码员" in doc and "裁决员" in doc
        assert "Reconciler" in doc and "internal grant identifier" in doc, \
            "F47：文档必须说明 Reconciler 只作内部授权标识"


# ---------------------------------------------------------------------------
# F48：证据工作区多语言覆盖 + 构建期一致性门
# ---------------------------------------------------------------------------
class TestEvidenceLocaleGate:
    VALIDATOR = REPO / "build" / "validate_evidence_locale_static.cjs"

    def test_validator_exists_and_passes_on_the_live_tree(self):
        assert self.VALIDATOR.is_file(), "F48：缺少字面量一致性检查脚本"
        proc = subprocess.run([NODE, str(self.VALIDATOR), str(REPO)],
                              capture_output=True, text=True, encoding="utf-8", timeout=180)
        assert proc.returncode == 0, f"F48：一致性门在干净树上也红：{proc.stderr[-1500:]}"
        report = json.loads(proc.stdout)
        assert report["status"] == "passed"
        assert report["evidence_modules"] >= 3, "F48：门必须覆盖报告关联/证据上传/证据地图"
        assert report["scanned_literals"] > 100, \
            "F48：扫描量过低说明扫描器空转（防门假绿）"

    def test_validator_run_is_part_of_the_ci_gate(self):
        """构建期强制：本文件把校验器当子进程跑，而 CI 跑的就是 ``pytest build/tests``。

        批准单 F48 要的是「构建期检查防复发」。实现路径选择：**不**去改
        ``build/build_*.sh`` / ``release.yml`` 的校验器清单——那三处各有一份写死的
        文件列表（``test_r8_fl8_build.py`` 的 FOUR_VALIDATORS 与
        ``test_r7_dayfix_build_scripts.py`` 的 fixture 拷贝表），加第四/五个校验器
        属于打包泳道的改动，且会让那两个文件的隔离树因缺文件而红（本轮实测：
        改动前 38 项全绿，改动后 6 项红）。CI 的 pytest 步已覆盖本目录，本测试
        文件跑校验器即等于 CI 门的一部分。
        """
        ci = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        assert "python -m pytest build/tests -q" in ci, \
            "F48：CI 必须在 build/tests 上跑 pytest（本文件跑校验器即构建期强制）"
        # 别把「本文件自己存在」当断言：pytest 已经在跑它，那是恒真式。
        assert self.VALIDATOR.is_file()

    def test_validator_reds_on_an_unregistered_literal(self, tmp_path):
        """破坏：往证据模块里加一条未注册的中文串 → 门必须非零退出。"""
        tree = tmp_path / "tree"
        # 跳过未跟踪的 Mimosa 钩子状态目录（非产品文件；其深层长路径会让 copytree 失败）
        shutil.copytree(FRONTEND, tree / "custom_frontend", ignore=shutil.ignore_patterns(".mimosa"))
        target = tree / "custom_frontend" / "workflow_evidence.js"
        text = target.read_text(encoding="utf-8")
        target.write_text(text + "\n/* probe */\nfunction probeF48(){return '未注册的证据工作区文案';}\n",
                          encoding="utf-8")
        proc = subprocess.run([NODE, str(self.VALIDATOR), str(tree)],
                              capture_output=True, text=True, encoding="utf-8", timeout=180)
        assert proc.returncode != 0, "F48：未注册字面量必须让门变红"
        assert "未注册的证据工作区文案" in (proc.stdout + proc.stderr), \
            "F48：门必须点名具体字面量"

    def test_validator_reds_on_a_one_character_near_miss(self, tmp_path):
        """破坏：写「尚无评估。」（注册键是「暂无评估。」）→ 必须点名一字之差。"""
        tree = tmp_path / "tree"
        # 跳过未跟踪的 Mimosa 钩子状态目录（非产品文件；其深层长路径会让 copytree 失败）
        shutil.copytree(FRONTEND, tree / "custom_frontend", ignore=shutil.ignore_patterns(".mimosa"))
        target = tree / "custom_frontend" / "workflow_evidence.js"
        text = target.read_text(encoding="utf-8")
        target.write_text(text + "\n/* probe */\nfunction probeNear(){return '尚无评估。';}\n",
                          encoding="utf-8")
        proc = subprocess.run([NODE, str(self.VALIDATOR), str(tree)],
                              capture_output=True, text=True, encoding="utf-8", timeout=180)
        assert proc.returncode != 0, "F48：一字之差必须让门变红"
        combined = proc.stdout + proc.stderr
        assert "只差一个汉字" in combined, \
            f"F48：门必须专门指出「一字之差」变体（原病灶「尚无评估。」）：{combined[-800:]}"

    def test_assessment_empty_state_uses_the_registered_key(self):
        """F48 原病灶：`尚无评估。` 与注册键 `暂无评估。` 一字之差永不命中。"""
        assert "尚无评估" not in _read("report_evidence.js"), \
            "F48：证据工作区不得再发射未注册的「尚无评估。」"
        rows = {row[0]: row for row in _locale_rows("report_evidence_locale.js")}
        assert "暂无评估。" in rows, "F48：空评估键必须仍在注册表"
        _assert_ten_language(rows["暂无评估。"], "暂无评估。")

    def test_evidence_modules_have_no_unregistered_cjk_literals(self):
        """证据工作区三个模块的中文字面量必须全部可命中（25 串清单的回归面）。"""
        script = tmp_path_script = REPO / "build" / "tests" / "_r10_u5_scan.tmp.cjs"
        script.write_text(r"""
const fs=require('fs'), path=require('path'), vm=require('vm');
const fe=process.argv[2];
const html=fs.readFileSync(path.join(fe,'index.html'),'utf8');
function extractArray(src,name){
  const re=new RegExp('const\\s+'+name+'\\s*=\\s*\\[');
  const m=re.exec(src); const open=m.index+m[0].length-1;
  let depth=0, quote=null;
  for(let i=open;i<src.length;i+=1){
    const c=src[i];
    if(quote){ if(c==='\\'){i+=1;continue;} if(c===quote)quote=null; continue; }
    if(c==='\''||c==='\"'||c==='`'){quote=c;continue;}
    if(c==='[')depth+=1; else if(c===']'){depth-=1; if(!depth)return src.slice(open,i+1);}
  }
}
const keys=new Set();
function absorb(row){
  if(!Array.isArray(row)) return;
  for(const v of row) if(typeof v==='string') keys.add(v);
  for(const a of row.aliases||[]) if(typeof a==='string') keys.add(a);
}
for(const n of ['rows','APP_ROWS','PRISMA_ROWS'])
  for(const r of vm.runInNewContext('('+extractArray(html,n)+')',{})) absorb(r);
const stub={register:rows=>rows.forEach(absorb),t:v=>v,get:()=>'zh',index:()=>0,capture:()=>null,render:v=>v};
const sb={console:{warn(){},log(){},error(){}},RFLang:stub,
  document:{querySelector:()=>null,querySelectorAll:()=>[],addEventListener(){},removeEventListener(){},getElementById:()=>null,documentElement:{lang:''},body:{appendChild(){},addEventListener(){}}},
  navigator:{language:'zh-CN'},localStorage:{getItem:()=>null,setItem(){}}};
sb.window=sb; sb.globalThis=sb;
for(const f of fs.readdirSync(fe).filter(n=>n.endsWith('_locale.js')))
  try{ vm.runInNewContext(fs.readFileSync(path.join(fe,f),'utf8'),sb,{filename:f,timeout:20000}); }catch(e){}
const missing=[];
let scanned=0;
for(const file of ['report_evidence.js','workflow_evidence.js','evidence_mapper.js']){
  const src=fs.readFileSync(path.join(fe,file),'utf8')
    .replace(/\/\*[\s\S]*?\*\//g,' ').replace(/\/\/[^\n]*/g,'');
  const re=/(['"`])((?:[^\\]|\\.)*?)\1/g; let m;
  while((m=re.exec(src))){
    if(!/[\u4e00-\u9fff]/.test(m[2])) continue;
    scanned+=1;
    if(!keys.has(m[2])) missing.push(file+': '+JSON.stringify(m[2]));
  }
}
process.stdout.write(JSON.stringify({scanned, missing:[...new Set(missing)]}));
""", encoding="utf-8")
        try:
            proc = subprocess.run([NODE, str(script), str(FRONTEND)],
                                  capture_output=True, text=True, encoding="utf-8", timeout=180)
        finally:
            pathlib_unlink(script)
        assert proc.returncode == 0, proc.stderr[-1200:]
        report = json.loads(proc.stdout)
        assert report["scanned"] > 200, f"F48：扫描量过低（{report['scanned']}），扫描器可能空转"
        assert report["missing"] == [], \
            "F48：证据工作区仍有未注册的用户可见文案（9 种非中文界面会漏中文）：" + \
            json.dumps(report["missing"], ensure_ascii=False)


def pathlib_unlink(path: Path) -> None:
    path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# F49：英文界面 article / record（随版缓解：同屏一个名词）
# ---------------------------------------------------------------------------
class TestEnglishNounConsistency:
    SCREENING_ROWS = (
        ("workflow_locale.js", "此文献不符合当前阶段条件。", "This record is not eligible for this stage."),
        ("workflow_locale.js", "此文献不在允许的来源范围内。",
         "This record is outside the permitted sources."),
        ("workflow_locale.js", "该文献不在当前队列中，请重新载入初筛后再查看。",
         "This record is not in the current queue; reload screening and try again."),
        ("workflow_locale.js", "此已保存文献不在所选队列内。请返回队列继续。",
         "This saved record is outside the selected queue. Return to the queue to continue."),
        ("team_prisma_locale.js", "该文献正在再次初筛，请先完成本轮或选择继续全文筛选。",
         "This record is in a repeat screening round. Finish the round or proceed to full-text screening first."),
    )

    def test_same_screen_uses_one_noun(self):
        """筛选语境的资格提示英文槽一律 Record（术语表首选）。

        破坏：任一行改回 article → 红——那正是「同一筛选单元两个名词」的原病灶。
        取行径真引擎（部分行以 ``Object.assign`` 注册，源码行首不是 ``[``）。
        """
        probes = [zh for _, zh, _ in self.SCREENING_ROWS]
        result = _resolve(probes)
        for file, zh, en in self.SCREENING_ROWS:
            row = result[zh]
            assert row is not None, f"F49：{zh} 未注册"
            assert row["slots"][1] == en, \
                f"F49：{file} 的英文槽应统一用 Record：{row['slots'][1]!r}"
            assert "article" not in row["slots"][1].lower(), \
                f"F49：{file} 仍用 article：{row['slots'][1]!r}"

    def test_old_english_strings_stay_reachable_as_aliases(self):
        """缓存控件/旧页面仍按旧英文串发射，必须仍能命中（随版缓解的兼容面）。"""
        result = _resolve([
            "This article is not eligible for this stage.",
            "This article is outside the permitted sources.",
            "This article is in a repeat screening round. Finish the round or proceed to full-text screening first.",
            "This article is not in the current queue; reload screening and try again.",
            "This saved article is outside the selected queue. Return to the queue to continue.",
        ])
        for probe, row in result.items():
            assert row is not None, f"F49：旧英文串 {probe!r} 不再是可命中的键"
            assert "record" in row["slots"][1].lower(), \
                f"F49：旧英文串必须解析到 Record 措辞：{row['slots'][1]!r}"

    def test_terminology_doc_records_the_queue_rule(self):
        doc = (REPO / "docs" / "UI_TERMINOLOGY.md").read_text(encoding="utf-8")
        assert "Queue eligibility messages" in doc, "F49：术语文档必须记录队列资格提示口径"
        assert "Record" in doc


# ---------------------------------------------------------------------------
# F50：任务删除去向命名与区间/标点
# ---------------------------------------------------------------------------
class TestDeletionWording:
    def test_destination_and_range_wording(self):
        html = INDEX.read_text(encoding="utf-8")
        assert ".trash" not in html, "F50：界面不得显示内部目录名 .trash"
        assert "已删除（移入隔离区）" in html, "F50：删除成功提示必须统一说「隔离区」"
        assert "任务将移入隔离区" in html, "F50：确认文案必须统一说「隔离区」"
        user_visible = "\n".join(
            line for line in html.splitlines()
            if not line.strip().startswith(("/*", "*", "//"))
        )
        assert "2~9" not in user_visible, "F50：区间写法必须统一为 2–9（不得 2~9）"
        # 用户可见面（非注释）不得残留 2~9；注释只允许出现在代码说明里
        for name in ("help_locale.js",):
            assert "2~9" not in _read(name), f"F50：{name} 仍用 2~9"

    def test_help_item_ends_with_a_period(self):
        html = INDEX.read_text(encoding="utf-8")
        assert "<li>逐条选择仲裁结论并保存。</li>" in html, "F50：帮助清单条目必须补句号"
        assert "<li>逐条选择仲裁结论并保存</li>" not in html, "F50：无句号的旧条目必须消失"
        rows = {row[0]: row for row in _locale_rows("help_locale.js")}
        assert "逐条选择仲裁结论并保存。" in rows, "F50：补句号后的词条必须在词典里"
        _assert_ten_language(rows["逐条选择仲裁结论并保存。"], "逐条选择仲裁结论并保存。")

    def test_terminology_doc_notes_the_destination(self):
        doc = (REPO / "docs" / "UI_TERMINOLOGY.md").read_text(encoding="utf-8")
        assert "隔离区" in doc and ".trash" in doc, "F50：文档必须说明 .trash 即隔离区"


# ---------------------------------------------------------------------------
# F51：登录/注册占位符的约束摘要
# ---------------------------------------------------------------------------
class TestLoginConstraintCopy:
    ZH_USER = "3–64 位，可用字母、数字、. _ @ - 与汉字"
    ZH_PASS = "8–128 位"
    EN_USER = "3–64 characters: letters, digits, . _ @ - and Chinese characters"
    EN_PASS = "8–128 characters"

    def test_login_constraints_are_stated_before_submitting(self):
        """占位符必须给字符集与上限，而不是只给长度。

        破坏：把 placeholder 改回「3–64 个字符」/「至少 8 个字符」→ 红。
        """
        html = INDEX.read_text(encoding="utf-8")
        assert f'placeholder="{self.ZH_USER}"' in html, "F51：用户名占位符必须给出字符集"
        assert f'placeholder="{self.ZH_PASS}"' in html, "F51：密码占位符必须给出上限"
        assert 'placeholder="3–64 个字符"' not in html, "F51：不得退回只给长度"
        assert 'placeholder="至少 8 个字符"' not in html, "F51：不得退回「至少 8 个字符」"

    def test_constraints_match_the_backend(self):
        """文案必须与后端正则/上限同步（正则一改，这里先红）。"""
        auth = (REPO / "custom_backend" / "auth.py").read_text(encoding="utf-8")
        assert r'^[0-9A-Za-z._@\-\u4e00-\u9fff]{3,64}$' in auth, \
            "F51：后端用户名正则变了——占位符文案必须同步复核"
        assert "_MIN_PASSWORD_LEN = 8" in auth and "_MAX_PASSWORD_LEN = 128" in auth, \
            "F51：后端密码长度区间变了——占位符文案必须同步复核"

    def test_placeholders_are_registered_in_ten_languages(self):
        result = _resolve([self.ZH_USER, self.ZH_PASS])
        assert result[self.ZH_USER]["slots"][1] == self.EN_USER
        assert result[self.ZH_PASS]["slots"][1] == self.EN_PASS
        _assert_ten_language(result[self.ZH_USER]["slots"], self.ZH_USER)
        _assert_ten_language(result[self.ZH_PASS]["slots"], self.ZH_PASS)


# ---------------------------------------------------------------------------
# F52：应用内确认对话框
# ---------------------------------------------------------------------------
class TestConfirmDialog:
    def test_confirm_dialog_replaces_native_confirm(self):
        """破坏性确认不得走原生 confirm；删除任务走 2.1.5 两选项删除对话框。

        2.1.5 任务删除（frontend_contract_2.1.5-task-delete.md §一）把「删除任务」
        从单按钮 RFConfirm 升级为「移入回收站（默认）/彻底删除（需键入任务 ID）」
        专用对话框（task_delete.js）；移除成员/删除维度仍走 RFConfirm。

        破坏：任一处退回 ``confirm(T(...)`` → 红（按钮文字又会随浏览器语言）；
        删除任务退回单按钮确认（丢掉键入任务 ID 的误删闸门）→ 红。
        """
        html = INDEX.read_text(encoding="utf-8")
        assert re.findall(r"(?<![.\w])confirm\s*\(\s*T\(", html) == [], \
            "F52：index.html 仍在使用原生 confirm(T(...))"
        assert html.count("await RFConfirm({") >= 2, \
            "F52：两处破坏性确认（移除成员/删除维度）都必须走 RFConfirm"
        assert "await window.RFTaskDelete?.open(t);" in html, \
            "F52：删除任务按钮必须进入 2.1.5 两选项删除对话框（task_delete.js）"
        dialog = _read("task_delete.js")
        assert re.findall(r"(?<![.\w])confirm\s*\(", dialog) == [], \
            "F52：删除对话框不得退回原生 confirm（按钮文字会随浏览器语言）"
        assert "value:'trash', checked:''" in dialog, \
            "F52：默认选项必须是「移入回收站」（task-delete 契约 §一）"
        assert "input.value !== task.task_id" in dialog and \
               "new URLSearchParams({purge:'true', confirm:input.value})" in dialog, \
            "F52：彻底删除必须键入与任务 ID 完全一致的确认串才放行提交（契约 §一）"
        assert "confirmation.hidden = !purge.checked" in dialog, \
            "F52：选中彻底删除即展示不可逆警示（契约 §一）"

    def test_dialog_contract(self):
        """焦点陷阱、ESC、危险色、初始焦点落「取消」、关闭后归还焦点。"""
        html = INDEX.read_text(encoding="utf-8")
        confirm = re.search(r"(?ms)^function RFConfirm\(options\) \{.*?^\}", html)
        assert confirm is not None, "F52：必须保留 RFConfirm"
        body = confirm.group()
        assert "dialog.showModal();" in body, "F52：必须用 showModal（原生焦点陷阱底座）"
        assert "opts.input ? el('input'" in body, "F52：仅显式请求时显示输入框"
        assert "(input || cancel).focus();" in body, "F52：无输入框时初始焦点必须落在「取消」"
        assert "event.key !== 'Tab'" in body and "event.preventDefault(); last.focus();" in body, \
            "F52：Tab 必须在对话框内循环"
        assert "addEventListener('cancel', event => { event.preventDefault(); finish(false); })" in body, \
            "F52：Esc 必须取消并返回 false"
        assert "opener.focus()" in body, "F52：关闭后焦点必须回到打开它的控件"
        assert "'b-exc'" in body and "opts.danger === false ? 'b-inc' : 'b-exc'" in body, \
            "F52：危险动作必须是红色主按钮"
        assert "aria-labelledby': 'rf-confirm-title'" in body and \
               "aria-describedby': 'rf-confirm-message'" in body, "F52：对话框必须有可访问名与描述"
        assert "#rf-confirm-dialog::backdrop" in html, "F52：对话框必须有自己的 ::backdrop 样式"

    def test_dialog_labels_are_ten_language(self):
        result = _resolve(["请确认", "取消", "确定"])
        for probe in ("请确认", "取消", "确定"):
            assert result[probe] is not None, f"F52：对话框文案 {probe!r} 未注册"
            _assert_ten_language(result[probe]["slots"], probe)
        assert result["请确认"]["slots"][1] == "Please confirm"
        assert result["取消"]["slots"][1] == "Cancel"
        assert result["确定"]["slots"][1] == "Confirm"

    def test_terminology_doc_records_the_component(self):
        doc = (REPO / "docs" / "UI_TERMINOLOGY.md").read_text(encoding="utf-8")
        assert "Confirmation dialogs" in doc, "F52：文档必须记录确认对话框形态"
        assert "RFConfirm" in doc and "Escape" in doc

    def test_confirm_dialog_browser_nail(self):
        """真浏览器：危险色、初始焦点、Tab 循环、Esc 取消、焦点归还、确认返回 true。"""
        if not (os.environ.get("REVIEWFLOW_PLAYWRIGHT") and os.environ.get("PLAYWRIGHT_BROWSERS_PATH")):
            pytest.skip("playwright runtime not configured")
        script = REPO / "build" / "tests" / "test_r10_u5_confirm_browser.cjs"
        assert script.is_file(), "F52：缺少真浏览器钉子"
        proc = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                              encoding="utf-8", timeout=300, cwd=str(REPO))
        assert proc.returncode == 0, \
            f"F52：真浏览器钉子失败\nstdout={proc.stdout[-2500:]}\nstderr={proc.stderr[-2500:]}"
        assert "R10-U5 CONFIRM OK" in proc.stdout


class TestVisibleCopyBrowser:
    """F23/F47 的**渲染面**：静态钉只证明源串与词条，证明不了屏幕。

    真浏览器装真实 index.html，验证二元决策历史计数、旧记录的兼容标签，
    以及角色下拉在两种语言下的译名。
    """

    def test_copy_render_browser_nail(self):
        if not (os.environ.get("REVIEWFLOW_PLAYWRIGHT") and os.environ.get("PLAYWRIGHT_BROWSERS_PATH")):
            pytest.skip("playwright runtime not configured")
        script = REPO / "build" / "tests" / "test_r10_u5_copy_browser.cjs"
        assert script.is_file(), "F23/F47：缺少渲染面真浏览器钉子"
        proc = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                              encoding="utf-8", timeout=300, cwd=str(REPO))
        assert proc.returncode == 0, \
            f"F23/F47：渲染面钉子失败\nstdout={proc.stdout[-2500:]}\nstderr={proc.stderr[-2500:]}"
        assert "R10-U5 COPY OK" in proc.stdout


# ---------------------------------------------------------------------------
# 缓存令牌
# ---------------------------------------------------------------------------
class TestCacheTokens:
    """本轮改过的资产必须带 r10u5 令牌（CacheTokenBindingTests 同款内容的局部对应物）。"""

    BASELINE = {
        "workflow_locale.js": "20261101-r10u5a",
        "team_prisma_locale.js": "20261101-r10u5a",
        "help_locale.js": "20261101-r10u5a",
    }

    def test_changed_assets_carry_a_bumped_token(self):
        html = INDEX.read_text(encoding="utf-8")
        for asset, pinned in self.BASELINE.items():
            # 用引号锚定文件名：`import_export_help_locale.js` 以 `help_locale.js` 结尾，
            # 裸子串匹配会把它的令牌也算进来
            tokens = set(re.findall(r'\./' + re.escape(asset) + r'\?v=([^"]+)', html))
            assert len(tokens) == 1, f"{asset} 令牌引用异常：{tokens}"
            token = tokens.pop()
            # 并行泳道可能继续推进同一令牌，因此断言「≥ 本批令牌」而非逐字相等
            assert token >= pinned, f"{asset} 令牌未随本批内容上调（仍是 {token} < {pinned}）"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
