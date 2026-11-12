# -*- coding: utf-8 -*-
"""review67 L-12 · review_imports freeze 守卫的直接回归钉（评审 2026-11-12）。

评审报告（out/code_review_2026-11-12/REPORT.md L-12，unconfirmed）：coscreen/
review_imports.py:773-785 的 freeze 守卫——已确认重复图（status='duplicate'
候选的 record 成员）不可被后续重叠候选改判到另一保留者——行为正确但测试
套件零直接覆盖（grep build/tests 无 "previously confirmed duplicate" 命中），
回退即静默失守。本文件补上该不变量的直接测试：

  1. test_freeze_rejects_rewrite_of_frozen_member：重叠候选以 R3 为保留者
     改写已冻结成员 R2 → 精确 ValueError、三行指针/候选/留痕全部原样、
     候选保持 pending（改写在任何写库语句之前被拒）。
  2. test_freeze_allows_existing_root_and_freezes_new_link：同一重叠候选改以
     既有根 R1 为保留者 → 放行（R2 ∈ frozen 但指针与意图相等，走 continue
     分支），仅新增 R3→R1 链路；新增链路随后同样进入冻结集（c3 改判 R3 抛错）。

不经 API 层，直接调用 store.decide_candidate（模块 docstring 契约：「All
mutations accept the caller's transaction」），失败调用随 with conn 事务回滚。
"""
from contextlib import closing
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_fixture = tempfile.TemporaryDirectory(prefix='rf_r67_freeze_')
os.environ.setdefault('COBOOKSHELF_DATA_DIR', _fixture.name)

from coscreen import db as articles_db, review_imports as store, review_workflow as workflow  # noqa: E402
from coscreen.models import Article  # noqa: E402

#: 与 review_imports.py:784-785 逐字一致的守卫文案（改一字即红——前端/翻译
#: 也依赖该文案指纹，故钉精确串而非子串）。
FREEZE_MESSAGE = ('This resolution would rewrite a previously confirmed duplicate relationship. '
                  'Keep its existing keeper or leave this candidate unresolved.')

DOIS = '10.test/same'  # 同一身份 DOI 的重复簇；staged 记录无 articles 行，无撞号


class FreezeGuardTests(unittest.TestCase):
    """decide_candidate freeze 守卫（review_imports.py:773-785）。"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory(prefix='rf_r67_freeze_db_')
        self.addCleanup(self._dir.cleanup)
        self.db = Path(self._dir.name) / 'freeze.db'
        # init_db 建 articles 表（apply_keeper_fill 的 _occupied_identity_dois
        # 会查它）；store.migrate 建 workflow_import_* 全套（与产品路径同序）。
        articles_db.init_db(self.db)
        store.migrate(self.db)
        with closing(workflow.connect(self.db)) as c, c:
            c.execute('INSERT INTO workflow_sources VALUES(?,?,?,?,?)',
                      ('s', 'a.ris', 'a.ris', 1, 'now'))
            # 两个导入批：b1（R1、R2），b2（R3、R4）——重叠候选来自 b2 的冲突行
            for bid, hid in (('b1', 'h1'), ('b2', 'h2')):
                c.execute('INSERT INTO workflow_import_batches VALUES(?,?,?,?,?,?,?)',
                          (bid, 'u1', hid, 'ris', 0, 'staged', 'now'))
            # 题录仅 title+doi，其余字段留空且被合并者同样留空 → 只补空无写
            # 入，测试聚焦指针语义而非字段合并。
            self._record(c, 'R1', 'b1', 0, 'Freeze keeper one')
            self._record(c, 'R2', 'b1', 1, 'Freeze dup two')
            self._record(c, 'R3', 'b2', 0, 'Freeze dup three')
            self._record(c, 'R4', 'b2', 1, 'Freeze dup four')
            # c1：批 1 的重复候选（R1+R2）；c2：批 2 与已确认图重叠的候选
            # （R1+R2+R3，含既有根——保留者必须是候选自身的 staged 成员）；
            # c3：第三轮重叠候选（R3+R4），用于钉新链路也被冻结。
            self._candidate(c, 'c1', ('R1', 'R2'))
            self._candidate(c, 'c2', ('R1', 'R2', 'R3'))
            self._candidate(c, 'c3', ('R3', 'R4'))
        # 前置：确认 c1 为重复、保留者 R1 → R2 冻结（R2→R1），c1 终局。
        self._decide('b1', 'c1', 'R1')
        self.assertEqual(self._row('R2'), (None, 'R1', 'confirmed_duplicate'))
        self.assertEqual(self._candidate_state('c1'), ('duplicate', 'R1'))

    # ------------------------- 构造辅助 -------------------------

    def _record(self, conn, rid, batch, index, title):
        article = Article(zotero_key=rid, title=title, doi=DOIS).to_dict()
        conn.execute(
            'INSERT INTO workflow_source_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (rid, 's', 'digest-' + rid, index, 'a.ris', json.dumps(article),
             json.dumps(article), rid, rid, None, None, 'new', 'staged',
             '2026-11-12T00:00:%02d' % index))
        conn.execute('INSERT INTO workflow_import_members VALUES(?,?,?)',
                     (batch, rid, index))

    def _candidate(self, conn, cid, members):
        conn.execute(
            'INSERT INTO workflow_import_candidates(id,signature,kind,status,created_at,updated_at) '
            'VALUES(?,?,?,?,?,?)', (cid, 'sig-' + cid, 'identity_conflict', 'pending', 'now', 'now'))
        for ref in members:
            conn.execute('INSERT INTO workflow_import_candidate_members VALUES(?,?,?)',
                         (cid, 'record', ref))

    def _decide(self, batch, cid, keeper):
        """在调用方事务内执行 decide_candidate（与 API 层同一契约）。"""
        with closing(workflow.connect(self.db)) as conn, conn:
            return store.decide_candidate(conn, batch, cid, 'duplicate',
                                          'tester', None, keeper)

    def _row(self, rid):
        with closing(workflow.connect(self.db, read_only=True)) as conn:
            return tuple(conn.execute(
                'SELECT canonical_key,target_record_id,outcome FROM workflow_source_records '
                'WHERE id=?', (rid,)).fetchone())

    def _candidate_state(self, cid):
        with closing(workflow.connect(self.db, read_only=True)) as conn:
            return tuple(conn.execute(
                'SELECT status,keeper_record_id FROM workflow_import_candidates '
                'WHERE id=?', (cid,)).fetchone())

    def _pointer_snapshot(self):
        """全部 staged 记录指针 + 候选状态 + 留痕/提案行数（指针原样比对基线）。"""
        with closing(workflow.connect(self.db, read_only=True)) as conn:
            records = [tuple(r) for r in conn.execute(
                'SELECT id,canonical_key,target_record_id,outcome,state FROM workflow_source_records '
                'ORDER BY id')]
            candidates = [tuple(r) for r in conn.execute(
                'SELECT id,status,keeper_key,keeper_record_id FROM workflow_import_candidates '
                'ORDER BY id')]
            merges = conn.execute('SELECT COUNT(*) FROM workflow_import_field_merges').fetchone()[0]
            proposals = conn.execute('SELECT COUNT(*) FROM workflow_import_metadata').fetchone()[0]
        return records, candidates, merges, proposals

    # ------------------------- L-12 测试 -------------------------

    def test_freeze_rejects_rewrite_of_frozen_member(self):
        """改写冻结成员：精确抛错 + 指针原样 + 候选 pending，零部分写入。"""
        before = self._pointer_snapshot()
        with self.assertRaises(ValueError) as ctx:
            self._decide('b2', 'c2', 'R3')
        # 守卫文案逐字钉（review_imports.py:784-785）
        self.assertEqual(str(ctx.exception), FREEZE_MESSAGE)
        # 指针原样：四行记录、三个候选、留痕与提案计数与改判前逐一相等——
        # freeze 拒绝发生在任何 UPDATE 之前（decide_candidate 先验证完整改动集）。
        self.assertEqual(self._pointer_snapshot(), before)
        self.assertEqual(self._row('R2'), (None, 'R1', 'confirmed_duplicate'))
        self.assertEqual(self._row('R3'), (None, None, 'new'))
        self.assertEqual(self._candidate_state('c2'), ('pending', None))

    def test_freeze_allows_existing_root_and_freezes_new_link(self):
        """既有根放行：R2 ∈ frozen 但指针与意图相等 → 不抛错，仅新增 R3→R1。"""
        result = self._decide('b2', 'c2', 'R1')
        self.assertIsNone(result)  # 只补空无写入 → 无 merge_fill 返回
        # 新链路：R3→R1；既有冻结指针 R2→R1 原样；c2 终局为 duplicate/R1。
        self.assertEqual(self._row('R3'), (None, 'R1', 'confirmed_duplicate'))
        self.assertEqual(self._row('R2'), (None, 'R1', 'confirmed_duplicate'))
        self.assertEqual(self._row('R1'), (None, None, 'new'))
        self.assertEqual(self._candidate_state('c2'), ('duplicate', 'R1'))
        # 新增链路随即进入冻结集：第三轮重叠候选改判 R3 → 同一守卫精确抛错。
        with self.assertRaises(ValueError) as ctx:
            self._decide('b2', 'c3', 'R4')
        self.assertEqual(str(ctx.exception), FREEZE_MESSAGE)
        self.assertEqual(self._row('R3'), (None, 'R1', 'confirmed_duplicate'))
        self.assertEqual(self._candidate_state('c3'), ('pending', None))


if __name__ == '__main__':
    unittest.main(verbosity=2)
