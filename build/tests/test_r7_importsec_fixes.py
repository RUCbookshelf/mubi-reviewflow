"""r7 导入撤销与库迁移泳道修复回归测试（MM-04 / MM3-01 / MM4-02 / MM3-03）。

每条用例注明对应编号与修复前的失败表现（复现报告 probe 手法原样回放）：
- 【MM-04】import_review_undo.remember 的作废 UPDATE 无 batch/actor 过滤，
  B 在 batch-2 的复核会把 A 在 batch-1 的撤销条目静默置 superseded
  （minimax_findings_verification.md §MM-04，probe mm04.py）；激活侧
  UPDATE（review_imports_api mutate）同病：激活任一批次终结全部批次条目。
- 【MM3-01/MM4-02】_ensure_effect_schema 建唯一索引前不去重：旧库同层两行
  selected=1（或无 selected 列被回填 1）→ CREATE UNIQUE INDEX 每次抛
  IntegrityError，_connect 从此整库不可用（verification_3 §MM3-01 probe
  mm3_01.py；MM4-02 钉子要求：无 selected 列 + 同层两行夹具 → _connect
  成功且至多一条 selected=1）。
- 【MM3-03】import_sources v4 迁移的「识别属主命名来源」分支恒不执行
  （detect() 恒 source_type=None）→ 死分支与 ALIASES 已删除（取舍：删除
  而非实现，理由见用例与源码注释）。

全部使用临时目录，不触碰真实数据。
"""
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import db as articles_db, import_review_undo as review_undo
from coscreen import import_sources, review_workflow as workflow
from coscreen.db import _connect
from coscreen.models import Article

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 【MM-04】撤销条目作废/激活的（批次, 操作人）口径
# ---------------------------------------------------------------------------

class ImportUndoScopeTests(unittest.TestCase):
    """MM-04：A 的撤销能力不得被 B（他人/他批次）的操作静默作废。"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory(prefix='rf_r7_mm04_')
        self.db = Path(self._dir.name) / 'task' / 'user.db'
        self.db.parent.mkdir(parents=True)
        articles_db.init_db(self.db)
        workflow.migrate(self.db)
        review_undo.migrate(self.db)

    def tearDown(self):
        self._dir.cleanup()

    def rows(self):
        with closing(workflow.connect(self.db, read_only=True)) as conn:
            return [tuple(r) for r in conn.execute(
                'SELECT id,batch_id,actor_id,state FROM workflow_import_undo ORDER BY id')]

    def seed(self, batch, actor, state='available'):
        with closing(workflow.connect(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO workflow_import_undo(batch_id,actor_id,kind,subject_id,before_json,"
                "after_json,policy_version,state,created_at) VALUES(?,?,?,?,?,?,?,?,'2026-01-01T00:00:00')",
                (batch, actor, 'candidate', 'r1', '{}', '{}', 1, state))

    def remember(self, batch, actor, marker):
        # 复现 probe mm04.py 同法：snapshot 打桩（remember 只要求前后快照不同，
        # 被测对象是作废 UPDATE 的过滤口径）。
        with closing(workflow.connect(self.db)) as conn, conn:
            with mock.patch.object(review_undo, 'snapshot', return_value={'marker': marker}):
                review_undo.remember(conn, batch, actor, 'candidate', 'r1',
                                     {'marker': 'before'}, 1)

    def test_other_actor_other_batch_keeps_actor_a_undo_available(self):
        """MM-04 钉子：B 在 batch-2 的复核不得作废 A 在 batch-1 的撤销条目。
        修复前 :77 的 UPDATE 无过滤 → A 的条目被置 superseded，
        latest(batch-1, actor-A) 返回 None（mm04.py 逐行复现）。"""
        self.seed('batch-1', 'actor-A')
        self.seed('batch-2', 'actor-B')
        self.remember('batch-2', 'actor-B', 'after')
        states = {(r[1], r[2]): r[3] for r in self.rows() if r[0] <= 2}
        self.assertEqual(states[('batch-1', 'actor-A')], 'available',
                         '他人/他批次的操作不得静默作废 A 的撤销能力')
        self.assertEqual(states[('batch-2', 'actor-B')], 'superseded',
                         'B 自己的旧条目仍应照常作废（不得过度收窄）')
        self.assertIn((3, 'batch-2', 'actor-B', 'available'), self.rows(),
                      'B 的新条目照常可用')
        with closing(workflow.connect(self.db, read_only=True)) as conn:
            self.assertIsNotNone(review_undo.latest(conn, 'batch-1', 'actor-A'),
                                 'A 的 latest 撤销条目必须仍在')

    def test_same_actor_same_batch_supersede_still_applies(self):
        """回归守护：同一（批次, 操作人）的新操作仍作废旧条目（last-save 语义）。"""
        self.remember('batch-1', 'actor-A', 'v1')
        self.remember('batch-1', 'actor-A', 'v2')
        self.assertEqual([r[3] for r in self.rows()], ['superseded', 'available'])

    def test_activation_scoped_to_own_batch(self):
        """MM-04 第二处 UPDATE（review_imports_api 激活路径）：激活 batch-2 只
        终结 batch-2 的撤销条目；A 在 batch-1 的条目保持 available
        （修复前全局 UPDATE 把跨批次条目一并置 activated）。"""
        self.seed('batch-1', 'actor-A')
        self.seed('batch-2', 'actor-B')
        with closing(workflow.connect(self.db)) as conn, conn:
            review_undo.activate(conn, 'batch-2')
        states = {(r[1], r[2]): r[3] for r in self.rows()}
        self.assertEqual(states[('batch-1', 'actor-A')], 'available',
                         '激活他批次不得终结 A 的撤销条目')
        self.assertEqual(states[('batch-2', 'actor-B')], 'activated')

    def test_activation_covers_all_actors_within_batch(self):
        """激活语义保持：同批次内全部操作人（含 A）的条目一并终结——批次已
        激活则该批次不可再撤销，与 available()/restore() 的守卫口径一致。"""
        self.seed('batch-1', 'actor-A')
        self.seed('batch-1', 'actor-B')
        with closing(workflow.connect(self.db)) as conn, conn:
            review_undo.activate(conn, 'batch-1')
        states = {(r[1], r[2]): r[3] for r in self.rows()}
        self.assertEqual(states[('batch-1', 'actor-A')], 'activated')
        self.assertEqual(states[('batch-1', 'actor-B')], 'activated')


# ---------------------------------------------------------------------------
# 【MM3-01 / MM4-02】review_effects 迁移建索引前去重
# ---------------------------------------------------------------------------

#: 复现报告的旧库布局 B：无 selected/input_json/entry_method/source_locator
#: 列、主键名 id、无 measure CHECK（触发整表重建路径，selected 回填 1）。
LEGACY_EFFECTS_SQL = """CREATE TABLE review_effects(
    id INTEGER PRIMARY KEY, study_id TEXT, comparison TEXT, outcome TEXT,
    timepoint TEXT, measure TEXT, estimate REAL, se REAL, source_key TEXT)"""


class EffectSchemaDedupTests(unittest.TestCase):
    """MM3-01/MM4-02：建唯一索引前必须按层去重（保留 result_id 最大者）。"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory(prefix='rf_r7_mm3_')
        self.db = Path(self._dir.name) / 'task' / 'user.db'
        self.db.parent.mkdir(parents=True)
        articles_db.init_db(self.db)
        articles_db.upsert_articles(
            self.db, [Article(zotero_key='K1', title='Study', doi='10.x/1')], [])
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO review_studies(id,label) VALUES('S1','Study 1')")

    def tearDown(self):
        self._dir.cleanup()

    def effects_state(self):
        with closing(sqlite3.connect(self.db)) as conn:
            rows = [tuple(r) for r in conn.execute(
                'SELECT result_id,selected FROM review_effects ORDER BY result_id')]
            index = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' "
                "AND name='uq_review_effects_selected'").fetchone()
        return rows, index

    def test_legacy_db_without_selected_column_connects_after_dedup(self):
        """MM4-02 钉子（复现报告旧库夹具）：无 selected 列 + 同层两行 →
        修复前 _connect 在 CREATE UNIQUE INDEX 处抛 IntegrityError 且每次
        重连都失败（整库不可用）；修复后成功打开且同层至多一条 selected=1。"""
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('DROP TABLE review_effects')
            conn.execute(LEGACY_EFFECTS_SQL)
            conn.execute("INSERT INTO review_effects VALUES"
                         "(10,'S1','C1','O1','T1','RR',1.5,0.3,'K1')")
            conn.execute("INSERT INTO review_effects VALUES"
                         "(20,'S1','C1','O1','T1','RR',1.2,0.4,'K1')")
        with closing(_connect(self.db)) as conn:  # 修复前：IntegrityError
            count = conn.execute("SELECT COUNT(*) FROM review_effects").fetchone()[0]
        self.assertEqual(count, 2, '去重不得丢行：两条数据都保留')
        rows, index = self.effects_state()
        self.assertTrue(index, '唯一索引必须成功建立')
        self.assertEqual(rows, [(10, 0), (20, 1)],
                         '同层仅保留 result_id 最大的一条 selected=1')
        with closing(_connect(self.db)) as conn:  # 幂等重连不复发
            pass
        self.assertEqual(self.effects_state()[0], [(10, 0), (20, 1)])

    def test_current_layout_duplicate_selected_connects_after_dedup(self):
        """MM3-01 布局 A：现列布局 + 库外改写出的同层两条 selected=1 →
        修复前同样 IntegrityError；修复后去重保最大 result_id 并建索引。"""
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('DROP INDEX uq_review_effects_selected')
            for estimate, se in ((1.5, 0.3), (1.2, 0.4)):
                conn.execute(
                    "INSERT INTO review_effects(study_id,comparison,outcome,timepoint,"
                    "measure,estimate,se,source_key,input_json,entry_method,selected,"
                    "source_locator) VALUES('S1','C1','O1','T1','RR',?,?,'K1','{}','manual',1,'')",
                    (estimate, se))
        with closing(_connect(self.db)) as conn:  # 修复前：IntegrityError
            pass
        rows, index = self.effects_state()
        self.assertTrue(index)
        self.assertEqual(rows, [(1, 0), (2, 1)], '同层保 result_id 最大者 selected=1')

    def test_normal_data_unaffected_by_dedup_step(self):
        """正常库零损：无重复行的既有 selected 分布不因去重步骤改变。"""
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('DROP INDEX uq_review_effects_selected')
            conn.execute(
                "INSERT INTO review_effects(study_id,comparison,outcome,timepoint,measure,"
                "estimate,se,source_key,input_json,entry_method,selected,source_locator) "
                "VALUES('S1','C1','O1','T1','RR',1.5,0.3,'K1','{}','manual',1,'')")
            conn.execute(
                "INSERT INTO review_effects(study_id,comparison,outcome,timepoint,measure,"
                "estimate,se,source_key,input_json,entry_method,selected,source_locator) "
                "VALUES('S1','C1','O1','T1','RR',1.4,0.35,'K1','{}','manual',0,'')")
        with closing(_connect(self.db)) as conn:
            pass
        rows, index = self.effects_state()
        self.assertTrue(index)
        self.assertEqual(rows, [(1, 1), (2, 0)], '合法的 1+0 分布保持原样')

    def test_integrity_error_translated_to_diagnostic_guidance(self):
        """MM3-01（静态守护，同 r6 StaticGuardTests 法）：建索引若仍失败必须
        翻译为带「同层重复行」指引的 ValueError 而非裸 IntegrityError。去重
        落地后该分支对数据不可达，属防御纵深，故以源码断言钉住。"""
        source = (ROOT / 'coscreen' / 'db.py').read_text(encoding='utf-8')
        start = source.index('def _ensure_effect_schema')
        end = source.index('def _connect', start)
        body = source[start:end]
        self.assertIn('MAX(result_id)', body, '建索引前必须有按层去重步骤')
        self.assertIn('sqlite3.IntegrityError', body, 'IntegrityError 必须被显式捕获')
        self.assertIn('ValueError', body, '失败必须翻译为可诊断的 ValueError')
        self.assertIn('同层', body, '指引必须点明 review_effects 同层重复行的排查方向')


# ---------------------------------------------------------------------------
# 【MM3-03】v4 迁移死分支与 ALIASES 的删除
# ---------------------------------------------------------------------------

class ImportSourcesDeadBranchTests(unittest.TestCase):
    """MM3-03：detect() 恒 source_type=None，「识别属主命名来源」分支双重死路
    （source_type 恒 None；e['name'] 是文件名而 label 是来源名，pubmed-export.ris
    ≠ PubMed）。取舍＝删除而非实现：v5 起 detect() 刻意只认上传文件名（见其
    注释 "never RIS DB/DP metadata"），实现识别需推翻该设计并改迁移产出，
    违反保守铁律 R3（能不改行为就不改）；删除零行为变化。"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory(prefix='rf_r7_mm3_03_')
        self.db = Path(self._dir.name) / 'task' / 'user.db'
        self.db.parent.mkdir(parents=True)
        articles_db.init_db(self.db)
        articles_db.upsert_articles(
            self.db, [Article(zotero_key='K1', title='Study', doi='10.x/1')], [])
        workflow.migrate(self.db)
        # pre-v4 形态：属主命名来源 PubMed + 已关联文献，无 v4/v5 标记、无明细表。
        self.sid = '11111111-2222-3333-4444-555555555555'
        with closing(workflow.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO workflow_sources(id,name,normalized_name,active,created_at) "
                         "VALUES(?,?,?,1,'2026-01-01T00:00:00')",
                         (self.sid, 'PubMed', workflow.normalized('PubMed')))
            conn.execute("DELETE FROM workflow_article_sources WHERE zotero_key='K1'")
            conn.execute("INSERT INTO workflow_article_sources VALUES('K1',?)", (self.sid,))

    def tearDown(self):
        self._dir.cleanup()

    def test_dead_recognition_branch_and_aliases_removed(self):
        """MM3-03 钉子（破坏即红）：死分支与 ALIASES 必须不存在于源码——
        恢复「迁移注释承诺了但从未生效」的误导代码即红。"""
        source = (ROOT / 'coscreen' / 'import_sources.py').read_text(encoding='utf-8')
        self.assertNotIn('ALIASES', source,
                         'ALIASES 仅被死分支引用，必须随死分支一并删除')
        self.assertNotIn('Recognize earlier owner-named sources', source,
                         '恒不执行的识别分支必须删除（恢复即回归本 finding）')

    def test_owner_named_source_stays_unrecognized_after_migration(self):
        """行为钉（mm3_03b.py 场景）：属主命名来源迁移后不获得
        ('metadata','database') 类型明细——识别被有意移除，保持 legacy 呈现；
        若日后重新实现识别（有意的产品决策），本用例提醒同步更新此口径。"""
        import_sources.migrate(self.db)
        with closing(workflow.connect(self.db, read_only=True)) as conn:
            if workflow.has_table(conn, 'workflow_source_details'):
                rows = conn.execute(
                    "SELECT identification,source_type FROM workflow_source_details "
                    "WHERE source_id=?", (self.sid,)).fetchall()
            else:
                rows = []
        self.assertEqual([tuple(r) for r in rows], [],
                         '属主命名来源不得由迁移自动获得 database 类型明细')


if __name__ == '__main__':
    unittest.main()
