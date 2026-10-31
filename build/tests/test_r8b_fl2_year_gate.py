"""r8b FL2 钉子 —— #8/#15：_key_merge_confirmed 年份门（exact_key 路径）。

复审复现口径（out/external_reviews/r6_verify_dedup.md #8/#15，探针 `8/15`）：
库内 K1=Trial of X/Smith, John/PY2020（已 save_decision include）；文件 B
同 K1/同题/同作者/PY2021 → 修复前第 2 次 CLI『导入 1 条 | 精确重复 1 |
进入筛选 0 | 新增 0 更新 1』、0 警告，DB 唯一行 year 改 2021 且静默继承
include；同文件对照解析器降级 auto 键并警告（口径分叉）。

修复：_key_merge_confirmed 增加 years_compatible（双方年份均明确且不同
→ 不合并，改 exact_key_conflict 候选 + 键消歧新行），并同步 docstring
（_match_legacy_key 的「年份不参与裁决」漂移表述）。撤修复必红。
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from io import StringIO
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import db as db_mod
from coscreen.dedup import _key_merge_confirmed
from coscreen.models import Article


def _run_cli(args: list[str]):
    """真实子进程 `python -B -m coscreen ...`（复审同款探针形态，评审 R2）。"""
    import os

    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-B", "-m", "coscreen", *[str(a) for a in args]],
        cwd=str(Path(__file__).resolve().parents[2]), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


class KeyMergeYearGateTests(unittest.TestCase):
    def test_same_year_and_missing_year_still_merge(self):
        # R2 防线：年份一致 / 单侧缺年（2.1.3 旧行常见）不得因新门被拆。
        a = Article(zotero_key="K1", title="Trial of X", authors="Smith, John",
                    year=2020)
        self.assertTrue(_key_merge_confirmed(
            a, Article(zotero_key="K1", title="Trial of X",
                       authors="Smith, John", year=2020)))
        self.assertTrue(_key_merge_confirmed(
            a, Article(zotero_key="K1", title="Trial of X",
                       authors="Smith, John", year=None)))
        self.assertTrue(_key_merge_confirmed(
            a, Article(zotero_key="K1", title="Trial of X",
                       authors="Smith, John")))

    def test_explicit_different_years_not_merged(self):
        a = Article(zotero_key="K1", title="Trial of X", authors="Smith, John",
                    year=2020)
        b = Article(zotero_key="K1", title="Trial of X", authors="Smith, John",
                    year=2021)
        self.assertFalse(_key_merge_confirmed(a, b))

    def test_equal_doi_still_merges_across_year_gap(self):
        # DOI 是最强机器证据，年份门不得越过 DOI 裁决。
        a = Article(zotero_key="K1", title="Trial of X", authors="Smith, John",
                    year=2020, doi="10.1/z")
        b = Article(zotero_key="K1", title="Trial of X", authors="Smith, John",
                    year=2021, doi="10.1/z")
        self.assertTrue(_key_merge_confirmed(a, b))


class CrossFileYearOverwriteTests(unittest.TestCase):
    """#15：跨文件同键异年 → 新行 + 键冲突提示，不静默继承决策。撤修复必红。"""

    def test_cross_file_same_key_different_year_keeps_both_rows(self):
        with tempfile.TemporaryDirectory(prefix="rf_r8b8_") as tmp:
            tmp = Path(tmp)
            db = str(tmp / "t.db")
            a = tmp / "a.ris"
            a.write_text(
                "TY  - JOUR\nID  - K1\nTI  - Trial of X\nAU  - Smith, John\n"
                "PY  - 2020\nER  -\n\n", encoding="ascii")
            p1 = _run_cli(["import", "--file", str(a), "--db", db])
            self.assertEqual(p1.returncode, 0, p1.stderr)
            db_mod.save_decision(db, "K1", "include")
            b = tmp / "b.ris"
            b.write_text(
                "TY  - JOUR\nID  - K1\nTI  - Trial of X\nAU  - Smith, John\n"
                "PY  - 2021\nER  -\n\n", encoding="ascii")
            p2 = _run_cli(["import", "--file", str(b), "--db", db])
            self.assertEqual(p2.returncode, 0, p2.stderr)
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT zotero_key, year FROM articles ORDER BY year").fetchall()
                decisions = conn.execute(
                    "SELECT zotero_key, decision FROM decisions").fetchall()
            # 修复前唯一行 year=2021（2020 行被覆盖、决策被继承）
            self.assertEqual(len(rows), 2, f"同键同题同作者异年应两行: {rows}")
            self.assertEqual([r[1] for r in rows], [2020, 2021])
            # 2021 新行进入筛选（键消歧），不是静默更新
            self.assertIn("键消歧 1 处", p2.stdout)
            self.assertIn("键冲突候选 1 条", p2.stdout)
            # 2020 行的 include 决策保留在 2020 行键上
            self.assertEqual(decisions, [("K1", "include")])


if __name__ == "__main__":
    unittest.main()
