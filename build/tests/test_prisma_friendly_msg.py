"""产品前端 prFriendlyMsg/prMsg 对 PRISMA 校验文案的重写集成回归钉（r6 评审反馈 1）。

评审反馈：prFriendlyMsg（index.html，不在本泳道允许改动段）按规则号 token
用**页面 db 列数值**（prVals，只读 db 字段）重写文案——其他方法列/提示态
的新消息若沿用 R4/R5/R13/R10 token 会被错误渲染（评审实测：「应该评估
18 篇，但你填了 18，数量一致」「初筛排除理由对不上：…差 30 篇」「应该
纳入 8 篇，但你填了 3，差 5 篇」，R10 丢失「可在备注中说明」）。

修复取「改用不会被重写的 rule」路径（评审给出的范围裁决之二，因
prFriendlyMsg/PR_MSG_RULES 段不在本泳道允许文件段）：后端改用带后缀
token R4B/R5B/R13W/R10W，前端 prFriendlyMsg 对其返回 null，消息原文
（中文半句）直显。

验证方式：node 子进程提取 index.html 的真实函数（prDiffPhrase/prZhOnly/
prFriendlyMsg/prMsg/prLocArg/prLocTpl + PR_MSG_RULES），stub zh-CN 语言
态与 prVals（按场景注入与表单一致的 db 数值），对**后端 validate 实际
产出**的文案字符串实跑：
- 新 token 消息：prFriendlyMsg 返回 null（不被重写），prMsg 输出含正确
  的列名/口径措辞，不含误导性改写；
- db 列 R4/R5（含 exp<0 分支）与 R13 拆分 error 的既有友好重写不变。
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
    validate,
)

REPO = Path(__file__).resolve().parents[2]
_FRONTEND = REPO / "custom_frontend" / "index.html"

_JS_FUNC_NAMES = ("prDiffPhrase", "prZhOnly", "prFriendlyMsg", "prMsg", "prLocArg", "prLocTpl")


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
    rules = re.search(r"\nconst PR_MSG_RULES = \[.*?\n\];", html, re.S)
    if not rules:
        raise AssertionError("index.html 中未找到 PR_MSG_RULES 定义")
    chunks.append(rules.group(0))
    return "\n".join(chunks)


def _v1_form(**overrides) -> PrismaFormData:
    """合法 v1 基线（与表单回归钉同基线）。"""
    defaults = dict(
        identified_rows_db=[IdentifiedRow("database", "WOS", 100)],
        duplicates_removed=20,
        records_screened=80,
        records_excluded_screening=60,
        screening_exclusion_reasons=[ReasonRow("scope", 60)],
        reports_sought_db=20,
        reports_not_retrieved_db=2,
        reports_assessed_db=18,
        exclusion_reasons_db=[ReasonRow("design", 10)],
        studies_included=6,
        reports_of_included_studies=8,
    )
    defaults.update(overrides)
    return PrismaFormData(**defaults)


def _v2_form(**overrides) -> PrismaFormData:
    """合法 v2 基线（与表单回归钉同基线）。"""
    defaults = dict(
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
        reports_of_included_studies=16,
    )
    defaults.update(overrides)
    return PrismaFormData(**defaults)


def _by_rule(issues, rule: str) -> str:
    matched = [i.text for i in issues if i.rule == rule]
    assert matched, f"未找到规则 {rule} 的消息"
    return matched[0]


def _official_template() -> PrismaFormData:
    return PrismaFormData(
        identified_rows_db=[
            IdentifiedRow("database", "Medline", 120),
            IdentifiedRow("database", "Embase", 30),
        ],
        duplicates_removed=20,
        removed_other=10,
        records_screened=120,
        records_excluded_screening=30,
        reports_sought_db=90,
        reports_not_retrieved_db=10,
        reports_assessed_db=80,
        exclusion_reasons_db=[ReasonRow("design", 30)],
        studies_included=40,
        reports_of_included_studies=50,
    )


class FriendlyMsgIntegrationTests(unittest.TestCase):
    """node 实拍：新 token 不被重写、db 列既有重写不变（r6 评审反馈 1）。"""

    @classmethod
    def setUpClass(cls):
        exe = _node_exe()
        if exe is None:
            raise unittest.SkipTest("本机无 node 可执行文件（行为级验证不可用）")
        cls.exe = exe
        html = _FRONTEND.read_text(encoding="utf-8")

        # ---- 后端 validate 实际产出的文案 + 与各表单 db 字段一致的 prVals ----
        scenarios = []

        # U6-P1 复现表单：other 10/0/5 → R4B error；页面 db 值与评审探针一致
        r4b = validate(_v2_form(reports_assessed_other=5, reports_of_included_studies=11))
        scenarios.append({
            "name": "R4B_other",
            "text": _by_rule(r4b.errors, "R4B"),
            "vals": {"prSought": 20, "prNotRetrieved": 2, "prAssessed": 18},
        })
        # r5#49 复现表单：other 理由 10 > 评估 5 → R5B error
        r5b = validate(_v2_form(
            exclusion_reasons_other=[ReasonRow("setting", 10)],
            reports_assessed_other=5,
            reports_of_included_studies=3,
        ))
        scenarios.append({
            "name": "R5B_other",
            "text": _by_rule(r5b.errors, "R5B"),
            "vals": {"prSought": 20, "prNotRetrieved": 2, "prAssessed": 18, "rsnFull": 10},
        })
        # U6-P9 官方模板仅报总数 → R13W warning
        r13w = validate(_official_template())
        assert r13w.ok, [i.text for i in r13w.errors]
        scenarios.append({
            "name": "R13W_total_only",
            "text": _by_rule(r13w.warnings, "R13W"),
            "vals": {"rsnScr": 0, "prExcScreen": 30},
        })
        # R10W 两分支 warning
        r10w_mis = validate(_v1_form(human_excluded_n=50, automation_excluded_n=5))
        r10w_half = validate(_v1_form(human_excluded_n=None, automation_excluded_n=5))
        scenarios.append({
            "name": "R10W_mismatch",
            "text": _by_rule(r10w_mis.warnings, "R10W"),
            "vals": {"prExcScreen": 60},
        })
        scenarios.append({
            "name": "R10W_half",
            "text": _by_rule(r10w_half.warnings, "R10W"),
            "vals": {"prExcScreen": 60},
        })
        # R10W 第三分支：使用了自动化工具但未提供拆分（r6 评审反馈·三轮
        # blocking——曾发裸 R10 token，zh-CN 被 prFriendlyMsg 静态重写）。
        r10w_adv = validate(_v1_form(duplicates_removed=10, automation_ineligible=10))
        scenarios.append({
            "name": "R10W_advice",
            "text": _by_rule(r10w_adv.warnings, "R10W"),
            "vals": {"prExcScreen": 60},
        })
        # R14 error（本就走 default=null，钉住不被重写）
        r14 = validate(_v1_form(
            exclusion_reasons_db=[ReasonRow("design", 18)],
            studies_included=5,
            reports_of_included_studies=0,
        ))
        scenarios.append({
            "name": "R14_studies",
            "text": _by_rule(r14.errors, "R14"),
            "vals": {"prIncluded": 5, "prIncludedReports": 0},
        })
        # ---- db 列既有友好重写不变 ----
        db_r4 = validate(_v1_form(reports_assessed_db=15))
        scenarios.append({
            "name": "R4_db",
            "text": _by_rule(db_r4.errors, "R4"),
            "vals": {"prSought": 20, "prNotRetrieved": 2, "prAssessed": 15},
        })
        db_r5 = validate(_v1_form(
            exclusion_reasons_db=[ReasonRow("design", 19)], reports_of_included_studies=8
        ))
        scenarios.append({
            "name": "R5_db_cap",
            "text": _by_rule([i for i in db_r5.errors if i.field == "exclusion_reasons_db"], "R5"),
            "vals": {"prAssessed": 18, "rsnFull": 19},
        })
        otf = _official_template()
        otf.screening_exclusion_reasons = [ReasonRow("scope", 20)]
        r13e = validate(otf)
        scenarios.append({
            "name": "R13_itemized_error",
            "text": _by_rule(r13e.errors, "R13"),
            "vals": {"rsnScr": 20, "prExcScreen": 30},
        })

        script = (
            _extract_js(html)
            + "\nconst RFLang = { get: () => 'zh-CN',"
            " t: (tpl, ...args) => args.reduce((s, a, i) => s.split('{' + i + '}').join(a), tpl) };\n"
            + "const window = { RFLang };\n"
            + "let PRV = {};\nfunction prVals() { return PRV; }\n"
            + "const SCENARIOS = " + json.dumps(scenarios) + ";\n"
            + "const out = SCENARIOS.map(s => { PRV = s.vals;"
            + " return { name: s.name, friendly: prFriendlyMsg(s.text), msg: prMsg(s.text) }; });\n"
            + "console.log(JSON.stringify(out));\n"
        )
        proc = subprocess.run(
            [cls.exe, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60
        )
        assert proc.returncode == 0, proc.stderr
        cls.results = {row["name"]: row for row in json.loads(proc.stdout.strip().splitlines()[-1])}

    # ---- 新 token：不被重写，原文直显（内容正确、不误导）----

    def test_r4b_other_column_not_rewritten(self):
        row = self.results["R4B_other"]
        self.assertIsNone(row["friendly"])
        self.assertIn("其他方法列", row["msg"])
        self.assertIn("(10)", row["msg"])
        self.assertIn("(5)", row["msg"])
        # 评审实测的误导性改写不得出现（db 值 18/数量一致）
        self.assertNotIn("数量一致", row["msg"])
        self.assertNotIn("应该评估 18", row["msg"])

    def test_r5b_other_column_not_rewritten(self):
        row = self.results["R5B_other"]
        self.assertIsNone(row["friendly"])
        self.assertIn("单列排除数不能超过该列评估数", row["msg"])
        # 评审实测「应该纳入 8 篇，但你填了 3，差 5 篇」不得出现
        self.assertNotIn("应该纳入", row["msg"])

    def test_r13w_total_only_not_rewritten(self):
        row = self.results["R13W_total_only"]
        self.assertIsNone(row["friendly"])
        self.assertIn("仅报单一总数", row["msg"])
        self.assertIn("官方模板允许不拆分", row["msg"])
        # 评审实测「初筛排除理由对不上：…差 30 篇」不得出现
        self.assertNotIn("对不上", row["msg"])
        self.assertNotIn("差 30 篇", row["msg"])

    def test_r10w_warnings_keep_note_hint(self):
        for name in ("R10W_mismatch", "R10W_half", "R10W_advice"):
            row = self.results[name]
            self.assertIsNone(row["friendly"], name)
            self.assertIn("可在备注中说明", row["msg"], name)
            self.assertNotIn("脚注拆分不符", row["msg"], name)

    def test_r10w_automation_advice_not_rewritten(self):
        # r6 评审反馈·三轮探针同法（automation_ineligible=10、human/auto
        # =None）：zh-CN 显示曾为「脚注拆分不符：…且两者之和应等于初筛
        # 排除总数」。现须原文直显且含「可在备注中说明」；注意 PR_MSG_
        # RULES 的「^使用了自动化工具（筛前移除 N 条）」前缀模式不锚定
        # 结尾，后端开头措辞已避开该模式，不会被旧模板重写。
        row = self.results["R10W_advice"]
        self.assertIsNone(row["friendly"])
        self.assertIn("自动化工具筛前移除了 10 条", row["msg"])
        self.assertIn("可在备注中说明", row["msg"])
        self.assertNotIn("两者之和应等于初筛排除总数", row["msg"])

    def test_r14_not_rewritten(self):
        row = self.results["R14_studies"]
        self.assertIsNone(row["friendly"])
        self.assertIn("每个纳入研究至少对应一份报告", row["msg"])

    # ---- db 列既有友好重写不变（回归守护）----

    def test_db_r4_friendly_rewrite_unchanged(self):
        row = self.results["R4_db"]
        self.assertIsNotNone(row["friendly"])
        self.assertIn("应该评估 18 篇", row["friendly"])
        self.assertIn("差 3 篇", row["friendly"])

    def test_db_r5_cap_friendly_rewrite_correct(self):
        row = self.results["R5_db_cap"]
        self.assertIsNotNone(row["friendly"])
        self.assertIn("超了 1 篇", row["friendly"])

    def test_r13_itemized_error_friendly_rewrite_unchanged(self):
        row = self.results["R13_itemized_error"]
        self.assertIsNotNone(row["friendly"])
        self.assertIn("各理由合计 20 篇", row["friendly"])
        self.assertIn("差 10 篇", row["friendly"])

    def test_static_guard_suffixed_tokens_present(self):
        # 静态守护：四个后缀 token 必须在后端源码中（改回裸 R4/R5/R13/R10
        # 会被上方行为级用例捕获——prFriendlyMsg 将不再返回 null）。
        source = (REPO / "coscreen" / "stats" / "prisma_form.py").read_text(encoding="utf-8")
        for token in ("R4B", "R5B", "R13W", "R10W"):
            self.assertIn(f'"{token}"', source)


if __name__ == "__main__":
    unittest.main()
