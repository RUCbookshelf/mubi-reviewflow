# -*- coding: utf-8 -*-
"""r10-u1 初筛/首页/撤销泳道静态钉子（F13/F14/F15/F24/F25/F26/F27/F62/F65）。

浏览器行为面在 ``build/tests/test_r10_u1_screen_browser.cjs``（Playwright 键盘与
DOM 实测，19 项）；本文件钉住「无法用一次点击复现」的结构与词典面：

- F24 决策行不再挂「存为常用理由」按钮：workflow_controls.js 的 decorateReasons
  必须只把自定义输入框放进 .wf-custom-reason-row，动作改为下拉特殊项
  （data-wf-save-reason），并带可撤销 toast。破坏：把按钮塞回 select.parentElement 即红。
- F25 alBadge/alHint 不在阅读帮助弹层里：screen_reader.js 必须把它们移入
  .rf-screen-ml-status，且 help 弹层只剩页面 hint 与 Esc 提示。
- F26 分数可见文案为「预测纳入概率 {0}%」并附「基于 {0} 条已标注」；
  「机器学习相关度 {0}%」在 index.html 中不再出现。
- F27 半步：dirty 模态正文含题名/所选理由/清空草稿，Save 对不可保存的理由草稿置灰。
- F13/F14 零任务空态与一步引导卡由 renderCurrentTask 统一渲染，且 loadTasks
  会派发 task-list-changed（空列表才会真的渲染空态）。
- F15 设置标签不再与侧栏页面逐字同名（编码页/筛选页）。
- F62/F65 撤销边界与 403 消息十语言双键已注册（中文槽 + 后端英文原串槽）。
- 十语言：workflow_locale.js 本轮新增行 10 槽非空、占位符逐槽一致。
- 缓存令牌：本轮改动过的 5 个资产必须带 r10u1 令牌（CacheTokenBindingTests 同款
  内容的局部对应物；全量历史回放仍由 test_r7_frontsec_fixes.py 看守）。
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


def _read(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


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


def _all_locale_rows() -> list[list[str]]:
    rows: list[list[str]] = []
    for path in sorted(FRONTEND.glob("*_locale.js")):
        rows.extend(_locale_rows(path.name))
    return rows


def _placeholders(text: str) -> list[str]:
    return sorted(re.findall(r"\{(\d+)\}", text))


# ---------------------------------------------------------------------------
# F24：动作移出决策行
# ---------------------------------------------------------------------------
class TestReasonSaveMovedOutOfDecisionRow:
    def test_action_is_a_dropdown_item_not_a_row_button(self):
        source = _read("workflow_controls.js")
        assert "data-wf-save-reason" in source, "F24：存为常用理由必须是下拉里的特殊项"
        assert "__save_reason__" in source, "F24：特殊项必须有自己的值，不能冒充理由取值"
        assert "已存为常用理由" in source, "F24：写入后必须有可感知反馈"
        # 破坏即红：把按钮塞回 select.parentElement（决策行）即命中此断言
        assert "select.parentElement.append(b)" not in source, \
            "F24：存为常用理由按钮不得再挂进决策行"
        assert "Save as reusable reason" not in source, \
            "F24：旧按钮文案应随按钮一并移除（动作改为下拉项）"

    def test_custom_reason_row_holds_input_only(self):
        """自定义输入框仍住在 .wf-custom-reason-row（移动端整行布局契约），
        但该行里不再有第二个子元素（按钮）。"""
        source = _read("workflow_controls.js")
        match = re.search(r"row\.append\(custom\)", source)
        assert match, "F24：自定义输入框应单独进 .wf-custom-reason-row"
        assert "row.append(custom,save)" not in source, "F24：行内不得再挂保存按钮"


# ---------------------------------------------------------------------------
# F25 / F26：ML 可见化与措辞
# ---------------------------------------------------------------------------
class TestMachineLearningPresentation:
    def test_status_lives_outside_the_help_popover(self):
        source = _read("screen_reader.js")
        assert "rf-screen-ml-status" in source, "F25：alBadge/alHint 必须有常驻状态行"
        assert "for(const id of ['alBadge','alHint']){const node=document.getElementById(id);if(node)help.append(node);}" not in source, \
            "F25：不得再把 alBadge/alHint 收进默认隐藏的阅读帮助弹层"

    def test_help_popover_keeps_only_page_hint_and_escape(self):
        source = _read("screen_reader.js")
        assert "if(hint)help.append(hint);" in source, "阅读帮助弹层应保留页面快捷键说明"
        assert "help.append(escapeHint);" in source

    def test_score_wording_is_predicted_probability_with_sample_size(self):
        html = _read("index.html")
        # 断言渲染调用点（text: T(...)），不误判词典源行：旧串作为历史词条留在
        # ROWS 字面量里无害，只要没有调用点再用它。
        assert "text: T('预测纳入概率 {0}%'" in html, "F26：分数文案应为「预测纳入概率」"
        assert "text: T('基于 {0} 条已标注'" in html, "F26：必须常显训练样本数"
        assert "text: T('机器学习相关度 {0}%'" not in html, "F26：旧措辞不得再被渲染"

    def test_score_title_states_p_include_not_relevance(self):
        html = _read("index.html")
        title = re.search(r"class: 'al-score', title: T\('([^']+)'\)", html)
        assert title, "F26：分数条 title 必须存在"
        assert "P(include)" in title.group(1), "F26：title 必须写明 P(include) 语义"
        assert "不是相关度评分" in title.group(1), "F26：title 必须否定「相关度」语义"

    def test_order_label_shows_current_order(self):
        source = _read("screen_reader.js")
        assert "rf-screen-order-now" in source, "F09：标签旁常显实际顺序"
        assert "当前：{0}" in source

    def test_reorder_indicator_is_transient(self):
        source = _read("screen_reader.js")
        assert "排序更新中…" in source, "F59：重排期间必须有轻量指示"
        html = _read("index.html")
        assert "RFScreenReason?.ranking(true)" in html, "F59：refreshALRank 必须挂上指示"
        assert source.count("rankingRow?.remove()") >= 2, "F59：落定路径必须移除指示"


# ---------------------------------------------------------------------------
# F27 半步
# ---------------------------------------------------------------------------
class TestUnsavedReasonDraftHalfStep:
    def test_dialog_shows_draft_title_and_reason(self):
        source = _read("workflow_controls.js")
        assert "Unsaved screening reason" in source, "F27：模态正文必须列出理由草稿"
        assert "所选理由：{0}" in source, "F27：草稿必须显示所选理由"
        assert "Clear draft" in source, "F27：必须有「清空草稿」按钮"
        assert "wfClearReasonDraft" in source, "F27：清空按钮需要可被测试指认的标记"

    def test_save_is_disabled_for_an_unsaveable_reason_draft(self):
        source = _read("workflow_controls.js")
        assert "reasonDraftOnly" in source, "F27：必须区分「只有理由草稿」的情形"
        assert "saveButton.disabled=true" in source, "F27：无物可存时 Save 必须置灰"
        # 2.1.5 W1 二选一（frontend_contract_2.1.5-w1-binary.md §1/F1：maybe 恒 409、
        # 初筛卡移除「2 暂不确定」）——决策只剩纳入/排除，置灰 title 随之换词。
        assert "saveButton.title=T('请先保存纳入或排除决定，或清空理由草稿。')" in source, \
            "F27：置灰必须以 title 说明原因（W1 后只剩纳入/排除两条路）"
        assert "纳入 / 待定 / 排除" not in source, \
            "F27：W1 已删「待定」决策，title 不得退回三选一措辞"
        rows = {row[0]: row for row in _locale_rows("workflow_locale.js")}
        title = rows.get("请先保存纳入或排除决定，或清空理由草稿。")
        assert title and len(title) == 10, "F27：新置灰 title 必须注册十语言"

    def test_clearing_the_draft_marks_the_decision_clean(self):
        source = _read("workflow_controls.js")
        block = re.search(r"W\.clearReasonDraft=stage=>\{(.*?)\n  \};", source, re.S)
        assert block, "F27：clearReasonDraft 必须存在"
        assert "markDecisionClean" in block.group(1), "F27：清空后必须回到干净基线"


# ---------------------------------------------------------------------------
# F13 / F14 / F15：首页
# ---------------------------------------------------------------------------
class TestHomeEmptyStateAndGuidance:
    def test_zero_task_state_is_rendered_by_one_function(self):
        source = _read("workflow_controls.js")
        assert "function renderCurrentTask()" in source, "F13：空态与载入态必须由一个函数裁决"
        assert "S.tasks.length===0" in source.replace(" ", ""), "F13：空态判定必须基于任务列表"
        assert "No task yet" in source, "F13：必须渲染空态文案"
        block = re.search(r"function renderCurrentTask\(\)\{(.*?)\n  \}", source, re.S)
        assert block, "F13：renderCurrentTask 结构被改动"
        assert "Loading scope…" in block.group(1), "F13：有任务但未定上下文时仍保留载入占位"

    def test_task_list_change_notifies_the_overview(self):
        html = _read("index.html")
        assert "reviewflow:task-list-changed" in html, "F13：任务列表到货必须通知概览重算"
        start = html.find("async function loadTasks()")
        assert start >= 0, "F13：loadTasks 缺失"
        end = html.find("async function pickTask(", start)
        assert end > start, "F13：loadTasks 之后应紧跟 pickTask"
        assert "reviewflow:task-list-changed" in html[start:end], \
            "F13：派发点必须在 loadTasks 内（空列表才有机会渲染空态）"

    def test_first_step_card_points_at_task_creation(self):
        source = _read("workflow_controls.js")
        assert "wf-first-step" in source, "F14：概览需要一步引导卡"
        assert "First step: create a task" in source
        card = re.search(r"const firstStep = n\('div', null, \{(.*?)\);", source, re.S)
        assert card, "F14：引导卡结构缺失"
        assert "activate('tasks')" in source, "F14：按钮必须直达「任务与团队」标签"

    def test_settings_tabs_no_longer_collide_with_sidebar_pages(self):
        source = _read("workflow_controls.js")
        labels = re.search(r"const labels = \[(.*?)\];", source, re.S)
        assert labels, "F15：标签数组缺失"
        text = labels.group(1)
        assert "'数据提取与编码'" not in text, "F15：设置标签不得与编码页逐字同名"
        assert "'Screening settings'" not in text, "F15：设置标签不得与筛选页近似同名"
        assert "'Coding evidence requirements'" in text and "'Screening policy settings'" in text

    def test_renamed_tabs_are_translated_in_all_ten_languages(self):
        rows = {row[0]: row for row in _locale_rows("workflow_locale.js")}
        for key in ("筛选策略设置", "编码证据要求"):
            assert key in rows, f"F15：{key} 必须进十语言词典"
            assert len(rows[key]) == 10, f"F15：{key} 必须恰为十语言"


# ---------------------------------------------------------------------------
# F62 / F65：撤销错误与 403 混排
# ---------------------------------------------------------------------------
class TestUndoAndPermissionCopy:
    def test_undo_error_concatenation_goes_through_t(self):
        source = _read("workspace_undo.js")
        assert "T('撤销失败：') + T(error.message)" in source, "F62：撤销失败拼接必须过 T()"
        assert "T('已恢复上次保存前的状态') + ' · ' + T(error.message)" in source, \
            "F62：已提交后失败的提示同样必须过 T()"

    def test_backend_boundary_messages_registered_with_dual_keys(self):
        rows = _all_locale_rows()
        keys = {slot for row in rows for slot in row}
        for zh, en in (
            ("编码证据仅限你自己的工作区。", "Coding evidence is limited to your own workspace."),
            ("未找到证据图片。", "Evidence image not found."),
            ("证据字段或文献不可用。", "Evidence field or article is unavailable."),
            ("维度或文献不存在（请确认后重试）。", "The dimension or article does not exist (check and retry)."),
        ):
            assert zh in keys, f"F62：中文键未注册：{zh}"
            assert en in keys, f"F62：后端英文原串未注册（双键）：{en}"
            row = next(row for row in rows if row[0] == zh)
            assert len(row) == 10, f"F62：{zh} 必须恰为十语言"
            assert row[1] == en, f"F62：{zh} 的英文槽必须逐字等于后端原串"

    def test_permission_messages_registered_and_no_mixed_language_splice(self):
        rows = _all_locale_rows()
        keys = {slot for row in rows for slot in row}
        for zh, en in (
            ("无权访问该任务。", "Task access denied."),
            ("任务为只读。", "Task is read-only."),
            ("无权提交任务编码。", "Task coding submission access denied."),
            ("仅经核实的任务属主可管理工作流设置。", "Only a verified task owner can manage workflow settings."),
            ("编码方案仅限你自己的工作区。", "Coding schemes are limited to your own workspace."),
        ):
            assert zh in keys and en in keys, f"F65：403 双键未注册：{zh} / {en}"
        source = _read("workflow_controls.js")
        assert "W.errorText = error => {" in source, "F65：errorText 必须改为可读实现"
        assert "if (error.status === 403 && detail === (error.message || '')) return base;" in source, \
            "F65：未译的 403 细节不得再拼接（中英混排的成因）"
        assert "+ error.message;" not in source.split("W.errorText = error => {")[1].split("};")[0], \
            "F65：errorText 内不得直接拼接原始 error.message"


# ---------------------------------------------------------------------------
# F25 可见快捷键提示 / F09 一次性提示条 / F22 沿用标记走词典
# （行为面在 test_r10_u1_undo_hint_browser.cjs；此处钉住结构不变量）
# ---------------------------------------------------------------------------
class TestUndoShortcutHintAndMlTip:
    def test_undo_button_carries_a_visible_shortcut_node(self):
        """F25：批准单要求「Ctrl+Z 在撤销按钮旁给可见快捷键提示」。

        破坏即红：把 paint() 改回 b.textContent=…（整段覆盖）会把 <kbd> 冲掉，
        按钮退回「只有 title 悬停可见」的原状。"""
        source = _read("workspace_undo.js")
        assert "el('kbd'" in source, "F25：撤销按钮内必须有 <kbd> 快捷键提示节点"
        assert "replaceChildren(label" in source, \
            "F25：sync() 必须重建子节点（replaceChildren），不能整体写 textContent"
        assert "b.textContent = T(busy" not in source, \
            "F25：按钮文案不得再用 textContent 整体覆盖（会冲掉 <kbd>）"
        assert "aria-keyshortcuts" in source, "F25：屏幕阅读器需要 aria-keyshortcuts"
        css = _read("screen_reader.css")
        assert ".rf-undo-kbd" in css, "F25：快捷键提示需要样式锚点"
        assert "max-width:760px" in css and "pointer:coarse" in css, \
            "F25：窄屏/粗指针必须隐藏提示（那里没有物理键盘）"

    def test_ml_tip_is_single_shot_and_dismissible(self):
        """F09：批准单「首次进初筛给一次性可关闭提示条」，风险项是常驻噪音。"""
        source = _read("screen_reader.js")
        assert "rf_screen_ml_tip" in source, "F09：限频标记必须落在本机存储"
        assert "localStorage.setItem(TIP_KEY" in source, "F09：关闭与「试试」都必须写限频标记"
        assert "tipSeen()" in source, "F09：渲染前必须检查限频标记（已看过不再弹）"
        assert "buildTip()" in source, "F09：提示条必须在初始化路径上构建"
        close = re.search(r"const close=\(dismiss\)=>\{(.*?)\};", source)
        assert close, "F09：关闭处理缺失"
        assert "aiButton.click()" in close.group(1), \
            "F09：「试试机器学习排序」必须真的切换排序，不能退化成纯关闭"

    def test_ml_settings_parameters_have_plain_language_titles(self):
        """F09：批准单「ML 设置参数每项补一句白话 title」。"""
        html = _read("index.html")
        for control in ("alStrategy", "alStopThreshold", "alSeed", "alReorderInterval"):
            tag = re.search(r'<[^>]*id="%s"[^>]*>' % control, html)
            assert tag, f"F09：找不到 {control} 控件"
            assert 'title="' in tag.group(0), f"F09：{control} 缺白话 title"

    def test_carry_mark_goes_through_the_dictionary(self):
        """F22：沿用标记直接拼在选项文案里，属于用户可见文本，必须十语言同步。

        破坏即红：把 carryMark() 改回常量 '（沿用上次）' 会让英文界面出现中文后缀
        （test_r10_u1_undo_hint_browser.cjs 的 F22 用例同款断言）。"""
        source = _read("screen_reader.js")
        assert "const carryMark=()=>T('（沿用上次）');" in source, \
            "F22：沿用标记必须经 T() 输出"
        assert "CARRY_MARK" not in source, "F22：不得再保留硬编码的中文标记常量"
        rows = {row[0]: row for row in _locale_rows("workflow_locale.js")}
        assert "（沿用上次）" in rows, "F22：沿用标记未进十语言词典"
        assert len(rows["（沿用上次）"]) == 10, "F22：沿用标记必须恰为十语言"


# ---------------------------------------------------------------------------
# 十语言 + 缓存令牌
# ---------------------------------------------------------------------------
class TestLocaleAndTokenGates:
    KEY_ROWS = (
        "已沿用上次理由：{0}", "最近使用", "筛选理由…", "请先选择排除理由",
        "＋ 将此理由存为常用", "已存为常用理由：{0}", "查看全部已决文献",
        "全部已决文献", "排序更新中…", "当前：{0}", "预测纳入概率 {0}%",
        "基于 {0} 条已标注", "已选择机器学习排序，当前标签不足，暂按导入顺序",
        "No task yet", "Create a task", "First step: create a task",
        "（沿用上次）", "试试机器学习排序", "关闭提示",
        "可以切换为机器学习排序：按模型预测的纳入概率排列待筛文献",
        "决定下一篇读哪条：相关度优先＝先看模型最可能纳入的；混合探索＝在已排序的基础上留出随机抽样；不确定性优先＝先看模型最没把握的",
        "连续排除这么多篇后提示你暂停复查，避免机械式连点漏掉该纳入的文献；填 0 关闭该提示",
        "混合探索模式下的随机起点。同一个种子得到同一套抽样顺序，便于复现；一般不需要改动",
        "每做多少条决策后重新计算一次排序。越小越贴合最新判断，但每次重排都要等一会儿",
    )

    def test_every_new_row_is_ten_non_empty_slots(self):
        rows = {row[0]: row for row in _locale_rows("workflow_locale.js")}
        for key in self.KEY_ROWS:
            assert key in rows, f"r10-u1 新词条未注册：{key}"
            row = rows[key]
            assert len(row) == 10, f"{key} 必须恰为十语言，实为 {len(row)}"
            assert all(slot.strip() for slot in row), f"{key} 存在空槽"
            reference = _placeholders(row[1])
            for slot in row:
                assert _placeholders(slot) == reference, f"{key} 占位符不一致：{slot!r}"

    def test_validator_runs_clean_for_this_lane(self):
        """静态校验器是共享门：本轮并行泳道可能正在改别的模块，其未注册串会让
        门整体变红。这里钉的是**本泳道**的契约——校验器报告的未注册串里不得有
        任何一个来自本泳道改过的文件；同时校验器本身必须能跑完（不是崩溃）。

        破坏即红：本泳道新增了未注册的 T()/n() 字面量时，该串会出现在缺失清单里，
        而它就在 screen_reader.js / workflow_controls.js / workspace_undo.js 中。"""
        lane_files = ("screen_reader.js", "workflow_controls.js", "workspace_undo.js")
        lane_source = "\n".join(_read(name) for name in lane_files)
        proc = subprocess.run([NODE, str(REPO / "build" / "validate_import_review_static.cjs")],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=180)
        # 门必须跑完（崩溃=红）；跑完但报别的泳道的串属共享门状态，见下。
        assert "Unregistered display strings in" in proc.stdout + proc.stderr or proc.returncode == 0, \
            f"静态校验器未正常退出：{proc.stdout[-800:]}{proc.stderr[-800:]}"
        missing = re.findall(r"'((?:[^'\\]|\\.)*)'", proc.stdout + proc.stderr)
        mine = [value for value in missing if value and value in lane_source]
        assert mine == [], f"本泳道存在未注册的用户可见串：{mine}"

    def test_changed_assets_carry_a_bumped_token(self):
        """令牌必须随内容上调。并行泳道可能把同一个令牌继续推进（如 r10u5a），
        因此断言「与基线不同」而不是钉死本批的字符串——忘记 bump 才是要抓的破坏。"""
        baseline = {
            "screen_reader.css": "20261012-rounds1",
            "workflow_locale.js": "20261101-r9l4b1",
            "screen_reader.js": "20261012-order-label1",
            "workflow_controls.js": "20261030-r8b-fl6-reason2",
            "workspace_undo.js": "20261013-keyguard1",
        }
        html = _read("index.html")
        for asset, old in baseline.items():
            tokens = set(re.findall(re.escape(asset) + r"\?v=([^\"]+)", html))
            assert len(tokens) == 1, f"{asset} 令牌引用异常：{tokens}"
            assert tokens != {old}, f"{asset} 令牌未随内容上调（仍是基线 {old}）"
