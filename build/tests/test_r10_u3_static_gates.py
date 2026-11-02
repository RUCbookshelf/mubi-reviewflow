# -*- coding: utf-8 -*-
"""r10-u3 移动端泳道静态钉子（UX 批准单 §三 F37/F38/F39/F40/F41/F42/F43/F44）。

行为面（真实浏览器、窄视口、44px 命中区）在
``build/tests/test_r10_u3_mobile_browser.cjs``，由
``build/tests/test_r10_u3_mobile_gate.py`` 起停隔离实例后驱动。本文件钉住
「无法用一次点击复现」的结构、样式、词典与令牌面：

- F37 自定义理由输入框整行占满（``#excCustomReason{grid-column:1/-1}``），且
  该规则**不再**挂在 ``can_manage`` 才生成的 ``.wf-custom-reason-row`` 上——
  破坏：把规则改回只对 ``.wf-custom-reason-row>.input`` 生效即红（这正是原缺陷：
  无理由库管理权的筛选员只拿到 1/3 列宽）。
- F37 后半（复筛页移动端常驻桌面键盘提示）：该提示在基线上已由
  ``screen_reader.css`` 一条全宽度规则隐藏；本轮只钉「隐藏仍然成立」。
- F38 理由行默认折叠：折叠类由 mobile_concept.js 施加、由 CSS 隐藏三个控件，
  且触屏两段式（先 3 展开、再 3 提交）与**实体键盘 3** 都不得落进「提示指向
  隐藏控件」的死路。
- F39 轨迹入口命中区 ≥44×44（WCAG 2.5.8 下限之上），顶栏列宽同步放宽。
- F40 横向容器滚动条恢复可见：``scrollbar-width:none`` 与
  ``::-webkit-scrollbar{display:none}`` 在本泳道样式不得再出现。
- F41 页顶阶段切换 tab 存在且只用既有 go()，导航主干/首页快捷区不动。
- F42 复筛与编码保存条粘底（position:sticky + safe-area），初筛粘底不变。
- F43 「停止」短标签与图标并排、按钮高度仍是 48px。
- F44 DOI/类型不再被整条隐藏信息丢掉：DOI 提到「文献信息」折叠区标题上。
- 十语言：本泳道新增/引用的每个用户可见串必须十槽非空、占位符逐槽一致。
- 缓存令牌：本泳道改过的资产必须在 index.html 带非基线 ?v=。

对照组纪律：桌面（≥761px）行为与既有资产逐字不变——本文件对
``mobile_concept.css`` 的每条移动规则都断言其在 ``@media(max-width:760px)``
之内，对 mobile_concept.js 断言其不触任何写接口。
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

LANGS = ("zh-CN", "en", "fr", "ru", "es", "ja", "pt", "de", "sr", "ko")
LANE_ASSETS = ("mobile_concept.css", "mobile_concept.js", "mobile_concept_locale.js")


def _read(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


def _mobile_css() -> str:
    """mobile_concept.css 去掉 ``@media(max-width:760px)`` 之前的文件头注释之外
    的全部规则都受该媒体查询约束；这里返回整段（含注释）供正则断言。"""
    return _read("mobile_concept.css")


def _css_media_span(source: str) -> tuple[int, int]:
    """返回 ``@media(max-width:760px){`` 大括号内的 [start, end) 区间。"""
    marker = "@media(max-width:760px){"
    start = source.index(marker) + len(marker)
    depth = 1
    index = start
    while index < len(source) and depth:
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    assert depth == 0, "mobile_concept.css 的 760px 媒体查询未闭合"
    return start, index - 1


def _mobile_rules() -> str:
    start, end = _css_media_span(_mobile_css())
    return _mobile_css()[start:end]


def _strip_comments(css: str) -> str:
    """去掉 /* … */ —— 注释里出现的类名/选择器字样不是规则，误判会让钉子在
    「注释解释了原缺陷」时假红（本轮实测踩过）。"""
    return re.sub(r"/\*.*?\*/", " ", css, flags=re.S)


def _selector_before(css: str, index: int) -> str:
    """取 index 处 ``{`` 之前的整条选择器（从上一个 ``}`` 或 ``;`` 起算）。"""
    head = max(css.rfind("}", 0, index), css.rfind(";", 0, index))
    return css[head + 1:index]


def _mobile_js() -> str:
    return _read("mobile_concept.js")


def _locale_rows(name: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in _read(name).splitlines():
        text = line.strip()
        if not text.startswith("["):
            continue
        try:
            row = json.loads(text.rstrip(","))
        except ValueError:
            continue
        if isinstance(row, list) and all(isinstance(slot, str) for slot in row):
            rows.append(row)
    return rows


def _mobile_labels() -> dict[str, dict[str, str]]:
    """用 node 执行 mobile_concept_locale.js，取回 RFMobileLabels 原文。"""
    script = (
        "const fs=require('fs'),vm=require('vm');"
        "const ctx={window:{}};"
        "vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),ctx);"
        "process.stdout.write(JSON.stringify(ctx.window.RFMobileLabels));"
    )
    proc = subprocess.run(
        [NODE, "-e", script, str(FRONTEND / "mobile_concept_locale.js")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-1200:]
    return json.loads(proc.stdout)


def _placeholders(text: str) -> list[str]:
    return sorted(re.findall(r"\{(\d+)\}", text))


def _dictionary_keys() -> set[str]:
    """用产品自己的 RFLang.register 收集全部词典键（所有 *_locale.js 的每个槽位
    都是可命中键——RFLang.register 对每行逐槽 dictionary.set）。"""
    script = r"""
const fs=require('fs'),path=require('path'),vm=require('vm');
const dir=process.argv[1],keys=new Set();
const rows=[];
const ctx={RFLang:{register:r=>rows.push(...r)},document:{querySelectorAll:()=>[],addEventListener:()=>{}}};
ctx.window=ctx;
for(const f of fs.readdirSync(dir).filter(f=>f.endsWith('_locale.js')))
  vm.runInNewContext(fs.readFileSync(path.join(dir,f),'utf8'),ctx,{timeout:20000});
for(const row of rows)for(const slot of row)if(slot)keys.add(slot);
process.stdout.write(JSON.stringify([...keys]));
"""
    proc = subprocess.run(
        [NODE, "-e", script, str(FRONTEND)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    keys = set(json.loads(proc.stdout))
    # index.html 的内联注册段与 mobile_concept_locale.js（RFMobileLabels）同样计入。
    html = _read("index.html")
    for match in re.finditer(r'\["((?:[^"\\]|\\.){1,160})"\s*,', html):
        keys.add(match.group(1))
    keys.update(_mobile_labels().keys())
    return keys


# ===========================================================================
# F37：自定义理由输入框整行 + 复筛键盘提示
# ===========================================================================
class TestCustomReasonFullWidth:
    def test_input_takes_the_whole_decision_row(self):
        css = _mobile_rules()
        rule = re.search(r"#appMain #excCustomReason\{([^}]*)\}", css)
        assert rule, "F37：#excCustomReason 必须有整行规则"
        body = rule.group(1)
        assert "grid-column:1/-1" in body, "F37：自定义理由输入框必须跨满三列网格"
        assert "width:100%!important" in body, "F37：宽度必须顶过内联 max-width:210px"

    def test_rule_does_not_depend_on_manage_rights(self):
        """原缺陷：全宽只挂在 can_manage 才生成的 .wf-custom-reason-row 上。
        这里选择器必须是 #excCustomReason 本身，不得以 .wf-custom-reason-row 为前提。
        破坏即红：把选择器改成 ``#appMain .wf-custom-reason-row>#excCustomReason``。"""
        css = _strip_comments(_mobile_rules())
        seen = 0
        for match in re.finditer(r"#excCustomReason[^{}]*\{", css):
            selector = _selector_before(css, match.start() + len("#excCustomReason"))
            seen += 1
            assert ".wf-custom-reason-row" not in selector, (
                "F37：自定义理由输入框的宽度不得以 .wf-custom-reason-row 为前提：" + selector.strip()
            )
        assert seen, "F37：#excCustomReason 的移动端规则不得缺失"

    def test_fulltext_keyboard_hint_is_not_shown_on_mobile(self):
        """F37 后半：复筛页决策条的桌面键盘提示在 ≤760px 必须不可见。

        如实记录取证结果：这条要求在基线上**已经满足**——``screen_reader.css``
        有一条落在所有 @media 之外（即全宽度生效）的既有规则
        ``.ft-decide>.row>.hint{display:none}``（该规则早于本轮，见
        ``git log -S`` 指向 bca4685）。本轮没有为它新增规则，因此这里钉的是
        「该隐藏仍然成立」而不是「本轮新增了移动规则」——写成后者会是虚报。
        破坏即红：删掉那条既有规则即命中（浏览器钉子的 F37 第二项同样会红）。"""
        reader = _read("screen_reader.css")
        scoped = re.search(r"\.ft-decide[^{}]*\.hint[^{}]*\{[^}]*display:none", reader)
        assert scoped, (
            "F37：复筛页决策条的键盘提示必须在移动端不可见（既有的全宽度隐藏规则被删即红）"
        )
        # 桌面对照组：本泳道的整行规则不得泄漏到 760px 之外
        whole = _mobile_css()
        start, end = _css_media_span(whole)
        outside = whole[: whole.index("@media(max-width:760px){")] + whole[end:]
        assert "excCustomReason" not in outside, "F37：整行规则不得泄漏到桌面宽度"


# ===========================================================================
# F38：排除理由行默认折叠 + 两条输入路径都不落死路
# ===========================================================================
class TestReasonRowCollapse:
    def test_js_collapses_by_default_and_css_hides_the_three_controls(self):
        js = _mobile_js()
        assert "rf-reason-collapsed" in js, "F38：折叠类必须由 mobile_concept.js 施加"
        assert "if(!select.value)bar.classList.add('rf-reason-collapsed')" in js, (
            "F38：无理由时默认折叠（已有理由/预填时不折叠）"
        )
        css = _mobile_rules()
        for selector in ("#excReason", "#excCustomReason", ".wf-custom-reason-row"):
            assert re.search(
                r"\.scr-decisions\.rf-reason-collapsed[^{}]*" + re.escape(selector) + r"[^{}]*\{[^}]*display:none",
                css,
            ), f"F38：折叠态必须隐藏 {selector}"

    def test_touch_two_step_reveals_without_submitting(self):
        js = _mobile_js()
        assert "function revealReason(" in js, "F38：必须有展开函数"
        block = re.search(r"document\.addEventListener\('click',e=>\{(.*?)\n  \},true\);", js, re.S)
        assert block, "F38：触屏两段式的点击处理必须存在"
        body = block.group(1)
        assert "'#btnExc'" in body, "F38：只拦截「3 排除」"
        assert "preventDefault" in body and "stopPropagation" in body, (
            "F38：折叠态第一次点击不得提交决策"
        )
        assert "revealReason()" in body, "F38：第一次点击必须先展开理由行"

    def test_keyboard_three_never_points_at_a_hidden_control(self):
        """本轮实测发现的死路：折叠态下按实体键盘 3，F08 会提示「请先选择排除
        理由」并 focus 理由控件——而该控件当时是 display:none。修复口径是在捕获
        阶段先展开、**不拦截**事件，让 screenDecide 与 F08 照常执行。
        破坏即红：删掉这段 keydown，浏览器钉子会抓到「提示 + 控件不可见」。"""
        js = _mobile_js()
        block = re.search(
            r"/\* 同一两段式也覆盖实体键盘.*?\n  document\.addEventListener\('keydown',e=>\{(.*?)\n  \},true\);",
            js, re.S,
        )
        assert block, "F38：实体键盘的折叠态展开必须存在"
        body = block.group(1)
        assert "revealReason()" in body, "F38：实体键盘按 3 必须先展开理由行"
        assert "preventDefault" not in body and "stopPropagation" not in body, (
            "F38：实体键盘路径不得吞掉事件（否则 F08 的文字说明与原有键盘流一起丢失）"
        )
        assert "'3'" in body, "F38：只处理 3 键"


# ===========================================================================
# F39：轨迹入口命中区 ≥44×44
# ===========================================================================
class TestTraceShortcutHitArea:
    def test_hit_area_is_at_least_44px(self):
        css = _mobile_rules()
        rule = re.search(r"#appMain \.top \.trace-shortcut-status\{([^}]*)\}", css)
        assert rule, "F39：轨迹入口必须有移动端规则"
        body = rule.group(1)
        for prop, value in (("width", "44px"), ("min-width", "44px"),
                            ("height", "44px"), ("min-height", "44px")):
            assert f"{prop}:{value}" in body, f"F39：命中区 {prop} 必须为 {value}"

    def test_appbar_column_widened_to_match(self):
        css = _mobile_rules()
        rule = re.search(r"body\.rf-navigation-sheet #appMain>\.top\{([^}]*)\}", css)
        assert rule, "F39：顶栏栅格必须存在"
        assert "44px minmax(0,1fr) 44px auto" in rule.group(1), (
            "F39：第三列（轨迹入口）列宽必须由 20px 放宽到 44px，否则命中区被裁"
        )

    def test_dot_itself_is_preserved(self):
        """命中区放大不得改动状态圆点本身（它的 ::before 由 research_trace.css 定义）。"""
        css = _mobile_rules()
        assert ".trace-shortcut-status:before{margin:0}" in css, (
            "F39：圆点必须保留（只放宽按钮盒），不得重绘状态指示"
        )


# ===========================================================================
# F40：横向容器恢复可见滚动条（缓解半步，渐隐遮罩属结构包不做）
# ===========================================================================
class TestHorizontalScrollAffordance:
    def test_no_scrollbar_is_hidden_any_more(self):
        css = _mobile_rules()
        assert "scrollbar-width:none" not in css, (
            "F40：横向容器不得再隐藏滚动条（原缺陷：360-390px 屏外内容无任何线索）"
        )
        assert "display:none" not in "".join(
            match.group(0) for match in re.finditer(r"[^{}]*::-webkit-scrollbar[^{}]*\{[^}]*\}", css)
        ), "F40：::-webkit-scrollbar 不得再被 display:none 隐藏"

    def test_wide_containers_ask_for_a_thin_scrollbar(self):
        css = _strip_comments(_mobile_rules())
        for selector, label in (
            (r"\.wf-tabs", "页签条"),
            (r"\.rf-prisma-preview #prView", "PRISMA 预览"),
            (r"\.an-jump-categories", "分析目录"),
            (r"\.trace-timeline", "轨迹时间线"),
            (r"\.trace-panel", "轨迹面板"),
        ):
            # 选择器可能是逗号列表的一部分（如 ``.wf-tabs,.trace-tabs``），
            # 因此取「含该选择器的那条规则」，而不是要求它以该选择器开头。
            found = False
            for match in re.finditer(r"\{([^}]*)\}", css):
                selector_text = _selector_before(css, match.start())
                if not re.search(selector + r"(?![\w-])", selector_text):
                    continue
                body = match.group(1)
                if "scrollbar-width:thin" in body:
                    found = True
                    break
            assert found, f"F40：{label} 必须恢复细滚动条（scrollbar-width:thin）"

    def test_fade_mask_component_is_not_attempted(self):
        """批准单明示：渐隐遮罩组件归移动端结构包，本单不做。
        这里钉住「没有越界做完整版」——CSS 不得出现遮罩伪元素。"""
        css = _read("mobile_concept.css")
        for marker in ("mask-image", "linear-gradient(90deg,transparent"):
            assert marker not in css, f"F40：渐隐遮罩属结构包，本单不做（发现 {marker}）"


# ===========================================================================
# F41：页顶工作流阶段切换 tab（缓解半步）
# ===========================================================================
class TestStageTabs:
    def test_tabs_cover_the_three_stages_and_reuse_go(self):
        js = _mobile_js()
        assert re.search(r"stageOrder=\[\[(.|\n)*?'screen'(.|\n)*?'fulltext'(.|\n)*?'bench'", js), (
            "F41：阶段 tab 必须覆盖初筛/复筛/编码三页"
        )
        block = re.search(r"function stageTabs\(host\)\{(.*?)\n  \}", js, re.S)
        assert block, "F41：stageTabs 必须存在"
        assert "()=>go(id)" in block.group(1), "F41：必须复用既有 go()，不得改导航主干"
        assert "aria-current" in block.group(1), "F41：当前阶段必须有无障碍标记"

    def test_tabs_are_wired_into_compose_for_all_three_pages(self):
        """F41：stageTabs 定义了不等于用上了——compose() 必须对三个阶段页调用它，
        否则页面顶根本没有切换条。破坏即红：删掉 compose 里的循环即命中。"""
        js = _mobile_js()
        compose = re.search(r"function compose\(\)\{(.*?)\n  \}", js, re.S)
        assert compose, "compose() 必须存在"
        body = compose.group(1)
        assert "for(const [id] of stageOrder)stageTabs(page(id));" in body, (
            "F41：compose() 必须为初筛/复筛/编码三页各建一条阶段切换条"
        )

    def test_tabs_are_removed_when_leaving_mobile_width(self):
        """对照组：退出窄视口时新增节点必须被 restore() 清掉（不得泄漏到桌面）。"""
        js = _mobile_js()
        restore = re.search(r"function restore\(\)\{(.*?)\n  \}", js, re.S)
        assert restore, "restore() 必须存在"
        assert "for(const n of added){labels.delete(n);n.remove();}" in restore.group(1), (
            "F41：阶段切换条必须随移动呈现层一起移除（add() 登记 → restore() 删除）"
        )

    def test_home_shortcut_convergence_is_not_attempted(self):
        """批准单明示：首页快捷区收敛 = 可放弃包。这里钉住没有越界。
        破坏即红：把首页 11 个快捷入口改成「最近使用 2-3 项」即命中此断言。"""
        js = _mobile_js()
        block = re.search(r"function home\(\)\{(.*?)\n  \}", js, re.S)
        assert block, "首页 compose 必须存在"
        assert "for(const id of ['import','screen','fulltext','merge','export','prisma','scheme','bench','analysis','trace','help'])" in block.group(1), (
            "F41：首页快捷区必须保持原 11 项（完整导航收敛属可放弃包）"
        )

    def test_full_navigation_rewrite_is_not_attempted(self):
        """完整导航收敛（抽屉改常驻等）属可放弃包：抽屉仍是 82dvh 上滑面板。"""
        css = _mobile_rules()
        assert "#rfNavigationSheet{top:auto!important;left:0;bottom:0;width:100%!important;height:82dvh!important" in css, (
            "F41：导航主干不得改动（完整收敛属可放弃包）"
        )


# ===========================================================================
# F42：三阶段决策条行为一致（粘底）
# ===========================================================================
class TestStickyDecisionBars:
    def test_all_three_stages_are_sticky_with_safe_area(self):
        css = _mobile_rules()
        for selector, label in (
            (r"#appMain \.scr-decisions", "初筛决策条"),
            (r"#appMain \.ft-decide", "复筛决策条"),
            (r"#appMain \.rf-mobile-coding-footer", "编码保存条"),
        ):
            match = re.search(selector + r"\{([^}]*)\}", css)
            assert match, f"F42：{label} 的移动端规则缺失"
            body = match.group(1)
            assert "position:sticky" in body, f"F42：{label} 必须粘底常驻"
            assert "bottom:0" in body or "bottom:-28px" in body, f"F42：{label} 必须贴底"

    def test_safe_area_is_respected(self):
        css = _mobile_rules()
        assert "env(safe-area-inset-bottom" in css, "F42：粘底条必须处理安全区"
        assert css.count("env(safe-area-inset-bottom") >= 2, (
            "F42：复筛与编码两条粘底条都要处理安全区"
        )

    def test_orphan_mobile_workspace_file_stays_deleted(self):
        """批准单 F42 顺带评估：mobile_workspace.css 是未被引用的孤儿文件。
        它已在 r8 删除并有独立钉子，这里只确认没有被本泳道重新引入。"""
        assert not (FRONTEND / "mobile_workspace.css").exists()
        assert not (FRONTEND / "mobile_workspace.js").exists()


# ===========================================================================
# F43：「停止」短标签
# ===========================================================================
class TestStopLabel:
    def test_label_is_rendered_beside_the_icon(self):
        js = _mobile_js()
        assert "rf-mobile-stop-label" in js, "F43：停止按钮必须有短标签节点"
        assert "add(node('span','rf-mobile-stop-label','Stop'),stop)" in js, (
            "F43：短标签必须以可翻译键加进按钮（不硬编码中文）"
        )
        css = _mobile_rules()
        rule = re.search(r"\.rf-stop-summary>\.rf-mobile-stop-label\{([^}]*)\}", css)
        assert rule, "F43：短标签需要可见规则"
        assert "display:inline" in rule.group(1)

    def test_container_height_unchanged_at_48px(self):
        css = _mobile_rules()
        rule = re.search(r"#appMain \.rf-mobile-source \.rf-stop-summary\{([^}]*)\}", css)
        assert rule, "F43：停止按钮的移动端规则必须存在"
        body = rule.group(1)
        assert "min-height:48px" in body, "F43：加标签后按钮高度仍必须是 48px（命中区不缩）"
        assert "min-width:48px" in body, "F43：加标签后按钮最小宽度仍 48px"

    def test_icon_redraw_is_not_attempted(self):
        """批准单明示：换图标属可放弃包。钉住仍有原图标路径。"""
        assert "rf-mobile-stop-icon" in _mobile_js(), "F43：图标保留（换图标属可放弃包）"


# ===========================================================================
# F44：DOI/类型不再彻底消失
# ===========================================================================
class TestArticleMetadataVisibility:
    def test_doi_is_hoisted_into_the_details_summary(self):
        js = _mobile_js()
        block = re.search(r"function articleDetails\(host\)\{(.*?)\n  \}", js, re.S)
        assert block, "F44：articleDetails 必须存在"
        body = block.group(1)
        assert "'.scr-doi'" in body, "F44：必须读取 DOI chip"
        assert "'Article details'" in body, "F44：DOI 必须写进「文献信息」折叠区标题"
        # 破坏即红：把 DOI 拼接删掉（只剩 tr('Article details')）即命中此断言——
        # 光断言 textContent=text 是不够的，那行在「不拼 DOI」时也成立。
        assert re.search(r"doi\s*\?\s*'\.\s*'\s*\+\s*doi\.textContent", body), (
            "F44：折叠区标题必须真的拼上 DOI（否则 DOI 在移动端仍然不可见）"
        )

    def test_article_details_is_called_on_every_screen_render(self):
        """F44：光有函数不够——必须在 screening() 里被调用，否则标题永远不更新。"""
        js = _mobile_js()
        block = re.search(r"function screening\(\)\{(.*?)\n  \}", js, re.S)
        assert block, "screening() 必须存在"
        assert "articleDetails(host);" in block.group(1), (
            "F44：screening() 每次渲染都必须更新折叠区标题"
        )

    def test_details_panel_still_holds_every_field(self):
        """折叠区内原有字段（作者/期刊/年份/类型/DOI）由 screen_reader.js 渲染。"""
        source = _read("screen_reader.js")
        for key in ("'作者'", "'期刊'", "'年份'", "'文献类型'", "'DOI'"):
            assert key in source, f"F44：文献信息折叠区必须保留 {key}"

    def test_summary_is_a_tap_target(self):
        css = _read("mobile_concept.css")
        assert "#appMain summary{min-height:48px" in css, (
            "F44：折叠区标题必须是可触达的命中区（≥44px）"
        )


# ===========================================================================
# 十语言与令牌
# ===========================================================================
class TestLocaleAndTokens:
    NEW_KEYS = ("Stop", "Workflow stages", "Article details")

    def test_new_label_rows_are_ten_non_empty_slots(self):
        labels = _mobile_labels()
        for key in self.NEW_KEYS:
            assert key in labels, f"r10-u3 新词条未注册：{key}"
            row = labels[key]
            missing = [lang for lang in LANGS if not str(row.get(lang, "")).strip()]
            assert not missing, f"{key} 缺槽：{missing}"
            assert len(row) == 10, f"{key} 必须恰为十语言，实为 {len(row)}"

    def test_placeholders_are_consistent_per_slot(self):
        labels = _mobile_labels()
        for key, row in labels.items():
            reference = _placeholders(str(row.get("en", "")))
            for lang, value in row.items():
                assert _placeholders(str(value)) == reference, (
                    f"{key} 的 {lang} 槽占位符与英文不一致：{value!r}"
                )

    def test_every_referenced_label_is_translated(self):
        """本泳道 JS 里所有走 tr()/node()/button() 的键必须都能命中词典。

        词典用**产品自己的注册机制**取（node 执行全部 *_locale.js + index.html 内联
        注册），而不是手写解析：locale 文件里有单引号行、内联字典有单行多行，
        正则解析会把真注册的键判成缺失（本轮实测踩过 Import history/轨迹/关闭）。"""
        js = _mobile_js()
        keys = set(re.findall(r"tr\('([^']+)'\)", js))
        keys |= {m for m in re.findall(r"node\('[^']*','[^']*','([^']+)'\)", js)}
        keys |= {m for m in re.findall(r"button\('([^']+)'", js)}
        assert keys, "本泳道应当引用若干可翻译键"
        dictionary = _dictionary_keys()
        unresolved = sorted(k for k in keys if k not in dictionary)
        assert unresolved == [], f"本泳道存在未注册的用户可见串：{unresolved}"

    def test_changed_assets_carry_a_bumped_token(self):
        """令牌必须随内容上调。并行泳道可能继续推进同一令牌，因此断言
        「与基线不同」而不是钉死字符串——忘记 bump 才是要抓的破坏。"""
        baseline = {
            "mobile_concept.css": "20261013-disclosure5",
            "mobile_concept.js": "2.1.2.2",
            "mobile_concept_locale.js": "20261013-motion13",
        }
        html = _read("index.html")
        for asset, old in baseline.items():
            tokens = set(re.findall(re.escape(asset) + r"\?v=([^\"]+)", html))
            assert len(tokens) == 1, f"{asset} 令牌引用异常：{tokens}"
            assert tokens != {old}, f"{asset} 令牌未随内容上调（仍是基线 {old}）"

    def test_mobile_rules_never_escape_the_760px_media_query(self):
        """对照组纪律：所有移动定制不得影响桌面（≥761px）。"""
        css = _read("mobile_concept.css")
        start, end = _css_media_span(css)
        outside = css[: css.index("@media(max-width:760px){")] + css[end:]
        for marker in ("#excCustomReason", "rf-reason-collapsed", "rf-mobile-stage-tabs",
                       "rf-mobile-stop-label", "trace-shortcut-status"):
            assert marker not in outside, f"r10-u3 的移动规则泄漏到桌面宽度：{marker}"


class TestPresentationOnly:
    def test_lane_js_never_writes_to_the_server(self):
        """铁律①：展示层改动只落展示层。本文件不得出现任何写请求。"""
        js = _mobile_js()
        for marker in ("method:'POST'", 'method: "POST"', "method:'PUT'",
                       "method:'DELETE'", "method:'PATCH'", "/decisions", "fetch("):
            assert marker not in js, f"mobile_concept.js 不得发起写请求（发现 {marker}）"
