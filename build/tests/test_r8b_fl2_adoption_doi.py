"""r8b FL2 钉子 —— #2/#3：adoption 的 DOI 跨行归属检查与内容层候选回退。

复审复现口径（out/external_reviews/r6_verify_dedup.md #2/#3，探针 `2`/`3`）：

- #2：K1=Same Topic/Smith/2020 无 DOI、K2=Other Topic/Jones/2021 持
  10.1/x；导入无 Key 的 Same Topic/Smith/2020 + DOI 10.1/x → 修复前 CLI
  把 DOI 写入 K1，库内两行同 DOI、is_duplicate_of 均 NULL、零警告
  （Web prepare 同记录 outcome=exact_doi/canonical=K2，双路径分叉）。
- #3：库内同内容两行（K_a 持 10.1/a、K_b 无 DOI）；导入同内容 + DOI
  10.1/c → 修复前 content_index 首匹配全局桶，K_a DOI 冲突被跳过后不再
  试 K_b，新增第 3 行且零候选/警告。

修复：adoption 前查入 DOI 的库内归属（DOI 属另一行时不沿用内容层目标
并显式警告，记录交由 DOI 相等的 exact 证据链到归属行——与 Web 同一身份
选择）；内容层逐候选尝试，首个 DOI 冲突的库内行跳过后继续试同内容键的
其他行。撤修复必红。
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


def _run_cli(args: list[str]):
    """真实子进程 `python -B -m coscreen ...`（复审同款探针形态，评审 R2）。"""
    import os

    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-B", "-m", "coscreen", *[str(a) for a in args]],
        cwd=str(Path(__file__).resolve().parents[2]), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _rows(db: str):
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute(
            "SELECT zotero_key, title, doi, is_duplicate_of FROM articles"
            " ORDER BY zotero_key").fetchall()


class AdoptionDoiOwnershipTests(unittest.TestCase):
    """#2：入 DOI 已属另一库内行时不沿用内容层命中的目标。撤修复必红。"""

    def test_incoming_doi_owned_by_other_row_not_written_into_k1(self):
        with tempfile.TemporaryDirectory(prefix="rf_r8b2_") as tmp:
            tmp = Path(tmp)
            db = str(tmp / "t.db")
            lib = tmp / "lib.ris"
            lib.write_text(
                "TY  - JOUR\nID  - K1\nTI  - Same Topic\nAU  - Smith, John\n"
                "PY  - 2020\nER  -\n\n"
                "TY  - JOUR\nID  - K2\nTI  - Other Topic\nAU  - Jones, Jane\n"
                "PY  - 2021\nDO  - 10.1/x\nER  -\n\n", encoding="ascii")
            p1 = _run_cli(["import", "--file", str(lib), "--db", db])
            self.assertEqual(p1.returncode, 0, p1.stderr)
            inc = tmp / "inc.ris"
            inc.write_text(
                "TY  - JOUR\nTI  - Same Topic\nAU  - Smith, John\nPY  - 2020\n"
                "DO  - 10.1/x\nER  -\n\n", encoding="ascii")
            p2 = _run_cli(["import", "--file", str(inc), "--db", db])
            self.assertEqual(p2.returncode, 0, p2.stderr)
            rows = _rows(db)
            # 不得出现「两行同 DOI 且零警告」：K1 不被写入 10.1/x
            k1 = [r for r in rows if r[0] == "K1"][0]
            self.assertEqual(k1[2], "", "入批 DOI 已属 K2 时不得写入 K1")
            dois = [r[2] for r in rows if r[2]]
            self.assertEqual(len(dois), len(set(dois)), "库内不得有两行同 DOI")
            # R3：归属冲突必须显式警告（修复前 stderr 为空）
            self.assertIn("已属于库内另一行 K2", p2.stderr)
            self.assertIn("K1", p2.stderr)


class SourceKeyDoiOwnershipTests(unittest.TestCase):
    """#2 来源键路径（评审 R2·F4 high）：带 ID 的记录同样不得静默造出两行同 DOI。

    复审第 1 轮的守卫只写在生成键身份匹配里（`_is_generated_key_article`
    为真才走），带来源键的记录走键级合并，r6 #2 的「两行同 DOI 且零警告」
    在更常见的带键路径上仍复现。现按最终键域统一检查并显式警告（不改变
    合并/更新行为——R2 不以拒绝替代提示）。撤守卫必红（stderr 空）。
    """

    def _run(self, tmp: Path):
        db = str(tmp / "t.db")
        lib = tmp / "lib.ris"
        lib.write_text(
            "TY  - JOUR\nID  - K1\nTI  - Same Topic\nAU  - Smith, John\n"
            "PY  - 2020\nER  -\n\n"
            "TY  - JOUR\nID  - K2\nTI  - Other Topic\nAU  - Jones, Jane\n"
            "PY  - 2021\nDO  - 10.1/x\nER  -\n\n", encoding="ascii")
        inc = tmp / "inc.ris"
        inc.write_text(
            "TY  - JOUR\nID  - K1\nTI  - Same Topic\nAU  - Smith, John\n"
            "PY  - 2020\nDO  - 10.1/x\nER  -\n\n", encoding="ascii")
        p1 = _run_cli(["import", "--file", str(lib), "--db", db])
        self.assertEqual(p1.returncode, 0, p1.stderr)
        p2 = _run_cli(["import", "--file", str(inc), "--db", db])
        self.assertEqual(p2.returncode, 0, p2.stderr)
        return db, p2

    def test_source_key_path_warns_about_shared_doi(self):
        with tempfile.TemporaryDirectory(prefix="rf_r8b2s_") as tmp:
            db, p2 = self._run(Path(tmp))
            self.assertIn("已持有 DOI 10.1/x", p2.stderr,
                          "带来源键的路径也必须在两行同 DOI 时显式警告")
            self.assertIn("K2", p2.stderr)

    def test_legitimate_duplicate_pair_does_not_warn(self):
        # 评审 R3·F4（medium）：同一论文两个来源（K1/K2 同 DOI）由 exact_doi
        # 打成 is_duplicate_of 属正常结构，不得刷「两行同 DOI」警告。
        # 撤「仅非重复主行才告警」的过滤必红（旧实现每个 dup 都报）。
        with tempfile.TemporaryDirectory(prefix="rf_r8b2n_") as tmp:
            tmp = Path(tmp)
            db = str(tmp / "t.db")
            a = tmp / "a.ris"
            b = tmp / "b.ris"
            for path, key in ((a, "K1"), (b, "K2")):
                path.write_text("TY  - JOUR\nID  - " + key + "\nTI  - Same Paper\nAU  - Smith, John\nPY  - 2020\nDO  - 10.9/x\nER  -\n\n", encoding="ascii")
            p1 = _run_cli(["import", "--file", str(a), "--db", db])
            self.assertEqual(p1.returncode, 0, p1.stderr)
            p2 = _run_cli(["import", "--file", str(b), "--db", db])
            self.assertEqual(p2.returncode, 0, p2.stderr)
            self.assertNotIn("两行均为非重复主行", p2.stderr,
                             "正常 is_duplicate_of 结构不得触发同 DOI 警告")
            with closing(sqlite3.connect(db)) as conn:
                marks = dict(conn.execute(
                    "SELECT zotero_key, is_duplicate_of FROM articles").fetchall())
            self.assertEqual(marks, {"K1": None, "K2": "K1"})

    def test_single_file_multi_doi_does_not_spam(self):
        # 评审 R3·F4：同一文件 4 条同 DOI 曾产生 3 条一模一样的假警告。
        with tempfile.TemporaryDirectory(prefix="rf_r8b2m_") as tmp:
            tmp = Path(tmp)
            db = str(tmp / "t.db")
            ris = tmp / "d.ris"
            ris.write_text("".join(
                "TY  - JOUR\nID  - K" + str(i) + "\nTI  - Same Paper\nAU  - Smith, John\nPY  - 2020\nDO  - 10.7/x\nER  -\n\n" for i in range(4)),
                encoding="ascii")
            proc = _run_cli(["import", "--file", str(ris), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stderr.count("两行均为非重复主行"), 0,
                             f"批内同 DOI 不得刷警告: {proc.stderr!r}")

    def test_generated_key_replacement_of_owner_row_is_announced(self):
        # 评审 R2 同条后半：记录被 exact_doi 并到 K2 时，K2 的题名/作者/年
        # 会被本批改写——必须列出改动，而不是只提示「某键未被沿用」。
        with tempfile.TemporaryDirectory(prefix="rf_r8b2g_") as tmp:
            tmp = Path(tmp)
            db = str(tmp / "t.db")
            lib = tmp / "lib.ris"
            lib.write_text(
                "TY  - JOUR\nID  - K1\nTI  - Same Topic\nAU  - Smith, John\n"
                "PY  - 2020\nER  -\n\n"
                "TY  - JOUR\nID  - K2\nTI  - Other Topic\nAU  - Jones, Jane\n"
                "PY  - 2021\nDO  - 10.1/x\nER  -\n\n", encoding="ascii")
            inc = tmp / "inc.ris"
            inc.write_text(
                "TY  - JOUR\nTI  - Same Topic\nAU  - Smith, John\nPY  - 2020\n"
                "DO  - 10.1/x\nER  -\n\n", encoding="ascii")
            p1 = _run_cli(["import", "--file", str(lib), "--db", db])
            self.assertEqual(p1.returncode, 0, p1.stderr)
            p2 = _run_cli(["import", "--file", str(inc), "--db", db])
            self.assertEqual(p2.returncode, 0, p2.stderr)
            self.assertIn("将按本批记录改写元数据", p2.stderr)
            self.assertIn("Other Topic", p2.stderr)
            self.assertIn("Jones, Jane", p2.stderr)
            # 身份选择与 Web 一致：DOI 归属行 K2 建组
            self.assertIn("已属于库内另一行 K2", p2.stderr)


class ContentLayerFallbackTests(unittest.TestCase):
    """#3：内容层首候选 DOI 冲突时试同内容键其他库内行。撤修复必红。"""

    def test_second_same_content_row_adopted_when_first_doi_conflicts(self):
        with tempfile.TemporaryDirectory(prefix="rf_r8b3_") as tmp:
            tmp = Path(tmp)
            db = str(tmp / "t.db")
            lib = tmp / "lib.ris"
            lib.write_text(
                "TY  - JOUR\nTI  - Shared Title\nAU  - Doe, Jane\nPY  - 2020\n"
                "DO  - 10.1/a\nER  -\n\n"
                "TY  - JOUR\nTI  - Shared Title\nAU  - Doe, Jane\nPY  - 2020\n"
                "ER  -\n\n", encoding="ascii")
            p1 = _run_cli(["import", "--file", str(lib), "--db", db])
            self.assertEqual(p1.returncode, 0, p1.stderr)
            before = _rows(db)
            self.assertEqual(len(before), 2)
            inc = tmp / "inc.ris"
            inc.write_text(
                "TY  - JOUR\nTI  - Shared Title\nAU  - Doe, Jane\nPY  - 2020\n"
                "DO  - 10.1/c\nER  -\n\n", encoding="ascii")
            p2 = _run_cli(["import", "--file", str(inc), "--db", db])
            self.assertEqual(p2.returncode, 0, p2.stderr)
            after = _rows(db)
            # 空 DOI 的同内容行本可兼容沿用：不得新增第 3 行
            self.assertEqual(len(after), 2,
                             f"应沿用同内容的空 DOI 库内行，实际 {after}")
            holder = [r for r in after if r[2] == "10.1/c"]
            self.assertEqual(len(holder), 1, "入批 DOI 应并入沿用的库内行")
            self.assertIn("更新 1 条", p2.stdout)
            # 10.1/a 行原样保留
            self.assertTrue(any(r[2] == "10.1/a" for r in after))


if __name__ == "__main__":
    unittest.main()
