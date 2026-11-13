# -*- coding: utf-8 -*-
"""r10-U2 导入泳道钉子（批准单 §二 8 条 ＋ §五 F35/F46 ＋ §六 F54/F55/F56/F57）。

按「每条至少一个破坏即红」组织，静态钉查源码字符串与词条结构，行为钉跑
node/真浏览器（浏览器钉子见 test_r10_u2_behavior_browser.cjs，本文件以
subprocess 门控并在未配置 playwright 时如实 skip）。

钉 ↔ 破坏对照：
- test_batch_option_carries_record_count_and_time：compactBatch 退回只拼文件数 → 红；
- test_save_refreshes_authoritative_ready_count：applyResult 不再同步 batchOptions/
  status 行无撤销说明 → 红；
- test_tab_counts_show_units_and_hide_zero_ge：计数单位丢失或「≥ 0」回归 → 红；
- test_scope_badges_for_later_tab：依赖阻塞/本地暂存徽标缺失 → 红；
- test_save_flash_near_actions：保存反馈回到只靠顶部状态行 → 红；
- test_undo_disabled_states_explain_reason：撤销禁用无 title/启用后无通告 → 红；
- test_bulk_dialog_reports_truncation_and_undo：截断提示/整批可撤销文案丢失 → 红；
- test_bulk_eligibility_reads_backend_field_only（G5 C8）：批量资格恢复前端
  判据镜像或总项数本地重算 → 红；
- test_bulk_ineligible_reason_renders_from_message_key（G5 C8）：不可批量原因
  不走词典/无字段守卫 → 红；
- test_bulk_keeper_defaults_to_suggestion_and_allows_override（G5 C8/F1/F3）：
  保留者不预选 suggested_keeper/不可改选/可空选提交 → 红；
- test_bulk_conflict_409_renders_translated_reason（G5 X11/C8）：409 不按
  message_key 词典渲染 → 红；
- test_activation_action_named_add_to_task（F46＋#2）：四 locale 出现旧动作名 → 红；
- test_retain_wording_avoids_duplicate_verbs（F35）：同卡再出现两个「保留」动词 → 红；
- test_upload_copy_and_precheck（F54/F55/F61）：上传期文案/停滞检测/100MB 预检缺失 → 红；
- test_merge_busy_feedback（F57）：合并上传期无禁用/计时 → 红；
- test_progress_callbacks_in_long_transaction（F56）：三次 find_duplicates 未传回调 → 红；
- test_upload_rejection_copy（F67）：扩展名仍渲染 list repr / UTF-8 无转码指引 → 红；
- test_new_keys_are_ten_language_complete：新增/改写键任一槽缺失或英文回退 → 红。
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
FRONTEND = REPO / "custom_frontend"
INDEX = FRONTEND / "index.html"
NODE_CANDIDATES = (r"C:\Users\16073\AppData\Local\hermes\node\node.exe",)
LANGS = 10


def _node_exe():
    for exe in (*NODE_CANDIDATES,):
        if pathlib.Path(exe).is_file():
            return exe
    import shutil
    return shutil.which("node")


def _read(path):
    return pathlib.Path(path).read_text(encoding="utf-8")


_ROW_CACHE = None


def _locale_rows():
    """求值三个 import locale 文件的全部 RFLang.register 字面量行（模块级缓存）。

    返回 [{"file": name, "row": [...]}]；node 不可用时 skip（不冒充通过）。
    """
    global _ROW_CACHE
    if _ROW_CACHE is not None:
        return _ROW_CACHE
    exe = _node_exe()
    if exe is None:
        raise unittest.SkipTest("node 不可用")
    script = r"""
const fs=require('fs'),path=require('path');
const fe=path.join(process.argv[1],'custom_frontend');
const files=['import_export_locale.js','import_review_refined_locale.js','import_review_locale.js'];
const rows=[];
for(const f of files){
  const text=fs.readFileSync(path.join(fe,f),'utf8');
  for(const m of text.matchAll(/RFLang\??\.register\s*\(\s*\[/g)){
    let k=text.indexOf('[',m.index+m[0].length-1),depth=0,i=k,instr=null;
    for(;i<text.length;i++){const c=text[i];if(instr){if(c==='\\\\'){i++;continue;}if(c===instr)instr=null;continue;}if(c==='"'||c==="'"){instr=c;continue;}if(c==='[')depth++;else if(c===']'){depth--;if(!depth)break;}}
    let arr;try{arr=Function('return '+text.slice(k,i+1))();}catch(e){continue;}
    for(const r of arr)if(Array.isArray(r)&&typeof r[0]==='string')rows.push({file:f,row:r});
  }
}
process.stdout.write(JSON.stringify(rows));
"""
    proc = subprocess.run([exe, "-e", script, str(REPO)], capture_output=True, text=True,
                          encoding="utf-8", timeout=120)
    if proc.returncode != 0:
        raise AssertionError("locale 求值失败：" + proc.stderr[-1200:])
    _ROW_CACHE = json.loads(proc.stdout.strip().splitlines()[-1])
    return _ROW_CACHE


class ImportReviewBehaviourSourceTests(unittest.TestCase):
    """unified_import_workspace.js 的行为钉子（源码级，破坏即红）。"""

    def setUp(self):
        self.src = _read(FRONTEND / "unified_import_workspace.js")

    def test_batch_option_carries_record_count_and_time(self):
        """F05：批次选项 = 文件名 · N 条记录 · 时间（两个同名批次不再无法区分）。"""
        self.assertIn("T('{0} records',records)", self.src, "F05：选项必须拼记录数")
        self.assertIn("toLocaleString(RFLang.get(),{dateStyle:'short',timeStyle:'short'})", self.src,
                      "F05：选项必须拼导入时间")
        self.assertIn("function syncBatchOption()", self.src,
                      "F05：落库后必须把服务端确认的批次摘要同步回下拉选项")

    def test_save_refreshes_authoritative_ready_count(self):
        """F05 后半：保存回调立即重算启用按钮计数与禁用态（不依赖切换批次往返）。"""
        self.assertIn("if(authoritativeCounts)savedBatch={...savedBatch,review_counts:authoritativeCounts};", self.src,
                      "F05：保存响应的权威 review_counts 必须写回 batch")
        self.assertIn("legacyUndo=legacyUndo&&batch.legacy_undo_allowed!==false;syncBatchOption();", self.src,
                      "F05：applyResult 收尾必须同步批次下拉摘要")

    def test_tab_counts_show_units_and_hide_zero_ge(self):
        """F32：计数常显单位；空分类不显示「≥ 0」。"""
        self.assertIn("const tabUnit={duplicate:'groups'", self.src, "F32：页签单位表缺失")
        self.assertIn("{class:'count-unit'}", self.src, "F32：计数旁必须渲染单位小字")
        self.assertIn("count>0;button.append(l('span',(unknown?'≥ ':'')+count", self.src,
                      "F32：「≥」只对非零计数显示（空分类不得出现「≥ 0」）")
        self.assertIn("icon('check'),n('h3',loading?'More records are loading':'No records in this category')", self.src,
                      "F32：空分类不得再挂第二个转圈图标")

    def test_scope_badges_for_later_tab(self):
        """F30 缓解半步：依赖阻塞/仅本浏览器两个徽标；主动暂缓不标。"""
        self.assertIn("function scopeBadge(item)", self.src, "F30：来源徽标函数缺失")
        self.assertIn("if(reason==='dependency'||reason==='waiting')return 'Needs a linked batch first';", self.src,
                      "F30：依赖阻塞项必须标出（首选的原因缺失）")
        self.assertIn("if(item.metadata&&deferred().has(id(item)))return 'Saved in this browser only';", self.src,
                      "F30：本地暂存项必须标「仅保存在本浏览器」")
        window = self.src[self.src.index("function scopeBadge"):]
        window = window[:window.index("function legacySection")]
        self.assertNotIn("undecided", window, "F30：主动暂缓（undecided）不得加标识")

    def test_save_flash_near_actions(self):
        """F36：保存成功在操作区旁浮出「已保存 ✓」，不再只靠顶部状态行。"""
        self.assertIn("function flashSaved(text)", self.src, "F36：就近保存反馈缺失")
        self.assertIn("if(saved)flashSaved(T('Saved ✓'));", self.src,
                      "F36：反馈必须在收尾 render 之后浮出（否则被重建的 DOM 清掉）")
        # #12：撤销窗口说明是状态区里的独立提示行——顶部 .status-message 的既有
        # 文案保持逐字 'Saved'（既有浏览器门禁按等值钉住，对照组逐位不变）。
        self.assertIn("saved=true;errorMessage=T('Saved');", self.src,
                      "#12：主状态行必须保持既有逐字 'Saved'（既有浏览器门禁按等值钉住）")
        self.assertIn("class:'save-undo-note'", self.src, "#12：撤销窗口说明必须有独立提示行")
        self.assertIn("You can undo this until records are added to the task.", self.src,
                      "#12：撤销窗口说明文案缺失")
        self.assertIn("host.querySelector('.main-panel>.card .card-actions')||host.querySelector('.compact-tools')", self.src,
                      "F36：反馈锚点必须是操作区/工具条")

    def test_undo_disabled_states_explain_reason(self):
        """F34：撤销禁用按原因给 title；启用后主动通告一次。"""
        self.assertIn("'Nothing to undo yet'", self.src, "F34：无操作可撤销时必须有原因")
        self.assertIn("Adding records to the task closed the undo window; earlier reviews can no longer be undone", self.src,
                      "F34：启用关闭撤销窗口时必须有原因")
        self.assertIn("Records were added to the task. The review undo window for earlier actions is now closed.", self.src,
                      "F34：启用后必须在 state-message 主动通告一次")

    def test_bulk_dialog_reports_truncation_and_undo(self):
        """F33＋#12：超 100 项如实说明、整批提交可一步撤销。"""
        self.assertIn("groups.filter(group=>group.bulk_eligible===true)", self.src,
                      "F33/C8：弹窗总项数只来自后端 bulk_eligible 字段（单一事实源），"
                      "不得恢复前端判据镜像")
        self.assertIn("'{0} of {1} items; this bulk action covers the first 100.'", self.src,
                      "F33：超限时必须如实说明范围")
        self.assertIn("'This bulk submission can be undone as one step before adding to the task.'", self.src,
                      "#12：批量弹窗必须说明整批可撤销")

    def test_bulk_eligibility_reads_backend_field_only(self):
        """G5 C8（2026-11-10 门禁收尾）：批量资格单一事实源=后端 bulk_eligible。

        破坏即红：恢复任何前端判据镜像（eligible()/identityKey() 回归）、或
        按钮可用性/总项数本地重算判据 → 红。后端三段判据矩阵与载荷同源钉在
        test_r10_u2_bulk_identity.py（钉子 2/3）与 test_2_1_5_g5_merge_rules.py。
        """
        self.assertIn("!groups.some(group=>group.bulk_eligible===true)", self.src,
                      "C8：批量按钮可用性必须只读 bulk_eligible 字段")
        self.assertIn("groups.filter(group=>group.bulk_eligible===true)", self.src,
                      "C8：批量池/总项数必须只按 bulk_eligible 过滤")
        self.assertNotIn("ImportReviewUpdates.eligible", self.src,
                         "C8：前端判据镜像不得回归（2.1.5 G5 F2 已删，后端单一事实源）")
        self.assertNotIn("identityKey(", self.src,
                         "C8：identityKey 镜像不得回归")

    def test_bulk_ineligible_reason_renders_from_message_key(self):
        """G5 C8：不可批量原因按 import.merge.ineligible.* 词典渲染。"""
        self.assertIn("item.bulk_eligible===false&&item.bulk_ineligible_reason", self.src,
                      "C8：原因渲染必须有字段守卫（缺字段不显示）")
        self.assertIn("T('import.merge.ineligible.'+item.bulk_ineligible_reason)", self.src,
                      "C8：原因文案必须走词典键拼接（不显示原始 reason 码/JSON）")
        self.assertIn("U.details('import.review.bulk_ineligible'", self.src,
                      "C8：批量弹窗必须列出未纳入的组及各自原因")

    def test_bulk_keeper_defaults_to_suggestion_and_allows_override(self):
        """G5 C8/F1/F3：默认选中 suggested_keeper、理由随行，可人工改选且必选非空。"""
        self.assertIn("select.value=suggestedId(item)||''", self.src,
                      "C8：保留者下拉必须预选 suggested_keeper")
        self.assertIn("item.suggested_keeper?.keeper_key||item.suggested_keeper?.keeper_record_id", self.src,
                      "C8：预选 id 必须读 suggested_keeper 字段（兼容两种键）")
        self.assertIn("select.onchange=()=>{updateReason();updateConfirm();}", self.src,
                      "C8：人工改选必须生效（刷新理由显示与确认按钮状态）")
        self.assertIn("confirm.disabled=[...selections.values()].some(({select})=>!select.value)", self.src,
                      "C8：任一组未选保留者时确认必须禁用（不静默缺省提交）")

    def test_bulk_conflict_409_renders_translated_reason(self):
        """G5 X11/C8：整批 409 按词典渲染原因；零部分写入的 API 级钉在
        test_r10_u2_bulk_identity.py（近似组整批回滚）。"""
        self.assertIn("error.messageKey==='import_merge.bulk_ineligible'", self.src,
                      "X11：409 必须按 message_key 分流（不显示原始 JSON）")
        self.assertIn("T('import.merge.ineligible.'+error.params.reason)", self.src,
                      "X11：409 原因必须走词典渲染")

    def test_batch_card_counts_carry_units(self):
        """#7-A：批次卡片每个数字都带单位（前四项记录、末项候选【组】）。

        r13「批次统计显示」重钉：统计行的五个参数必须是「原始数字 + 已译单位」
        字符串。index.html 的 ``RFLang.t`` 对每个 ``{n}`` 参数做 ``String()``，
        此前传 ``U.literal`` 的 ``<span>`` 元素会让五个数字显示成
        ``[object HTMLSpanElement]``（十语言同坏）；本钉同时禁止该行再出现
        元素构造实参（旧形态），强度不小于旧钉。
        """
        self.assertIn("+' '+T('records')", self.src, "#7-A：记录数必须带「条记录」单位")
        self.assertIn("+' '+T('groups')", self.src, "#7-A：候选组数必须带「组」单位")
        card = re.search(r"counts\.textContent=T\('Source records: \{0\}.*?T\('groups'\)\);", self.src)
        self.assertIsNotNone(card, "#7-A：批次卡片计数行必须逐项带单位")
        line = card.group(0)
        self.assertEqual(line.count("T('records')"), 4,
                         "#7-A：前四项（来源/已激活/暂存/书目建议）都是记录数")
        self.assertEqual(line.count("T('groups')"), 1,
                         "#7-A：末项待核验候选是组数，不得与记录数混加")
        # r13：参数形态必须是 String(原始值)+' '+单位，而不是元素实参。
        self.assertEqual(line.count(")+' '+T('records')"), 4,
                         "#7-A：四个记录数参数都必须写成 String(...)+' '+T('records')")
        self.assertEqual(line.count(")+' '+T('groups')"), 1,
                         "#7-A：候选组参数必须写成 String(...)+' '+T('groups')")
        self.assertEqual(line.count("String(item."), 5,
                         "#7-A：五个参数各自携带原始数值（不得改传元素/对象）")
        for element_form in ("U.literal(", "l('span'", 'l("span"', "n('span'", 'n("span"'):
            self.assertNotIn(element_form, line,
                             "#7-A：统计行参数不得是元素实参（String() 会显示 [object …]）")

    def test_later_tab_grouped_by_source(self):
        """#10-A：稍后处理栏分「我暂缓的 / 等待关联记录」，先我能处理的。"""
        self.assertIn("function laterGroup(item)", self.src, "#10-A：分组判据缺失")
        group = re.search(r"function laterGroup\(item\)\{[^\n]*\}", self.src)
        self.assertIsNotNone(group, "#10-A：分组判据函数缺失")
        self.assertIn("'blocked':'mine'", group.group(0), "#10-A：依赖阻塞必须归 blocked 组")
        self.assertIn("const LATER_GROUP_LABEL={mine:'Deferred by me',blocked:'Waiting for a linked record'}", self.src,
                      "#10-A：两个分组头文案缺失")
        self.assertIn("if(tab==='later')visible=laterOrdered(visible);", self.src, "#10-A：只在稍后处理栏重排（其它页签顺序不变）")
        self.assertIn("'data-later-group':group", self.src, "#10-A：分组头必须可被定位/断言")
        order = re.search(r"function laterOrdered\(pageItems\)\{[^\n]*\}", self.src)
        self.assertIsNotNone(order, "#10-A：排序函数缺失")
        self.assertIn("order={mine:0,blocked:1}", order.group(0),
                      "#10-A：我暂缓的（可直接处理）必须排在等待关联之前")
        self.assertIn("||a.index-b.index", order.group(0),
                      "#10-A：同组内必须保持原顺序（页内稳定重排）")

    def test_legacy_groups_outside_batch_tabs(self):
        """#6-A：任务级 legacy 组不进批次分类列表，由独立分区承载。"""
        items_line = re.search(r"function items\(\)\{[^\n]*\}", self.src).group(0)
        self.assertNotIn(",...legacy,", items_line, "#6-A：items() 不得再并入 legacy 组")
        self.assertIn("function legacySection()", self.src, "#6-A：必须有任务级分区")
        self.assertIn("'legacy-queue'", self.src, "#6-A：分区使用独立类名，不与批次队列混算")
        self.assertIn("data-legacy-scope", self.src, "#6-A：分区必须可被外部定位")

    def test_completed_flow_badges(self):
        """#1：已完成栏明确「已加入任务 / 组结论已存且全组已加入 / 任务级」流向。"""
        for literal in ("'Task-level, outside this batch'", "'Added to the task'",
                        "'Group decided; all records added to the task'"):
            self.assertIn(literal, self.src, "#1 流向徽章缺失：" + literal)
        self.assertIn("its staged member records moved to Ready to add to task", self.src,
                      "#1：已决未启用组的去向必须在保存后讲明")

    def test_activation_action_named_add_to_task(self):
        """F46＋#2：调用点统一到 'Add to task'；旧的 Activate*/启用类调用点清零。"""
        self.assertIn("const ADD_TO_TASK='Add to task';", self.src, "动作名常量缺失")
        self.assertIn("if(label===ADD_TO_TASK)button.dataset.importActivation='true';", self.src,
                      "激活按钮识别必须跟随统一后的键")
        for legacy in ("write('Activate resolved records'", "b('Activate reviewed records'",
                       "n('h2','Activate reviewed records'"):
            self.assertNotIn(legacy, self.src, "旧动作名调用点残留：" + legacy)


class ImportLocaleTests(unittest.TestCase):
    """十语言词条结构 + F35/F46 术语口径。"""

    def setUp(self):
        self.export_locale = _read(FRONTEND / "import_export_locale.js")
        self.refined_locale = _read(FRONTEND / "import_review_refined_locale.js")
        self.review_locale = _read(FRONTEND / "import_review_locale.js")
        self.index = _read(INDEX)

    _ROWS_CACHE = None

    def _rows(self):
        """用 node 求值三个 locale 文件的全部 RFLang.register 字面量行。

        直接 json.loads 单引号数组会漏行（import_export_locale 用单引号），
        因此在 node 里 Function('return [...]') 求值后回读；求值失败的行跳过。
        """
        if ImportLocaleTests._ROWS_CACHE is not None:
            return ImportLocaleTests._ROWS_CACHE
        exe = _node_exe()
        if exe is None:
            self.skipTest("node 不可用")
        script = r"""
const fs=require('fs'),path=require('path');
const fe=path.join(process.argv[1],'custom_frontend');
const files=['import_export_locale.js','import_review_refined_locale.js','import_review_locale.js'];
const rows=[];
for(const f of files){
  const text=fs.readFileSync(path.join(fe,f),'utf8');
  for(const m of text.matchAll(/RFLang\??\.register\s*\(\s*\[/g)){
    let k=text.indexOf('[',m.index+m[0].length-1);
    let depth=0,i=k,instr=null;
    for(;i<text.length;i++){const c=text[i];if(instr){if(c.charCodeAt(0)===92){i++;continue;}if(c===instr)instr=null;continue;}if(c==='"'||c==="'"){instr=c;continue;}if(c==='[')depth++;else if(c===']'){depth--;if(!depth)break;}}
    let arr;
    try{arr=Function('return '+text.slice(k,i+1))();}catch(e){continue;}
    for(const r of arr)if(Array.isArray(r)&&typeof r[0]==='string')rows.push({file:f,row:r});
  }
}
process.stdout.write(JSON.stringify(rows));
"""
        proc = subprocess.run([exe, "-e", script, str(REPO)], capture_output=True, text=True,
                              encoding="utf-8", timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-1200:])
        ImportLocaleTests._ROWS_CACHE = json.loads(proc.stdout.strip().splitlines()[-1])
        return ImportLocaleTests._ROWS_CACHE

    def test_activation_naming_is_unified(self):
        """F46：同一 zh 键只注册一次、英文名词唯一（reviewed/resolved 不再漂移）。"""
        rows = [(r["row"][0], r["row"]) for r in self._rows()]
        by_key = {}
        for key, row in rows:
            by_key.setdefault(key, []).append(row)
        for legacy_key in ("启用已核验记录", "启用已解决记录", "激活已解决记录"):
            self.assertNotIn(legacy_key, by_key, "F46：旧动作名仍注册：" + legacy_key)
        action_rows = [r for k, r in rows if r[1] == "Add to task"]
        self.assertTrue(action_rows, "F46：必须注册唯一动作名 'Add to task'")
        self.assertEqual(len({tuple(r) for r in action_rows}), 1, "F46：同一动作名不得有多个不同译文")
        for key, row in rows:
            if row[1] in ("Activate reviewed records", "Activate resolved records"):
                self.assertIn(key, ("Adding to the task ends the undo window for these import-review actions.",),
                              "F46：旧英文名词仍有注册（应统一到 reviewed/加入任务口径）：%r -> %s" % (key, row[1]))

    def test_retain_wording_avoids_duplicate_verbs(self):
        """F35：候选侧与书目侧不再共用「保留」动词，同卡统一「书目」称谓。"""
        self.assertIn("['以这条为准（确认同一篇）','Retain this record'", self.export_locale,
                      "F35：候选侧单选标签未改写")
        self.assertIn("['沿用当前书目','Keep existing metadata'", self.export_locale,
                      "F35：书目侧按钮未改写")
        self.assertIn("['改用导入书目','Accept incoming metadata'", self.export_locale,
                      "F35：书目侧另一按钮未改写")
        self.assertIn("'Existing bibliographic information','Incoming bibliographic information'",
                      _read(FRONTEND / "unified_import_workspace.js"),
                      "F35：比较表头必须统一「书目信息」称谓")
        # 同一英文键两处注册必须给出一字不差的中文串（否则非中文界面随机取一条）。
        for en in ("Keep existing metadata", "Accept incoming metadata", "Confirm duplicate and retain selected"):
            zh = {r["row"][0] for r in self._rows() if r["row"][1] == en and r["file"] != "import_review_locale.js"}
            self.assertEqual(len(zh), 1, "F35：同一英文键 %r 注册了多个中文串：%r" % (en, zh))

    def test_single_registration_per_key(self):
        """F35 的结构要求：同键只注册一处（先修双注册结构，再改词）。

        此前这四个键同时在 import_export_locale.js 与 import_review_refined_locale.js
        注册，且外语槽口径不同（refined 里的「沿用/改用」仍译成 metadata，
        「将已核验记录加入任务」的 8 个外语槽仍是 Activate 口径）。加载顺序
        决定胜者 → 改一处漏一处，跨语言界面各自取到不同措辞。破坏即红：
        任一键出现两条不同行。
        """
        rows = self._rows()
        for key in ("确认同一篇，保留所选记录", "沿用当前书目", "改用导入书目",
                    "将已核验记录加入任务", "以这条为准（确认同一篇）", "Retain this record"):
            variants = {tuple(r["row"]) for r in rows if r["row"][0] == key}
            self.assertLessEqual(len(variants), 1,
                                 "F35：键 %r 注册了 %d 条不同译法（加载顺序决定胜者）" % (key, len(variants)))

    def test_retain_wording_has_no_activation_verb(self):
        """F46：书目/候选侧的外语槽也不得混入 Activate/启用 口径。"""
        rows = self._rows()
        banned = ("Activer les notices", "Активировать", "Activar registros",
                  "有効化", "Ativar registros", "aktivieren", "Активирај", "활성화")
        for key in ("将已核验记录加入任务", "沿用当前书目", "改用导入书目", "以这条为准（确认同一篇）"):
            row = next((r["row"] for r in rows if r["row"][0] == key), None)
            self.assertIsNotNone(row, "F46：缺少词条 " + key)
            for slot, value in enumerate(row):
                for word in banned:
                    self.assertNotIn(word, str(value),
                                     "F46：%s 第 %d 槽仍是 Activate 口径：%s" % (key, slot, value))

    def test_new_keys_are_ten_language_complete(self):
        """全部新增/改写键：10 槽齐全、占位符一致、除专名外不得英文回退。"""
        exe = _node_exe()
        if exe is None:
            self.skipTest("node 不可用")
        script = r"""
const fs=require('fs'),path=require('path');
const fe=path.join(process.argv[1],'custom_frontend');
const files=['import_export_locale.js','import_review_refined_locale.js','import_review_locale.js'];
const rows=[];
for(const f of files){
  const text=fs.readFileSync(path.join(fe,f),'utf8');
  for(const m of text.matchAll(/RFLang\??\.register\s*\(\s*\[/g)){
    let k=text.indexOf('[',m.index+m[0].length-1);
    let depth=0,i=k,instr=null;
    for(;i<text.length;i++){const c=text[i];if(instr){if(c==='\\'){i++;continue;}if(c===instr)instr=null;continue;}if(c==='"'||c==="'"){instr=c;continue;}if(c==='[')depth++;else if(c===']'){depth--;if(!depth)break;}}
    let arr;
    try{arr=Function('return '+text.slice(k,i+1))();}catch(e){continue;}
    for(const r of arr)rows.push({file:f,row:r});
  }
}
const KEYS=['Add to task','Add reviewed records to the task','Ready to add to task','Ready to activate',
'Retain this record','Confirm duplicate and retain selected','Keep existing metadata','Accept incoming metadata',
'Existing bibliographic information','Incoming bibliographic information','Current bibliographic information',
'{0} records','records','groups',
'No records in this category','No review needed','Needs a linked batch first','Saved in this browser only',
'Task-level duplicate checks (outside this batch)','Task-level, outside this batch','Added to the task',
'Group decided; all records added to the task','Saved ✓',
'Saved. You can undo this until records are added to the task.',
'Accepts immediately: the library record is updated now, before adding to the task. You can undo before adding to the task.',
'Accepting incoming information updates the library record immediately, before adding to the task; it replaces the fields shown above. Undo is available until records are added to the task.',
'Records were added to the task. The review undo window for earlier actions is now closed.',
'Adding records to the task closed the undo window; earlier reviews can no longer be undone',
'Nothing to undo yet','Only the task owner can undo import review',
'Undo ends when you add records to the task, save another review, or someone else changes the data.',
'This will add {0} reviewed record(s) to the task.',
'Adding records enables resolved records or source links. This is not an inclusion decision; unresolved records stay staged, and repeating it does not add duplicate work.',
'Adding to the task ends the undo window for these import-review actions.',
'The group decision is saved; its staged member records moved to Ready to add to task.',
'This bulk submission can be undone as one step before adding to the task.',
'Reviewed records still need to be added to the task. Unresolved records stay staged.',
'A linked record must be added to the task or reviewed first. Existing articles remain available.',
'{0} of {1} items; this bulk action covers the first 100.','{0} review items',
'Needs your review','Reviewed: {0} of {1}','Needs your review: {0}. Reviewed: {1} of {2}.','items'];
const failures=[];
for(const key of KEYS){
  const row=(rows.find(r=>r.row[1]===key)||{}).row;
  if(!row){failures.push('missing '+key);continue;}
  if(row.length!==10)failures.push('slots '+key+' = '+row.length);
  for(let i=0;i<row.length;i++)if(!String(row[i]).trim())failures.push('empty slot '+i+' '+key);
  const ph=(v)=>((v.match(/\{\d+\}/g)||[]).sort().join(','));
  for(let i=0;i<row.length;i++)if(ph(row[i])!==ph(row[1]))failures.push('placeholder '+i+' '+key);
  for(let i=2;i<10;i++)if(row[i]===row[1]&&!/^[A-Za-z0-9 .()]+$/.test(row[0]))failures.push('english fallback slot '+i+' '+key);
}
process.stdout.write(JSON.stringify(failures));
"""
        proc = subprocess.run([exe, "-e", script, str(REPO)], capture_output=True, text=True,
                              encoding="utf-8", timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-1200:])
        failures = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(failures, [], "十语言/占位符/英文回退检查未通过：%r" % (failures[:12],))

    def test_upload_and_merge_copy_registered(self):
        """F54/F55/F57/F61/F67 的用户可见文案必须在词典里（10 槽）。"""
        for key in ("正在上传文件…", "正在合并 {0} 份决策 CSV…已等待 {1} 秒", "已合并 {0} 份决策 CSV。",
                    "单个文件最大 100 MB。", "超过 100 MB 的文件无法上传。"):
            self.assertIn('"' + key + '"', self.export_locale if key.startswith(("正在", "已合并", "单个", "超过")) else self.index,
                          "缺失词条：" + key)
        self.assertIn("进度暂不可知（已用 {0} 分 {1} 秒）", self.index, "F55：停滞态文案缺失")


class ImportBackendCopyTests(unittest.TestCase):
    """security.py（F67）与 review_imports_api.py（F56）的钉子。"""

    def setUp(self):
        self.security = _read(REPO / "custom_backend" / "security.py")
        self.imports_api = _read(REPO / "custom_backend" / "review_imports_api.py")

    def test_upload_rejection_copy(self):
        """F67：扩展名错误串不得渲染 Python list repr；UTF-8 拒绝必须给转码指引。

        破坏即红的三件事：
        ① 回到 `sorted(allowed)` 的 list repr（"['.csv', '.ris']"）；
        ② 拒绝串重新变成中文硬编码（十语言铁律：必须是英文整句 + 词典 en 槽）；
        ③ 词典整句与 TEXT_EXTENSIONS 脱钩（加扩展名却忘了改文案）。
        """
        self.assertNotIn("sorted(allowed)", self.security, "F67：不得回到 sorted() 的 list repr")
        self.assertNotIn("_extension_text(allowed)", self.security, "F67：可读清单常量已改为整句")
        # 三条文案都是模块级英文整句常量，被 UploadRejected 直接抛出——T() 只做
        # 整串精确查找，含插值的句子永远命中不了词典（=中文硬编码）。
        for name in ("UPLOAD_EXTENSION_MESSAGE", "UPLOAD_BINARY_MESSAGE", "UPLOAD_UTF8_MESSAGE"):
            self.assertIn(name + " = (", self.security, "F67：文案必须是模块级整句常量：" + name)
        self.assertIn("UploadRejected(UPLOAD_EXTENSION_MESSAGE", self.security, "F67：扩展名拒绝必须用整句常量")
        self.assertIn("UploadRejected(UPLOAD_UTF8_MESSAGE", self.security, "F67：UTF-8 拒绝必须用整句常量")
        self.assertIn("UploadRejected(UPLOAD_BINARY_MESSAGE", self.security, "F67：二进制内容拒绝必须用整句常量")
        utf8 = self._message("UPLOAD_UTF8_MESSAGE")
        self.assertIn("not valid UTF-8", utf8, "F67：UTF-8 拒绝必须点明编码问题")
        self.assertIn("UTF-8", utf8, "F67：必须给出另存为 UTF-8 的可操作指引")
        self.assertIn("NoteExpress", utf8, "F67：必须点出中文文献环境的高频导出工具")

        # 十语言：三条英文整句必须是词典的 en 槽，且 10 槽齐全、无中文回退。
        self._assert_registered(self._message("UPLOAD_EXTENSION_MESSAGE"), "F67 扩展名")
        self._assert_registered(self._message("UPLOAD_BINARY_MESSAGE"), "F67 二进制")
        self._assert_registered(utf8, "F67 UTF-8")

        # 清单与 TEXT_EXTENSIONS 同步：新增允许类型却忘了改文案 → 红。
        from custom_backend.security import TEXT_EXTENSIONS
        for ext in TEXT_EXTENSIONS:
            self.assertIn(ext, self._message("UPLOAD_EXTENSION_MESSAGE"),
                          "F67：允许扩展名 %s 未在拒绝文案里列出" % ext)

    def _message(self, name):
        """取 security.py 里模块级字符串常量的**真实值**（导入模块读取，不猜拼接）。"""
        from custom_backend import security as security_module
        value = getattr(security_module, name, None)
        self.assertIsNotNone(value, "缺少常量 " + name)
        return value

    def _assert_registered(self, english, label):
        """英文整句必须作为词典 en 槽存在，且 10 槽齐全。"""
        rows = _locale_rows()
        row = next((r["row"] for r in rows if r["row"][1] == english), None)
        self.assertIsNotNone(row, label + "：英文整句未登记为词典 en 槽（中文硬编码）")
        self.assertEqual(len(row), LANGS, label + "：词条必须十槽齐全，实际 %d" % len(row))
        for index, value in enumerate(row):
            self.assertTrue(str(value).strip(), "%s：第 %d 槽为空" % (label, index))
        self.assertNotEqual(row[0], row[1], label + "：中文槽不得回退英文")

    def test_progress_callbacks_in_long_transaction(self):
        """F56：legacy_plan 三次 find_duplicates 必须接进度回调，且 95→97 真细分。

        破坏即红：
        ① 三次匹配少接一次回调；
        ② 三个槽位共用一个回调——那样三次调用的起止比例落在同一格，整段
          95→97 只会打出一个 96（继承版实现正是如此，细分形同虚设）；
        ③ 打点去重失效（同一百分比反复落盘）或越界到 95–97 之外。
        """
        self.assertEqual(self.imports_api.count("progress_callback=progress_callback("), 3,
                         "F56：三次匹配都必须接**各自的**槽位回调")
        for slot in ("(0)", "(1)", "(2)"):
            self.assertIn("progress_callback=progress_callback" + slot, self.imports_api,
                          "F56：槽位 %s 未接回调" % slot)
        self.assertIn("def _legacy_progress(", self.imports_api, "F56：95→97 细分打点工厂缺失")
        self.assertIn("if percent <= seen['percent']:", self.imports_api,
                      "F56：同一百分比不得重复落盘，且进度只前进不回退")
        self.assertIn("percent = max(95, min(97, percent))", self.imports_api,
                      "F56：打点区间必须限定在 95–97")
        self.assertIn("def slot(index, slots=3):", self.imports_api,
                      "F56：必须按调用槽位取回调（共用一个回调时细分退化为单点）")

    def test_progress_subdivision_emits_distinct_percentages(self):
        """F56 行为钉：三次匹配走完必须真的依次打出 95、96、97（不是只一个）。"""
        sys.path.insert(0, str(REPO))
        from custom_backend.review_imports_api import ImportRuntime
        calls = []

        class Recorder:
            def progress(self, job, task, user, phase, percent, **extra):
                calls.append((phase, percent))

        slots = ImportRuntime._legacy_progress(Recorder(), "job", "task", "user")
        slots(0)("exact", 100, 100)
        for i in range(1, 26):
            slots(1)("fuzzy", i, 25)
        slots(2)("fuzzy", 0, 0)
        percents = [p for _, p in calls]
        self.assertEqual(percents, [95, 96, 97],
                         "F56：三次匹配必须依次打 95/96/97，实际：%r" % (percents,))
        self.assertTrue(all(phase == "save" for phase, _ in calls),
                        "F56：细分打点必须留在 save 阶段（不改写其它阶段语义）")
        before = len(calls)
        slots(1)("fuzzy", 25, 25)
        slots(2)("fuzzy", 0, 0)
        self.assertEqual(len(calls), before, "F56：同一百分比不得重复落盘")

    def test_progress_range_and_no_save_phase_hijack(self):
        """F56：细分打点不得改写 save 阶段的主 97 打点与其它阶段。"""
        self.assertIn("self.progress(job_id, task_id, user['user_id'], 'save', 97)", self.imports_api,
                      "F56：写库事务的 97 打点必须保留")


class UploadAndMergeFrontendTests(unittest.TestCase):
    """index.html / import_export_workspace.js：F54/F55/F57/F61。"""

    def setUp(self):
        self.html = _read(INDEX)
        self.ui = _read(FRONTEND / "import_export_workspace.js")

    def test_upload_stage_and_stall_copy(self):
        self.assertIn("phase === 'starting' ? T('正在上传文件…')", self.html,
                      "F54：上传期必须单独文案，不得落入解析默认文案")
        self.assertIn("noteImportProgress(p.phase, p.percent)", self.html, "F55：轮询未接停滞判定")
        self.assertIn("performance.now() - importProgressChangedAt > 10000", self.html,
                      "F55：停滞阈值必须是 10 秒")
        self.assertIn("aria-valuenow", self.html, "F55：不确定态必须撤下确定的 aria-valuenow")

    def test_size_limit_precheck(self):
        self.assertIn("const IMPORT_MAX_BYTES=100*1024*1024;", self.ui, "F61：前端上限常量缺失")
        self.assertGreaterEqual(self.ui.count("IMPORT_MAX_BYTES"), 2, "F61：上限必须被复用（定义 + 预检 + 导出）")
        self.assertIn("start.disabled=blocked||!queued.length", self.ui,
                      "F61：超限文件必须禁用上传按钮")
        self.assertIn("One file can be up to 100 MB.", self.ui, "F61：拖放区必须常显上限")
        self.assertIn("window.ImportExportWorkspace?.oversize?.(file)", self.html,
                      "F61：realImport 必须有二次守卫（绕过按钮的路径也不上传）")
        security = _read(REPO / "custom_backend" / "security.py")
        self.assertIn("MAX_UPLOAD_BYTES = 100 * 1024 * 1024", security,
                      "F61：后端上限校验必须保留（前端预检只是体验层）")
        # 接线端：realImport 的二次守卫读的是 window.ImportExportWorkspace.oversize，
        # 而它必须在**定义之后**挂上去。曾把它挂在 UI.tabs 之后、const 声明之前
        # ——模块加载即抛 TDZ ReferenceError，oversize 永远不存在，守卫静默失效
        # （超限文件照样上传）。这里以"先定义后导出"的顺序钉住；整页装载由
        # test_r10_u2_page_load_browser.cjs 真浏览器验证。
        define_at = self.ui.index("const oversize=file=>")
        export_at = self.ui.index("UI.maxUploadBytes=IMPORT_MAX_BYTES;UI.oversize=oversize;")
        self.assertLess(define_at, export_at,
                        "F61：UI.oversize 必须在 const 定义之后导出（否则 TDZ 崩溃、守卫失效）")
        self.assertEqual(self.ui.count("UI.oversize=oversize;"), 1,
                         "F61：UI.oversize 只允许有定义后的一处导出")
        self.assertIn("UI.maxUploadBytes=IMPORT_MAX_BYTES;UI.oversize=oversize;", self.ui,
                      "F61：超限预检必须导出给 index.html 的二次守卫")

    @unittest.skipIf(os.environ.get("CI") == "true", "真实浏览器钉需本地图形会话")
    def test_page_load_browser_nail(self):
        """F61 接线端：整页装载零 pageerror（TDZ/初始化顺序类崩溃立即暴露）。"""
        if not (os.environ.get("REVIEWFLOW_PLAYWRIGHT") and os.environ.get("PLAYWRIGHT_BROWSERS_PATH")):
            self.skipTest("playwright runtime not configured")
        exe = _node_exe()
        if exe is None:
            self.skipTest("node 不可用")
        proc = subprocess.run([exe, str(REPO / "build" / "tests" / "test_r10_u2_page_load_browser.cjs")],
                              cwd=REPO, capture_output=True, text=True, encoding="utf-8", timeout=600,
                              env=dict(os.environ))
        self.assertEqual(proc.returncode, 0, proc.stdout[-3000:] + proc.stderr[-3000:])
        self.assertIn("PASS r10-U2 page-load nail", proc.stdout)

    def test_merge_busy_feedback(self):
        self.assertIn("function setMergeBusy(active)", self.html, "F57：合并忙碌反馈缺失")
        self.assertIn("mergePriorDisabled", self.html, "F57：必须快照并恢复原禁用态")
        self.assertIn("正在合并 {0} 份决策 CSV…已等待 {1} 秒", self.html, "F57：必须显示已用时长")
        self.assertIn("setMergeBusy(true);\n  try {", self.html, "F57：上传后必须立即进入忙碌态")
        self.assertIn("} finally {\n    setMergeBusy(false);", self.html, "F57：成功/失败都必须恢复")


@unittest.skipIf(os.environ.get("CI") == "true", "真实浏览器钉需本地图形会话")
class BrowserNailTests(unittest.TestCase):
    """真浏览器钉子（未配置 playwright 时如实 skip，不冒充通过）。"""

    @unittest.skipUnless(os.environ.get("REVIEWFLOW_PLAYWRIGHT") and os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
                         "playwright runtime not configured")
    def test_behavior_browser_nails(self):
        exe = _node_exe()
        if exe is None:
            self.skipTest("node 不可用")
        proc = subprocess.run([exe, str(REPO / "build" / "tests" / "test_r10_u2_behavior_browser.cjs")],
                              cwd=REPO, capture_output=True, text=True, encoding="utf-8", timeout=600,
                              env=dict(os.environ))
        self.assertEqual(proc.returncode, 0, proc.stdout[-3000:] + proc.stderr[-3000:])
        self.assertIn("PASS r10-U2 browser nails", proc.stdout)
