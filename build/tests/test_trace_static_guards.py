"""2.1.4 静态守护：前端词条覆盖（防 F10 类复发）与 STALE 409 文案耦合。

只读源码文件做静态断言，不启动应用、不依赖数据环境：

1. 词条覆盖：custom_frontend/report_evidence.js 与 research_trace.js 中含中文
   表意字符的字符串字面量，必须已在 custom_frontend/*_locale.js 之一注册
   （RFLang 全局字典口径——T() 实际按全局字典解析，跨文件注册同样生效）。
   仅容忍下方逐项注明理由的豁免清单，且豁免清单被精确钉住：出现新的未注册
   界面文案（F10 类缺陷复发）或豁免项已注册未清理，都会使测试失败。
2. STALE 耦合：后端凡由 ``revision != body.expected_revision`` 触发（含与其他
   条件组合的单行守卫）的 409 文案，必须与前端 report_evidence.js 的 STALE
   正则匹配，保证 409 冲突自动刷新契约成立。当前四个触发点：保存 PUT
   （"Evidence changed in another window."）、个人撤销 /undo（"Evidence
   changed. Reload before undo."）、裁决/确认 /relations（"Confirmed
   relationships changed. Reload first."——注意这不是共享撤销路由）、共享撤销
   /relations/undo（版本过期守卫已从「无可撤销变更」组合条件中拆分，单独返回
   "Confirmed relationships changed. Reload before undo."；而 "No current
   relationship change is available to undo." 仅由无可撤销变更触发，不参与
   版本冲突，不得命中 STALE）。其余 409 文案不得命中 STALE，防止非版本
   冲突误触发自动刷新。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "custom_frontend"
BACKEND = ROOT / "custom_backend"

# report_evidence.js 的 CJK 字面量已全部注册（含 9 个 change() 轨迹 label，批次3
# 规格要求注册十语言）；本豁免清单必须保持为空——出现新的未注册界面文案（F10 类
# 缺陷复发）即失败。唯一不进入检查范围的是数据分隔符「；」：纯标点、不含表意字符。
REPORT_EVIDENCE_EXEMPT = set()

# 「日本語/简体中文」为语言自称名（各语言界面内一律用本名，不翻译）；
# 「选项/填空/单选/多选」4 个字段类型 UI 文案与报告关联载荷键名词条已在
# 2.1.4-r3 补注册（research_trace_locale.js），豁免清单只剩语言自称名。
RESEARCH_TRACE_EXEMPT = {
    "日本語",
    "简体中文",
}

_CJK = re.compile("[\u4e00-\u9fff]")
# 由 expected_revision 不匹配触发的 409：单行守卫形态，允许与其他条件组合
# （``if revision != body.expected_revision or ...: raise HTTPException(409, …)``）。
# 凡被该守卫覆盖的 409 文案都必须命中前端 STALE 正则——组合条件同样会在
# 版本过期时触发，不能豁免（共享撤销 /relations/undo 曾以组合形态漏出不可
# 命中文案，已拆分修正）。
_REVISION_CONFLICT_409 = re.compile(
    r"if\s+revision\s*!=\s*body\.expected_revision[^:\n]*:\s*"
    r"raise HTTPException\(409,\s*\"([^\"]+)\"\)"
)
_ANY_409 = re.compile(r"HTTPException\(409,\s*\"([^\"]+)\"\)")


def _js_string_literals(path):
    """抽取 JS 文件全部字符串字面量（跳过注释），返回其中含表意字符者。"""
    src = path.read_text(encoding="utf-8")
    out = []
    i = 0
    n = len(src)
    state = None
    buf = ""
    while i < n:
        c = src[i]
        if state is None:
            if c == "/" and i + 1 < n and src[i + 1] == "/":
                state = "line"
                i += 2
                continue
            if c == "/" and i + 1 < n and src[i + 1] == "*":
                state = "block"
                i += 2
                continue
            if c == '"' or c == "'" or c == "`":
                state = c
                buf = ""
                i += 1
                continue
            i += 1
            continue
        if state == "line":
            if c == "\n":
                state = None
            i += 1
            continue
        if state == "block":
            if c == "*" and i + 1 < n and src[i + 1] == "/":
                state = None
                i += 2
                continue
            i += 1
            continue
        # 字符串内部：转义序列按原样保留一个字符，避免提前闭合。
        if c == "\\" and i + 1 < n:
            buf += src[i + 1]
            i += 2
            continue
        if c == state:
            out.append(buf)
            state = None
            i += 1
            continue
        buf += c
        i += 1
    return {s.strip() for s in out if _CJK.search(s)}


def _locale_keys():
    """全部 *_locale.js 注册键的并集（第一列中文源串），对齐 RFLang 全局字典。"""
    keys = set()
    for path in sorted(FRONTEND.glob("*_locale.js")):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"\[\s*[\'\"]([^\'\"]+)[\'\"]", src):
            if _CJK.search(m.group(1)):
                keys.add(m.group(1))
    return keys


def _frontend_stale_pattern():
    src = (FRONTEND / "report_evidence.js").read_text(encoding="utf-8")
    m = re.search(r"const\s+STALE\s*=\s*/(.+?)/\s*;", src)
    if not m:
        raise AssertionError("report_evidence.js 未找到 const STALE=/…/ 正则")
    return re.compile(m.group(1))


def _backend_409_messages():
    src = (BACKEND / "report_evidence_api.py").read_text(encoding="utf-8")
    conflicts = _REVISION_CONFLICT_409.findall(src)
    everything = set(_ANY_409.findall(src))
    return conflicts, everything - set(conflicts)


class TraceLocaleCoverageTests(unittest.TestCase):
    """中文字面量 ⊆ 全局注册键，豁免清单精确钉住（多退少补均失败）。"""

    def _assert_covered(self, js_name, exempt):
        path = FRONTEND / js_name
        self.assertTrue(path.exists(), f"缺少源文件 {js_name}")
        keys = _locale_keys()
        uncovered = _js_string_literals(path) - keys
        self.assertEqual(
            uncovered,
            exempt,
            f"{js_name} 未注册词条集合与豁免清单不一致：\n"
            f"  新增未注册（应为空）: {sorted(uncovered - exempt)}\n"
            f"  已可移除的豁免（应清理）: {sorted(exempt - uncovered)}",
        )

    def test_report_evidence_js_literals_registered(self):
        self._assert_covered("report_evidence.js", REPORT_EVIDENCE_EXEMPT)

    def test_research_trace_js_literals_registered(self):
        self._assert_covered("research_trace.js", RESEARCH_TRACE_EXEMPT)


class BackendTraceLabelCouplingTests(unittest.TestCase):
    """后端轨迹 label 与前端词条/显式 label 集合的耦合。

    report_evidence_api.py 中 ``trace(c, ctx, actor, <label>, …)`` 写入载荷
    ``value`` 的中文 label 必须同时满足：① 已在某个 *_locale.js 注册十语言
    （否则非中文界面的轨迹摘要/详情输出原样中文——F10 类缺陷的后端侧入口）；
    ② 作为字符串字面量出现在 research_trace.js 的 RE_LABELS 显式集合里
    （label 判定刻意不用整本界面词典，纯编码值不误译；漏登记则该 label
    回退为原样输出）。集合精确钉住：后端新增/改名 label 而未同步前端时，
    本测试失败提醒。前端 change() 的 9 个 label 由 report_evidence.js 产生，
    已被其字面量覆盖测试钉住，不在此重复。
    """

    # 与 report_evidence_api.py 当前四个 trace() 调用点一一对应：
    # 撤销 PUT /undo、裁决确认/解除 /relations、共享撤销 /relations/undo。
    EXPECTED_BACKEND_LABELS = {
        "撤销报告关联与证据变更",
        "确认报告关系",
        "解除报告关系",
        "撤销报告关系裁决",
    }

    def _backend_trace_labels(self):
        src = (BACKEND / "report_evidence_api.py").read_text(encoding="utf-8")
        labels = set()
        for line in src.splitlines():
            if "trace(c, ctx" not in line:
                continue
            for literal in re.findall(r'"([^"]*)"', line):
                if _CJK.search(literal):
                    labels.add(literal)
        return labels

    def test_backend_labels_registered_and_pinned(self):
        labels = self._backend_trace_labels()
        self.assertEqual(
            labels, self.EXPECTED_BACKEND_LABELS,
            "report_evidence_api.py 的 trace() 中文 label 集合与钉住清单不一致——"
            "后端新增/改名 label 时须同步：注册十语言词条并把其加入 research_trace.js "
            "的 RE_LABELS 集合（未知 label 会以原样中文出现在非中文界面）",
        )
        keys = _locale_keys()
        unregistered = labels - keys
        self.assertEqual(unregistered, set(), f"后端轨迹 label 未注册词条: {sorted(unregistered)}")
        trace_src = (FRONTEND / "research_trace.js").read_text(encoding="utf-8")
        missing = {label for label in labels if f"'{label}'" not in trace_src}
        self.assertEqual(missing, set(), f"后端轨迹 label 未进入 research_trace.js 的 RE_LABELS: {sorted(missing)}")


class Stale409CouplingTests(unittest.TestCase):
    """后端版本冲突 409 文案与前端 STALE 正则的双向耦合。"""

    def test_revision_conflict_409_messages_match_frontend_stale(self):
        pattern = _frontend_stale_pattern()
        conflicts, _ = _backend_409_messages()
        # 四个触发点：保存 PUT / 个人撤销 /undo / 裁决确认 /relations /
        # 共享撤销 /relations/undo（版本过期守卫已拆分为独立 STALE 文案）。
        # 少于 4 条说明守卫被重构，钉子需跟进。
        self.assertGreaterEqual(
            len(conflicts), 4,
            f"expected_revision 触发的 409 文案少于 4 条（当前 {conflicts}），耦合钉子失效",
        )
        mismatched = [m for m in conflicts if not pattern.search(m)]
        self.assertEqual(
            mismatched, [],
            "以下 409 文案由 expected_revision 不匹配触发（含组合条件），但未命中前端 "
            "STALE 正则——共享撤销等路径的 409 冲突自动刷新契约会破裂。需所属泳道修正："
            "拆分组合条件使版本过期单独返回可命中文案，或对齐后端文案/扩展前端正则: "
            f"{mismatched}",
        )

    def test_other_409_messages_do_not_match_stale(self):
        pattern = _frontend_stale_pattern()
        _, others = _backend_409_messages()
        self.assertTrue(others, "未取到任何非版本冲突 409 文案，分类可能失效")
        for message in sorted(others):
            self.assertFalse(
                pattern.search(message),
                f"非版本冲突 409 文案命中 STALE 正则，会误触发自动刷新: {message!r}",
            )


class AppraiseDraftScopeGuardTests(unittest.TestCase):
    """G2 残余收窄的静态钉子：appraise 暂存只入栈当前 tool|source 条目。

    多差异键时取 ``keys[0]`` 会把非当前条目（本对话框早前切走的报告）当作
    当前草稿暂存，与 ``:100-118`` 已验收的单条恢复/保存口径不一致。钉住：
    ① trackDraft('appraise') 的脏判定只看当前条目 ``entryDiff(tool+'|'+source)``；
    ② 写回调只携带当前 ``drafts[tool+'|'+source]``；
    ③ 不得再出现 ``keys[0]`` / ``diffKeys`` 形态的回退挑选。
    """

    def test_appraise_track_draft_stages_current_entry_only(self):
        src = (FRONTEND / "report_evidence.js").read_text(encoding="utf-8")
        line = next(
            (ln for ln in src.splitlines() if "trackDraft(m,'appraise'" in ln),
            None,
        )
        self.assertIsNotNone(line, "未找到 appraise 的 trackDraft 注册行，钉子失效")
        self.assertIn("entryDiff(tool+'|'+source)", line, "脏判定必须只比较当前 tool|source 条目")
        self.assertIn("drafts[tool+'|'+source]", line, "写回调必须只入栈当前 tool|source 条目")
        self.assertNotIn("keys[0]", src, "多差异键回退挑选（keys[0]）不得回流")
        self.assertNotIn("diffKeys", src, "整表差异键枚举（diffKeys）不得回流")


if __name__ == "__main__":
    unittest.main()
