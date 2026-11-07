"""r8b FL2 钉子 —— #4：manuscript 的 duplicates_removed 不计 pending 候选。

复审复现口径（out/external_reviews/r6_verify_dedup.md #4，探针 `4`）：
真实 sqlite 预置 WM1（库内旧行）/WM2（本批 pending 成员），两行
is_duplicate_of 均 NULL；真实 ``custom_backend.main._collect_manuscript_data``
修复前 all=2、unique=1、公式=1 → PRISMA ``duplicates_removed=1``（把待
人工确认候选计成已去除重复）；同库 IncrementalResult
duplicates_removed=0/pending=1。

修复：只按**实际打标移除**的行（is_duplicate_of 非空）计数，与
living_review/prisma_task_metrics 的 is_duplicate_of 口径统一；人工确认
duplicate 后（decide_title_duplicate_review）计入。撤修复必红。
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import db as db_mod
from coscreen.models import Article
from coscreen.tasks import TaskInfo


def _pending_fixture(db: str) -> None:
    db_mod.init_db(db)
    db_mod.upsert_articles(db, [
        Article(zotero_key="WM1", title="Effects of drug X in women",
                authors="Wang, Wei"),
        Article(zotero_key="WM2", title="Effects of drug X in women",
                authors="Wang, Wei"),
    ], [], title_review_groups=[{
        "normalized_title": "g:women",
        "members": [
            {"zotero_key": "WM1", "imported_in_batch": False},
            {"zotero_key": "WM2", "imported_in_batch": True},
        ]}])


def _collect(db_path: Path):
    import custom_backend.main as backend_main

    info = TaskInfo(task_id="t", dir=db_path.parent, display_name="t",
                    screeners=["alice"])
    return backend_main._collect_manuscript_data(
        info, {"username": "alice"}, "fixed", "normal")


class ManuscriptPendingNotRemovedTests(unittest.TestCase):
    def test_pending_title_review_members_not_counted_as_removed(self):
        with tempfile.TemporaryDirectory(prefix="rf_r8b4_") as tmp:
            tmp = Path(tmp)
            db = tmp / "alice.db"
            _pending_fixture(str(db))
            data = _collect(db)
            all_n = len(db_mod.list_articles(str(db), include_duplicates=True))
            unique_n = len(db_mod.list_articles(str(db)))
            self.assertEqual((all_n, unique_n), (2, 1))  # 复审同款夹具形态
            self.assertEqual(
                data["prisma"]["duplicates_removed"], 0,
                "pending 候选（is_duplicate_of 均 NULL）不得计成已去除重复"
                "（修复前 len(all)-len(unique)=1）")

    def test_confirmed_duplicate_marks_counted_as_removed(self):
        # 对照：人工确认 duplicate（打 is_duplicate_of）后计入——保证新公式
        # 不是把一切重复都计 0，而是与 is_duplicate_of 口径一致。
        with tempfile.TemporaryDirectory(prefix="rf_r8b4b_") as tmp:
            tmp = Path(tmp)
            db = tmp / "alice.db"
            _pending_fixture(str(db))
            db_mod.decide_title_duplicate_review(
                str(db), _first_pending_review(str(db)), "duplicate",
                keeper_key="WM1", decided_by="alice")
            with closing(sqlite3.connect(str(db))) as conn:
                marked = conn.execute(
                    "SELECT COUNT(*) FROM articles WHERE is_duplicate_of IS NOT NULL"
                ).fetchone()[0]
            self.assertEqual(marked, 1)
            data = _collect(db)
            self.assertEqual(data["prisma"]["duplicates_removed"], 1)


def _first_pending_review(db: str) -> str:
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute(
            "SELECT review_id FROM title_duplicate_reviews "
            "WHERE status IN ('pending','undecided') LIMIT 1").fetchone()[0]


if __name__ == "__main__":
    unittest.main()
