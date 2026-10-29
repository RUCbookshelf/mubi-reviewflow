"""r7-MM9 修复泳道回归钉：消歧键存量迁移的撤销快照与复筛轮次（MM9-20/21）。

外部复审（minimax_full_audit_9.md §3.20–3.21，deepseek 独立复审
out/external_reviews/mm9_verify_g4.md 双双 confirmed）：

- MM9-20：迁移 JSON 白名单 _JSON_KEY_REFERENCE_COLUMNS 未含
  workflow_import_undo 的 before/after_json。升级前最后一条 import-review
  撤销条目的快照滞留 313 旧键，available() 的「现算快照 == 库存
  after_json」全等比较永久失配 → 迁移后 available=False、真实 HTTP
  POST /import-batches/{id}/undo 409；即便放行，restore() 也会把
  before_json 里的旧 canonical_key 回写。修法：两列入清单（结构感知
  同步——快照内嵌真实表整行，只改与库内列同步的键位，库内未迁移的
  staged_key/incoming_key/article_json 等位置必须原样保留，否则全等
  比较同样失配）。
- MM9-21：workflow_screen_rounds.snapshot_json 是 {文章键: 指纹}，文章键
  在 JSON 对象键名位（带引号精确 token 替换可命中）。滞留旧键时轮次
  keys/pending_keys 全为库内已不存在的旧键：completed 永不收敛、初筛
  队列被 round_keys 过滤为空、开启新轮次被 409 阻断（手动 finish 才能
  释放）。修法：snapshot_json 入清单。

钉子（破坏即红，造库法与 mm9_verify_g4.md §1/§2 相同——真实
review_imports.prepare/activate、真实 import_review_undo.snapshot/
remember/available/restore、真实 screening_rounds.start/current、真实
init_db 迁移入口）：

- MM9-20 主钉：prepare/activate 造 313 键旧库 → 第二批同键 exact_doi
  记录生成 metadata proposal → mutate() 同序列 undo remember → init_db
  迁移 → available=True 且 restore 成功、回写的是迁移后新键（修前
  available=False / restore ValueError → 红）；迁移幂等：再跑
  init_db 后 after_json 逐字节不变、available 仍 True。
- MM9-21 主钉：旧库建复筛轮次（313 键 + 该键 decisions 行，按复审口径
  这是唯一能存在的形态）→ 迁移 → current() keys 为 300 新键；对新键
  重新决策后 completed==total 收敛（修前 keys 恒为 313 旧键、completed
  永不等于 total → 红）。
- 破坏态钉（R4）：以 monkeypatch 把 _JSON_KEY_REFERENCE_COLUMNS 还原为
  修复前的 3 列，在同一造库法下复现「钉子必失败」的两态——撤销
  available=False 且 restore 抛 ValueError、轮次滞留旧键且不收敛；
  并断言库存快照文本确实仍持旧键（证明列缺失即载荷滞留）。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import db as db_mod
from coscreen import import_review_undo as review_undo
from coscreen import review_imports as imports
from coscreen import review_workflow as workflow
from coscreen import screening_rounds as rounds_mod
from coscreen.dedup import compliant_key_for_overlong
from coscreen.models import Article

KEY_MAX = 300

#: 修复前（基线 5cb6314）的 JSON 键引用列清单——破坏态钉用它还原「不加列」
PRE_FIX_JSON_COLUMNS = (
    ("title_duplicate_reviews", "members_json"),
    ("title_duplicate_undo", "before_json"),
    ("title_duplicate_undo", "after_json"),
)


def _legacy_key(prefix: str) -> str:
    """313 字符历史键形态：base(300) + "-" + 12hex 后缀（G-02 之前原样入库）。"""
    key = f"{prefix * 300}-abcdef123456"
    assert len(key) == 313
    return key


def _saved_screen_rows(db: str) -> dict:
    """与 custom_backend.review_workflow_api.saved_rows('screen') 同一口径。"""
    with closing(workflow.connect(db, read_only=True)) as conn:
        return {row["zotero_key"]: dict(row)
                for row in conn.execute("SELECT * FROM decisions")}


def _no_title_groups(*_args, **_kwargs):
    """prepare 的 title_groups 参数：场景内标题唯一，真实实现亦返回空组。"""
    return []


# ---------------------------------------------------------------------------
# 造库法（mm9_verify_g4.md §1）：真实 prepare/activate + 真实 undo 链路
# ---------------------------------------------------------------------------
def _seed_import_undo_legacy_db(db: str) -> dict:
    """构造 v2.1.3 形态旧库：313 键文章 + 升级前最后一条 metadata 撤销条目。

    1. 第一批 prepare+activate：313 超长来源键原样入库成 active 文章
       （v2.1.3 解析器无长度截断）；
    2. 第二批 prepare：同键、同 DOI、不同摘要的记录 → exact_doi 链到
       313 键文章并生成 metadata proposal；
    3. 与 custom_backend/review_imports_api.mutate() 逐字同序：snapshot →
       decide_metadata(accept_incoming) → version+1 → remember，得到
       **升级前**的撤销条目（before/after_json 内嵌 313 旧键）。
    """
    db_mod.init_db(db)
    imports.migrate(db)
    review_undo.migrate(Path(db))
    legacy = _legacy_key("K")

    def _files(digest: str, abstract: str) -> list[dict]:
        art = Article(zotero_key=legacy, title="Legacy keyed article",
                      doi="10.999/mm9-migration", abstract=abstract)
        return [{"name": "legacy.csv", "digest": digest,
                 "source_id": workflow.UNASSIGNED, "articles": [art],
                 "originals": [{"Key": legacy, "Title": art.title, "DOI": art.doi}]}]

    with closing(workflow.connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        imports.prepare(conn, _files("d1", "alpha"), batch_id="b1", history_id="h1",
                        owner_id="owner-1", import_type="batch", protected=False,
                        title_groups=_no_title_groups, progress_callback=None)
    with closing(workflow.connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        summary = imports.activate(conn, Path(db), "b1")
    assert summary["activated_new"] == 1, summary
    with closing(workflow.connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        plan = imports.prepare(conn, _files("d2", "beta"), batch_id="b2", history_id="h2",
                               owner_id="owner-1", import_type="batch", protected=False,
                               title_groups=_no_title_groups, progress_callback=None)
    rid = plan["new_record_ids"][0]
    with closing(workflow.connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        imports.batch_summary(conn, "b2")  # mutate() 的批次存在性校验
        before = review_undo.snapshot(conn, "metadata", rid)
        imports.decide_metadata(conn, "b2", rid, "accept_incoming", "u1")
        conn.execute("UPDATE workflow_state SET version=version+1 WHERE singleton=1")
        version = conn.execute(
            "SELECT version FROM workflow_state WHERE singleton=1").fetchone()[0]
        review_undo.remember(conn, "b2", "u1", "metadata", rid, before, version)
    return {"legacy": legacy, "rid": rid}


# ---------------------------------------------------------------------------
# 造库法（mm9_verify_g4.md §2）：313 键文章 + 该键决策行 + 升级前轮次
# ---------------------------------------------------------------------------
def _seed_rounds_legacy_db(db: str) -> str:
    db_mod.init_db(db)
    workflow.migrate(db)
    rounds_mod.migrate(db)
    legacy = _legacy_key("R")
    db_mod.upsert_articles(db, [
        Article(zotero_key=legacy, title="Rounds legacy article",
                doi="10.999/mm9-rounds")], [])
    # 复审口径：>300 键的决策行只能经 CLI 决策回导（restore_decisions，
    # 只校验键在库、不限长）写入——HTTP DecisionIn 对其 422。
    db_mod.save_decision(db, legacy, "maybe")
    return legacy


class ImportUndoMigrationTests(unittest.TestCase):
    """MM9-20：迁移后 import-review 撤销必须恢复可用。"""

    def test_undo_available_and_restorable_after_migration(self):
        with tempfile.TemporaryDirectory(prefix="rf_mm9_undo_") as tmp:
            db = str(Path(tmp) / "owner.test.db")
            seeded = _seed_import_undo_legacy_db(db)
            legacy, rid = seeded["legacy"], seeded["rid"]
            # 前置健全性：升级前该撤销条目确实可用（复审 g4_group.out 同口径）
            with closing(workflow.connect(db, read_only=True)) as conn:
                self.assertTrue(review_undo.available(conn, "b2", "u1")["available"])
            # 升级：与 _screener_db 每请求触发的同一入口
            db_mod.init_db(db)
            new_key = compliant_key_for_overlong(legacy)
            self.assertLessEqual(len(new_key), KEY_MAX)
            with closing(sqlite3.connect(db)) as conn:
                keys = [r[0] for r in conn.execute("SELECT zotero_key FROM articles")]
            self.assertEqual(keys, [new_key], "文章键必须已迁移为合规新键")
            # 幂等：第二次 init_db 对快照零改动（无双重替换）
            with closing(sqlite3.connect(db)) as conn:
                after_json_1 = conn.execute(
                    "SELECT after_json FROM workflow_import_undo WHERE id=1").fetchone()[0]
            db_mod.init_db(db)
            with closing(sqlite3.connect(db)) as conn:
                after_json_2 = conn.execute(
                    "SELECT after_json FROM workflow_import_undo WHERE id=1").fetchone()[0]
            self.assertEqual(after_json_1, after_json_2,
                             "重复迁移改写了撤销快照（幂等被破坏）")
            # 键位级断言：与库内迁移同步的键列换成新键；库内未迁移的位置
            # （staged_key/incoming_key/内嵌 article_json 等，MM9-23/24 口径
            # 另行处理）原样保留——这正是 available() 全等比较通过的前提。
            snap = json.loads(after_json_1)
            self.assertEqual(snap["records"][0]["canonical_key"], new_key)
            self.assertEqual(snap["proposals"][0]["canonical_key"], new_key)
            self.assertEqual(snap["articles"][0]["zotero_key"], new_key)
            self.assertEqual(snap["acceptances"][0]["zotero_key"], new_key)
            self.assertEqual(snap["records"][0]["staged_key"], legacy)
            # 主断言（修前 available=False → 红）：迁移前最后一条撤销条目仍可用
            with closing(workflow.connect(db, read_only=True)) as conn:
                info = review_undo.available(conn, "b2", "u1")
            self.assertTrue(info["available"], info)
            self.assertEqual(info["kind"], "metadata")
            # 主断言（修前 ValueError → 红）：restore 成功回放
            with closing(workflow.connect(db)) as conn, conn:
                result = review_undo.restore(conn, "b2", "u1")
            self.assertTrue(result["undone"])
            self.assertEqual(result["subject_id"], rid)
            with closing(workflow.connect(db, read_only=True)) as conn:
                # 元数据回滚到 accept_incoming 之前（摘要 alpha）
                art = conn.execute(
                    "SELECT title, abstract FROM articles WHERE zotero_key=?",
                    (new_key,)).fetchone()
                self.assertEqual((art["title"], art["abstract"]),
                                 ("Legacy keyed article", "alpha"))
                # restore 回写的是迁移后新键，而非旧 313 键（复审指出的回写面）
                canonical = conn.execute(
                    "SELECT canonical_key FROM workflow_source_records WHERE id=?",
                    (rid,)).fetchone()[0]
                self.assertEqual(canonical, new_key)
                action = conn.execute(
                    "SELECT action FROM workflow_import_metadata WHERE record_id=?",
                    (rid,)).fetchone()[0]
                self.assertIsNone(action, "撤销未恢复 metadata proposal 的待审状态")
                state = conn.execute(
                    "SELECT state FROM workflow_import_undo WHERE id=1").fetchone()[0]
                self.assertEqual(state, "undone")
                acceptance = conn.execute(
                    "SELECT zotero_key FROM workflow_metadata_acceptances").fetchall()
                self.assertEqual([r[0] for r in acceptance], [new_key],
                                 "撤销发布的更正修订必须落在迁移后新键上")

    def test_undo_nail_fails_without_new_columns(self):
        # R4 破坏态：把 _JSON_KEY_REFERENCE_COLUMNS 还原为修复前的 3 列，
        # 同一造库法下主钉子的全部断言翻转为失败态（available=False、
        # restore ValueError、快照滞留旧键）——不加列钉子必红。
        with tempfile.TemporaryDirectory(prefix="rf_mm9_undo_broken_") as tmp:
            db = str(Path(tmp) / "owner.test.db")
            seeded = _seed_import_undo_legacy_db(db)
            legacy = seeded["legacy"]
            with mock.patch.object(db_mod, "_JSON_KEY_REFERENCE_COLUMNS",
                                   PRE_FIX_JSON_COLUMNS):
                db_mod.init_db(db)
            with closing(sqlite3.connect(db)) as conn:
                after_json = conn.execute(
                    "SELECT after_json FROM workflow_import_undo WHERE id=1").fetchone()[0]
            snap = json.loads(after_json)
            self.assertEqual(snap["records"][0]["canonical_key"], legacy,
                             "不加列时撤销快照滞留旧键")
            with closing(workflow.connect(db, read_only=True)) as conn:
                info = review_undo.available(conn, "b2", "u1")
            self.assertFalse(info["available"], info)  # 主钉子 available 断言 → 红
            with closing(workflow.connect(db)) as conn, conn:
                with self.assertRaises(ValueError):
                    review_undo.restore(conn, "b2", "u1")  # 主钉子 restore 断言 → 红
            # 撤销失败不留半态：条目仍 available 状态、文章元数据仍是已接受态
            with closing(workflow.connect(db, read_only=True)) as conn:
                state = conn.execute(
                    "SELECT state FROM workflow_import_undo WHERE id=1").fetchone()[0]
                abstract = conn.execute("SELECT abstract FROM articles").fetchone()[0]
            self.assertEqual(state, "available")
            self.assertEqual(abstract, "beta")


class ScreenRoundMigrationTests(unittest.TestCase):
    """MM9-21：迁移后复筛轮次 current() 必须返回新键、轮次可收敛。"""

    def test_round_keys_migrated_and_round_converges(self):
        with tempfile.TemporaryDirectory(prefix="rf_mm9_rounds_") as tmp:
            db = str(Path(tmp) / "owner.test.db")
            legacy = _seed_rounds_legacy_db(db)
            saved = _saved_screen_rows(db)
            rounds_mod.start(db, "s1", saved, [legacy])  # 升级前已开启的轮次
            before = rounds_mod.current(db, saved)
            self.assertEqual(before["keys"], [legacy])  # 前置健全性：升级前旧键
            self.assertEqual(before["completed"], 0)
            db_mod.init_db(db)  # 升级迁移
            new_key = compliant_key_for_overlong(legacy)
            self.assertLessEqual(len(new_key), KEY_MAX)
            saved2 = _saved_screen_rows(db)  # 决策行已随列名白名单同步为新键
            info = rounds_mod.current(db, saved2)
            # 主断言（修前滞留 313 旧键 → 红）：键域为迁移后 300 新键
            self.assertEqual(info["keys"], [new_key], info)
            self.assertEqual(info["pending_keys"], [new_key],
                             "基线指纹未变：该键尚待复筛")
            self.assertEqual(info["total"], 1)
            self.assertEqual(info["completed"], 0)
            # 主断言（修前永不收敛 → 红）：对新键重新决策后 completed==total
            db_mod.save_decision(db, new_key, "include")
            done = rounds_mod.current(db, _saved_screen_rows(db))
            self.assertEqual(done["completed"], done["total"])
            self.assertEqual(done["pending_keys"], [])
            self.assertEqual(done["keys"], [new_key])
            # 幂等：再跑迁移零改动，键域稳定
            self.assertEqual(db_mod.migrate_overlong_keys(db), 0)
            self.assertEqual(
                rounds_mod.current(db, _saved_screen_rows(db))["keys"], [new_key])

    def test_round_nail_fails_without_new_columns(self):
        # R4 破坏态：不加 (workflow_screen_rounds, snapshot_json) 列时，
        # 轮次滞留 313 旧键、重新决策后 completed 永不等于 total。
        with tempfile.TemporaryDirectory(prefix="rf_mm9_rounds_broken_") as tmp:
            db = str(Path(tmp) / "owner.test.db")
            legacy = _seed_rounds_legacy_db(db)
            saved = _saved_screen_rows(db)
            rounds_mod.start(db, "s1", saved, [legacy])
            with mock.patch.object(db_mod, "_JSON_KEY_REFERENCE_COLUMNS",
                                   PRE_FIX_JSON_COLUMNS):
                db_mod.init_db(db)
            new_key = compliant_key_for_overlong(legacy)
            info = rounds_mod.current(db, _saved_screen_rows(db))
            self.assertEqual(info["keys"], [legacy], "不加列时快照滞留旧键")
            self.assertEqual(len(info["keys"][0]), 313)
            # 即便对迁移后新键重新决策，轮次也永不收敛
            db_mod.save_decision(db, new_key, "include")
            done = rounds_mod.current(db, _saved_screen_rows(db))
            self.assertNotEqual(done["completed"], done["total"])
            self.assertEqual(done["pending_keys"], [legacy])
            with closing(sqlite3.connect(db)) as conn:
                snapshot = conn.execute(
                    "SELECT snapshot_json FROM workflow_screen_rounds "
                    "WHERE status='active'").fetchone()[0]
            # 注意不能用子串判断：新键（截断形态）本就是旧键的子串；
            # 解析后的对象键名才是判据。
            self.assertEqual(list(json.loads(snapshot)), [legacy],
                             "轮次快照对象键滞留旧键")


if __name__ == "__main__":
    unittest.main()
