"""r8b FL7 PRISMA 前端回归钉（r6 #47/#48/#51/#52/#55/#54 前端面/#56）。

破坏即红（修复前 a080fde 上必失败），探针形态与 r6 复审一致
（E:/mubi-reviewflow/out/external_reviews/r6_verify_prisma.md）：
node 子进程提取 index.html 真实函数（prUnescapeDot/prParseDot/
prFriendlyMsg/prOtherScopeInPlay/prMsg/prZhOnly/prDiffPhrase +
PR_MSG_RULES），对**后端 validate / prisma_form_dot 实际产出**的文案与
DOT 实跑；静态守护（PR_COL / 词典十语言行）在 Python 侧断言。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen.stats.prisma_form import (
    IdentifiedRow,
    PrismaFormData,
    ReasonRow,
    prisma_form_dot,
    validate,
)

REPO = Path(__file__).resolve().parents[2]
_FRONTEND = REPO / "custom_frontend" / "index.html"

_JS_FUNC_NAMES = (
    "prDiffPhrase", "prZhOnly", "prUnescapeDot", "prAttrMap", "prAttrBlock",
    "prParseDot", "prFriendlyMsg", "prMsg", "prLocArg", "prLocTpl",
)


def _node_exe() -> str | None:
    for exe in (r"C:\Users\16073\AppData\Local\hermes\node\node.exe", shutil.which("node")):
        if exe and Path(exe).is_file():
            return str(exe)
    return None


def _extract_js(html: str) -> str:
    chunks = []
    for name in _JS_FUNC_NAMES:
        match = re.search(r"\nfunction " + re.escape(name) + r"\([^)]*\) \{.*?\n\}", html, re.S)
        if not match:
            raise AssertionError(f"index.html 中未找到函数 {name}")
        chunks.append(match.group(0))
    for const_name, tail in (("PR_MSG_RULES", r"\];"), ("PR_COL", r"\};")):
        match = re.search(r"\nconst " + const_name + r" = .*?" + tail, html, re.S)
        if not match:
            raise AssertionError(f"index.html 中未找到 {const_name} 定义")
        chunks.append(match.group(0))
    return "\n".join(chunks)


def _by_rule(issues, rule: str) -> str:
    matched = [i.text for i in issues if i.rule == rule]
    assert matched, f"未找到规则 {rule} 的消息"
    return matched[0]


def _v2_r5_form() -> PrismaFormData:
    """r6 #48 场景 A：db assessed 18 / db 理由 10；other assessed 10 /
    other 理由 2；target 14 → 后端 R5 expected=16（含 other 列）。"""
    return PrismaFormData(
        sources_scope="db_reg_other",
        identified_rows_db=[IdentifiedRow("database", "WOS", 100)],
        identified_rows_other=[IdentifiedRow("website", "GS", 10)],
        duplicates_removed=20,
        records_screened=80,
        records_excluded_screening=60,
        screening_exclusion_reasons=[ReasonRow("scope", 60)],
        reports_sought_db=20,
        reports_not_retrieved_db=2,
        reports_assessed_db=18,
        exclusion_reasons_db=[ReasonRow("design", 10)],
        reports_sought_other=10,
        reports_not_retrieved_other=0,
        reports_assessed_other=10,
        exclusion_reasons_other=[ReasonRow("setting", 2)],
        studies_included=10,
        reports_of_included_studies=14,
    )


def _v2_r8_form() -> PrismaFormData:
    """r6 #48 场景 B：无任何理由、target 14 → 后端 R8「排除 14 条」。"""
    form = _v2_r5_form()
    form.exclusion_reasons_db = []
    form.exclusion_reasons_other = []
    return form


def _v1_r5_form() -> PrismaFormData:
    """v1（无 other 列）R5：expected 8、target 5——db 值重写应保留。"""
    return PrismaFormData(
        identified_rows_db=[IdentifiedRow("database", "WOS", 100)],
        duplicates_removed=20,
        records_screened=80,
        records_excluded_screening=60,
        screening_exclusion_reasons=[ReasonRow("scope", 60)],
        reports_sought_db=20,
        reports_not_retrieved_db=2,
        reports_assessed_db=18,
        exclusion_reasons_db=[ReasonRow("design", 10)],
        studies_included=5,
        reports_of_included_studies=5,
    )


def _row13_form() -> PrismaFormData:
    """r6 #56 场景 B（页面可达）：理由已填、n=0，其余全勾稽。"""
    return PrismaFormData(
        identified_rows_db=[IdentifiedRow("database", "WOS", 20)],
        duplicates_removed=0,
        records_screened=20,
        records_excluded_screening=0,
        screening_exclusion_reasons=[ReasonRow("scope", 0)],
        reports_sought_db=20,
        reports_not_retrieved_db=0,
        reports_assessed_db=20,
        exclusion_reasons_db=[ReasonRow("design", 18)],
        studies_included=1,
        reports_of_included_studies=2,
    )


def _r10w_form() -> PrismaFormData:
    return PrismaFormData(
        identified_rows_db=[IdentifiedRow("database", "WOS", 100)],
        duplicates_removed=10,
        records_screened=80,
        records_excluded_screening=60,
        screening_exclusion_reasons=[ReasonRow("scope", 60)],
        reports_sought_db=20,
        reports_not_retrieved_db=2,
        reports_assessed_db=18,
        exclusion_reasons_db=[ReasonRow("design", 14)],
        studies_included=4,
        reports_of_included_studies=4,
        automation_ineligible=10,
    )


def _r14w_form() -> PrismaFormData:
    form = _r10w_form()
    form.automation_ineligible = 0
    form.duplicates_removed = 20
    form.studies_included = 6
    form.reports_of_included_studies = 4
    return form


class FrontendBehaviorTests(unittest.TestCase):
    """node 实拍：修复项行为 + 既有重写回归守护。"""

    @classmethod
    def setUpClass(cls):
        exe = _node_exe()
        if exe is None:
            raise unittest.SkipTest("本机无 node 可执行文件（行为级验证不可用）")
        html = _FRONTEND.read_text(encoding="utf-8")

        scenarios = []
        v2 = validate(_v2_r5_form())
        scenarios.append({
            "name": "R5_v2_not_rewritten",
            "text": _by_rule(v2.errors, "R5"),
            "lang": "zh-CN", "scope": "db_reg_other",
            "vals": {"prAssessed": 18, "rsnFull": 10, "prIncludedReports": 14},
        })
        v2b = validate(_v2_r8_form())
        scenarios.append({
            "name": "R8_v2_not_rewritten",
            "text": _by_rule(v2b.errors, "R8"),
            "lang": "zh-CN", "scope": "db_reg_other",
            "vals": {"prAssessed": 18, "rsnFull": 0, "prIncludedReports": 14},
        })
        v1 = validate(_v1_r5_form())
        scenarios.append({
            "name": "R5_v1_rewrite_unchanged",
            "text": _by_rule(v1.errors, "R5"),
            "lang": "zh-CN", "scope": "databases_only",
            "vals": {"prAssessed": 18, "rsnFull": 10, "prIncludedReports": 5},
        })
        row13 = validate(_row13_form())
        scenarios.append({
            "name": "R13_row_not_rewritten",
            "text": _by_rule(row13.errors, "R13"),
            "lang": "zh-CN", "scope": "databases_only",
            "vals": {"rsnScr": 0, "prExcScreen": 0},
        })
        total13 = validate(PrismaFormData(
            identified_rows_db=[IdentifiedRow("database", "Medline", 120)],
            duplicates_removed=20, records_screened=100,
            records_excluded_screening=30,
            screening_exclusion_reasons=[ReasonRow("scope", 20)],
            reports_sought_db=70, reports_not_retrieved_db=0,
            reports_assessed_db=70,
            exclusion_reasons_db=[ReasonRow("design", 30)],
            studies_included=40, reports_of_included_studies=40,
        ))
        scenarios.append({
            "name": "R13_total_rewrite_unchanged",
            "text": _by_rule(total13.errors, "R13"),
            "lang": "zh-CN", "scope": "databases_only",
            "vals": {"rsnScr": 20, "prExcScreen": 30},
        })
        r10w = validate(_r10w_form())
        assert r10w.ok, [i.text for i in r10w.errors]
        scenarios.append({
            "name": "R10W_advice_en_translated",
            "text": _by_rule(r10w.warnings, "R10W"),
            "lang": "en-US", "scope": "databases_only", "vals": {},
        })
        # r6 #48 三轮定稿：数字一律取自后端文案本身，页面值（v.prVals）
        # 无论怎么变都不影响重写结果。
        upd = _v2_r5_form()
        upd.review_mode = "updated"
        upd.previous_studies, upd.previous_reports = 6, 6
        upd.new_studies, upd.new_reports = 4, 3
        upd.total_studies, upd.total_reports = 10, 9
        upd.studies_included, upd.reports_of_included_studies = 10, 9
        v2upd = validate(upd)
        scenarios.append({
            "name": "R5_v2_updated",
            "text": _by_rule(v2upd.errors, "R5"),
            # updated 下页面 #prIncludedReports 被 auto 派生为 total=9（≠ 后端 target 3）
            "lang": "zh-CN", "scope": "databases_only",
            "vals": {"prAssessed": 18, "rsnFull": 10, "prIncludedReports": 9},
        })
        # 页面字段被手动改成与后端不一致（三轮：「v1 页面值不一致被吞掉」）
        scenarios.append({
            "name": "R5_v1_page_edited",
            "text": _by_rule(v1.errors, "R5"),
            "lang": "zh-CN", "scope": "databases_only",
            "vals": {"prAssessed": 30, "rsnFull": 3, "prIncludedReports": 5},
        })
        # 页面字段全缺失（提取式/未渲染环境）也必须照常给引导
        scenarios.append({
            "name": "R5_no_page_values",
            "text": _by_rule(v1.errors, "R5"),
            "lang": "zh-CN", "scope": "databases_only", "vals": {},
        })

        r14w = validate(_r14w_form())
        assert r14w.ok, [i.text for i in r14w.errors]
        scenarios.append({
            "name": "R14W_en_translated",
            "text": _by_rule(r14w.warnings, "R14W"),
            "lang": "en-US", "scope": "databases_only", "vals": {},
        })

        # r6 #52/#55 二轮：updated 模式还会发「综述纳入」(total) R14W 变体，
        # 此前未登记规则/词典 ⇒ 非中文裸回退。
        updw = _r14w_form()
        updw.review_mode = "updated"
        # previous 2/2 合法；new 6/4 与 total 8/6 都触发「研究>报告」
        updw.previous_studies, updw.previous_reports = 2, 2
        updw.new_studies, updw.new_reports = 6, 4   # R5 勾稽：评估18 − 理由14 = 4
        updw.total_studies, updw.total_reports = 8, 6
        updw.studies_included, updw.reports_of_included_studies = 8, 6
        r14wt = validate(updw)
        assert r14wt.ok, [i.text for i in r14wt.errors]
        total_warns = [i.text for i in r14wt.warnings
                       if i.text.startswith("R14W") and "review_stories" not in i.text
                       and "综述纳入研究数" in i.text]
        assert total_warns, [i.text for i in r14wt.warnings]
        scenarios.append({
            "name": "R14W_total_en",
            "text": total_warns[0],
            "lang": "en-US", "scope": "databases_only", "vals": {},
        })
        # zh 路径（二轮：「只测 en」是缺口）
        scenarios.append({
            "name": "R14W_total_zh",
            "text": total_warns[0],
            "lang": "zh-CN", "scope": "databases_only", "vals": {},
        })
        # r6 #52/#55 三轮：updated total 研究数为 0 而报告数 >0
        # （previous 0/0 + new 0/5 → total 0/5；评估18 − 理由13 = 5）。
        z = _r14w_form()
        z.review_mode = "updated"
        z.exclusion_reasons_db = [ReasonRow("design", 13)]
        z.previous_studies, z.previous_reports = 0, 0
        z.new_studies, z.new_reports = 0, 5
        z.total_studies, z.total_reports = 0, 5
        z.studies_included, z.reports_of_included_studies = 0, 5
        zt = validate(z)
        assert zt.ok, [i.text for i in zt.errors]
        zero_total = [i.text for i in zt.warnings
                      if "综述纳入研究数为 0" in i.text]
        assert zero_total, [i.text for i in zt.warnings]
        scenarios.append({
            "name": "R14_total_zero_en",
            "text": zero_total[0],
            "lang": "en-US", "scope": "databases_only", "vals": {},
        })

        # 报告数=0 仍 error（前端必须能翻出，不得裸中文）
        zero = _r14w_form()
        zero.reports_of_included_studies = 0
        zero.studies_included = 4
        err14 = validate(zero)
        scenarios.append({
            "name": "R14_zero_reports_en",
            "text": _by_rule(err14.errors, "R14"),
            "lang": "en-US", "scope": "databases_only", "vals": {},
        })

        # #51：真实 DOT（字面反斜杠来源名）
        dot = prisma_form_dot(PrismaFormData(
            identified_rows_db=[
                IdentifiedRow("database", "A\\nB", 100),
                IdentifiedRow("database", "C:\\reports", 50),
                IdentifiedRow("database", "D\\lE", 50),
            ],
            duplicates_removed=0,
            records_screened=200,
            records_excluded_screening=0,
            reports_sought_db=200,
            reports_not_retrieved_db=0,
            reports_assessed_db=200,
            exclusion_reasons_db=[ReasonRow("design", 200)],
            studies_included=0,
            reports_of_included_studies=0,
        ))

        script = (
            _extract_js(html)
            + "\nlet LANG = 'zh-CN';\n"
            + "let DOC = null;\n"
            + "globalThis.document = { getElementById(id){"
            "  return (id === 'prScope' && DOC) ? { value: DOC } : null; } };\n"
            + "const RFLang = { get: () => LANG,"
            " t: (tpl, ...args) => {"
            "  const s = args.reduce((acc, a, i) => acc.split('{' + i + '}').join(a), tpl);"
            "  return LANG === 'zh-CN' ? s : 'TR[' + s + ']'; } };\n"
            + "const window = { RFLang };\n"
            + "let PRV = {};\nfunction prVals() { return PRV; }\n"
            + "const prLocDotLabel = (s) => s;\n"
            + "const SCENARIOS = " + json.dumps(scenarios) + ";\n"
            + "const out = SCENARIOS.map(s => { LANG = s.lang; DOC = s.scope; PRV = s.vals;"
            + " return { name: s.name, friendly: prFriendlyMsg(s.text), msg: prMsg(s.text) }; });\n"
            + "const parsed = prParseDot(" + json.dumps(dot) + ");\n"
            + "const synthetic = prParseDot('digraph P {\\n  x [label=\"D\\\\\\\\lE\"];\\n}');\n"
            + "console.log(JSON.stringify({ scenarios: out,"
            " ident_lines: parsed.nodes.ident_db.lines,"
            " ident_left: parsed.nodes.ident_db.left,"
            " synth_lines: synthetic.nodes.x.lines,"
            " synth_left: synthetic.nodes.x.left }));\n"
        )
        proc = subprocess.run(
            [exe, "-e", script], capture_output=True, text=True,
            encoding="utf-8", timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        cls.results = {row["name"]: row for row in payload["scenarios"]}
        cls.ident_lines = payload["ident_lines"]
        cls.ident_left = payload["ident_left"]
        cls.synth_lines = payload["synth_lines"]
        cls.synth_left = payload["synth_left"]

    # ---- #48：v2 在页时 R5/R8 不按 db-only prVals 重写 ----

    def test_r5_v2_numbers_come_from_backend_text(self):
        """r6 #48：v2（全列口径）下重写必须用后端数字 16/28/12，
        绝不能是 db-only 的 8/18/10（修复前实测「应该纳入 8 篇…多填了 6 篇」）。"""
        row = self.results["R5_v2_not_rewritten"]
        f = row["friendly"]
        self.assertIsNotNone(f)
        self.assertIn("应该纳入 16 篇", f)
        self.assertIn("评估了 28 篇", f)
        self.assertNotIn("应该纳入 8 篇", f)
        self.assertNotIn("多填了 6", f)

    def test_r8_v2_uses_backend_excluded_count(self):
        """r6 #48：R8 的「共排除 N」取后端 14，不是 db-only 的 4。"""
        row = self.results["R8_v2_not_rewritten"]
        f = row["friendly"]
        self.assertIsNotNone(f)
        self.assertIn("共排除 14 篇报告", f)
        self.assertNotIn("共排除 4 篇报告", f)

    def test_r5_v1_rewrite_unchanged(self):
        """回归守护：v1（无 other 列）db 值重写保留。"""
        row = self.results["R5_v1_rewrite_unchanged"]
        self.assertIsNotNone(row["friendly"])
        self.assertIn("应该纳入 8", row["friendly"])

    # ---- #56：行级 R13 不重写；总额 R13 重写保留 ----

    def test_r13_row_level_not_rewritten(self):
        row = self.results["R13_row_not_rewritten"]
        self.assertIsNone(row["friendly"])
        self.assertIn("n 需 ≥ 1", row["msg"])
        # 修复前被改写成「初筛排除理由对不上：各理由合计 0 篇…数量一致」
        self.assertNotIn("对不上", row["msg"])
        self.assertNotIn("各理由合计", row["msg"])

    def test_r13_total_rewrite_unchanged(self):
        row = self.results["R13_total_rewrite_unchanged"]
        self.assertIsNotNone(row["friendly"])
        self.assertIn("各理由合计 20 篇", row["friendly"])

    # ---- #52/#55：R10W 第三提醒规则可命中实际文案并翻出含尾注模板 ----

    def test_r10w_advice_rule_matches_backend_text(self):
        row = self.results["R10W_advice_en_translated"]
        # 修复前：尾锚 $ 死规则 → en 回退裸中文（无 TR 标记）
        self.assertTrue(row["msg"].startswith("TR["), row["msg"])
        self.assertIn("（可在备注中说明）", row["msg"])

    # ---- #54 前端面：R14W 文案可翻出（规则 + 模板已登记） ----

    def test_r14w_rule_matches_backend_text(self):
        row = self.results["R14W_en_translated"]
        self.assertTrue(row["msg"].startswith("TR["), row["msg"])
        self.assertIn("multi-study report", row["msg"])

    def test_r5_updated_uses_backend_numbers_not_page_total(self):
        """r6 #48 三轮：updated 模式下「你填了 X」必须用后端 new_reports，
        不得用页面自动派生的 total（旧实现把总额当「你填了」，正是 #48 要消灭
        的误导形式）。"""
        row = self.results["R5_v2_updated"]
        self.assertIsNotNone(row["friendly"])
        self.assertIn("新纳入报告数不对", row["friendly"])
        # 后端 new_reports = 3（不是页面 total 9）
        self.assertRegex(row["friendly"], r"但你填了 3")
        self.assertNotIn("但你填了 9", row["friendly"])

    def test_r5_page_values_edited_still_guides(self):
        """r6 #48 三轮（R2 向）：页面字段与后端数字不一致时仍必须给引导
        （上一版按一致性门控会退回后端原文，等于吞掉既有功能）。"""
        row = self.results["R5_v1_page_edited"]
        self.assertIsNotNone(row["friendly"])
        self.assertIn("也应该纳入".replace("也", ""), row["friendly"])
        self.assertIn("差 3 篇", row["friendly"])   # 8 − 5

    def test_r5_works_without_page_values(self):
        row = self.results["R5_no_page_values"]
        self.assertIsNotNone(row["friendly"])
        self.assertIn("应该纳入 8 篇", row["friendly"])

    def test_r14w_total_variant_translated(self):
        for name in ("R14W_total_en", "R14W_total_zh"):
            row = self.results[name]
            self.assertTrue(row["msg"].startswith("TR[") or name.endswith("zh"),
                            (name, row["msg"]))
            self.assertIn("multi-study report", row["msg"], name)
            self.assertIn("综述纳入研究数", row["msg"], name)

    def test_r14_total_zero_translated(self):
        """r6 #52 三轮：total 研究数为 0 的变体此前只有词典行、无规则 ⇒
        非中文裸回退（伪守护）。"""
        row = self.results["R14_total_zero_en"]
        self.assertTrue(row["msg"].startswith("TR["), row["msg"])
        self.assertIn("综述纳入研究数为 0", row["msg"])

    def test_r14_zero_reports_translated(self):
        row = self.results["R14_zero_reports_en"]
        self.assertTrue(row["msg"].startswith("TR["), row["msg"])
        self.assertIn("每个纳入研究至少对应一份报告", row["msg"])

    # ---- #51：字面反斜杠先折叠后解码 ----

    def test_literal_backslash_n_not_split(self):
        # 修复前：'A\nB'（字面）被拆成 '· A\' + 'B（数据库…' 两行
        joined = [l for l in self.ident_lines if "A" in l and "B（数据库" in l]
        self.assertTrue(joined, self.ident_lines)
        self.assertFalse(
            [l for l in self.ident_lines if l.startswith("B（数据库")],
            self.ident_lines)

    def test_literal_backslash_windows_path_intact(self):
        self.assertTrue(
            [l for l in self.ident_lines if "C:\\reports" in l],
            self.ident_lines)
        self.assertFalse(
            [l for l in self.ident_lines if l.startswith("eports")],
            self.ident_lines)

    def test_literal_backslash_l_not_marked_left(self):
        # 修复前：'D\lE' 的 '\\l' 误置 left=true 并拆行
        self.assertFalse(self.synth_left)
        self.assertEqual(self.synth_lines, ["D\\lE"])
        # 真实 \l 行标记的节点 left 判定不受影响（ident_db 用 \l 分行）
        self.assertTrue(self.ident_left)


class FrontendStaticGuardTests(unittest.TestCase):
    """静态守护：#47 PR_COL 键 + #55/#54 词典十语言行 + 规则模板。"""

    def test_pr_col_has_notret_other_in_other_side_column(self):
        html = _FRONTEND.read_text(encoding="utf-8")
        match = re.search(r"\nconst PR_COL = \{.*?\n\};", html, re.S)
        self.assertIsNotNone(match, "PR_COL 定义缺失")
        block = match.group(0)
        m = re.search(r"\bnotret_other\s*:\s*(\d+)", block)
        self.assertIsNotNone(m, "PR_COL 缺 notret_other 键（修复前回退列 2）")
        self.assertEqual(m.group(1), "5",
                         "notret_other 应属其他方法列侧框列 5")

    _LONG_TEMPLATES = [
        "综述纳入研究数({0}) 多于综述纳入报告数({1})：官方模板允许一份报告含多项研究（multi-study report），如确为该形态可在备注中说明",
        "综述纳入研究数({0}) 多于综述纳入报告数({1})：每个纳入研究至少对应一份报告",
        "综述纳入研究数为 0 但综述纳入报告数为 {0}：请确认口径（官方末框为『纳入研究的报告』）",
        "自动化工具筛前移除了 {0} 条，但未提供脚注**拆分——建议补填人工/自动化各排除多少（可在备注中说明）",
        "纳入综述研究数({0}) 多于纳入综述报告数({1})：官方模板允许一份报告含多项研究（multi-study report），如确为该形态可在备注中说明",
        "上一版综述纳入研究数({0}) 多于上一版综述纳入报告数({1})：官方模板允许一份报告含多项研究（multi-study report），如确为该形态可在备注中说明",
        "新纳入研究数({0}) 多于新纳入报告数({1})：官方模板允许一份报告含多项研究（multi-study report），如确为该形态可在备注中说明",
    ]

    def test_long_templates_registered_in_ten_language_dictionary(self):
        html = _FRONTEND.read_text(encoding="utf-8")
        for tpl in self._LONG_TEMPLATES:
            with self.subTest(template=tpl[:24]):
                # PR_MSG_RULES 模板（第二元素）
                self.assertIn('"' + tpl + '"', html)
                # 词典行存在且十语言列齐全（zh + 9 个非空译文）
                needle = '["' + tpl + '","'
                self.assertIn(needle, html, "词典行缺失")
                line = next(l for l in html.splitlines() if needle in l)
                fields = line.strip().rstrip(",").split('","')
                self.assertEqual(len(fields), 10, "词典行必须含 zh+9 语言共 10 列")
                self.assertTrue(all(len(f.strip('[]"')) >= 2 for f in fields),
                                "任一语言列不得为空")

    def test_layout_alias_covers_notret_other(self):
        """r6 #47 二轮：alias 表（其他方法列与 db 列同行对齐）也必须含
        notret_other —— 只补 PR_COL 会让该侧框与 notret_db 不同行。"""
        html = _FRONTEND.read_text(encoding="utf-8")
        m = re.search(r"const alias = \{([^}]*)\};", html)
        self.assertIsNotNone(m, "其他方法列 alias 表缺失")
        self.assertRegex(m.group(1), r"notret_other\s*:\s*'notret_db'")

    def test_every_long_template_has_a_matching_rule(self):
        """r6 #52 三轮：静态钉必须断言「规则能命中」，而不只是「词典里有行」
        ——上一版只查词典行，导致 total 研究数为 0 的模板缺规则却永不红。"""
        html = _FRONTEND.read_text(encoding="utf-8")
        rules_block = re.search(
            r"\nconst PR_MSG_RULES = \[(.*?)\n\];", html, re.S).group(1)
        rule_tpls = set(re.findall(r'^\s*"(.+?)"\],?$', rules_block, re.M))
        for tpl in self._LONG_TEMPLATES:
            with self.subTest(template=tpl[:24]):
                self.assertIn(tpl, rule_tpls,
                              "模板只在词典、缺 PR_MSG_RULES 规则（假守护）")

    def test_r10w_rule_is_not_tail_anchored(self):
        html = _FRONTEND.read_text(encoding="utf-8")
        match = re.search(
            r"\[/\^自动化工具筛前移除了 \(-\?\\d\+\) 条，但未提供脚注\\\*\\\*拆分[\s\S]*?\],", html)
        self.assertIsNotNone(match, "R10W 第三提醒规则缺失")
        self.assertNotIn("拆分$", match.group(0),
                         "规则不得再以「拆分$」死锚定（r6 #52）")
        self.assertIn("可在备注中说明", match.group(0))


if __name__ == "__main__":
    unittest.main()
