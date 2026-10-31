"""r8b FL2 钉子 —— #5/#11：CLI 口径文案与批×库近重复提示。

复审复现口径（out/external_reviews/r6_verify_dedup.md #5/#11，探针 `5`/`11`）：

- #5：同一 two.ris（K1/K2）连导两次，第 2 次打印『导入 2 条 | 精确重复 2 |
  进入筛选 0 条』+『新增 0 条，更新 2 条，重复标记 0 条』——「精确重复 2」
  把库内更新计成精确重复，与「重复标记 0」自相矛盾。
- #11：库内 K1（…lung cancer）导入 K2（…lung cancers，同作者、异键、双方
  无 DOI、token_set≈97）→ CLI 零模糊提示（修复前 combined 跑
  include_fuzzy=False），living_review 同输入 pending_candidates=1。

修复：「精确重复」只计被判为另一行同一文献、不单独进入筛选的本批记录
（批内合并/跨批打标记），沿用库内键的更新行归「更新库内」；模糊层改在
known+本批合并视图扫描（与 living_review/Web 同口径），展示仍只保留含
本批 key 的配对（FB4 不重复打印库内既有对）。撤修复必红。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _run_cli(args: list[str]):
    """真实子进程 `python -B -m coscreen ...`（复审同款探针形态，评审 R2）。"""
    import os

    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-B", "-m", "coscreen", *[str(a) for a in args]],
        cwd=str(Path(__file__).resolve().parents[2]), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


class CliExactDuplicateWordingTests(unittest.TestCase):
    """#5：纯重导不得把库内更新计成「精确重复」。撤修复必红。"""

    def test_pure_reimport_counts_updates_not_exact_duplicates(self):
        with tempfile.TemporaryDirectory(prefix="rf_r8b5_") as tmp:
            tmp = Path(tmp)
            ris = tmp / "two.ris"
            ris.write_text(
                "TY  - JOUR\nID  - K1\nTI  - Topic One\nAU  - Doe, Jane\n"
                "PY  - 2020\nER  -\n\n"
                "TY  - JOUR\nID  - K2\nTI  - Topic Two\nAU  - Roe, Rae\n"
                "PY  - 2021\nER  -\n\n", encoding="ascii")
            db = str(tmp / "t.db")
            p1 = _run_cli(["import", "--file", str(ris), "--db", db])
            self.assertEqual(p1.returncode, 0, p1.stderr)
            p2 = _run_cli(["import", "--file", str(ris), "--db", db])
            self.assertEqual(p2.returncode, 0, p2.stderr)
            self.assertIn("更新 2 条", p2.stdout)
            self.assertIn("重复标记 0 条", p2.stdout)
            # 修复前首行打印「精确重复 2」——与「重复标记 0」矛盾
            self.assertIn("精确重复 0", p2.stdout)
            self.assertIn("更新库内 2", p2.stdout)
            self.assertNotIn("精确重复 2", p2.stdout)

    def test_in_batch_duplicate_still_counted_as_exact(self):
        # 对照：批内真重复（同 DOI 两条）仍计「精确重复 1」——新口径不是
        # 把一切归 0。
        with tempfile.TemporaryDirectory(prefix="rf_r8b5b_") as tmp:
            tmp = Path(tmp)
            ris = tmp / "dup.ris"
            ris.write_text(
                "TY  - JOUR\nID  - K1\nTI  - Topic One\nAU  - Doe, Jane\n"
                "PY  - 2020\nDO  - 10.1/z\nER  -\n\n"
                "TY  - JOUR\nID  - K2\nTI  - Topic One\nAU  - Doe, Jane\n"
                "PY  - 2020\nDO  - 10.1/z\nER  -\n\n", encoding="ascii")
            db = str(tmp / "t.db")
            proc = _run_cli(["import", "--file", str(ris), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("精确重复 1", proc.stdout)
            self.assertIn("重复标记 1 条", proc.stdout)


class BatchLibraryFuzzyHintTests(unittest.TestCase):
    """#11：批×库近重复必须在 CLI 有提示。撤修复必红。"""

    def test_near_duplicate_against_library_prints_fuzzy_hint(self):
        with tempfile.TemporaryDirectory(prefix="rf_r8b11_") as tmp:
            tmp = Path(tmp)
            db = str(tmp / "t.db")
            lib = tmp / "lib.ris"
            lib.write_text(
                "TY  - JOUR\nID  - K1\nTI  - Deep learning for diagnosis of "
                "lung cancer\nAU  - Smith, John\nPY  - 2020\nER  -\n\n",
                encoding="ascii")
            p1 = _run_cli(["import", "--file", str(lib), "--db", db])
            self.assertEqual(p1.returncode, 0, p1.stderr)
            inc = tmp / "inc.ris"
            inc.write_text(
                "TY  - JOUR\nID  - K2\nTI  - Deep learning for diagnosis of "
                "lung cancers\nAU  - Smith, John\nPY  - 2020\nER  -\n\n",
                encoding="ascii")
            p2 = _run_cli(["import", "--file", str(inc), "--db", db])
            self.assertEqual(p2.returncode, 0, p2.stderr)
            self.assertIn("模糊候选 1 条", p2.stdout,
                          "批×库近重复必须有模糊提示（修复前 CLI 零提示）")
            self.assertIn("K2", p2.stdout)
            self.assertIn("K1", p2.stdout)

    def test_unrelated_import_prints_no_library_internal_candidates(self):
        # FB4 负例保持：库内既有相似标题对（无本批成员）不因合并视图扫描
        # 而重复打印。
        with tempfile.TemporaryDirectory(prefix="rf_r8b11b_") as tmp:
            tmp = Path(tmp)
            db = str(tmp / "t.db")
            lib = tmp / "lib.ris"
            lib.write_text(
                "TY  - JOUR\nID  - WM1\nTI  - Effects of drug X on mortality "
                "in women\nAU  - Wang, Wei\nPY  - 2005\nER  -\n\n"
                "TY  - JOUR\nID  - WM2\nTI  - Effects of drug X on mortality "
                "in men\nAU  - Wang, Wei\nPY  - 2006\nER  -\n\n", encoding="ascii")
            first = _run_cli(["import", "--file", str(lib), "--db", db])
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("模糊候选 1 条", first.stdout)
            other = tmp / "p1.ris"
            other.write_text(
                "TY  - JOUR\nID  - P1\nTI  - Unrelated paper on statins\n"
                "AU  - Doe, Jane\nPY  - 2020\nER  -\n\n", encoding="ascii")
            proc = _run_cli(["import", "--file", str(other), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("模糊候选", proc.stdout)
            self.assertNotIn("WM1", proc.stdout)
            self.assertNotIn("WM2", proc.stdout)


if __name__ == "__main__":
    unittest.main()
