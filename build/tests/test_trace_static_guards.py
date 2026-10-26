"""2.1.4 静态守护：前端词条覆盖（防 F10 类复发）与 STALE 409 文案耦合。

只读源码文件做静态断言，不启动应用、不依赖数据环境：

1. 词条覆盖：custom_frontend/report_evidence.js 与 research_trace.js 中含中文
   表意字符的字符串字面量，必须已在 custom_frontend/*_locale.js 之一注册
   （RFLang 全局字典口径——T() 实际按全局字典解析，跨文件注册同样生效）。
   仅容忍下方逐项注明理由的豁免清单，且豁免清单被精确钉住：出现新的未注册
   界面文案（F10 类缺陷复发）或豁免项已注册未清理，都会使测试失败。
2. STALE 耦合：后端「版本冲突」类 409 文案（形如
   ``if revision != body.expected_revision: raise HTTPException(409, ...)``,
   含共享撤销 /relations/undo 的 "Confirmed relationships changed"）必须与前端
   report_evidence.js 的 STALE 正则匹配，保证 409 冲突自动刷新契约成立；
   其余 409 文案不得命中 STALE，防止非版本冲突误触发自动刷新。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "custom_frontend"
BACKEND = ROOT / "custom_backend"

# 仅豁免发往后端、作为轨迹事件载荷 value 键存储并按原文展示的操作 label
# （已在 docs/release-2.1.4.md 验证记录披露「9 个后端轨迹 label 与 1 个数据
# 分隔符」；该分隔符为标点、不含表意字符，不进入本检查范围）。
REPORT_EVIDENCE_EXEMPT = {
    "保存个人关联线索",
    "保存个人定性证据草稿",
    "保存定性方法学评价草稿",
    "保存定性综合置信度草稿",
    "保存定性问题草稿",
    "保存方法工作草稿",
    "保存综述路径",
    "核对证据共同出处",
    "解除个人关联线索",
}

# 「日本語/简体中文」为语言自称名（各语言界面内一律用本名，不翻译）；
# 「选项/填空/单选/多选」为 2.1.3 遗留的 4 个未注册字段类型 UI 文案，属已知
# 缺口——前端补注册词条后应同步从本清单移除，精确集合断言会提醒这一步。
RESEARCH_TRACE_EXEMPT = {
    "日本語",
    "简体中文",
    "选项",
    "填空",
    "单选",
    "多选",
}

_CJK = re.compile("[\u4e00-\u9fff]")
# 行内版本冲突守卫 → 409 的固定形态（本仓两种后端基线均为此单行写法）。
_REVISION_CONFLICT_409 = re.compile(
    r"if\s+revision\s*!=\s*body\.expected_revision:\s*"
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


class Stale409CouplingTests(unittest.TestCase):
    """后端版本冲突 409 文案与前端 STALE 正则的双向耦合。"""

    def test_revision_conflict_409_messages_match_frontend_stale(self):
        pattern = _frontend_stale_pattern()
        conflicts, _ = _backend_409_messages()
        # 三个冲突点：保存 / 个人撤销 / 共享撤销（relations/undo）。
        self.assertGreaterEqual(
            len(conflicts), 3,
            f"版本冲突 409 文案少于 3 条（当前 {conflicts}），耦合钉子失效",
        )
        for message in conflicts:
            self.assertTrue(
                pattern.search(message),
                f"版本冲突 409 文案未命中前端 STALE 正则，自动刷新契约会破裂: {message!r}",
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


if __name__ == "__main__":
    unittest.main()
