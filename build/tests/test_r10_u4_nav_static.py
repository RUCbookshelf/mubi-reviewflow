# -*- coding: utf-8 -*-
"""r10-u4 导航泳道静态钉子（F06/F10/F17/F18/F19/F20 ＋ 十语言词典）。

每条修复对应一个「破坏即红」的断言；对照组纪律（与本条无关的既有输出逐位不变）
由各自的正向断言承担：断言的是新增结构，而不是改写既有结构。

- F06 目录页新手路径卡：三个步进直链必须由注册表生成（组 id 顺序 data→effects→
  synthesis），且目录里不出现手写的固定方法名——方法级取舍归内容评审。破坏：把
  卡片改成写死三个方法 id，或删掉 beginnerPath.hidden 的过滤条件，本条即红。
- F17 落地上下文条：syncLandingContext 只在 fragment/entryMode 工具上有内容，
  renderTool 与 openCatalog 都必须调用它（方法切换、进出目录都要对齐）。破坏：
  删掉 renderTool 里的调用即红。
- F18 坏深链提示：enterApp 只在「有片段且无任何已知目标」时置 unrecognizedLink，
  空片段不置位（避免每次登录误报）。破坏：把 `else if (fragment)` 改成无条件即红。
- F19 帮助挂链：初筛/编码/分析三处 data-rf-help-entry 入口齐全且文字走 T()。
- F20 阶段菜单 title：目的地取自注册表页名，不是手写字符串。
- 十语言：navigation_locale.js 全部行 10 个非空槽、占位符逐槽一致（RFLang 会把
  每一列注册成查找键，空格/漏槽会让该语言回退成英文）。
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
NODE = (
    os.environ.get("RF_NODE")
    or shutil.which("node")
    or r"C:\Users\16073\AppData\Local\hermes\node\node.exe"
)
pytestmark = pytest.mark.skipif(not os.path.exists(NODE), reason="node runtime not found")


def read(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


def run_node(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([NODE, "-e", script, *args], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)


# --------------------------------------------------------------- 十语言词典

LOCALE_ROWS = r"""
const vm=require('vm'),fs=require('fs');
const rows=[];
const ctx={RFLang:{register:r=>rows.push(...r)}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),ctx,{timeout:20000});
process.stdout.write(JSON.stringify(rows));
"""

R10_KEYS = (
    "新手路径",
    "按三个步骤开始：先准备数据，再计算效应量，最后合并分析。",
    "当前方法：{0}",
    "分析模块：{0}",
    "将前往：{0}",
    "未识别的链接，已返回首页。",
    "查看帮助",
    "你已注册为独立用户：可新建任务，或等任务属主邀请加入。",
)


def locale_rows() -> list[list[str]]:
    out = run_node(LOCALE_ROWS.replace("process.argv[1]", "process.argv[1]"),
                   str(FRONTEND / "navigation_locale.js"))
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def placeholders(value: str) -> list[str]:
    return sorted(re.findall(r"\{(\d+)\}", value))


def test_every_row_has_ten_non_empty_slots():
    rows = locale_rows()
    assert len(rows) > 8
    for row in rows:
        assert len(row) == 10, f"词条不是十语言：{row[:2]!r}"
        assert all(isinstance(s, str) and s.strip() for s in row), f"词条存在空槽：{row[0]!r}"


def test_placeholders_agree_across_languages():
    for row in locale_rows():
        primary = placeholders(row[1])
        for slot, value in enumerate(row):
            assert placeholders(value) == primary, f"占位符不一致（槽 {slot}）：{row[0]!r} → {value!r}"


def test_new_keys_registered_once_with_distinct_english():
    rows = locale_rows()
    by_zh: dict[str, list[list[str]]] = {}
    for row in rows:
        by_zh.setdefault(row[0], []).append(row)
    for key in R10_KEYS:
        assert key in by_zh, f"r10-u4 新文案未注册十语言：{key!r}"
        assert len(by_zh[key]) == 1, f"同一中文源串重复注册：{key!r}"
    english = [by_zh[key][0][1] for key in R10_KEYS]
    assert len(set(english)) == len(english), "同一英文串承载两个概念（RFLang 槽位冲突）：%r" % english


# ------------------------------------------------------- F06 新手路径卡

def test_f06_beginner_path_is_generated_from_registry():
    src = read("analysis_navigation.js")
    assert "for(const [index,groupId] of ['data','effects','synthesis'].entries())" in src, \
        "新手路径三步必须按注册表组 id 生成（改注册表即同步），不得手写方法名"
    assert "registry.group(groupId)" in src
    assert "registry.href({page:'analysis',group:groupId})" in src
    # 卡里不得出现具体方法 id——方法级取舍需内容评审，不在这里替用户决定。
    block = src[src.index("F06（随版缓解"):src.index("catalog.append(beginnerPath)")]
    for tool_id in ("pooled-effect", "effect-entry", "risk-of-bias"):
        assert tool_id not in block, f"新手路径卡不应指定具体方法：{tool_id}"


def test_f06_card_hidden_for_group_filter_and_search():
    src = read("analysis_navigation.js")
    assert "beginnerPath.hidden=!!state.catalogGroup||!!term;" in src, \
        "分组筛选或搜索时须收起新手路径卡（否则与过滤后的目录自相矛盾）"


def test_f06_card_precedes_group_sections():
    src = read("analysis_navigation.js")
    assert src.index("catalog.append(beginnerPath);") < src.index("catalogSections.push(section);catalog.append(section);"), \
        "新手路径卡必须在分组目录之前（目录页顶部）"


# ------------------------------------------------------- F17 上下文条

def test_f17_context_bar_only_for_fragment_and_entry_mode_tools():
    src = read("analysis_navigation.js")
    start = src.index("function syncLandingContext(")
    body = src[start:src.index("function refreshLandingContext(")]
    assert "if(!tool||!card||!(tool.fragment||tool.entryMode))return;" in body
    assert "T('当前方法：{0}',T(tool.label))" in body
    assert "T('分析模块：{0}',T(group.label))" in body
    assert "T('返回分析目录')" in body
    assert "heading.after(landingContext)" in body, "上下文条必须常驻在落地卡标题下"


def test_f17_context_bar_resynced_on_every_route_and_catalog_entry():
    src = read("analysis_navigation.js")
    render = src[src.index("function renderTool(tool,options={})"):src.index("function openCatalog(")]
    assert "syncLandingContext(tool,card,heading);" in render
    catalog = src[src.index("function openCatalog("):src.index("function returnToTool(")]
    assert "syncLandingContext(null);" in catalog, "进入目录时必须撤掉上下文条"
    assert "refreshLandingContext();" in src, "语言切换后必须重译上下文条"


def test_f17_context_bar_survives_fragment_hiding():
    src = read("analysis_navigation.js")
    assert "child!==landingContext" in src, \
        "fragment 工具的隐藏循环不得把上下文条一起隐藏（否则定位条只在无 fragment 时可见）"


def test_f17_entry_mode_tools_resolve_their_real_landing_card():
    """entryMode 工具（零单元格等）的落地卡是 anJumpEffects，卡 id 不等于 view。

    破坏即红：上下文条按 `cards.find(node=>node.id===tool.view)` 找卡时，这类入口
    找不到卡 → 上下文条永不出现（对着 35 个入口里 3 个 entryMode 工具静默失效）。
    这里钉住「落地卡判定」这一处单一事实源，且 renderTool 与语言切换都必须走它。
    """
    src = read("analysis_navigation.js")
    assert "function landingCardFor(tool){" in src, \
        "落地卡判定必须抽成一处（renderTool 与 refreshLandingContext 共用）"
    body = src[src.index("function landingCardFor(tool){"):src.index("function syncLandingContext(")]
    assert "card===effects&&(tool.view==='entry'||tool.view==='synthesis')" in body, \
        "entry/synthesis 共用 anJumpEffects，落地卡判定必须覆盖"
    assert "tool.view==='anIntervalsPanel'&&intervalCards.has(card.id)" in body, \
        "单研究区间类共用一组卡，落地卡判定必须覆盖"
    render = src[src.index("function renderTool(tool,options={})"):src.index("function openCatalog(")]
    assert "const card=landingCardFor(tool);" in render, \
        "renderTool 必须用落地卡判定取卡（写死 tool.view 会让 entryMode 工具拿不到卡）"
    assert "cards.find(node=>node.id===tool.view)" not in render, \
        "renderTool 不得再按 view 写死找卡"
    refresh = src[src.index("function refreshLandingContext()"):src.index("function refreshLandingContext()") + 400]
    assert "landingCardFor(state.tool)" in refresh, \
        "语言切换重译上下文条也必须用同一套落地卡判定"


# ------------------------------------------------------- F18 坏深链提示

def test_f18_toast_only_for_unrecognized_non_empty_fragment():
    html = read("index.html")
    start = html.index("let unrecognizedLink = false;")
    body = html[start:start + 1200]
    assert "else if (fragment) unrecognizedLink = true;" in body, \
        "只有「有片段但无已知目标」才提示；空片段是正常默认入口"
    assert "catch (_) { unrecognizedLink = location.hash.length > 1;" in body, \
        "解码失败同样是坏链，但空 hash 不提示"
    assert "if (unrecognizedLink) toast('未识别的链接，已返回首页。', 'warn', 5000);" in body
    assert body.index("go(initialPage);") < body.index("if (unrecognizedLink) toast("), \
        "提示必须在页面落到首页之后（用户先看到结果再看到解释）"


def test_f18_message_registered_in_all_languages():
    rows = locale_rows()
    row = next(row for row in rows if row[0] == "未识别的链接，已返回首页。")
    assert len(row) == 10 and row[1].startswith("Unrecognized link")


# ------------------------------------------------------- F19 帮助挂链

def test_f19_help_entries_on_screening_coding_and_analysis():
    shell = read("workspace_shell.js")
    assert "screenHeading.after(helpEntry('screen'));" in shell
    assert "benchCap.after(helpEntry('bench'));" in shell
    nav = read("analysis_navigation.js")
    assert "'data-rf-help-entry':'analysis'" in nav
    assert "headingTools.append(helpEntry);" in nav
    for source in (shell, nav):
        assert "registry.href({page:'help'})" in source, "帮助入口必须是规范路由，不得写死 #help"
        assert "data-rf-help-entry-label" in source, "入口文字必须可重译"


def test_f19_help_entry_labels_retranslated():
    shell = read("workspace_shell.js")
    assert "for(const node of document.querySelectorAll('[data-rf-help-entry-label]'))node.textContent=T('查看帮助');" in shell


def test_f19_help_content_out_of_scope():
    """只做挂链入口：帮助页仍只有原来的主题，不新增「工作流程」正文（归结构包）。"""
    html = read("index.html")
    help_page = html[html.index('data-p="help"'):html.index('data-p="home"')]
    assert "工作流程" not in help_page, "帮助内容编写不在本泳道范围（结构包）"


# ------------------------------------------------------- F20 阶段菜单 title

def test_f20_stage_menu_title_shows_real_destination():
    src = read("workspace_shell.js")
    start = src.index("makeLevel({id:'stage'")
    body = src[start:src.index("const siblings=")]
    assert "const target=registry.page(stageLast[item.id]||item.defaultPage);" in body, \
        "title 必须反映 stageLast 记忆后的真实目的地"
    assert "title:T('将前往：{0}',T(target.label))" in body
    assert "if(entry.title)link.setAttribute('title',entry.title);" in src


def test_f20_render_key_includes_stage_last():
    """绘制缓存必须把 stageLast 纳入键。

    破坏即红：navigate() 里 go() 先派发 workspace-changed（触发一次 refresh），之后才写
    stageLast（workspace_shell.js:180-181）。只按 [route, lang] 记忆时，那次 refresh 会
    提前返回，菜单 title 停在旧目的地——第一次进入某阶段页时显示的永远是上一个页面，
    要点第二次才对。真浏览器实测：缓存键修前 title='将前往：导入文献'，修后='将前往：文献复筛'。
    """
    src = read("workspace_shell.js")
    assert "const key=JSON.stringify([route,RFLang.get(),stageLast]);" in src, \
        "refresh 的渲染键必须包含 stageLast（否则阶段记忆更新后菜单 title 不刷新）"
    assert "JSON.stringify([route,RFLang.get()])" not in src, \
        "旧的两元素渲染键不得残留（title 会滞后一次导航）"


def test_f20_menu_route_unchanged():
    """对照组：只加 title，菜单项的路由与结构不变。"""
    src = read("workspace_shell.js")
    assert "route:{page:target.id}" in src
    assert "label:item.label,route:{page:target.id},current:item.id===stage.id" in src


# ------------------------------------------------------- F10 抽屉自动收起

def test_f10_drawer_closes_on_workspace_change():
    src = read("sidebar_sheet.js")
    assert "document.addEventListener('reviewflow:workspace-changed',()=>{if(target)setOpen(false,{immediate:true});refresh();scheduleLayout();});" in src, \
        "路由切换必须收起覆盖式抽屉（一个调用点）"
    assert src.count("reviewflow:workspace-changed") == 1, \
        "同一个事件不得挂两个监听（收起与刷新合并成一处，避免顺序依赖）"
    assert "pointerdown" in src, "点击外部收起的行为保持不变（对照组）"


def test_f10_drawer_collapse_also_resets_mobile_inert():
    """收起必须是完整收起：syncAccess 一并恢复主区交互性。

    破坏即红：只把 target 置 false 而不走 setOpen → syncAccess 时，移动端的
    `main.inert` 会停在 true，收起后页面依然点不动（F10 的原症状换个形态复现）。
    """
    src = read("sidebar_sheet.js")
    assert "if(modal&&!lockedMain){previousMainInert=main.inert;main.inert=true;lockedMain=true;}" in src
    assert "else if(!modal&&lockedMain){main.inert=previousMainInert;lockedMain=false;}" in src, \
        "收起（或退出移动端模态）必须把主区 inert 恢复成进入前的值"


# ------------------------------------------------------- F11 注册去向

def test_f11_register_hint_is_register_only():
    """注册成功提示只在注册分支出现，且不冲掉原有欢迎语。

    破坏即红：把提示放到 submit 分支之外（登录也弹）→ 老用户每次登录都被说「你已
    注册为…」；删掉欢迎语 → 对照组（既有输出）被破坏。
    """
    html = read("index.html")
    start = html.index("$('#authForm').addEventListener('submit'")
    body = html[start:html.index("\n});", start)]
    assert "const mode = authMode;" in body
    assert "api('/api/auth/' + mode," in body
    assert "toast(T('欢迎，{0}', d.user.username), 'inc');" in body, "原有欢迎语必须保留（对照组）"
    register_hint = (
        "if (mode === 'register') "
        "toast(T('你已注册为独立用户：可新建任务，或等任务属主邀请加入。'), 'info', 9000);"
    )
    assert register_hint in body, "角色/去向提示仅用于本次提交的注册模式"
    assert body.index("await enterApp();") < body.index(register_hint), \
        "提示必须在进入应用之后（用户先看到界面再看到解释）"


# ------------------------------------------------------- 静态校验器

def test_node_static_validators_pass():
    for cmd in (["build/validate_import_review_static.cjs"], ["build/validate_team_prisma_static.js"]):
        out = subprocess.run([NODE, *cmd], cwd=REPO, capture_output=True, text=True,
                             encoding="utf-8", timeout=180)
        assert out.returncode == 0, f"{cmd[0]} 失败：\n{out.stdout}\n{out.stderr}"
