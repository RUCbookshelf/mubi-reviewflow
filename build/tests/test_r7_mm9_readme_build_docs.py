"""R7 MM9 nails (MM9-08 / MM9-19) for the four-language README build documentation.

复审口径（deepseek 独立复审，E:/mubi-reviewflow/out/external_reviews/
mm9_verify_g1.md §5 与 mm9_verify_g5.md §3）：

MM9-08（confirmed/medium）：build/build_windows.bat:241-242 把 `where node.exe`
      变成硬门禁，缺 Node 即 `goto :fail_no_node`（:345-346 文案
      "node.exe not found - required to run the JavaScript static validators
      (install Node.js or add it to PATH)"）中止构建。修前 README 全文件
      Node/npm/JavaScript 零提及（`grep -ic node README.md` → 0），四个语种
      （zh/en/ru/fr）的 Windows 构建行只写 Inno Setup/NSIS——照 README 操作
      的贡献者必在 [4/6] 步中止。修复：四语种构建行各补 Node.js 前置。
      钉子：四个语种的 `build_windows.bat` 行都必须点名 Node（撤掉任一语种
      的修复即红）。

MM9-19（confirmed/low）：修前 README 四处（中文目录表 136 行、English 253、
      Русский 582、Français 682）把 `tests/` 列为顶层目录，而仓库根没有
      tests/——套件实际在 build/tests/（同段命令 `python -m pytest
      build/tests -q` 本来就是对的）。修复：四处改为 `build/tests/`。
      钉子：全文件不得残留反引号 `tests/`，且四个语种各自必须指向
      `build/tests/`（撤掉任一语种的修复即红；其余六个短语种章节本就不含
      该目录表，全局断言同时兜住它们未来复犯）。
"""
from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# 四个完整语种章节（含构建表与目录表）的边界：起始二级标题 -> 结束二级标题。
# 其余六个语言章节（Español/日本語/Português/Deutsch/Српски/한국어）是简写
# 章节，既无 build_windows.bat 行也无目录表，由全局断言兜底。
LANGUAGE_SECTIONS = {
    "zh": ("## 中文", "## English"),
    "en": ("## English", "## Español"),
    "ru": ("## Русский", "## Français"),
    "fr": ("## Français", "## Mobile interface · 2.1.0"),
}


def _read_readme() -> str:
    return (REPO_ROOT / "README.md").read_text(encoding="utf-8")


def _section(readme: str, start_heading: str, end_heading: str) -> list:
    """返回 [start_heading 行, 下一个二级标题行) 之间的行列表。"""
    lines = readme.splitlines()
    start_idx = None
    for i, line in enumerate(lines):
        if start_idx is None:
            if line.strip() == start_heading:
                start_idx = i
        elif line.startswith("## "):
            assert line.strip() == end_heading, (
                f"README 章节边界漂移：{start_heading!r} 之后第一个二级标题是 "
                f"{line.strip()!r}，预期 {end_heading!r}——请同步更新本测试的章节表"
            )
            return lines[start_idx:i]
    assert start_idx is not None, f"README 中找不到章节标题 {start_heading!r}"
    return lines[start_idx:]  # 章节一直延伸到文件末尾


class Mm9ReadmeBuildDocsTests(unittest.TestCase):
    """MM9-08/19：README 四语种构建文档与仓库事实一致（静态守护钉子）。"""

    def test_mm9_08_windows_build_rows_must_name_node(self):
        """MM9-08：四语种 Windows 构建行必须提及 Node（修前全文 0 提及，
        而 build_windows.bat 缺 node.exe 即 :fail_no_node 中止）。"""
        bat = (REPO_ROOT / "build" / "build_windows.bat").read_text(
            encoding="utf-8", errors="replace"
        )
        # 事实锚定：Node 确为硬依赖（若脚本将来放宽为可跳过，本钉子应随之一并修订）
        self.assertIn("where node.exe", bat)
        self.assertIn("goto :fail_no_node", bat)
        self.assertIn(
            "required to run the JavaScript static validators", bat,
            "build_windows.bat 的 :fail_no_node 文案变了——README 措辞与钉子需复核",
        )

        readme = _read_readme()
        for lang, (start, end) in LANGUAGE_SECTIONS.items():
            with self.subTest(lang=lang):
                section = _section(readme, start, end)
                rows = [ln for ln in section if "build_windows.bat" in ln]
                self.assertEqual(
                    len(rows), 1,
                    f"{lang} 章节应恰有 1 行 Windows 构建说明，实得 {len(rows)} 行",
                )
                self.assertIn(
                    "node", rows[0].lower(),
                    f"{lang} 章节的 Windows 构建行未提及 Node.js——"
                    f"缺 Node 时 build_windows.bat 在 :fail_no_node 中止，"
                    f"README 必须告知该前置（MM9-08）",
                )

    def test_mm9_19_directory_listings_must_use_build_tests(self):
        """MM9-19：README 不得再列出不存在的顶层 `tests/`（修前四处：
        zh 136 / en 253 / ru 582 / fr 682）；四语种须指向 build/tests/。"""
        # 事实锚定：顶层无 tests/ 目录，套件在 build/tests/
        self.assertFalse(
            (REPO_ROOT / "tests").is_dir(),
            "仓库根出现了顶层 tests/ 目录——若为有意迁移，请同步修订 README 与本钉子",
        )
        self.assertTrue((REPO_ROOT / "build" / "tests").is_dir())

        readme = _read_readme()
        self.assertNotIn(
            "`tests/`", readme,
            "README 仍把 `tests/`（反引号路径）当作顶层目录引用——"
            "实际位置是 build/tests/（MM9-19）",
        )
        for lang, (start, end) in LANGUAGE_SECTIONS.items():
            with self.subTest(lang=lang):
                section = "\n".join(_section(readme, start, end))
                self.assertIn(
                    "`build/tests/`", section,
                    f"{lang} 章节的目录说明未指向 `build/tests/`（MM9-19）",
                )


if __name__ == "__main__":
    unittest.main()
