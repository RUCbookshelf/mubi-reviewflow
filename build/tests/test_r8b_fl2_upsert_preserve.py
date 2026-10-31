"""r8b FL2 钉子 —— #1/#7/#13：combined 形态沿用库内键时保留库内元数据。

复审复现口径（out/external_reviews/r6_verify_dedup.md #1/#7/#13，探针
`1_same_key`/`1b`/`1_living`/`1c`）：

- CLI 同键稀疏重导（full.ris → 仅 TI 的 sparse.ris）：修复前第 2 次
  「新增 0 条，更新 1 条」stderr 空，库内 authors/year/journal 全空；
- living_review `process_incremental_articles` 同场景同样清空；
- 库有 DOI、批缺 DOI（内容层命中）：库内 DOI 被清成 ''；
- 分离传参分支回填 DOI 后 content_hash 仍为解析时（无 DOI）摘要
  （sha1('|Same Topic')），与落库 DOI 不一致。

修复：字段保留+警告移入 stabilize 的 combined 沿用路径（生成键成员沿用
库内键 + 来源键成员保留库内同键），回填后按同公式重算 content_hash；
本批非空字段照常更新（FB2 元数据补全语义不受影响——R2：稀疏更新不得
变成拒绝）。撤修复必红。
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from io import StringIO
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import db as db_mod
from coscreen.dedup import find_duplicates, stabilize_conflicting_keys
from coscreen.living_review import process_incremental_articles
from coscreen.models import Article, compute_content_hash
from coscreen.parsers import parse_file


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_cli(args: list[str]):
    """真实子进程 `python -B -m coscreen ...`（复审同款探针形态）。

    评审 R2：钉子此前是进程内 ``cli_module.main`` + redirect_stdout，与
    r6 复审的 ``python -B -m coscreen`` 子进程形态不同（子进程才能覆盖
    stdout/stderr 编码、退出码与 __main__ 入口）。cwd=仓库根、UTF-8 输出。
    """
    import os
    import subprocess

    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "coscreen", *[str(a) for a in args]],
        cwd=str(_REPO_ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return proc


class SparseReimportPreserveTests(unittest.TestCase):
    """#1/#7：同键稀疏重导不清空库内元数据（CLI 与 living_review 双路径）。"""

    def test_cli_same_key_sparse_reimport_keeps_library_metadata(self):
        # 复审 1_same_key：full → sparse，撤修复必红（authors/year/journal 空）。
        with tempfile.TemporaryDirectory(prefix="rf_r8b1a_") as tmp:
            tmp = Path(tmp)
            full = tmp / "full.ris"
            sparse = tmp / "sparse.ris"
            db = str(tmp / "t.db")
            full.write_text(
                "TY  - JOUR\nID  - K1\nTI  - Same Topic\nAU  - Wang, Wei\n"
                "PY  - 2005\nJO  - J of Probes\nER  -\n\n", encoding="ascii")
            sparse.write_text(
                "TY  - JOUR\nID  - K1\nTI  - Same Topic\nER  -\n\n", encoding="ascii")
            p1 = _run_cli(["import", "--file", str(full), "--db", db])
            self.assertEqual(p1.returncode, 0, p1.stderr)
            p2 = _run_cli(["import", "--file", str(sparse), "--db", db])
            self.assertEqual(p2.returncode, 0, p2.stderr)
            with closing(sqlite3.connect(db)) as conn:
                row = conn.execute(
                    "SELECT authors, year, journal, doi FROM articles"
                ).fetchone()
            self.assertEqual(row, ("Wang, Wei", 2005, "J of Probes", ""),
                             "稀疏重导不得清空库内元数据")
            # R3：保留动作必须显式警告（修复前 stderr 为空）
            self.assertIn("保留了库内既有值", p2.stderr)
            self.assertIn("K1", p2.stderr)

    def test_cli_richer_reimport_still_updates_authors(self):
        # R2 反向钉：本批**非空**字段照常更新（FB2 元数据补全语义保持）——
        # 撤修复（改成整行不更新/preserve_existing 冻结）必红。
        with tempfile.TemporaryDirectory(prefix="rf_r8b1b_") as tmp:
            tmp = Path(tmp)
            db = str(tmp / "k.db")
            csv1 = tmp / "a.csv"
            csv1.write_text(
                'Key,Title,Author,DOI\nK1,Some study on statins,"Kim, Lee",10.9/x\n',
                encoding="utf-8")
            arts = parse_file(csv1)
            self.assertEqual(arts[0].authors, "Kim, Lee")
            # faithful 2.1.3 旧行：authors 空（Author 列丢失形态）
            db_mod.init_db(db)
            db_mod.upsert_articles(db, [
                Article(zotero_key="K1", title=arts[0].title, authors="",
                        doi=arts[0].doi, content_hash=arts[0].content_hash)], [])
            proc = _run_cli(["import", "--file", str(csv1), "--db", db])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("新增 0 条", proc.stdout)
            with closing(sqlite3.connect(db)) as conn:
                authors = conn.execute(
                    "SELECT authors FROM articles WHERE zotero_key='K1'").fetchone()[0]
            self.assertEqual(authors, "Kim, Lee",
                             "本批非空作者必须写入（元数据补全，不得冻结整行）")

    def test_cli_library_doi_preserved_when_batch_lacks_doi(self):
        # 复审 1b：库有 DOI、批缺 DOI、无来源键（内容层命中沿用）——
        # 修复前库内 doi 被清成 ''。撤修复必红。
        with tempfile.TemporaryDirectory(prefix="rf_r8b1c_") as tmp:
            tmp = Path(tmp)
            db = str(tmp / "t.db")
            first = tmp / "one.ris"
            first.write_text(
                "TY  - JOUR\nTI  - Same Topic\nAU  - Wang, Wei\nPY  - 2005\n"
                "DO  - 10.42/xyz\nER  -\n\n", encoding="ascii")
            p1 = _run_cli(["import", "--file", str(first), "--db", db])
            self.assertEqual(p1.returncode, 0, p1.stderr)
            sparse = tmp / "two.ris"
            sparse.write_text(
                "TY  - JOUR\nTI  - Same Topic\nAU  - Wang, Wei\nPY  - 2005\n"
                "ER  -\n\n", encoding="ascii")
            p2 = _run_cli(["import", "--file", str(sparse), "--db", db])
            self.assertEqual(p2.returncode, 0, p2.stderr)
            with closing(sqlite3.connect(db)) as conn:
                doi = conn.execute("SELECT doi FROM articles").fetchone()[0]
            self.assertEqual(doi, "10.42/xyz", "批缺 DOI 不得清空库内 DOI")

    def test_living_review_sparse_update_keeps_library_metadata(self):
        # 复审 1_living：process_incremental_articles 同场景不清空。撤修复必红。
        with tempfile.TemporaryDirectory(prefix="rf_r8b1d_") as tmp:
            db = str(Path(tmp) / "living.db")
            db_mod.init_db(db)
            db_mod.upsert_articles(db, [
                Article(zotero_key="K1", title="Same Topic", authors="Wang, Wei",
                        year=2005, journal="J of Probes", doi="10.42/xyz")], [])
            result = process_incremental_articles(
                db, [Article(zotero_key="K1", title="Same Topic", raw={})],
                "A vs B", "mortality", "12 months", "RR")
            with closing(sqlite3.connect(db)) as conn:
                row = conn.execute(
                    "SELECT authors, year, journal, doi FROM articles").fetchone()
            self.assertEqual(row, ("Wang, Wei", 2005, "J of Probes", "10.42/xyz"))
            self.assertEqual(result.duplicates_removed, 0)


class TitlePreservationTests(unittest.TestCase):
    """评审 R3·F5（medium）：本批缺 TI 时不得清空库内 title。

    实测（评审口径）：库内 K1=`Real Title` + 同 DOI/同 key 但无 TI 的记录
    重导 -> 修复前 `title=''` 且 stderr 空（`_metadata_replacement_note` 把
    「本批 title 为空」当无改动，不触发）。现 title 纳入保留集并显式警告。
    撤 title 保留必红。
    """

    def test_missing_title_in_batch_keeps_library_title(self):
        with tempfile.TemporaryDirectory(prefix="rf_r8b1t_") as tmp:
            tmp = Path(tmp)
            db = str(tmp / "t.db")
            full = tmp / "full.ris"
            sparse = tmp / "sparse.ris"
            full.write_text(
                "TY  - JOUR\nID  - K1\nTI  - Real Title\nAU  - Smith, John\nPY  - 2020\nDO  - 10.5/x\nER  -\n\n", encoding="ascii")
            sparse.write_text(
                "TY  - JOUR\nID  - K1\nAU  - Smith, John\nPY  - 2020\nDO  - 10.5/x\nER  -\n\n", encoding="ascii")
            p1 = _run_cli(["import", "--file", str(full), "--db", db])
            self.assertEqual(p1.returncode, 0, p1.stderr)
            p2 = _run_cli(["import", "--file", str(sparse), "--db", db])
            self.assertEqual(p2.returncode, 0, p2.stderr)
            with closing(sqlite3.connect(db)) as conn:
                title = conn.execute(
                    "SELECT title FROM articles WHERE zotero_key='K1'").fetchone()[0]
            self.assertEqual(title, "Real Title",
                             "本批缺 TI 不得清空库内标题")
            self.assertIn("title", p2.stderr)


class ContentHashRecomputeTests(unittest.TestCase):
    """#13：分离分支回填 DOI 后 content_hash 按同公式重算。撤修复必红。"""

    def test_separated_adoption_backfills_doi_and_recomputes_hash(self):
        with tempfile.TemporaryDirectory(prefix="rf_r8b13_") as tmp:
            db = str(Path(tmp) / "t.db")
            db_mod.init_db(db)
            db_row = Article(zotero_key="K1", title="Same Topic", authors="Wang, Wei",
                             year=2005, doi="10.1/x", journal="BMJ",
                             abstract="abs", url="http://x")
            db_mod.upsert_articles(db, [db_row], [])
            incoming = Article(zotero_key="auto-abc", title="Same Topic",
                               authors="Wang, Wei", year=2005,
                               raw={"_generated_key": True})
            warnings: list[str] = []
            stabilize_conflicting_keys([incoming], [], known=[db_row],
                                       warnings=warnings)
            self.assertEqual(incoming.zotero_key, "K1")
            self.assertEqual(incoming.doi, "10.1/x")
            self.assertEqual(
                incoming.content_hash, compute_content_hash("10.1/x", "Same Topic"),
                "回填 DOI 后 content_hash 必须与落库字段一致（修复前为 "
                "sha1('|Same Topic')）")
            self.assertTrue(any("保留" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
