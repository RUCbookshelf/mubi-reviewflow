"""r9-L1 钉 —— 去重/库层：同键多行、重复行身份、超长键折叠、标题重复撤销、import_order 竞态。

权威清单（out/release_plan/inventory_夜扫286确认分组.md §3.1）行号与探针形态
逐条复现（复审基线 a080fde 的 night_verify_batch_04/12 探针）：

- F3-29【高】 db.py ``decide_title_duplicate_review``：duplicate→not_duplicate
  不清 is_duplicate_of，成员从筛选/进度静默消失（修前 B 仍标 'A'、list_articles
  仍 ['A']，需 undo 两次才恢复）。
- F3-31     ``upsert_articles`` 的 ``SELECT MAX(import_order)`` 在 autocommit 下
  先读后写；并发导入产生重复排序键（修前 rows=[[k1,0],[k2,0]]）。
- F3-33【高】 ``upsert_articles`` 合并删除 prior review 不消费其 undo 条目 →
  死条目永久阻塞该 actor 撤销（修前 undo 连续 3 次 ValueError）。
- F3-35【高】 dedup ``known_by_key`` setdefault 只留同键首行 → 本应更新的记录
  被加后缀成新行（修前 renames 非空、新键变成 K-<hash>）。
- F3-36【高】 dedup 身份索引首见优先且不过滤重复标记行 → 重导刷新写进不可见行、
  keeper 陈旧（修前 rec 沿用 dup-old，dup-old 标题被刷新且仍标重复）。
- F3-37     ``compliant_key_for_overlong`` 尾形正则把非消歧键当历史形态截断 →
  两条键折叠到同一目标、第二条靠 serial 变 ``-2``（修前 collide=true）。

对照组：单行库、短键、正常撤销、单线程导入的分页/顺序/键值逐位不变。
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import db as db_mod
from coscreen import dedup as dedup_mod
from coscreen.dedup import (
    _disambiguation_key,
    _stable_content_id,
    compliant_key_for_overlong,
    find_duplicates,
    fresh_key_for_overlong,
    stabilize_conflicting_keys,
)
from coscreen.models import Article


def _art(key: str, title: str = "", doi: str = "", authors: str = "A",
         year: int | None = 2020, **raw) -> Article:
    return Article(zotero_key=key, item_type="journalArticle", title=title or f"T {key}",
                   authors=authors, journal="J", year=year, doi=doi,
                   abstract="ab", url="u", source_format="csv", raw=raw)


def _review_group(title: str, keys: list[str], in_batch: bool = True) -> dict:
    return {"normalized_title": title,
            "members": [{"zotero_key": key, "imported_in_batch": in_batch}
                        for key in keys]}


# ---------------------------------------------------------------------------
# F3-29 · not_duplicate / undecided 回滚本组制造的重叠标记
# ---------------------------------------------------------------------------
class TitleDuplicateUndoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf_r9l1_f329_")
        self.db = str(Path(self._tmp.name) / "lib.db")
        db_mod.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def _pending_review(self) -> str:
        db_mod.upsert_articles(
            self.db, [_art("A", "Title A"), _art("B", "Title B")], [],
            title_review_groups=[_review_group("title a", ["A", "B"])])
        reviews, _ = db_mod.list_title_duplicate_reviews(self.db, status="pending")
        return reviews[0]["review_id"]

    def _marker(self, key: str):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute(
                "SELECT is_duplicate_of FROM articles WHERE zotero_key=?", (key,)
            ).fetchone()[0]

    def test_not_duplicate_rolls_back_member_marks(self):
        review_id = self._pending_review()
        self.assertTrue(db_mod.decide_title_duplicate_review(
            self.db, review_id, "duplicate", keeper_key="A", decided_by="alice"))
        self.assertEqual(self._marker("B"), "A")
        self.assertEqual([a.zotero_key for a in db_mod.list_articles(self.db)], ["A"])
        # 修前：B 仍标 'A'，list_articles 仍 ['A']（红）
        self.assertTrue(db_mod.decide_title_duplicate_review(
            self.db, review_id, "not_duplicate", decided_by="alice"))
        self.assertIsNone(self._marker("B"))
        self.assertEqual(
            sorted(a.zotero_key for a in db_mod.list_articles(self.db)), ["A", "B"])
        self.assertEqual(db_mod.get_progress(self.db)["total"], 2)

    def test_undecided_also_rolls_back(self):
        review_id = self._pending_review()
        db_mod.decide_title_duplicate_review(
            self.db, review_id, "duplicate", keeper_key="A", decided_by="alice")
        db_mod.decide_title_duplicate_review(
            self.db, review_id, "undecided", decided_by="alice")
        # 标记回滚成立。注意 undecided 组的本批成员仍按既有的「待人工确认
        # 候选」口径从筛选列表隐藏（title_duplicate_members +
        # imported_in_batch=1），该口径本次不改动（R3/R2）。
        self.assertIsNone(self._marker("B"))

    def test_rollback_preserves_marker_written_by_another_group(self):
        """本组制造之外的标记（此前由精确去重写入）必须原样恢复。"""
        db_mod.upsert_articles(self.db, [_art("A", "Title A"), _art("B", "Title B")], [],
                               title_review_groups=[_review_group("title a", ["A", "B"])])
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE articles SET is_duplicate_of='KEEP' WHERE zotero_key='B'")
        reviews, _ = db_mod.list_title_duplicate_reviews(self.db, status="pending")
        review_id = reviews[0]["review_id"]
        db_mod.decide_title_duplicate_review(
            self.db, review_id, "duplicate", keeper_key="A", decided_by="alice")
        self.assertEqual(self._marker("B"), "A")
        db_mod.decide_title_duplicate_review(
            self.db, review_id, "not_duplicate", decided_by="alice")
        # 「本次决策前」的真实值是 KEEP，不是 NULL
        self.assertEqual(self._marker("B"), "KEEP")

    def test_undo_still_works_after_rollback(self):
        review_id = self._pending_review()
        db_mod.decide_title_duplicate_review(
            self.db, review_id, "duplicate", keeper_key="A", decided_by="alice")
        db_mod.decide_title_duplicate_review(
            self.db, review_id, "not_duplicate", decided_by="alice")
        # 两次决策各留一条撤销日志：连撤两次回到原始待定状态
        self.assertTrue(db_mod.undo_title_duplicate_review(self.db, "alice"))
        self.assertTrue(db_mod.undo_title_duplicate_review(self.db, "alice"))
        self.assertFalse(db_mod.title_duplicate_undo_available(self.db, "alice"))

    def test_merged_prior_group_does_not_block_undo(self):
        """F3-33：合并删除 prior 组时必须消费其 undo 条目。"""
        db_mod.upsert_articles(
            self.db, [_art(k) for k in "ABCD"], [],
            title_review_groups=[
                _review_group("g1", ["A", "B"]),
                _review_group("g2", ["C", "D"]),
            ])
        reviews, _ = db_mod.list_title_duplicate_reviews(self.db, status="pending")
        by_title = {row["normalized_title"]: row["review_id"] for row in reviews}
        second = by_title["g2"]
        db_mod.decide_title_duplicate_review(
            self.db, second, "duplicate", keeper_key="C", decided_by="bob")
        db_mod.decide_title_duplicate_review(
            self.db, second, "undecided", decided_by="bob")
        # 新导入批把两个 prior 组合并成一组 → R2 被删除
        db_mod.upsert_articles(
            self.db, [_art(k) for k in "ABCD"], [],
            title_review_groups=[_review_group("g1", list("ABCD"))])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM title_duplicate_reviews").fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM title_duplicate_undo WHERE undone=0").fetchone()[0], 0)
        # 修前：undo_available 恒 True 且 undo 连续 3 次 ValueError（红）
        self.assertFalse(db_mod.title_duplicate_undo_available(self.db, "bob"))
        self.assertFalse(db_mod.undo_title_duplicate_review(self.db, "bob"))

    def test_single_group_happy_path_unchanged(self):
        """对照组：不合并的普通决策 + 撤销链逐位不变。"""
        review_id = self._pending_review()
        db_mod.decide_title_duplicate_review(
            self.db, review_id, "duplicate", keeper_key="A", decided_by="carol")
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT status,keeper_key,decision_by FROM title_duplicate_reviews "
                "WHERE review_id=?", (review_id,)).fetchone(),
                ("duplicate", "A", "carol"))
        self.assertTrue(db_mod.undo_title_duplicate_review(self.db, "carol"))
        self.assertEqual(self._marker("B"), None)


# ---------------------------------------------------------------------------
# F3-31 · import_order 与 MAX 读取同事务
# ---------------------------------------------------------------------------
class _BarrierAtMaxConnection:
    """在 ``SELECT COALESCE(MAX(import_order)`` 处等待另一线程（复审同款代理）。

    真实并发不一定命中窗口；把两个写者精确停在「已读到旧 MAX、尚未插入」
    这一刻，才是不依赖时序的复现。修前的读 MAX 在事务外，此时双方都读到
    同一旧值 → 落同一排序键（红）。
    """

    def __init__(self, conn, barrier):
        self._conn = conn
        self._barrier = barrier

    def execute(self, sql, params=()):
        result = self._conn.execute(sql, params)
        # 只在 **autocommit** 下读 MAX 时同步：那正是修前「读在事务外」的
        # 窗口。修后读取已处在 BEGIN IMMEDIATE 内，两个写者改在取写锁时
        # 自然排队，这里不再触发屏障（否则只会等到超时）。
        if "COALESCE(MAX(import_order)" in sql and not self._conn.in_transaction:
            try:
                self._barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass
        return result

    def __getattr__(self, name):
        return getattr(self._conn, name)


class ImportOrderRaceTests(unittest.TestCase):
    def test_import_order_is_unique_when_both_read_stale_max(self):
        with tempfile.TemporaryDirectory(prefix="rf_r9l1_f331w_") as tmp:
            db = str(Path(tmp) / "lib.db")
            db_mod.init_db(db)
            barrier = threading.Barrier(2)
            original = db_mod._connect
            db_mod._connect = lambda path: _BarrierAtMaxConnection(original(path), barrier)
            errors: list[BaseException] = []

            def worker(key: str) -> None:
                try:
                    db_mod.upsert_articles(
                        db, [_art(key, f"T {key}", doi=f"10.1/{key}")], [])
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            try:
                threads = [threading.Thread(target=worker, args=(k,))
                           for k in ("k1", "k2")]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=60)
            finally:
                db_mod._connect = original
            self.assertEqual(errors, [])
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT zotero_key,import_order FROM articles ORDER BY zotero_key"
                ).fetchall()
            orders = [row[1] for row in rows]
            self.assertEqual(len(set(orders)), len(orders),
                             f"出现重复 import_order: {rows}")

    def test_import_order_is_unique_under_thread_barrier(self):
        """双线程真并发冒烟（不依赖时序，仅作补充）。"""
        with tempfile.TemporaryDirectory(prefix="rf_r9l1_f331_") as tmp:
            db = str(Path(tmp) / "lib.db")
            db_mod.init_db(db)
            barrier = threading.Barrier(2)
            errors: list[BaseException] = []

            def worker(key: str) -> None:
                try:
                    barrier.wait(timeout=20)
                    db_mod.upsert_articles(db, [_art(key, f"T {key}", doi=f"10.1/{key}")], [])
                except BaseException as exc:  # noqa: BLE001 - 探针要看到任何逃逸
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(key,)) for key in ("k1", "k2")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
            self.assertEqual(errors, [])
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT zotero_key,import_order FROM articles ORDER BY zotero_key"
                ).fetchall()
            orders = [row[1] for row in rows]
            self.assertEqual(len(set(orders)), len(orders),
                             f"出现重复 import_order: {rows}")

    def test_sequential_imports_keep_dense_order(self):
        """对照组：单线程连续导入的排序键逐位不变（0,1,2…）。"""
        with tempfile.TemporaryDirectory(prefix="rf_r9l1_f331b_") as tmp:
            db = str(Path(tmp) / "lib.db")
            db_mod.init_db(db)
            for index, key in enumerate(("k1", "k2", "k3")):
                db_mod.upsert_articles(db, [_art(key, doi=f"10.1/{key}")], [])
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute(
                    "SELECT zotero_key,import_order FROM articles ORDER BY import_order"
                ).fetchall()
            self.assertEqual(rows, [("k1", 0), ("k2", 1), ("k3", 2)])
            self.assertEqual([a.zotero_key for a in db_mod.list_articles(db)],
                             ["k1", "k2", "k3"])


# ---------------------------------------------------------------------------
# F3-35 / F3-36 · 同键多行与重复标记行
# ---------------------------------------------------------------------------
class SameKeyMultipleRowsTests(unittest.TestCase):
    def test_same_key_two_known_rows_confirm_and_keep_key(self):
        known = [_art("K", "T1", "10.1/x"), _art("K", "T2", "10.1/x")]
        new = _art("K", "T2", "")
        combined = known + [new]
        _, report = find_duplicates(combined)
        renames = stabilize_conflicting_keys(
            combined,
            report.exact_pairs + report.fuzzy_pairs + report.key_conflict_pairs,
            known=known)
        # 修前：renames=[('K','K-<hash>')]、new.zotero_key='K-<hash>'（红）
        self.assertEqual(renames, [])
        self.assertEqual(new.zotero_key, "K")
        # 对照：单行库行为逐位不变（同样保留 K 走更新）
        single_known = [_art("K", "T2", "10.1/x")]
        single_new = _art("K", "T2", "")
        combined_single = single_known + [single_new]
        _, report_single = find_duplicates(combined_single)
        renames_single = stabilize_conflicting_keys(
            combined_single,
            report_single.exact_pairs + report_single.fuzzy_pairs
            + report_single.key_conflict_pairs,
            known=single_known)
        self.assertEqual(renames_single, [])
        self.assertEqual(single_new.zotero_key, "K")

    def test_disputer_in_second_known_row_still_blocks_merge(self):
        """同键两行都不兼容 → 仍按键冲突处理（不放宽拒绝合法域）。"""
        known = [_art("K", "T1", "10.1/x"), _art("K", "T2", "10.1/y")]
        new = _art("K", "T3", "10.1/z")
        combined = known + [new]
        _, report = find_duplicates(combined)
        stabilize_conflicting_keys(
            combined,
            report.exact_pairs + report.fuzzy_pairs + report.key_conflict_pairs,
            known=known)
        self.assertNotEqual(new.zotero_key, "K")
        self.assertLessEqual(len(new.zotero_key), 300)

    def test_disambiguation_key_accepts_multirow_index(self):
        """``_disambiguation_key`` 对新旧两种索引形态都可用（F3-35 消费点）。"""
        base = "K" * 300
        art = _art(base, "Shared", "10.1/b")
        suffix = hashlib.sha256(
            f"{base}|{_stable_content_id(art)}".encode()).hexdigest()[:12]
        legacy = f"{base}-{suffix}"
        # 单值 dict（既有调用/测试形态）与分组 dict（新形态）结果一致
        single = _disambiguation_key(base, art, {base}, {legacy: art})
        grouped = _disambiguation_key(base, art, {base}, {legacy: [art]})
        self.assertEqual(single, grouped)
        # 命中库内既往历史消歧行时沿用该键（MM6-04 链，行为不变）
        self.assertEqual(single, legacy)
        # 未命中时仍是 ≤300 的现行截断形态
        fresh = _disambiguation_key(base, art, {base}, {})
        self.assertLessEqual(len(fresh), 300)
        self.assertNotEqual(fresh, legacy)


class DuplicateRowIdentityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf_r9l1_f336_")
        self.db = str(Path(self._tmp.name) / "lib.db")
        db_mod.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def _seed_keeper_flip(self) -> None:
        db_mod.upsert_articles(
            self.db,
            [_art("dup-old", "Same", "10.1/y"), _art("keeper-new", "Same", "10.1/y")],
            [])
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute(
                "UPDATE articles SET is_duplicate_of='keeper-new' WHERE zotero_key='dup-old'")

    def test_reimport_refreshes_visible_keeper_not_hidden_duplicate_row(self):
        self._seed_keeper_flip()
        known = db_mod.list_articles(self.db, include_duplicates=True)
        self.assertEqual([a.zotero_key for a in known], ["dup-old", "keeper-new"])
        self.assertEqual(getattr(known[0], "is_duplicate_of", None), "keeper-new")
        record = _art("auto-abcdef123456", "Same", "10.1/y", _generated_key=True)
        combined = known + [record]
        _, report = find_duplicates(combined)
        stabilize_conflicting_keys(
            combined,
            report.exact_pairs + report.fuzzy_pairs + report.key_conflict_pairs,
            known=known)
        # 修前：record 沿用 'dup-old'（筛选不可见行，红）
        self.assertEqual(record.zotero_key, "keeper-new")
        # 两条独立防线都必须生效：身份索引（首见优先）与组内沿用（combined
        # 形态的 root 就是 dup-old）。
        self.assertEqual(
            dedup_mod._canonical_known_key(
                {"dup-old": [known[0]], "keeper-new": [known[1]]}, "dup-old"),
            "keeper-new")
        record.title = "refreshed"
        db_mod.upsert_articles(self.db, [record], [])
        with closing(sqlite3.connect(self.db)) as conn:
            rows = dict(conn.execute(
                "SELECT zotero_key,title FROM articles").fetchall())
        self.assertEqual(rows["keeper-new"], "refreshed")
        self.assertEqual(rows["dup-old"], "Same")
        # 重复标记行仍在，筛选可见集合不变
        self.assertEqual([a.zotero_key for a in db_mod.list_articles(self.db)],
                         ["keeper-new"])

    def test_separated_call_form_reroutes_to_keeper(self):
        """分离形态：身份索引唯一命中重复标记行时，仍须改挂其 keeper。

        构造：重复行与 keeper 的 content_hash 不同（keeper 元数据已被更新），
        因此 hash 层只会命中重复行——只有 _canonical_known_key 的链解析能把
        落点改到筛选可见的 keeper（修前 rec 沿用 dup-old → 红）。
        """
        db_mod.upsert_articles(
            self.db,
            [_art("dup-old", "Same", "10.1/y"), _art("keeper-new", "Same", "10.1/y")],
            [])
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute(
                "UPDATE articles SET is_duplicate_of='keeper-new' WHERE zotero_key='dup-old'")
            # keeper 的标题被更新过 → content_hash 与重复行不同
            conn.execute(
                "UPDATE articles SET title='Same (revised)' WHERE zotero_key='keeper-new'")
        known = db_mod.list_articles(self.db, include_duplicates=True)
        # 只让**重复行**带 _record_id（最高优先层的唯一命中），keeper 不带：
        # 身份索引（含「活动行优先」排序）无论如何都只能解析到 dup-old，
        # 落点必须靠 keeper 链解析纠正（修前 rec 沿用 'dup-old' → 红）。
        record = _art("auto-sep", "Same", "10.1/y",
                      _generated_key=True, _record_id="rec-1")
        known[0].raw = {"_record_id": "rec-1"}
        renames = stabilize_conflicting_keys([record], [], known=known)
        self.assertEqual(renames, [])
        self.assertEqual(record.zotero_key, "keeper-new")

    def test_without_duplicate_marker_behaviour_unchanged(self):
        """对照组：无重复标记时身份索引与键逐位不变。"""
        db_mod.upsert_articles(
            self.db,
            [_art("old", "Same", "10.1/y"), _art("keeper", "Same", "10.1/y")],
            [])
        known = db_mod.list_articles(self.db, include_duplicates=True)
        record = _art("auto-xyz", "Same", "10.1/y", _generated_key=True)
        combined = known + [record]
        _, report = find_duplicates(combined)
        stabilize_conflicting_keys(
            combined,
            report.exact_pairs + report.fuzzy_pairs + report.key_conflict_pairs,
            known=known)
        self.assertEqual(record.zotero_key, "old")

    def test_non_duplicate_rows_keep_listing_shape(self):
        db_mod.upsert_articles(self.db, [_art("K1"), _art("K2")], [])
        articles = db_mod.list_articles(self.db, include_duplicates=True)
        self.assertEqual([a.zotero_key for a in articles], ["K1", "K2"])
        self.assertEqual([a.title for a in articles], ["T K1", "T K2"])
        self.assertEqual([getattr(a, "is_duplicate_of", "missing") for a in articles],
                         [None, None])


# ---------------------------------------------------------------------------
# F3-37 · 超长键迁移目标不得随兄弟键存在而变
# ---------------------------------------------------------------------------
class OverlongCollapseTests(unittest.TestCase):
    KEY1 = "X" * 287 + "P" * 13 + "-1234567890ab"
    KEY2 = "X" * 287 + "Q" * 13 + "-1234567890ab"

    def test_collapsing_tail_keys_get_distinct_single_key_targets(self):
        self.assertEqual(len(self.KEY1), 313)
        self.assertEqual(len(self.KEY2), 313)
        # 单键公式仍是历史形态（MM6-04 沿用链依赖它，行为不变）
        self.assertEqual(compliant_key_for_overlong(self.KEY1),
                         compliant_key_for_overlong(self.KEY2))
        with tempfile.TemporaryDirectory(prefix="rf_r9l1_f337_") as tmp:
            db = str(Path(tmp) / "lib.db")
            db_mod.init_db(db)
            db_mod.upsert_articles(db, [_art(self.KEY1), _art(self.KEY2)], [])
            self.assertEqual(db_mod.migrate_overlong_keys(db), 2)
            with closing(sqlite3.connect(db)) as conn:
                keys = sorted(row[0] for row in conn.execute(
                    "SELECT zotero_key FROM articles"))
            self.assertEqual(len(keys), 2, "不得折叠/丢行")
            self.assertEqual(len(set(keys)), 2)
            for key in keys:
                self.assertLessEqual(len(key), 300)
            # 每个目标只依赖自己的旧键（修前第二条 = …-2，取决于兄弟键在场）
            self.assertIn(fresh_key_for_overlong(self.KEY2), keys)

    def test_single_legacy_key_target_is_stable_alone_and_with_sibling(self):
        """对照组：真历史消歧键（尾部=sha256(base) 前缀形态不存在时）单键结果不变。"""
        base = "B" * 300
        suffix = hashlib.sha256(b"mm6-unit-b").hexdigest()[:12]
        legacy = f"{base}-{suffix}"
        expected = base[:300 - 13] + "-" + suffix
        self.assertEqual(compliant_key_for_overlong(legacy), expected)
        with tempfile.TemporaryDirectory(prefix="rf_r9l1_f337b_") as tmp:
            db = str(Path(tmp) / "lib.db")
            db_mod.init_db(db)
            db_mod.upsert_articles(db, [_art(legacy)], [])
            self.assertEqual(db_mod.migrate_overlong_keys(db), 1)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute(
                    "SELECT zotero_key FROM articles").fetchone()[0], expected)

    def test_fresh_target_is_deterministic_and_bounded(self):
        first = fresh_key_for_overlong(self.KEY2)
        second = fresh_key_for_overlong(self.KEY2)
        self.assertEqual(first, second)
        self.assertLessEqual(len(first), 300)
        self.assertRegex(first, r"-[0-9a-f]{12}$")
        self.assertNotEqual(first, fresh_key_for_overlong(self.KEY1))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
