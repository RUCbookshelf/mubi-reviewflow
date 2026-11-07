"""r8-FL2 迁移泳道回归钉：MM9-23 / MM9-24 / D-05-03（缓修池族E+文档项）。

外部复审证据（out/external_reviews/，两条 MM9 均有 deepseek 独立复审判定）：

- MM9-23（mm9_verify_g4.md §4，confirmed）：迁移白名单只改
  workflow_source_records.canonical_key，staged_key/incoming_key 滞留 313
  旧键 → batch_summary（review_imports.py:462-467）的
  ``canonical_key in (staged_key, incoming_key)`` 归属判定迁移后恒假 →
  导入报告/导入历史的 activated_new_total 静默清零（复审实测 1→0，
  行态 (300,313,313)）。修法：两列纳入 _article_key_reference_columns
  的列名白名单。
- MM9-24（mm9_verify_g4.md §5，confirmed 低危）：引用型 JSON 载荷
  （workflow_source_records.article_json、workflow_import_metadata.
  before_json/incoming_json）迁移后仍持旧键。修法：入清单，且**只改
  顶层 zotero_key 键位**（结构感知）。r2 评审两处阻塞项即行文于此：
  (1) 按值的字面相等做全文替换会连 title/abstract 一并改写 → 迁移后
  decide_metadata 的 canonical 比对失配、原本可采纳的提议被新增拒绝
  为 409（违反 R2）；(2) original_json 与 articles.raw_json 是溯源载荷
  （review_imports.py:4「Original payloads are immutable」、
  review_imports_api.py:555-556、csv_parser.py:505-508、
  mm9_verify_g4.md:205），必须保持原始值、不入同步范围。
  连带约束（MM9-20 不回归）：workflow_import_undo 快照内嵌真实表整行，
  内嵌副本必须走与真实列**同一函数**（_rewrite_payload_key_fields），
  否则升级前最后一条撤销条目重新报废（POST /undo 409）。
  另：workflow_import_candidates.signature 覆盖成员 ref_id
  （review_imports.py:144-145），迁移后须重算，否则重导产生重复候选。
- D-05-03（glm_verify_v2.md §7，confirmed）：_UnionFind 注释称「同侧以
  按大小合并」而根选择只按索引、_size 只写不读（:215/:235/:238 全为
  写入）。修法：删死状态 _size、注释如实描述「根只按较小索引（产品
  语义：保留输入顺序靠前条目），防退化由路径压缩单独承担」。

钉子（破坏即红；造库法与 mm9_verify_g4.md §1 相同——真实
review_imports.prepare/activate、真实 import_review_undo.snapshot/
remember/available/restore、真实 init_db 迁移入口；Article.raw 携带
来源 Key 列以覆盖溯源载荷）：

- MM9-23 主钉：313 键旧库迁移后 batch_summary.activated_new_total 保持
  1（修前 0 → 红）、三键列行态 (300,300,300)（修前 (300,313,313)）。
- MM9-24 主钉：三个引用型载荷的顶层 zotero_key 同步；original_json /
  articles.raw_json 及嵌套副本保持原始来源值（溯源契约）；幂等；
  撤销快照内嵌副本与真实列**逐字节一致**且 available() 仍 True。
  R2 钉：title==abstract==旧键时迁移不改写它们、decide_metadata 仍成功。
  签名钉：迁移后 add_candidate 复用既有候选（不产生第二行）。
- D-05-03 主钉：dedup.py 源不再含「按大小合并」假声称与 _size 死状态；
  最小索引根 + 路径压缩行为钉住（防误删改坏语义）。
- 破坏态钉（R4）：(a) 把 _article_key_reference_columns 还原为不含
  staged_key/incoming_key → activated_new_total=0、行态 (300,313,313)；
  (b) 把 _JSON_KEY_REFERENCE_COLUMNS 还原为 r7 的 6 列 → 载荷键位滞留旧键；
  (c) 只屏蔽快照内嵌载荷同步（_JSON_KEY_PAYLOAD_COLUMNS 置空，真实列
  仍迁移）→ available()=False、restore ValueError（MM9-20 回归即红）。
"""

from __future__ import annotations

import inspect
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
from coscreen import dedup as dedup_mod
from coscreen import import_review_undo as review_undo
from coscreen import review_imports as imports
from coscreen import review_workflow as workflow
from coscreen.dedup import compliant_key_for_overlong
from coscreen.models import Article

KEY_MAX = 300

#: r7 修复后（本组修复前基线 a080fde）的 JSON 键引用列清单——MM9-24
#: 破坏态钉用它还原「不加 5 个真实表载荷列」。
R7_JSON_COLUMNS = (
    ("title_duplicate_reviews", "members_json"),
    ("title_duplicate_undo", "before_json"),
    ("title_duplicate_undo", "after_json"),
    ("workflow_import_undo", "before_json"),
    ("workflow_import_undo", "after_json"),
    ("workflow_screen_rounds", "snapshot_json"),
)

_REAL_REFERENCE_COLUMNS = db_mod._article_key_reference_columns


def _legacy_key(prefix: str) -> str:
    """313 字符历史键形态：base(300) + "-" + 12hex 后缀（G-02 之前原样入库）。"""
    key = f"{prefix * 300}-abcdef123456"
    assert len(key) == 313
    return key


def _prefilter_reference_columns(conn):
    """MM9-23 破坏态：从发现结果中剔除 staged_key/incoming_key（等价修复前白名单）。"""
    return [t for t in _REAL_REFERENCE_COLUMNS(conn)
            if t[1] not in ("staged_key", "incoming_key")]


def _no_title_groups(*_args, **_kwargs):
    """prepare 的 title_groups 参数：场景内标题唯一，真实实现亦返回空组。"""
    return []


def _seed_legacy_import_db(db: str, *, title_is_key: bool = False) -> dict:
    """构造 v2.1.3 形态旧库（mm9_verify_g4.md §1 造库法 + 溯源载荷）。

    1. 第一批 prepare+activate：313 超长来源键原样入库成 active 文章
       （staged_key=incoming_key=canonical_key=313、outcome=new）；Article.raw
       携带来源 Key 列 → article_json/before_json/incoming_json 三个载荷
       内嵌旧键，original_json/articles.raw_json 保留原始来源值（provenance）；
    2. 第二批 prepare：同键、同 DOI、不同摘要 → exact_doi 链到 313 键文章
       并生成 metadata proposal（before_json=articles 整行、incoming_json=
       article_json，均内嵌旧键）；
    3. 与 custom_backend/review_imports_api.mutate() 逐字同序：snapshot →
       decide_metadata(accept_incoming) → version+1 → remember，得到升级前
       的撤销条目（快照内嵌真实表整行含全部载荷列）。

    ``title_is_key``：把 title/abstract 也设为 313 旧键本身（v2.1.3 CSV
    里 Key 列与 Title 列同值可达），用于钉住「迁移不得按值的字面相等改写
    非键字段」——否则 decide_metadata 的 canonical 比对失配（r2 评审阻塞项）。
    """
    db_mod.init_db(db)
    imports.migrate(db)
    review_undo.migrate(Path(db))
    legacy = _legacy_key("K")

    def _files(digest: str, abstract: str, *, key_shaped: bool) -> list[dict]:
        raw = {"Key": legacy, "Title": "Legacy keyed article", "DOI": "10.999/r8-fl2"}
        art = Article(zotero_key=legacy,
                      title=legacy if key_shaped else "Legacy keyed article",
                      doi="10.999/r8-fl2", abstract=abstract,
                      source_format="csv", raw=raw)
        return [{"name": "legacy.csv", "digest": digest,
                 "source_id": workflow.UNASSIGNED, "articles": [art],
                 "originals": [dict(raw)]}]

    with closing(workflow.connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        imports.prepare(conn, _files("d1", "alpha", key_shaped=title_is_key),
                        batch_id="b1", history_id="h1",
                        owner_id="owner-1", import_type="batch", protected=False,
                        title_groups=_no_title_groups, progress_callback=None)
    with closing(workflow.connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        summary = imports.activate(conn, Path(db), "b1")
    assert summary["activated_new"] == 1, summary
    with closing(workflow.connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        plan = imports.prepare(conn, _files("d2", "beta", key_shaped=title_is_key),
                               batch_id="b2", history_id="h2",
                               owner_id="owner-1", import_type="batch", protected=False,
                               title_groups=_no_title_groups, progress_callback=None)
    rid = plan["new_record_ids"][0]
    with closing(workflow.connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        before = review_undo.snapshot(conn, "metadata", rid)
        imports.decide_metadata(conn, "b2", rid, "accept_incoming", "u1")
        conn.execute("UPDATE workflow_state SET version=version+1 WHERE singleton=1")
        version = conn.execute(
            "SELECT version FROM workflow_state WHERE singleton=1").fetchone()[0]
        review_undo.remember(conn, "b2", "u1", "metadata", rid, before, version)
    return {"legacy": legacy, "rid": rid}


class MM9_23ActivatedNewTotalTests(unittest.TestCase):
    """MM9-23：迁移后 activated_new_total 不得失真。"""

    def test_activated_new_total_preserved_after_migration(self):
        with tempfile.TemporaryDirectory(prefix="rf_r8_mm923_") as tmp:
            db = str(Path(tmp) / "owner.test.db")
            seeded = _seed_legacy_import_db(db)
            legacy = seeded["legacy"]
            # 前置健全性（复审 g4_group.out BEFORE 同口径）
            with closing(workflow.connect(db, read_only=True)) as conn:
                before = imports.batch_summary(conn, "b1")
            self.assertEqual(before["activated_new_total"], 1)
            self.assertEqual(before["canonical_unique"], 1)
            self.assertEqual(before["existing_source_links"], 1)
            db_mod.init_db(db)  # 升级：与 _screener_db 每请求触发的同一入口
            new_key = compliant_key_for_overlong(legacy)
            self.assertLessEqual(len(new_key), KEY_MAX)
            # 主断言（修前 1→0 → 红）：导入报告/导入历史的「新增」计数不失真
            with closing(workflow.connect(db, read_only=True)) as conn:
                after = imports.batch_summary(conn, "b1")
            self.assertEqual(after["activated_new_total"], 1, after)
            self.assertEqual(after["canonical_unique"], 1)
            self.assertEqual(after["existing_source_links"], 1)
            # 行态（修前 (300,313,313) 半迁移 → 红）：三键列全部同步为新键
            with closing(workflow.connect(db, read_only=True)) as conn:
                lens = [tuple(r) for r in conn.execute(
                    "SELECT length(canonical_key),length(staged_key),length(incoming_key)"
                    " FROM workflow_source_records WHERE id IN"
                    " (SELECT record_id FROM workflow_import_members WHERE batch_id='b1')"
                ).fetchall()]
            self.assertEqual(lens, [(KEY_MAX, KEY_MAX, KEY_MAX)], lens)
            with closing(workflow.connect(db, read_only=True)) as conn:
                keys = conn.execute(
                    "SELECT canonical_key,staged_key,incoming_key FROM"
                    " workflow_source_records WHERE id IN"
                    " (SELECT record_id FROM workflow_import_members WHERE batch_id='b1')"
                ).fetchone()
            self.assertEqual(tuple(keys), (new_key, new_key, new_key))
            # 幂等：重复迁移零改动
            self.assertEqual(db_mod.migrate_overlong_keys(db), 0)

    def test_nail_activated_new_total_zeroed_without_staged_incoming_sync(self):
        # R4 破坏态：白名单还原为不含 staged_key/incoming_key → 主钉子全部
        # 断言翻转为失败态（activated_new_total=0、行态半迁移）。
        with tempfile.TemporaryDirectory(prefix="rf_r8_mm923b_") as tmp:
            db = str(Path(tmp) / "owner.test.db")
            legacy = _seed_legacy_import_db(db)["legacy"]
            with mock.patch.object(db_mod, "_article_key_reference_columns",
                                   _prefilter_reference_columns):
                db_mod.init_db(db)
            with closing(workflow.connect(db, read_only=True)) as conn:
                summary = imports.batch_summary(conn, "b1")
            self.assertEqual(summary["activated_new_total"], 0,
                             "不加列时 activated_new_total 静默清零")  # 主钉子 → 红
            with closing(workflow.connect(db, read_only=True)) as conn:
                lens = [tuple(r) for r in conn.execute(
                    "SELECT length(canonical_key),length(staged_key),length(incoming_key)"
                    " FROM workflow_source_records WHERE id IN"
                    " (SELECT record_id FROM workflow_import_members WHERE batch_id='b1')"
                ).fetchall()]
            self.assertEqual(lens, [(KEY_MAX, 313, 313)], lens)  # 行态断言 → 红


class MM9_24PayloadCarrierTests(unittest.TestCase):
    """MM9-24：真实表 JSON 载荷列与撤销快照内嵌副本必须同步。"""

    def _migrated_rows(self, db: str) -> dict:
        with closing(workflow.connect(db, read_only=True)) as conn:
            record = conn.execute(
                "SELECT article_json,original_json FROM workflow_source_records"
                " WHERE id=(SELECT record_id FROM workflow_import_members"
                " WHERE batch_id='b1')").fetchone()
            meta = conn.execute(
                "SELECT before_json,incoming_json FROM workflow_import_metadata"
                " WHERE record_id=(SELECT record_id FROM workflow_import_members"
                " WHERE batch_id='b2')").fetchone()
            raw = conn.execute("SELECT raw_json FROM articles").fetchone()
        return {"article_json": record["article_json"],
                "original_json": record["original_json"],
                "before_json": meta["before_json"],
                "incoming_json": meta["incoming_json"],
                "raw_json": raw["raw_json"]}

    def test_key_positions_synced_and_provenance_preserved(self):
        with tempfile.TemporaryDirectory(prefix="rf_r8_mm924_") as tmp:
            db = str(Path(tmp) / "owner.test.db")
            legacy = _seed_legacy_import_db(db)["legacy"]
            db_mod.init_db(db)
            new_key = compliant_key_for_overlong(legacy)
            rows = self._migrated_rows(db)
            # 键位（MM9-24 主体）：三个引用型载荷的顶层 zotero_key 同步为新键
            self.assertEqual(json.loads(rows["article_json"])["zotero_key"], new_key)  # 主钉 → 红
            for column in ("before_json", "incoming_json"):
                self.assertEqual(json.loads(rows[column])["zotero_key"], new_key, column)
            # 溯源载荷（r2 评审第 2 条）：original_json / articles.raw_json 及
            # 其嵌套副本保持**原始来源值**——模块契约「Original payloads are
            # immutable」（review_imports.py:4）、导入审查 CSV 的
            # 「preserving exact originals」（review_imports_api.py:555-556）。
            self.assertEqual(json.loads(rows["original_json"])["Key"], legacy)  # 主钉 → 红
            self.assertEqual(json.loads(rows["raw_json"])["Key"], legacy)  # 主钉 → 红
            for column in ("before_json", "incoming_json"):
                nested = json.loads(json.loads(rows[column])["raw_json"])
                self.assertEqual(nested["Key"], legacy, f"{column} 嵌套 raw_json 被改写")
            # 幂等：第二次迁移对全部载荷逐字节零改动
            db_mod.init_db(db)
            self.assertEqual(self._migrated_rows(db), rows,
                             "重复迁移改写了 JSON 载荷（幂等被破坏）")

    def test_metadata_decision_still_accepted_when_title_equals_key(self):
        # R2 钉（r2 评审阻塞项）：title/abstract 恰好等于旧键时，迁移**不得**
        # 把它们改成新键——否则 decide_metadata 的 canonical 元数据比对
        # （review_imports.py:439 `metadata(current) != metadata(before_json)`）
        # 失配，原本可采纳的提议被新增拒绝为 409。修前（全文字面替换）此
        # 用例 ValueError → 红。
        with tempfile.TemporaryDirectory(prefix="rf_r8_mm924r2_") as tmp:
            db = str(Path(tmp) / "owner.test.db")
            seeded = _seed_legacy_import_db(db, title_is_key=True)
            legacy, rid = seeded["legacy"], seeded["rid"]
            db_mod.init_db(db)
            new_key = compliant_key_for_overlong(legacy)
            with closing(workflow.connect(db, read_only=True)) as conn:
                # 真实列的非键字段原样保留（未被按值字面相等改写）
                article = conn.execute(
                    "SELECT title FROM articles WHERE zotero_key=?",
                    (new_key,)).fetchone()
                self.assertEqual(article["title"], legacy, "title 被迁移改写")
                meta = conn.execute(
                    "SELECT before_json FROM workflow_import_metadata"
                    " WHERE record_id=?", (rid,)).fetchone()
            payload = json.loads(meta["before_json"])
            self.assertEqual(payload["zotero_key"], new_key)
            self.assertEqual(payload["title"], legacy,
                             "before_json 的 title 被迁移改写（R2 误拒来源）")
            # 主断言（修前 ValueError → 红）：元数据采纳照常成功
            with closing(workflow.connect(db)) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                imports.decide_metadata(conn, "b2", rid, "accept_incoming", "u1")

    def test_nail_payload_key_unsynced_without_columns(self):
        # R4 破坏态：_JSON_KEY_REFERENCE_COLUMNS 还原为 r7 的 6 列 →
        # 引用型载荷的键位滞留旧键。
        with tempfile.TemporaryDirectory(prefix="rf_r8_mm924b_") as tmp:
            db = str(Path(tmp) / "owner.test.db")
            legacy = _seed_legacy_import_db(db)["legacy"]
            with mock.patch.object(db_mod, "_JSON_KEY_REFERENCE_COLUMNS",
                                   R7_JSON_COLUMNS):
                db_mod.init_db(db)
            rows = self._migrated_rows(db)
            for column in ("article_json", "before_json", "incoming_json"):
                self.assertEqual(json.loads(rows[column])["zotero_key"], legacy,
                                 f"不加载荷列时 {column} 滞留旧键")  # 主钉 → 红

    def test_undo_snapshot_consistent_and_available_after_full_sync(self):
        # MM9-20 不回归：真实列迁移后，快照内嵌副本与真实列逐字节一致，
        # available() 全等比较通过、restore 成功。
        with tempfile.TemporaryDirectory(prefix="rf_r8_mm924u_") as tmp:
            db = str(Path(tmp) / "owner.test.db")
            seeded = _seed_legacy_import_db(db)
            legacy, rid = seeded["legacy"], seeded["rid"]
            with closing(workflow.connect(db, read_only=True)) as conn:
                self.assertTrue(review_undo.available(conn, "b2", "u1")["available"])
            db_mod.init_db(db)
            new_key = compliant_key_for_overlong(legacy)
            with closing(sqlite3.connect(db)) as conn:
                after_text = conn.execute(
                    "SELECT after_json FROM workflow_import_undo WHERE id=1").fetchone()[0]
            snap = json.loads(after_text)
            self.assertEqual(snap["records"][0]["id"], rid)
            # 键位级：快照内嵌键列已随白名单同步（staged/incoming 含内）
            for column in ("canonical_key", "staged_key", "incoming_key"):
                self.assertEqual(snap["records"][0][column], new_key, column)
            self.assertEqual(snap["proposals"][0]["canonical_key"], new_key)
            # 内嵌 JSON 载荷副本与真实列**逐字节一致**（同一函数改写的直接证据）
            with closing(workflow.connect(db, read_only=True)) as conn:
                record = conn.execute(
                    "SELECT article_json,original_json FROM workflow_source_records"
                    " WHERE id=?", (rid,)).fetchone()
                meta = conn.execute(
                    "SELECT before_json,incoming_json FROM workflow_import_metadata"
                    " WHERE record_id=?", (rid,)).fetchone()
            self.assertEqual(snap["records"][0]["article_json"], record["article_json"])
            self.assertEqual(snap["records"][0]["original_json"], record["original_json"])
            self.assertEqual(snap["proposals"][0]["before_json"], meta["before_json"])
            self.assertEqual(snap["proposals"][0]["incoming_json"], meta["incoming_json"])
            # 主断言（内嵌同步缺失时 False → 红）：撤销条目仍可用且可回放
            with closing(workflow.connect(db, read_only=True)) as conn:
                info = review_undo.available(conn, "b2", "u1")
            self.assertTrue(info["available"], info)
            with closing(workflow.connect(db)) as conn, conn:
                result = review_undo.restore(conn, "b2", "u1")
            self.assertTrue(result["undone"])

    def test_nail_undo_breaks_when_real_columns_migrated_but_snapshot_not(self):
        # R4 破坏态（MM9-20 回归）：真实载荷列仍迁移（_JSON_KEY_REFERENCE_
        # COLUMNS 未动），但快照内嵌载荷同步被屏蔽（_JSON_KEY_PAYLOAD_
        # COLUMNS 置空）→ available()=False、restore ValueError。
        with tempfile.TemporaryDirectory(prefix="rf_r8_mm924ub_") as tmp:
            db = str(Path(tmp) / "owner.test.db")
            seeded = _seed_legacy_import_db(db)
            legacy, rid = seeded["legacy"], seeded["rid"]
            with mock.patch.object(db_mod, "_JSON_KEY_PAYLOAD_COLUMNS", frozenset()):
                db_mod.init_db(db)
            new_key = compliant_key_for_overlong(legacy)
            # 健全性：真实列确实已迁移（破坏态只破坏快照侧）
            with closing(workflow.connect(db, read_only=True)) as conn:
                record = conn.execute(
                    "SELECT article_json FROM workflow_source_records WHERE id=?",
                    (rid,)).fetchone()
                self.assertEqual(json.loads(record["article_json"])["zotero_key"], new_key)
            with closing(sqlite3.connect(db)) as conn:
                after_text = conn.execute(
                    "SELECT after_json FROM workflow_import_undo WHERE id=1").fetchone()[0]
            snap = json.loads(after_text)
            self.assertEqual(json.loads(snap["records"][0]["article_json"])["zotero_key"],
                             legacy, "屏蔽内嵌同步时快照滞留旧键")
            with closing(workflow.connect(db, read_only=True)) as conn:
                info = review_undo.available(conn, "b2", "u1")
            self.assertFalse(info["available"], info)  # 主钉子 → 红
            with closing(workflow.connect(db)) as conn, conn:
                with self.assertRaises(ValueError):
                    review_undo.restore(conn, "b2", "u1")  # 主钉子 → 红

    def test_candidate_signature_recomputed_so_reimport_does_not_duplicate(self):
        # 评审第 3 条：signature = sha256(成员 refs)，ref_id 迁移后不重算则
        # add_candidate 的去重不变式失效、重导插重复候选。
        with tempfile.TemporaryDirectory(prefix="rf_r8_mm924sig_") as tmp:
            db = str(Path(tmp) / "owner.test.db")
            db_mod.init_db(db)
            imports.migrate(db)
            legacy = _legacy_key("S")
            new_key = compliant_key_for_overlong(legacy)
            db_mod.upsert_articles(db, [
                Article(zotero_key=legacy, title="Sig article", doi="10.999/r8-sig",
                        raw={"Key": legacy})], [])
            with closing(workflow.connect(db)) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO workflow_source_records(id,source_id,digest,row_index,"
                    "filename,original_json,article_json,incoming_key,staged_key,"
                    "outcome,state,created_at) VALUES('rec-1',?,'d',0,'f.csv',"
                    "'{}','{}',?,'staged-x','new','staged','t')",
                    (workflow.UNASSIGNED, legacy))
                cid = imports.add_candidate(
                    conn, {("active", legacy), ("record", "rec-1")}, "exact_key")
            db_mod.init_db(db)  # 迁移
            new_sig = db_mod._candidate_signature(
                [("active", new_key), ("record", "rec-1")])
            with closing(workflow.connect(db)) as conn:
                stored = conn.execute(
                    "SELECT signature FROM workflow_import_candidates WHERE id=?",
                    (cid,)).fetchone()[0]
            self.assertEqual(stored, new_sig,
                             "迁移后 signature 未重算（add_candidate 去重不变式失效）")
            # 主断言（修前产生第二行 → 红）：同（迁移后等价）成员集复用既有候选
            with closing(workflow.connect(db)) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                again = imports.add_candidate(
                    conn, {("active", new_key), ("record", "rec-1")}, "exact_key")
            self.assertEqual(again, cid, "迁移后同一冲突组产生了重复候选")
            with closing(workflow.connect(db)) as conn:
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM workflow_import_candidates").fetchone()[0], 1)

    def test_candidate_undo_available_after_migration(self):
        # r3 评审阻塞项：candidate 撤销快照内嵌候选整行（含 signature，
        # import_review_undo.py:44-49）。迁移重算了真实行 signature 后，
        # 快照侧若不同步，available() 的「现算 == 库存 after_json」失配 →
        # 升级后最后一条候选审阅撤销永久失效（还原层:available()=False）。
        with tempfile.TemporaryDirectory(prefix="rf_r8_mm924cand_") as tmp:
            db = str(Path(tmp) / "owner.test.db")
            db_mod.init_db(db)
            imports.migrate(db)
            review_undo.migrate(Path(db))
            legacy = _legacy_key("C")
            new_key = compliant_key_for_overlong(legacy)
            db_mod.upsert_articles(db, [
                Article(zotero_key=legacy, title="Cand article", doi="10.999/r8-cand",
                        raw={"Key": legacy})], [])
            with closing(workflow.connect(db)) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO workflow_source_records(id,source_id,digest,row_index,"
                    "filename,original_json,article_json,incoming_key,staged_key,"
                    "outcome,state,created_at) VALUES('rec-c',?,'d',0,'f.csv',"
                    "'{}','{}',?,'staged-x','new','staged','t')",
                    (workflow.UNASSIGNED, legacy))
                cid = imports.add_candidate(
                    conn, {("active", legacy), ("record", "rec-c")}, "exact_key")
            # 与 mutate() 同序：snapshot(kind='candidate') → 改动 → version+1
            # → remember
            with closing(workflow.connect(db)) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                before = review_undo.snapshot(conn, "candidate", cid)
                conn.execute("UPDATE workflow_import_candidates SET status='duplicate',"
                             " keeper_key=? WHERE id=?", (legacy, cid))
                conn.execute("UPDATE workflow_state SET version=version+1 WHERE singleton=1")
                version = conn.execute(
                    "SELECT version FROM workflow_state WHERE singleton=1").fetchone()[0]
                review_undo.remember(conn, "b1", "u1", "candidate", cid, before, version)
            with closing(workflow.connect(db, read_only=True)) as conn:
                self.assertTrue(review_undo.available(conn, "b1", "u1")["available"])
            db_mod.init_db(db)  # 迁移
            # 主断言（修前 False → 红）：候选撤销仍然可用
            with closing(workflow.connect(db, read_only=True)) as conn:
                info = review_undo.available(conn, "b1", "u1")
            self.assertTrue(info["available"], info)
            # 双向一致：库存快照 signature == 真实行 signature（新成员集）
            with closing(sqlite3.connect(db)) as conn:
                snap = json.loads(conn.execute(
                    "SELECT after_json FROM workflow_import_undo WHERE id=1").fetchone()[0])
                live = conn.execute(
                    "SELECT signature FROM workflow_import_candidates WHERE id=?",
                    (cid,)).fetchone()[0]
            self.assertEqual(snap["candidates"][0]["signature"], live,
                             "撤销快照的 signature 与真实行不一致")
            self.assertEqual(live, db_mod._candidate_signature(
                [("active", new_key), ("record", "rec-c")]))
            with closing(workflow.connect(db)) as conn, conn:
                self.assertTrue(review_undo.restore(conn, "b1", "u1")["undone"])

    def test_bulk_undo_available_after_migration(self):
        # bulk 撤销把多个 kind 的子快照合并（import_review_undo.py:37-43），
        # 因此 candidate 分支的 signature 失配同样会让 bulk 撤销失效。
        with tempfile.TemporaryDirectory(prefix="rf_r8_mm924bulk_") as tmp:
            db = str(Path(tmp) / "owner.test.db")
            db_mod.init_db(db)
            imports.migrate(db)
            review_undo.migrate(Path(db))
            legacy = _legacy_key("B")
            db_mod.upsert_articles(db, [
                Article(zotero_key=legacy, title="Bulk article", doi="10.999/r8-bulk",
                        raw={"Key": legacy})], [])
            with closing(workflow.connect(db)) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO workflow_source_records(id,source_id,digest,row_index,"
                    "filename,original_json,article_json,incoming_key,staged_key,"
                    "outcome,state,created_at) VALUES('rec-b',?,'d',0,'f.csv',"
                    "'{}','{}',?,'staged-x','new','staged','t')",
                    (workflow.UNASSIGNED, legacy))
                cid = imports.add_candidate(
                    conn, {("active", legacy), ("record", "rec-b")}, "exact_key")
            subject = json.dumps([{"kind": "candidate", "id": cid}])
            with closing(workflow.connect(db)) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                before = review_undo.snapshot(conn, "bulk", subject)
                conn.execute("UPDATE workflow_import_candidates SET status='duplicate',"
                             " keeper_key=? WHERE id=?", (legacy, cid))
                conn.execute("UPDATE workflow_state SET version=version+1 WHERE singleton=1")
                version = conn.execute(
                    "SELECT version FROM workflow_state WHERE singleton=1").fetchone()[0]
                review_undo.remember(conn, "b1", "u1", "bulk", subject, before, version)
            db_mod.init_db(db)
            with closing(workflow.connect(db, read_only=True)) as conn:
                info = review_undo.available(conn, "b1", "u1")
            self.assertTrue(info["available"], info)  # 主钉 → 红

    def test_payload_sync_follows_every_whitelisted_column(self):
        # r3 评审第 2 条：载荷改写必须按 references 逐列同步，而不是硬编码
        # 单个 zotero_key——否则 articles.is_duplicate_of 这类同样在白名单里
        # 的列出现在整行 before_json 时会与库内不一致。
        columns = db_mod._payload_key_columns(
            [("articles", "is_duplicate_of", ""),
             ("workflow_source_records", "canonical_key", "")])
        self.assertEqual(columns,
                         {"is_duplicate_of", "canonical_key", "zotero_key"})
        legacy = _legacy_key("P")
        new_key = compliant_key_for_overlong(legacy)
        payload = {"zotero_key": legacy, "is_duplicate_of": legacy,
                   "title": legacy, "raw_json": json.dumps({"Key": legacy})}
        out = json.loads(db_mod._rewrite_payload_key_fields(
            json.dumps(payload, ensure_ascii=False), [(legacy, new_key)], columns))
        # 白名单内的两个键位都跟随迁移
        self.assertEqual(out["zotero_key"], new_key)
        self.assertEqual(out["is_duplicate_of"], new_key)
        # 非键字段与嵌套溯源载荷保持原值（R2 + provenance）
        self.assertEqual(out["title"], legacy)
        self.assertEqual(json.loads(out["raw_json"])["Key"], legacy)
        # 未命中任何键位 → 逐字节原样（不重排）
        untouched = json.dumps({"zotero_key": "other", "title": legacy})
        self.assertIs(db_mod._rewrite_payload_key_fields(
            untouched, [(legacy, new_key)], columns), untouched)


class D05_03UnionFindAlignmentTests(unittest.TestCase):
    """D-05-03：dedup 并查集注释与实现对齐（_size 死状态删除）。"""

    def test_source_has_no_size_claim_and_no_dead_state(self):
        # 钉在 _UnionFind 类源（假声称与死状态原本所在处），不殃及文件他处
        # 的历史注记。
        source = inspect.getsource(dedup_mod._UnionFind)
        # 修前注释含「同侧以按大小合并」假声称 → 红
        self.assertNotIn("按大小合并", source,
                         "并查集仍声称按大小合并（与实现相悖，D-05-03 回归）")
        # 修前 _size 三处只写不读 → 红（若将来恢复，必须真正读取它）
        self.assertNotIn("_size", source,
                         "_UnionFind 死状态 _size 复活（只写不读，D-05-03 回归）")

    def test_min_index_root_and_path_compression_semantics(self):
        # 行为钉（防删 _size 时改坏语义）：根恒为组内最小索引——
        # 「保留输入顺序靠前条目」的产品语义。
        uf = dedup_mod._UnionFind(6)
        uf.union(3, 1)
        self.assertEqual(uf.find(3), 1)
        uf.union(4, 2)
        self.assertEqual(uf.find(4), 2)
        uf.union(1, 2)  # 跨组合并：小索引根胜出
        for i in (2, 3, 4):
            self.assertEqual(uf.find(i), 1)
        uf.union(5, 5)  # 自合并与同集合并均为 no-op：5 仍是自己的根
        self.assertEqual(uf.find(5), 5)
        uf.union(0, 3)
        for i in range(5):
            self.assertEqual(uf.find(i), 0)
        # 路径压缩仍生效：链式合并后 find 把沿途节点直接挂到根
        uf2 = dedup_mod._UnionFind(6)
        for a, b in ((5, 4), (4, 3), (3, 2), (2, 1)):
            uf2.union(a, b)
        self.assertEqual(uf2.find(5), 1)
        self.assertEqual(uf2._parent[5], 1, "路径压缩未把叶子直接挂到根")


if __name__ == "__main__":
    unittest.main()
