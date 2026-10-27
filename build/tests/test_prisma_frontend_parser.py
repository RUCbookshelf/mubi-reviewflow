"""前端 PRISMA DOT 解析器（index.html prParseDot）r5 复审 #53 回归钉。

nodeRe 曾用 [^\]]* 字符组截断节点属性段：标签含 ']'（来源名如
'CINAHL [Ebsco]'、自由文本 'see [Annex 1]'）时整条节点语句失配，该框从
预览与全部导出图（SVG/PNG/PDF 共用 prismaDotToSvg）中静默消失。

验证方式：
- 行为级：提取 index.html 内的解析函数原样在 node 子进程中实跑（本机无
  node 时跳过，静态守护仍然生效）；
- 静态守护：nodeRe 定义不得再含 [^\]] 截断式字符组。
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

from coscreen.stats.prisma_form import IdentifiedRow, PrismaFormData, ReasonRow, prisma_form_dot

REPO = Path(__file__).resolve().parents[2]
_FRONTEND = REPO / "custom_frontend" / "index.html"

_JS_FUNC_NAMES = ("prUnescapeDot", "prAttrMap", "prAttrBlock", "prParseDot")


def _legal_v1_form(**overrides) -> PrismaFormData:
    """合法 v1（new）基线表单（与表单回归钉同基线，仅用于生成真实 DOT）。"""
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


def _node_exe() -> str | None:
    for exe in (r"C:\Users\16073\AppData\Local\hermes\node\node.exe", shutil.which("node")):
        if exe and Path(exe).is_file():
            return str(exe)
    return None


def _extract_js_funcs(html: str) -> str:
    chunks = []
    for name in _JS_FUNC_NAMES:
        match = re.search(r"\nfunction " + re.escape(name) + r"\([^)]*\) \{.*?\n\}", html, re.S)
        if not match:
            raise AssertionError(f"index.html 中未找到函数 {name}")
        chunks.append(match.group(0))
    return "\n".join(chunks)


class FrontendDotParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _FRONTEND.read_text(encoding="utf-8")

    def test_node_regex_is_not_bracket_cutoff_static_guard(self):
        # 静态守护：nodeRe 不得再用 [^\]] 截断节点属性段（r5#53 根因）。
        match = re.search(r"const nodeRe = (.*);", self.html)
        self.assertIsNotNone(match, "index.html 未找到 nodeRe 定义")
        self.assertNotIn("[^\\]]", match.group(1))

    def _run_node_parse(self, dot: str) -> dict:
        exe = _node_exe()
        if exe is None:
            self.skipTest("本机无 node 可执行文件（r5#53 行为级验证跳过，静态守护已跑）")
        script = (
            _extract_js_funcs(self.html)
            + "\nconst prLocDotLabel = (s) => s;\n"
            + "const dot = " + json.dumps(dot) + ";\n"
            + "const p = prParseDot(dot);\n"
            + "console.log(JSON.stringify({nodes: Object.keys(p.nodes),"
            "identLabel: p.nodes.ident_db ? p.nodes.ident_db.lines.join('|') : null,"
            "removedNote: p.nodes.removed_db ? p.nodes.removed_db.lines.join('|') : null,"
            "footer: p.label}));\n"
        )
        proc = subprocess.run(
            [exe, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_bracket_in_source_name_keeps_node(self):
        # r5#53 同法：name='A]B (J) Ltd' / 'CINAHL [Ebsco]' 修复前 ident_db
        # 整条语句失配、该框从预览与全部导出图中消失。
        form = _legal_v1_form(
            identified_rows_db=[
                IdentifiedRow("database", "CINAHL [Ebsco]", 50),
                IdentifiedRow("database", "A]B (J) Ltd", 50),
            ],
        )
        out = self._run_node_parse(prisma_form_dot(form))
        self.assertIn("ident_db", out["nodes"])
        self.assertIn("CINAHL [Ebsco]", out["identLabel"])
        self.assertIn("A]B (J) Ltd", out["identLabel"])
        # 其余节点不受影响
        for node in ("screened_db", "sought_db", "assessed_db", "final_box"):
            self.assertIn(node, out["nodes"])

    def test_bracket_in_removed_note_keeps_node(self):
        # r5#53 同法：removed_other_note='see [Annex 1] for detail'（配合
        # removed_other>0）修复前 removed_db 缺失。
        form = _legal_v1_form(removed_other=5, removed_other_note="see [Annex 1] for detail")
        out = self._run_node_parse(prisma_form_dot(form))
        self.assertIn("removed_db", out["nodes"])
        self.assertIn("see [Annex 1] for detail", out["removedNote"])

    def test_bracket_in_footer_label_kept(self):
        # graph 级页脚（merge_note 含 ']'）不再因 [^\]] 截断丢失。
        form = _legal_v1_form()
        out = self._run_node_parse(prisma_form_dot(form, merge_note="see [Annex 2]"))
        self.assertIn("[Annex 2]", out["footer"])


if __name__ == "__main__":
    unittest.main()
