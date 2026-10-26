"""2.1.4 UX 前端泳道静态守护：五项高危 UX 修复的代码级钉子。

只读源码做静态断言，不启动应用（本机无浏览器/Node Playwright 环境，
行为推演见各修复的提交信息；此处钉住“接线存在且方向正确”）：

1. 高1（F01 会话静默丢失）：401 统一登出分支在调用 logout() 之前先弹十语言
   显式 toast；logout() 本体不清除 URL 片段；enterApp() 按 location.hash
   复原页面——三者合起来保证“提示可见 + 重登录回到原处”。
2. 高2（F02 分析报错）：校验一次性汇总全部缺失项（missing.push ×3）且汇总
   文案十语言注册；后端合并分析错误串注册进 analysis_locale 双键字典；
   「查看原始信息」details 由显式点击接管展开；分析页加载的 inert 恢复
   不再绑定 current()（旧代码在任务/筛选员中途变化时会让整页永久 inert，
   这是“点不开”的可复现根因）；localized 标记沿 synthesis-failed 事件传递。
3. 高4（F29/F05 导入核验）：'Ready to activate' 全前端只剩唯一注册行
   （import_export_locale 的「待启用」，import_review_locale 的重复行
   「可激活」已删除）；已完成分类加「已启用/已核验待启用」徽标；批次下拉
   标签纳入文件名；applyResult 从合并后的本地数据即时重算 review_counts.ready。
4. 高5（F31 加载动画复活）：preserve 重开批次缓存可用时起始状态记为
   complete 并压住 initial/background 的可见切换（30 秒轮询不重放转圈）；
   complete 胶囊 4 秒后折叠。
5. 高7（F53 导入遮罩无取消）：遮罩含取消按钮/已用时长/如实提示三要素；
   apiRequest 把 opts.signal 透传给 fetch 且 AbortError 原样抛出；realImport
   创建 AbortController 并由取消按钮触发 abort；取消提示十语言注册。

词条断言解析 locale 行（zh 键 + 十语言列），行内不含嵌套数组，按字符串感知
扫描器提取；任何键缺列、未注册或重复注册都会失败。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "custom_frontend"


def _read(path):
    return Path(path).read_text(encoding="utf-8")


def _find_row(text, key):
    """按 zh/任意列键定位 locale 行并提取全部字符串字面量（十语言列）。"""
    idx = -1
    for quote in ('"', "'"):
        idx = text.find(quote + key + quote)
        if idx >= 0:
            break
    if idx < 0:
        return None
    start = text.rfind("[", 0, idx)
    if start < 0:
        return None
    depth = 0
    i = start
    end = -1
    while i < len(text):
        c = text[i]
        if c in ('"', "'"):
            i += 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == c:
                    break
                i += 1
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = i
                break
        i += 1
    if end < 0:
        return None
    row = text[start:end + 1]
    values = []
    j = 0
    while j < len(row):
        c = row[j]
        if c in ('"', "'"):
            q = c
            j += 1
            buf = []
            while j < len(row):
                if row[j] == "\\":
                    buf.append(row[j + 1])
                    j += 2
                    continue
                if row[j] == q:
                    break
                buf.append(row[j])
                j += 1
            values.append("".join(buf))
        j += 1
    return values


def _require_row(text, key, where):
    row = _find_row(text, key)
    assert row is not None, f"{where} 未注册词条行：{key}"
    assert len(row) == 10, f"{where} 词条 {key} 应为十语言列，实际 {len(row)} 列：{row}"
    assert all(row), f"{where} 词条 {key} 存在空列：{row}"
    return row


class SessionExpiryTests(unittest.TestCase):
    """高1（F01）：401 显式提示 + URL 片段保留。"""

    def setUp(self):
        self.html = _read(FRONTEND / "index.html")

    def test_401_toast_registered_ten_languages(self):
        row = _require_row(self.html, "登录已过期。当前页面地址已保留，重新登录后将回到此处。", "index.html")
        self.assertTrue(row[1].startswith("Your session expired"))

    def test_401_branch_toasts_before_logout(self):
        m = re.search(r"if \(resp\.status === 401 && !path\.startsWith\('/api/auth/'\)\) \{", self.html)
        assert m, "401 统一登出分支不存在"
        snippet = self.html[m.end():m.end() + 1200]
        cut = snippet.find("throw new ApiError(401")
        assert cut >= 0, "401 分支未抛出 ApiError"
        branch = snippet[:cut]
        toast_at = branch.find("toast('登录已过期。当前页面地址已保留")
        logout_at = branch.find("logout('登录已过期，请重新登录。')")
        self.assertGreaterEqual(toast_at, 0, "401 分支缺少跳登录前的显式 toast")
        self.assertGreaterEqual(logout_at, 0, "401 分支缺少统一 logout 调用（统一登出架构不变）")
        self.assertLess(toast_at, logout_at, "toast 必须先于 logout 调用（跳登录前可见）")

    def test_logout_does_not_clear_url_fragment(self):
        start = self.html.find("async function logout(msg)")
        assert start >= 0, "logout 函数不存在"
        end = self.html.find("/* ---------- 全局状态", start)
        body = self.html[start:end]
        self.assertNotIn("location.hash", body, "logout 不得改动 URL 片段（重登录返回依赖它）")
        self.assertNotIn("history.", body, "logout 不得改动会话历史")

    def test_enter_app_restores_fragment(self):
        start = self.html.find("async function enterApp()")
        assert start >= 0
        body = self.html[start:start + 4000]
        self.assertIn("location.hash", body, "enterApp 应读取保留的 URL 片段")
        self.assertIn("go(initialPage)", body, "enterApp 应按片段复原页面")


class AnalysisErrorTests(unittest.TestCase):
    """高2（F02）：错误翻译接入、展开修复、校验一次性汇总。"""

    def setUp(self):
        self.html = _read(FRONTEND / "index.html")
        self.locale = _read(FRONTEND / "analysis_locale.js")
        self.nav = _read(FRONTEND / "analysis_navigation.js")

    def test_backend_synthesis_errors_registered(self):
        row = _require_row(self.locale, "至少需要两项独立研究才能合并。", "analysis_locale.js")
        self.assertEqual(row[1], "at least two independent studies are required")
        row = _require_row(self.locale, "不支持的效应指标或模型。", "analysis_locale.js")
        self.assertEqual(row[1], "unsupported measure or model")

    def test_validation_messages_registered(self):
        row = _require_row(self.locale, "运行合并分析前，请先选择：{0}。", "analysis_locale.js")
        self.assertTrue(row[1].startswith("Before running the synthesis"))
        _require_row(self.locale, "修正 HKSJ 区间需要随机效应模型。", "analysis_locale.js")
        _require_row(self.locale, "运行前请先选择合并模型。", "analysis_locale.js")
        _require_row(self.locale, "运行前请先选择置信区间方法。", "analysis_locale.js")
        _require_row(self.locale, "、", "analysis_locale.js")

    def test_validation_aggregates_all_missing_fields(self):
        start = self.html.find("function anSynthesisQuery()")
        assert start >= 0
        body = self.html[start:start + 1600]
        for field in ("anModel", "anCiMethod", "anMeasure"):
            self.assertIn(f"anValue('{field}')", body, f"校验汇总应覆盖 {field}")
        self.assertIn("missing.push", body, "校验应汇总缺失项而不是逐个抛错")
        self.assertIn("error.rfLocalized = true", body, "汇总错误应带 rfLocalized 标记")

    def test_localized_flag_flows_to_error_host(self):
        self.assertIn("detail:{message:e.message, localized:e.rfLocalized === true}", self.html,
                      "synthesis-failed 事件应把 localized 放进 detail（CustomEvent init 顶层键会被浏览器丢弃）")
        self.assertIn("payload.localized===true", self.nav,
                      "分析页错误条应从 detail 解包 localized")
        self.assertIn("options.localized", self.locale, "message() 应识别已本地化文案")

    def test_raw_message_details_toggles_explicitly(self):
        self.assertIn("details.open = !details.open", self.locale,
                      "「查看原始信息」应由显式点击接管展开")

    def test_analysis_load_inert_recovery_not_gated_by_current(self):
        start = self.html.find("async function loadAnalysis()")
        assert start >= 0
        body = self.html[start:start + 6000]
        self.assertIn("clearInterval(loadTimer);", body, "加载收尾应清理自身定时器")
        self.assertIn("ticket === analysisLoadGeneration", body,
                      "inert 恢复应按加载代际判断，而不是 current()（任务/筛选员变化不得让整页永久 inert）")
        self.assertNotIn("if (current()) {\n      clearInterval(analysisLoadTimer)", body,
                         "旧的 current() 门控收尾不允许回归")


class ImportReviewLabelTests(unittest.TestCase):
    """高4（F29/F05）：术语统一、徽标、批次下拉与启用计数。"""

    def setUp(self):
        self.workspace = _read(FRONTEND / "unified_import_workspace.js")
        self.review_locale = _read(FRONTEND / "import_review_locale.js")
        self.export_locale = _read(FRONTEND / "import_export_locale.js")

    def test_ready_to_activate_registered_once_as_pending_activation(self):
        row = _require_row(self.export_locale, "待启用", "import_export_locale.js")
        self.assertEqual(row[1], "Ready to activate")
        self.assertIsNone(_find_row(self.review_locale, "可激活"),
                          "import_review_locale 的重复「可激活」注册必须删除")
        occurrences = 0
        for path in FRONTEND.glob("*_locale.js"):
            occurrences += len(re.findall(r"[\"']Ready to activate[\"']", _read(path)))
        self.assertEqual(occurrences, 1, "『Ready to activate』必须全前端唯一注册（待启用）")

    def test_completed_badges_registered(self):
        enabled = _require_row(self.review_locale, "已启用", "import_review_locale.js")
        self.assertEqual(enabled[1], "Already active")
        pending = _require_row(self.review_locale, "已核验待启用", "import_review_locale.js")
        self.assertEqual(pending[1], "Reviewed, pending activation")

    def test_completed_tab_renders_state_badge(self):
        self.assertIn("function completionBadge(item){return item.members?(item.members.every(m=>m.ref_type==='active'||m.record?.state==='active')?'已启用':'已核验待启用'):'已启用';}",
                      self.workspace,
                      "已完成分类应按成员实际状态渲染徽标（启用后的候选组是「已启用」，不能只看是否候选组）")

    def test_batch_dropdown_includes_filenames(self):
        self.assertIn("files.join(' · ')", self.workspace,
                      "批次下拉标签应纳入文件名以区分同秒批次")

    def test_activation_count_recomputed_after_save(self):
        self.assertIn("ready:records.filter(r=>M.reason(r)==='ready').length", self.workspace,
                      "applyResult 应从合并后的本地数据即时重算 ready 计数（保存响应不含 review_counts）")


class ImportLoadingTests(unittest.TestCase):
    """高5（F31）：preserve 重开静默合并、胶囊折叠、轮询不重放动画。"""

    def setUp(self):
        self.workspace = _read(FRONTEND / "unified_import_workspace.js")
        self.loading = _read(FRONTEND / "import_review_loading.js")

    def test_preserve_reopen_keeps_complete_state(self):
        self.assertIn("loadState=cachedUsable?{phase:'complete'", self.workspace,
                      "preserve 重开且缓存确有数据时，起始状态应记为 complete（不重现转圈）；空缓存保持 initial")
        self.assertIn("cachedUsable&&['initial','background'].includes(state.phase)?loadState:state",
                      self.workspace,
                      "首轮 initial/background 通知应被压住（静默合并；failed/complete 照常透传）")

    def test_complete_capsule_folds_after_a_few_seconds(self):
        self.assertIn("setTimeout", self.loading)
        self.assertIn("4000", self.loading, "complete 胶囊应在数秒后折叠")
        self.assertIn("maxHeight", self.loading)
        self.assertIn("prefers-reduced-motion", self.loading, "减少动态效果时不应播放折叠过渡")


class ImportMaskCancelTests(unittest.TestCase):
    """高7（F53）：遮罩取消按钮、abort 接线与如实提示。"""

    def setUp(self):
        self.html = _read(FRONTEND / "index.html")
        self.export_locale = _read(FRONTEND / "import_export_locale.js")

    def test_mask_contains_cancel_elapsed_and_honest_hint(self):
        for anchor in ('id="importBusyCancel"', 'id="importBusyElapsed"', 'id="importBusyCancelHint"'):
            self.assertIn(anchor, self.html, f"遮罩缺少元素 {anchor}")

    def test_fetch_passes_signal_and_preserves_abort_identity(self):
        self.assertIn("signal: opts.signal", self.html, "apiRequest 应把取消信号透传给 fetch")
        self.assertIn("e.name === 'AbortError') throw e", self.html,
                      "网络异常包装不得吞掉 AbortError 的取消语义")

    def test_real_import_wires_abort_controller(self):
        self.assertIn("new AbortController()", self.html, "realImport 应创建 AbortController")
        self.assertIn("signal: abort.signal", self.html, "导入请求应携带取消信号")
        self.assertIn("importAbort?.abort();", self.html, "取消按钮应触发 abort")
        self.assertIn("e.name === 'AbortError'", self.html.split("async function realImport")[-1][:4000],
                      "realImport 的 catch 应区分用户取消")
        self.assertIn("已取消等待导入结果。已上传的文件可能仍在服务器处理，稍后可在导入批次列表查看。",
                      self.html, "取消提示必须如实告知服务器可能仍在处理")

    def test_cancel_strings_registered_ten_languages(self):
        _require_row(self.export_locale, "取消导入", "import_export_locale.js")
        _require_row(self.export_locale, "取消仅停止本页等待；已上传的文件可能仍在服务器处理。", "import_export_locale.js")
        _require_row(self.export_locale,
                     "已取消等待导入结果。已上传的文件可能仍在服务器处理，稍后可在导入批次列表查看。",
                     "import_export_locale.js")
        _require_row(self.export_locale, "已等待 {0} 秒", "import_export_locale.js")

    def test_elapsed_timer_wired(self):
        self.assertIn("setInterval(importBusyTick, 1000)", self.html, "遮罩应每秒刷新已用时长")
        self.assertIn("clearInterval(importBusyTimer)", self.html, "遮罩关闭时应停止时长计时")


if __name__ == "__main__":
    unittest.main()
