"""r17 钉子：期刊样式 DOI 不得作为「同一文献」判定键。

实证（2026-11-07 用户验证 2.1.4，副本
``E:/rf214tmp/verify214data/tasks/test-0ea4``）：ERIC 导出的 EJ948630
（Vasquez 2011）与 EJ948631（Benson 2011）是 TESOL Quarterly 的两篇不同
文献，却因两条 DOI 同为 ``10.1002/(issn)1545-7249``——ERIC 给整刊写的
期刊样式 DOI，不是文章 DOI——被配成同一文献：第二条记录 outcome 为
``exact_staged``、canonical 指向第一条，后续以重复标记相连，并在导入
复核中作为「信息差异」要求人工核验。

修前红（基线 ca6d6ba）：①③⑤⑥ 及 prepare/批量确认用例在基线实现上失败；
②④ 是回归保护（修前修后均应通过）。

钉子：
- ① 两条不同标题、同 ``(issn)`` DOI → 不产生 exact_doi 配对、不进
  identity_conflict、不生成「信息差异」（metadata proposal）；
- ② 真实文章 DOI 相同 → 仍配对（回归保护）；
- ③ 样式 DOI 与同刊文章的真实 DOI 不同 → 不配对；
- ④ 样式 DOI 记录存储与 RIS 导出字段原样保留；
- ⑤ 样式 DOI 不产生任何 dedup 事件/轨迹（配对清单、重复计数、消歧改名、
  警告均为空）；
- ⑥ 大小写变体 ``(ISSN)``/``(Issn)`` 同样排除（NFKC 全角括号同）。
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import db as articles_db
from coscreen import review_imports as imports
from coscreen import review_workflow as workflow
from coscreen.dedup import (
    find_duplicates,
    is_journal_pattern_doi,
    stabilize_conflicting_keys,
)
from coscreen.export import export_articles_ris
from coscreen.models import Article
from custom_backend.import_review_actions import eligible_duplicate

PATTERN_DOI = "10.1002/(issn)1545-7249"
REAL_DOI = "10.1002/tesq.123"


def _article(key: str, title: str, doi: str, authors: str = "",
             year: int | None = None) -> Article:
    return Article(zotero_key=key, title=title, authors=authors, doi=doi,
                   year=year)


def _no_title_groups(*_args, **_kwargs):
    return []


class PatternDoiPredicateTests(unittest.TestCase):
    """纯函数：期刊样式判定口径（大小写/全角/普通 DOI）。"""

    def test_pattern_doi_detected(self):
        self.assertTrue(is_journal_pattern_doi(PATTERN_DOI))
        self.assertTrue(is_journal_pattern_doi("10.1002/(ISSN)1545-7249"))
        self.assertTrue(is_journal_pattern_doi("10.1002/(Issn)1545-7249"))
        # NFKC 折叠全角括号后同样命中
        self.assertTrue(is_journal_pattern_doi("10.1002/（ISSN）1545-7249"))
        # 前缀/空白不影响（判定前走 normalize_doi）
        self.assertTrue(is_journal_pattern_doi(
            " https://doi.org/10.1002/(ISSN)1545-7249 "))

    def test_real_and_empty_doi_not_pattern(self):
        self.assertFalse(is_journal_pattern_doi(REAL_DOI))
        self.assertFalse(is_journal_pattern_doi("10.1002/issn1545-7249"))
        self.assertFalse(is_journal_pattern_doi(""))


class PatternDoiDedupTests(unittest.TestCase):
    """find_duplicates：样式 DOI 不参与身份配对（①③⑤⑥）。"""

    def test_different_articles_with_same_pattern_doi_are_not_merged(self):
        arts = [
            _article("EJ948630",
                     "TESOL, teacher identity, and the need for 'small story' research",
                     PATTERN_DOI),
            _article("EJ948631",
                     "Language learning careers as an object of narrative research in TESOL",
                     PATTERN_DOI),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(len(kept), 2, "不同文献不得被合并")
        self.assertEqual(report.exact_pairs, [])
        self.assertEqual(report.key_conflict_pairs, [])
        self.assertEqual(report.fuzzy_pairs, [])
        self.assertEqual(report.n_duplicates(), 0)

    def test_pattern_doi_yields_no_dedup_events_or_renames(self):
        arts = [
            _article("EJ948630", "First article", PATTERN_DOI),
            _article("EJ948631", "Second article", PATTERN_DOI),
            _article("EJ948632", "Third article", PATTERN_DOI),
        ]
        kept, report = find_duplicates(arts)
        pairs = report.exact_pairs + report.key_conflict_pairs + report.fuzzy_pairs
        self.assertEqual(pairs, [])
        warnings: list[str] = []
        renames = stabilize_conflicting_keys(kept, pairs, warnings=warnings)
        self.assertEqual(renames, [])
        self.assertEqual(warnings, [])

    def test_stabilize_does_not_warn_pattern_doi_ownership(self):
        """库内行与入库记录同持整刊 DOI 不是「同一 DOI 落到两行」缺陷（r17）。"""
        known = [_article("EJ948630", "First article", PATTERN_DOI)]
        incoming = [_article("EJ948631", "Second article", PATTERN_DOI)]
        warnings: list[str] = []
        stabilize_conflicting_keys(known + incoming, [], known=known,
                                   warnings=warnings)
        self.assertEqual(warnings, [], "样式 DOI 不得触发同一 DOI 归属/元数据改写警告")

    def test_real_article_doi_still_pairs(self):
        arts = [
            _article("K1", "Same paper, source A", REAL_DOI),
            _article("K2", "Same paper, source B", REAL_DOI),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(len(kept), 1)
        self.assertEqual([p.method for p in report.exact_pairs], ["exact_doi"])

    def test_pattern_doi_does_not_pair_with_real_article_doi(self):
        arts = [
            _article("EJ948630", "First article", PATTERN_DOI),
            _article("EJ948631", "Second article", REAL_DOI),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(len(kept), 2)
        self.assertEqual(report.exact_pairs, [])

    def test_uppercase_issn_variant_excluded(self):
        arts = [
            _article("K1", "First article", "10.1002/(ISSN)1545-7249"),
            _article("K2", "Second article", "10.1002/(issn)1545-7249"),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(len(kept), 2)
        self.assertEqual(report.exact_pairs, [])

    def test_same_key_pattern_doi_does_not_confirm_merge(self):
        """样式 DOI 也不得作为 exact_key 的 DOI 裁决证据（失去身份键效力）。"""
        arts = [
            _article("K1", "First article", PATTERN_DOI),
            _article("K1", "Second article", PATTERN_DOI),
        ]
        kept, report = find_duplicates(arts)
        self.assertEqual(len(kept), 2)
        self.assertEqual(report.exact_pairs, [])
        self.assertEqual([p.method for p in report.key_conflict_pairs],
                         ["exact_key_conflict"])


class PatternDoiImportReviewTests(unittest.TestCase):
    """导入复核：同批/对活动行都不得因样式 DOI 进入 exact/_信息差异_。"""

    def setUp(self):
        self._fixture = tempfile.TemporaryDirectory(prefix="rf_r17_pattern_doi_")
        self.addCleanup(self._fixture.cleanup)
        self.db_path = Path(self._fixture.name) / "shelf.db"
        articles_db.init_db(self.db_path)
        imports.migrate(self.db_path)

    def _prepare(self, records: list[Article], *, batch_id: str = "b1"):
        files = [{
            "name": "eric.ris", "digest": "d1",
            "source_id": workflow.UNASSIGNED,
            "articles": list(records),
            "originals": [{"Key": a.zotero_key, "Title": a.title,
                           "DOI": a.doi} for a in records],
        }]
        with closing(workflow.connect(self.db_path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            plan = imports.prepare(
                conn, files, batch_id=batch_id, history_id="h1",
                owner_id="owner-1", import_type="batch", protected=False,
                title_groups=_no_title_groups, progress_callback=None)
            rows = {r["incoming_key"]: dict(r) for r in conn.execute(
                "SELECT incoming_key,outcome,canonical_key,target_record_id "
                "FROM workflow_source_records")}
            metadata_count = conn.execute(
                "SELECT COUNT(*) FROM workflow_import_metadata").fetchone()[0]
            candidates = [dict(r) for r in conn.execute(
                "SELECT kind,status FROM workflow_import_candidates")]
        return plan, rows, metadata_count, candidates

    def test_same_batch_pattern_doi_records_stay_independent(self):
        plan, rows, metadata_count, candidates = self._prepare([
            _article("EJ948630",
                     "TESOL, teacher identity, and the need for 'small story' research",
                     PATTERN_DOI),
            _article("EJ948631",
                     "Language learning careers as an object of narrative research in TESOL",
                     PATTERN_DOI),
        ])
        self.assertEqual(len(plan["new_record_ids"]), 2)
        for key in ("EJ948630", "EJ948631"):
            self.assertEqual(rows[key]["outcome"], "new",
                             f"{key} 不得因样式 DOI 被链到另一条记录")
            self.assertIsNone(rows[key]["canonical_key"])
            self.assertIsNone(rows[key]["target_record_id"])
        self.assertEqual(metadata_count, 0, "不得生成「信息差异」元数据提案")
        self.assertFalse([c for c in candidates if c["kind"] == "identity_conflict"],
                         "不得进入 identity_conflict（信息差异）候选")

    def test_incoming_pattern_doi_does_not_match_active_row(self):
        articles_db.upsert_articles(self.db_path, [
            _article("EJ948630", "First article", PATTERN_DOI)], [])
        plan, rows, metadata_count, candidates = self._prepare([
            _article("EJ948631", "Second article", PATTERN_DOI)])
        self.assertEqual(len(plan["new_record_ids"]), 1)
        self.assertEqual(rows["EJ948631"]["outcome"], "new")
        self.assertIsNone(rows["EJ948631"]["canonical_key"])
        self.assertEqual(metadata_count, 0)

    def test_bulk_duplicate_confirm_rejects_pattern_doi_only_evidence(self):
        candidate = {
            "kind": "title", "status": "pending",
            "members": [
                {"ref_type": "record", "article": {"doi": PATTERN_DOI,
                                                   "title": "First article",
                                                   "authors": "Vasquez, A.",
                                                   "year": 2011}},
                {"ref_type": "record", "article": {"doi": PATTERN_DOI,
                                                   "title": "Second article",
                                                   "authors": "Benson, P.",
                                                   "year": 2011}},
            ],
        }
        self.assertFalse(eligible_duplicate(candidate),
                         "同样式 DOI 不能作为批量确认重复的唯一证据")


class PatternDoiStorageTests(unittest.TestCase):
    """④ 样式 DOI 原样保留在存储与导出中（数据不动）。"""

    def test_storage_and_ris_export_keep_pattern_doi(self):
        with tempfile.TemporaryDirectory(prefix="rf_r17_pattern_doi_store_") as tmp:
            db_path = Path(tmp) / "shelf.db"
            articles_db.init_db(db_path)
            art = _article("EJ948630", "First article", PATTERN_DOI,
                           authors="Vasquez, A.", year=2011)
            articles_db.upsert_articles(db_path, [art], [])
            stored = articles_db.list_articles(db_path)
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0].doi, PATTERN_DOI)
            self.assertEqual(stored[0].to_dict()["doi"], PATTERN_DOI)

            articles_db.save_decision(db_path, "EJ948630", "include")
            out = export_articles_ris(db_path, Path(tmp) / "out.ris")
            text = out.read_text(encoding="utf-8")
            self.assertIn(f"DO  - {PATTERN_DOI}", text,
                          "RIS 导出必须原样保留期刊样式 DOI")


if __name__ == "__main__":
    unittest.main()
