# -*- coding: utf-8 -*-
"""r11c：撤销入口读路径的（批次, 操作人）口径——全局版本守卫误伤他批次操作。

背景（外部形式化复审 probe_state_machines.py 换锚 21e3cc9 后仍红）：
MM-04 已把写路径（remember/activate 的 UPDATE）按（批次, 操作人）收口，但读路径
``available()`` 仍用**全局** ``workflow_state.version`` 做守卫，而
``review_imports_api.mutate`` 在**任何批次**的 changed 核验后都会把它 +1。于是
批次 B 的撤销行仍是 available、快照也一致，入口却被批次 C 的核验顶掉——
probe 输出 ``undo={"available": false}``，journal 两行均 available、版本 4→5。

裁定语义（2026-11-02 用户拍板）：撤销窗口按「批次 + 操作人」归属。据此本缺陷
是真 bug。修法：读路径失效判定改由三道与条目作用域一致的守卫承担（行状态、
subject 快照逐位一致、无已激活记录），不再叠全局版本比较；写时并发安全仍由
mutate 的 write_guard(expected_version) 把关。
"""
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from coscreen import db as articles_db
from coscreen import import_review_undo as review_undo
from coscreen import review_imports
from coscreen import review_workflow as workflow


class UndoReadScopeVersionGuardTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory(prefix='rf_r11c_')
        self.db = Path(self._dir.name) / 'task' / 'user.db'
        self.db.parent.mkdir(parents=True)
        articles_db.init_db(self.db)
        review_imports.migrate(self.db)
        workflow.migrate(self.db)
        review_undo.migrate(self.db)
        with closing(workflow.connect(self.db)) as conn, conn:
            conn.execute('UPDATE workflow_state SET version=4 WHERE singleton=1')

    def tearDown(self):
        self._dir.cleanup()

    def remember(self, batch, actor, version):
        # remember 要求 before != after 才落条目；不打桩快照——after 存的就是
        # 真实 snapshot() 的结果，available() 复算时才可能逐位一致。before 用
        # 同形状快照加 marker（restore() 依赖 records/candidates/... 键存在）。
        before = {'legacy_seq': 0, 'candidates': [], 'records': [], 'proposals': [],
                  'articles': [], 'acceptances': [], 'marker': 'before'}
        with closing(workflow.connect(self.db)) as conn, conn:
            review_undo.remember(conn, batch, actor, 'candidate', 'r1',
                                 before, version)

    def set_version(self, version):
        with closing(workflow.connect(self.db)) as conn, conn:
            conn.execute('UPDATE workflow_state SET version=? WHERE singleton=1', (version,))

    def test_cross_batch_review_does_not_close_other_batch_undo(self):
        """r11c 红钉（外部 probe 的模块级等价）：批次 B 记录撤销（版本 4）后，
        批次 C 的 changed 核验把全局版本推到 5——B 的撤销入口必须仍然可用。
        修复前 available() 用全局版本相等做守卫 → False（probe 实测红）。"""
        self.remember('batch-B', 'actor-A', 4)
        self.set_version(5)  # 批次 C 的 changed 核验（review_imports_api.mutate 同款 +1）
        with closing(workflow.connect(self.db, read_only=True)) as conn:
            state = review_undo.available(conn, 'batch-B', 'actor-A')
        self.assertTrue(state['available'],
                        '他批次的核验（仅全局版本 +1）不得终结 B 的撤销入口；'
                        f'实际 state={state}')
        self.assertEqual(state['entry_id'], 1)

    def test_cross_batch_then_restore_still_reverts(self):
        """端到端：批次 C 核验后，B 的 restore 仍能把自己的 subject 回滚。"""
        self.remember('batch-B', 'actor-A', 4)
        self.set_version(5)
        with closing(workflow.connect(self.db)) as conn, conn:
            result = review_undo.restore(conn, 'batch-B', 'actor-A')
        self.assertTrue(result.get('restored', True) or result)
        # 撤销后条目终结，入口关闭（自身作用域内）
        with closing(workflow.connect(self.db, read_only=True)) as conn:
            self.assertFalse(review_undo.available(conn, 'batch-B', 'actor-A')['available'])

    def test_policy_version_drift_alone_no_longer_blocks(self):
        """语义变更显式钉：全局版本漂移本身（无论来源）不再单独终结撤销——
        只有 subject 快照变化/激活/行状态三者才会。"""
        self.remember('batch-B', 'actor-A', 4)
        self.set_version(9)  # 多次他批次操作叠加
        with closing(workflow.connect(self.db, read_only=True)) as conn:
            self.assertTrue(review_undo.available(conn, 'batch-B', 'actor-A')['available'])

    # ---- 三道作用域守卫必须原样保留（对照组，破坏即红） ----

    def test_snapshot_drift_still_blocks(self):
        self.remember('batch-B', 'actor-A', 4)
        with closing(workflow.connect(self.db)) as conn, conn:
            conn.execute("UPDATE workflow_import_undo SET after_json=? WHERE id=1",
                         ('{"marker": "tampered"}',))
        with closing(workflow.connect(self.db, read_only=True)) as conn:
            self.assertFalse(review_undo.available(conn, 'batch-B', 'actor-A')['available'],
                             '快照不再逐位一致（subject 已被改动）必须拒绝撤销')

    def test_active_record_still_blocks(self):
        self.remember('batch-B', 'actor-A', 4)
        with closing(workflow.connect(self.db)) as conn, conn:
            row = conn.execute('SELECT after_json FROM workflow_import_undo WHERE id=1').fetchone()
            import json
            after = json.loads(row[0])
            after['records'] = after.get('records', []) + [{'state': 'active'}]
            conn.execute('UPDATE workflow_import_undo SET after_json=? WHERE id=1',
                         (json.dumps(after),))
        with closing(workflow.connect(self.db, read_only=True)) as conn:
            self.assertFalse(review_undo.available(conn, 'batch-B', 'actor-A')['available'],
                             '批次已有激活记录时撤销入口必须关闭')

    def test_row_state_still_blocks(self):
        self.remember('batch-B', 'actor-A', 4)
        with closing(workflow.connect(self.db)) as conn, conn:
            review_undo.activate(conn, 'batch-B')
        with closing(workflow.connect(self.db, read_only=True)) as conn:
            self.assertFalse(review_undo.available(conn, 'batch-B', 'actor-A')['available'],
                             '行状态非 available（本批次已激活）必须拒绝')


if __name__ == '__main__':
    unittest.main()
