"""r9 L3 撤销条目载体钉子（b15·F4-78/F4-79/F4-80、b16·F4-81）。

每条用例复刻 `out/release_plan/inventory_夜扫286确认分组.md` §3.3 同款探针形态，
修复前为红：

- F4-78：建表前备份仅以 ``backup.exists()`` 判定跳过，0 字节/截断 .bak 被永久
  接受，迁移 DDL 照常执行（探针：0B .bak → 迁移照做、备份 tables=[]；截断
  → restore 报 malformed）。
- F4-79：bulk 条目的 ``available()`` 每次全量重放快照，查询数随成员规模线性
  放大（探针：bulk(40) → 282 条 SELECT），且该重放发生在写事务内。
- F4-80：``restore()`` 从不读快照 ``acceptances``，撤销 accept 后 acceptance 行
  的 ``record_id`` 被改写成被撤销记录，真实来源记录的归属永久丢失（探针：
  m0 → ('K',4,'m0')、m1 → ('K',5,'m1')，undo 后 → ('K',7,'m1')）。
- F4-81：兜底 ``row['subject_id']`` 对 bulk 是 actions JSON 串 → 向
  ``workflow_metadata_acceptances.record_id`` 插非法 FK 值，``IntegrityError``
  不在 ``except (ValueError, KeyError)`` 内（探针：restore → FOREIGN KEY
  constraint failed）。

对照组钉住合法路径逐位不变（usable 备份不重写、快照比较语义不变、正常撤销
恢复元数据、metadata 条目仍可发布 acceptance）。
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_BASE = Path(os.environ.get('RF_R9_TMP', 'E:/rf214tmp/r9/l3')).resolve()
_BASE.mkdir(parents=True, exist_ok=True)
_fixture = tempfile.TemporaryDirectory(prefix='rf_r9_l3_iu_', dir=str(_BASE))
os.environ['COBOOKSHELF_DATA_DIR'] = _fixture.name
os.environ.setdefault('REVIEWFLOW_MODE', 'desktop')

from coscreen import db as articles_db, import_review_undo as iru, review_imports as imports
from coscreen import review_workflow as workflow
from coscreen.db import _connect
from coscreen.models import Article

BATCH = 'b'


def _new_db() -> Path:
    root = Path(tempfile.mkdtemp(dir=str(_BASE), prefix='rf_r9_l3_iu_case_'))
    db = root / 'task' / 'user.db'
    db.parent.mkdir(parents=True)
    articles_db.init_db(db)
    workflow.migrate(db)
    imports.migrate(db)
    iru.migrate(db)
    return db


def _article(db, key='K', title='Existing M0'):
    with closing(_connect(db)) as conn, conn:
        conn.row_factory = sqlite3.Row
        conn.execute("DELETE FROM articles WHERE zotero_key=?", (key,))
        conn.execute("INSERT INTO articles(zotero_key,title,item_type,authors,journal,year,doi,abstract,url,import_order) "
                     "VALUES(?,?,'journalArticle','','',2020,'10.1/x','','',0)", (key, title))


def _batch(db):
    with closing(sqlite3.connect(db, timeout=15)) as conn:
        conn.execute("INSERT OR IGNORE INTO workflow_import_batches(id,owner_id,history_id,import_type,protected,status,created_at) "
                     "VALUES(?,'1','h','file',1,'staged','2020-01-01')", (BATCH,))
        conn.commit()


def _record(db, rid, key, index=0):
    with closing(sqlite3.connect(db, timeout=15)) as conn:
        conn.execute("INSERT OR REPLACE INTO workflow_source_records(id,source_id,digest,row_index,filename,original_json,"
                     "article_json,incoming_key,staged_key,canonical_key,target_record_id,outcome,state,created_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (rid, '00000000-0000-0000-0000-000000000000', rid, index, 'f.csv', '{}',
                      json.dumps({'zotero_key': key, 'title': 'T-' + rid}), key, key, key, None,
                      'exact_key', 'staged', '2020-01-01'))
        conn.commit()


def _proposal(db, rid, key, title, index=0):
    with closing(_connect(db)) as conn, conn:
        conn.row_factory = sqlite3.Row
        current = dict(conn.execute('SELECT * FROM articles WHERE zotero_key=?', (key,)).fetchone())
        conn.execute("INSERT OR REPLACE INTO workflow_import_metadata(record_id,canonical_key,before_json,incoming_json) "
                     "VALUES(?,?,?,?)", (rid, key, imports.dumps(imports.metadata(current)),
                                         imports.dumps(imports.metadata(dict(current, title=title)))))
        conn.execute("INSERT OR IGNORE INTO workflow_import_members VALUES(?,?,?)", (BATCH, rid, index))


def _apply_accept(conn, rid):
    """真实 decide_metadata(accept_incoming) + 撤销快照/条目（与 API 同序，调用方持事务）。"""
    before = iru.snapshot(conn, 'metadata', rid)
    imports.decide_metadata(conn, BATCH, rid, 'accept_incoming', '1')
    conn.execute('UPDATE workflow_state SET version=version+1 WHERE singleton=1')
    version = conn.execute('SELECT version FROM workflow_state WHERE singleton=1').fetchone()[0]
    iru.remember(conn, BATCH, '1', 'metadata', rid, before, version)


def _accept_and_remember(db, rid):
    with closing(sqlite3.connect(db, timeout=15)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        _apply_accept(conn, rid)
        conn.commit()


def _undo(db):
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        try:
            result = iru.restore(conn, BATCH, '1')
            conn.commit()
            return result
        except BaseException:
            conn.rollback()
            raise


def _acceptances(db):
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute('SELECT zotero_key,revision,record_id FROM workflow_metadata_acceptances').fetchall()


def _title(db, key='K'):
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute('SELECT title FROM articles WHERE zotero_key=?', (key,)).fetchone()[0]


# ---------------------------------------------------------------------------
# b15·F4-78
# ---------------------------------------------------------------------------
class UndoBackupIntegrityTests(unittest.TestCase):
    def _prepare(self, payload: bytes | None):
        db = _new_db()
        with closing(sqlite3.connect(db)) as conn:
            conn.execute('DROP TABLE workflow_import_undo')
            conn.execute('DELETE FROM workflow_migrations WHERE version=?', (iru.MIGRATION_VERSION,))
            conn.commit()
        backup = db.with_name(db.name + '.pre-import-undo-v3.bak')
        if payload is None:
            full = db.read_bytes()
            backup.write_bytes(full[:max(1, len(full) // 3)])
        else:
            backup.write_bytes(payload)
        return db, backup

    def _usable(self, backup: Path):
        with closing(sqlite3.connect(backup)) as conn:
            return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

    def test_f4_78_empty_backup_is_not_trusted(self):
        db, backup = self._prepare(b'')
        iru.migrate(db)
        with closing(sqlite3.connect(db)) as conn:
            self.assertTrue(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                         "AND name='workflow_import_undo'").fetchone(), '迁移本身照常完成')
        self.assertGreater(backup.stat().st_size, 0, '0 字节残留备份必须被真备份替换')
        self.assertIn('articles', self._usable(backup), '替换后的备份必须是可用数据库')

    def test_f4_78_truncated_backup_is_not_trusted(self):
        db, backup = self._prepare(None)
        iru.migrate(db)
        self.assertIn('articles', self._usable(backup), '截断残留备份必须被可用备份替换而不是永久信任')

    def test_f4_78_control_usable_backup_is_left_untouched(self):
        db, _ = self._prepare(None)
        backup = db.with_name(db.name + '.pre-import-undo-v3.bak')
        with closing(sqlite3.connect(db)) as conn:
            conn.backup  # noqa: B018 - 真实可用备份由真实迁移产出
        # 先用一次真实迁移产出可用备份，再回退版本行触发第二次迁移
        iru.migrate(db)
        good = backup.read_bytes()
        with closing(sqlite3.connect(db)) as conn:
            conn.execute('DROP TABLE workflow_import_undo')
            conn.execute('DELETE FROM workflow_migrations WHERE version=?', (iru.MIGRATION_VERSION,))
            conn.commit()
        iru.migrate(db)
        self.assertEqual(backup.read_bytes(), good, '已可用的备份不得被重写（既有语义不变）')


# ---------------------------------------------------------------------------
# b15·F4-79
# ---------------------------------------------------------------------------
class BulkSnapshotCostTests(unittest.TestCase):
    def _bulk_fixture(self, count=40) -> tuple[Path, str]:
        db = _new_db()
        _batch(db)
        _article(db)
        actions = []
        with closing(sqlite3.connect(db, timeout=15)) as conn:
            for index in range(count):
                cid, rid = f'c{index}', f'r{index}'
                conn.execute("INSERT INTO workflow_import_candidates(id,signature,kind,status,keeper_key,keeper_record_id,"
                             "decided_by,created_at,updated_at) VALUES(?,?,'title','duplicate','K',NULL,'1','2020-01-01','2020-01-01')",
                             (cid, cid))
                conn.commit()
                _record(db, rid, 'K', index)
                conn.execute("INSERT INTO workflow_import_candidate_members(candidate_id,ref_type,ref_id) VALUES(?,?,?)",
                             (cid, 'record', rid))
                conn.commit()
                _proposal(db, rid, 'K', 'Incoming ' + rid, index)
                actions.append({'kind': 'candidate', 'id': cid})
        return db, json.dumps(actions, sort_keys=True)

    def _remember(self, db, subject):
        """按 bulk 口径记一条撤销条目：before 是决定前的状态（候选仍 pending）。"""
        import copy
        with closing(sqlite3.connect(db, timeout=15)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('BEGIN IMMEDIATE')
            after = iru.snapshot(conn, 'bulk', subject)
            before = copy.deepcopy(after)
            for candidate in before['candidates']:
                candidate['status'] = 'pending'
            version = conn.execute('SELECT version FROM workflow_state WHERE singleton=1').fetchone()[0]
            iru.remember(conn, BATCH, '1', 'bulk', subject, before, version)
            conn.commit()

    def test_f4_79_bulk_available_is_not_query_amplified(self):
        db, subject = self._bulk_fixture(40)
        self._remember(db, subject)
        with closing(sqlite3.connect(db)) as conn:
            conn.row_factory = sqlite3.Row
            statements = []
            conn.set_trace_callback(statements.append)
            result = iru.available(conn, BATCH, '1')
            conn.set_trace_callback(None)
        self.assertTrue(result['available'], '快照未变时仍必须可用（语义不变）')
        self.assertLessEqual(len(statements), 20,
                             f'bulk(40) available() 不得线性放大查询（实测 {len(statements)} 条）')

    def test_f4_79_control_mutations_still_detected(self):
        db, subject = self._bulk_fixture(5)
        self._remember(db, subject)
        with closing(sqlite3.connect(db)) as conn:
            conn.row_factory = sqlite3.Row
            self.assertTrue(iru.available(conn, BATCH, '1')['available'])
            conn.execute("UPDATE workflow_import_candidates SET status='pending' WHERE id='c2'")
            conn.commit()
            self.assertFalse(iru.available(conn, BATCH, '1')['available'],
                             '候选状态变更后 must 判为不可撤销（快照比较语义不变）')

    def test_f4_79_control_snapshot_rows_and_order_unchanged(self):
        """集合查询改写的对照组：行内容与行序必须与逐点点查的口径逐位一致。"""
        db, subject = self._bulk_fixture(4)
        actions = json.loads(subject)
        # 追加一条 metadata part，让三条分支（candidate / metadata / bulk 合并）都覆盖到
        _record(db, 'm0', 'K', 90)
        _proposal(db, 'm0', 'K', 'Incoming m0', 90)
        with closing(sqlite3.connect(db)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('BEGIN IMMEDIATE')
            _apply_accept(conn, 'm0')
            conn.commit()
        bulk_subject = json.dumps(actions[:2] + [{'kind': 'metadata', 'id': 'm0'}] + actions[2:],
                                  sort_keys=True)
        with closing(sqlite3.connect(db)) as conn:
            conn.row_factory = sqlite3.Row
            single = iru.snapshot(conn, 'candidate', 'c1')
            bulk = iru.snapshot(conn, 'metadata', 'm0')
            merged = iru.snapshot(conn, 'bulk', bulk_subject)
        # candidate part：候选行 + 成员记录按 id 升序
        self.assertEqual(single['candidates'][0]['id'], 'c1')
        self.assertEqual([r['id'] for r in single['records']], ['r1'])
        self.assertEqual([p['record_id'] for p in single['proposals']], ['r1'])
        self.assertEqual(single['articles'], [])
        self.assertEqual(single['acceptances'], [])
        # metadata part：文章 + acceptance
        self.assertEqual([a['zotero_key'] for a in bulk['articles']], ['K'])
        self.assertEqual([a['record_id'] for a in bulk['acceptances']], ['m0'])
        # bulk 合并：按 part 顺序、同键后到覆盖（dict 保序），legacy_seq 取全局
        self.assertEqual([c['id'] for c in merged['candidates']], ['c0', 'c1', 'c2', 'c3'])
        self.assertEqual([r['id'] for r in merged['records']], ['r0', 'r1', 'm0', 'r2', 'r3'])
        self.assertEqual([p['record_id'] for p in merged['proposals']], ['r0', 'r1', 'm0', 'r2', 'r3'])
        self.assertEqual([a['zotero_key'] for a in merged['articles']], ['K'])
        self.assertEqual(merged['legacy_seq'], 0)

    def test_f4_79_control_single_metadata_entry_semantics(self):
        db = _new_db()
        _batch(db)
        _article(db)
        _record(db, 'm0', 'K')
        _proposal(db, 'm0', 'K', 'Incoming m0')
        _accept_and_remember(db, 'm0')
        with closing(sqlite3.connect(db)) as conn:
            conn.row_factory = sqlite3.Row
            self.assertTrue(iru.available(conn, BATCH, '1')['available'])
            conn.execute("UPDATE articles SET title='changed outside' WHERE zotero_key='K'")
            conn.commit()
            self.assertFalse(iru.available(conn, BATCH, '1')['available'])


# ---------------------------------------------------------------------------
# b15·F4-80 / b16·F4-81
# ---------------------------------------------------------------------------
class AcceptanceAttributionTests(unittest.TestCase):
    def setUp(self):
        self.db = _new_db()
        _batch(self.db)
        _article(self.db, 'K', 'Existing M0')

    def test_f4_80_undo_restores_previous_acceptance_record(self):
        _record(self.db, 'm0', 'K', 0)
        _proposal(self.db, 'm0', 'K', 'Incoming m0', 0)
        _accept_and_remember(self.db, 'm0')
        self.assertEqual(_acceptances(self.db)[0][2], 'm0')

        _record(self.db, 'm1', 'K', 1)
        _proposal(self.db, 'm1', 'K', 'Incoming m1', 1)
        _accept_and_remember(self.db, 'm1')
        self.assertEqual(_acceptances(self.db)[0][2], 'm1')

        _undo(self.db)
        self.assertEqual(_title(self.db), 'Incoming m0', '文章元数据必须回滚到 m0 的版本')
        acceptance = _acceptances(self.db)[0]
        self.assertEqual(acceptance[2], 'm0',
                         'acceptance 的 record_id 必须回到快照里的来源记录（修复前被改写为被撤销的 m1）')

    def test_f4_80_control_single_accept_undo_publishes_revision(self):
        _record(self.db, 'm0', 'K', 0)
        _proposal(self.db, 'm0', 'K', 'Incoming m0', 0)
        _accept_and_remember(self.db, 'm0')
        revision_before = _acceptances(self.db)[0][1]
        _undo(self.db)
        self.assertEqual(_title(self.db), 'Existing M0')
        rows = _acceptances(self.db)
        self.assertEqual(len(rows), 1)
        self.assertGreater(rows[0][1], revision_before, '撤销仍要发布新元数据修订（既有同步语义不变）')
        self.assertEqual(rows[0][2], 'm0', '单次 accept 的撤销保持来源记录归属')

    def test_f4_81_bulk_subject_never_becomes_acceptance_record_id(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('BEGIN IMMEDIATE')
            subject = json.dumps([{'kind': 'metadata', 'id': 'rec1'}])
            before = {'legacy_seq': 0, 'candidates': [], 'records': [], 'acceptances': [],
                      'proposals': [{'record_id': 'm1', 'canonical_key': 'other-key',
                                     'before_json': '{}', 'incoming_json': '{}', 'action': None,
                                     'accepted_json': None, 'decided_by': None, 'decided_at': None}],
                      'articles': [{'zotero_key': 'K', 'item_type': 'journalArticle', 'title': 'Restored',
                                    'authors': '', 'journal': '', 'year': 2020, 'doi': '', 'abstract': '',
                                    'url': ''}]}
            after = dict(before, articles=[], proposals=[])
            conn.execute("INSERT INTO workflow_import_undo(batch_id,actor_id,kind,subject_id,before_json,after_json,"
                         "policy_version,created_at) VALUES(?,'1','bulk',?,?,?,?,?)",
                         (BATCH, subject, imports.dumps(before), imports.dumps(after),
                          conn.execute('SELECT version FROM workflow_state WHERE singleton=1').fetchone()[0],
                          imports.now()))
            conn.commit()
            self.assertTrue(iru.available(conn, BATCH, '1')['available'])
            try:
                iru.restore(conn, BATCH, '1')
            except sqlite3.IntegrityError as exc:  # 修复前即此路径
                self.fail(f'restore 不得抛出未翻译的 IntegrityError: {exc}')
            except ValueError:
                pass  # 明确拒绝亦可接受（契约内异常）
            conn.rollback()
        with closing(sqlite3.connect(self.db)) as conn:
            rows = conn.execute('SELECT record_id FROM workflow_metadata_acceptances').fetchall()
        self.assertEqual(rows, [], '非法兜底值不得写入 acceptance 表')


if __name__ == '__main__':
    unittest.main()
