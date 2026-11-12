"""2.1.4 U3（受保护撤销）8 项修复回归测试。

覆盖首审 core_audit_findings.md 单元 3 的 U3-P1..P8：
- P1  数据目录迁移改写撤销 journal 中的旧绝对路径；已 detach 图片的悬空
      引用在撤销前被明确拒绝（不产生 404 引用）。
- P2/P7 编码方案新增/删除**空**维度不再使 screen/fulltext 撤销误报
      "下游已变更"；任一侧有真实数据时仍照常阻断。
- P3  纯布局编辑（section/position）不再阻断 coding 撤销；description 属
      语义键集（与 coding_evidence.definition 同口径）仍参与比较。
- P4  撤销后清理该保存的幂等请求摘要，同 X-Request-ID 重试重新执行。
- P5  screen/fulltext 守卫快照只记有数据的证据维度；journal 每篇文章保留
      最近 50 条，更早条目置 superseded（不删除、可追溯）。
- P6  个人撤销事件带 source_record_id=workspace_undo:<id>；前端 actions 表
      与 has_research 白名单收录 coding_evidence_undone。
- P8  不再被引用（无 coding_note_images 行且不在活跃撤销快照中）的上传
      附件 attached 复位 0，7 天垃圾回收可回收。

全部使用临时目录/临时库，不触碰真实数据。
"""
import json
import os
import re
import shutil
import sys
import time
import unittest
import uuid
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import coding, coding_evidence, db as articles_db, fulltext, local_storage, workspace_undo
from coscreen.db import _connect
from coscreen.models import Article
from coscreen.research_trace import TraceContext, ensure_default_recording

TASK = 'undo-repair-task'
ACTOR = 101


def _png_bytes(color=(255, 0, 0)) -> bytes:
    """最小合法 PNG（1x1，魔数校验与 PIL 解码均可通过）。"""
    import io
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', (1, 1), color).save(buffer, format='PNG')
    return buffer.getvalue()



# 门禁墙钟（模块级模板库）：init_db 冷启动自愈链 ~30ms×每用例——模块加载时
# 建一份「init_db + K1 种入」的库字节，用例 setUp 复制（~3ms）。内容与逐
# 用例自建同源（同一条产品路径产出），隔离不变（各自独立副本）。
def _template_db_bytes() -> bytes:
    import tempfile as _tempfile
    from pathlib import Path as _Path
    with _tempfile.TemporaryDirectory() as _tmp:
        _db = _Path(_tmp) / 'task' / 'user.db'
        _db.parent.mkdir(parents=True)
        articles_db.init_db(_db)
        articles_db.upsert_articles(_db, [Article(zotero_key='K1', title='Study', doi='10.x/1')], [])
        return _db.read_bytes()

_TEMPLATE_DB_BYTES = _template_db_bytes()

class UndoRepairTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._fixture = tempfile.TemporaryDirectory(prefix='rf_undo_repair_')
        self.root = Path(self._fixture.name)
        self.db = self.root / 'task' / 'user.db'
        self.db.parent.mkdir(parents=True)
        self.db.write_bytes(_TEMPLATE_DB_BYTES)

    def tearDown(self):
        self._fixture.cleanup()

    def ctx(self, request_id=None, source='manual'):
        return TraceContext(task_id=TASK, actor_id=ACTOR, source=source,
                            request_id=request_id or str(uuid.uuid4()))

    def latest_entry(self, stage):
        with closing(_connect(self.db)) as conn:
            row = conn.execute(
                'SELECT id FROM workspace_undo WHERE task_id=? AND actor_id=? AND stage=? AND undone=0 '
                'ORDER BY id DESC LIMIT 1', (TASK, str(ACTOR), stage)).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------
    # U3-P2 / U3-P7：编码方案增删空维度不阻断 screen/fulltext 撤销
    # ------------------------------------------------------------------
    def test_screen_undo_survives_empty_scheme_changes(self):
        coding.add_dimension(self.db, '已有维度', 'text', [])
        articles_db.save_decision(self.db, 'K1', 'include', trace_context=self.ctx())
        entry = self.latest_entry('screen')
        # 新增空维度（无任何编码值/备注/图片）
        coding.add_dimension(self.db, '新增空维度', 'text', [])
        result = workspace_undo.undo(self.db, self.ctx(source='undo'), 'screen', entry)
        self.assertTrue(result['undone'])
        with closing(_connect(self.db)) as conn:
            self.assertIsNone(conn.execute('SELECT 1 FROM decisions WHERE zotero_key=?', ('K1',)).fetchone())

    def test_screen_undo_blocked_by_real_downstream_data(self):
        dim = coding.add_dimension(self.db, '数值', 'numeric', [])
        articles_db.save_decision(self.db, 'K1', 'include', trace_context=self.ctx())
        entry = self.latest_entry('screen')
        coding.add_dimension(self.db, '后加维度', 'text', [])
        coding.save_coding_value(self.db, dim, 'K1', '42', trace_context=self.ctx())
        with self.assertRaises(ValueError) as caught:
            workspace_undo.undo(self.db, self.ctx(source='undo'), 'screen', entry)
        self.assertIn('downstream work has changed', str(caught.exception))

    def test_fulltext_undo_survives_empty_dimension_delete(self):
        dim = coding.add_dimension(self.db, '临时维度', 'text', [])
        fulltext.save_stage2(self.db, 'K1', 'include', trace_context=self.ctx())
        entry = self.latest_entry('fulltext')
        coding.delete_dimension(self.db, dim, trace_context=self.ctx())
        result = workspace_undo.undo(self.db, self.ctx(source='undo'), 'fulltext', entry)
        self.assertTrue(result['undone'])

    def test_screen_undo_survives_empty_value_and_note_rows(self):
        """空串取值/空备注行不算下游数据（与 complete_keys 的非空口径一致）。"""
        dim = coding.add_dimension(self.db, '表单', 'text', [])
        articles_db.save_decision(self.db, 'K1', 'include', trace_context=self.ctx())
        entry = self.latest_entry('screen')
        # 保存表单留下空串值与空备注（均为"未填"语义）
        coding_evidence.save_form(self.db, 'K1', {dim: ''}, notes={dim: ''}, evidence=None,
                                  trace_context=self.ctx())
        result = workspace_undo.undo(self.db, self.ctx(source='undo'), 'screen', entry)
        self.assertTrue(result['undone'])

    def test_review67_f3_noop_save_still_writes_receipt(self):
        # review67 增补 F3（GitNexus P2 复核属实）：无内容变化的保存也必须落
        # 幂等回执——修前 no-op 保存（r6 #21 把回执与 before!=after 绑定）不
        # 留 workflow_form_requests 行，后续修改后旧请求重试会因查无回执而
        # 重放，把新值覆盖回旧值。
        dim = coding.add_dimension(self.db, '表单F3', 'text', [])
        articles_db.save_decision(self.db, 'K1', 'include', trace_context=self.ctx())
        req_x = self.ctx(request_id='req-r67-x')
        req_y = self.ctx(request_id='req-r67-y')
        coding_evidence.save_form(self.db, 'K1', {dim: 'A'}, notes={dim: ''},
                                  evidence=None, trace_context=req_x)  # X 首存 A
        coding_evidence.save_form(self.db, 'K1', {dim: 'A'}, notes={dim: ''},
                                  evidence=None, trace_context=req_x)  # X 同值重发=no-op
        with closing(_connect(self.db)) as conn:
            n = conn.execute(
                'SELECT COUNT(*) FROM workflow_form_requests WHERE request_id=?',
                ('req-r67-x',)).fetchone()[0]
        self.assertEqual(n, 1, 'no-op 保存也必须落幂等回执（修前为 0）')
        coding_evidence.save_form(self.db, 'K1', {dim: 'B'}, notes={dim: ''},
                                  evidence=None, trace_context=req_y)  # Y 改 B
        coding_evidence.save_form(self.db, 'K1', {dim: 'A'}, notes={dim: ''},
                                  evidence=None, trace_context=req_x)  # X 旧请求重试=命中回执幂等返回
        with closing(_connect(self.db)) as conn:
            value = conn.execute(
                'SELECT value FROM coding_values WHERE zotero_key=? AND dimension_id=?',
                ('K1', dim)).fetchone()
        self.assertIsNotNone(value)
        self.assertEqual(value[0], 'B', '旧请求重试不得把后续修改覆盖回旧值')

    # ------------------------------------------------------------------
    # U3-P3：布局编辑不阻断 coding 撤销；description 属语义键集仍阻断
    # ------------------------------------------------------------------
    def test_layout_edit_does_not_block_coding_undo(self):
        dim = coding.add_dimension(self.db, '年龄', 'numeric', [], section='基线')
        coding.save_coding_value(self.db, dim, 'K1', '42', trace_context=self.ctx())
        entry = self.latest_entry('coding')
        coding.update_dimension(self.db, dim, section='结局', position=99, trace_context=self.ctx())
        result = workspace_undo.undo(self.db, self.ctx(source='undo'), 'coding', entry)
        self.assertTrue(result['undone'])
        with closing(_connect(self.db)) as conn:
            self.assertIsNone(conn.execute(
                'SELECT 1 FROM coding_values WHERE zotero_key=? AND dimension_id=?', ('K1', dim)).fetchone())

    def test_description_edit_still_blocks_coding_undo(self):
        """description 在语义键集内（与 coding_evidence.definition 的映射口径一致），
        其编辑仍阻断撤销——但 409 文案区分"字段说明已变更"（U3-P3 仲裁案 B，
        R6-U1）：不再误导性地报"下游工作已变化"。"""
        dim = coding.add_dimension(self.db, '年龄', 'numeric', [])
        coding.save_coding_value(self.db, dim, 'K1', '42', trace_context=self.ctx())
        entry = self.latest_entry('coding')
        coding.update_dimension(self.db, dim, description='新的字段说明', trace_context=self.ctx())
        with self.assertRaises(ValueError) as caught:
            workspace_undo.undo(self.db, self.ctx(source='undo'), 'coding', entry)
        self.assertEqual(str(caught.exception), workspace_undo.DESCRIPTION_CHANGED)

    # ------------------------------------------------------------------
    # U3-P1：迁移改写 journal + 悬空图片明确拒绝
    # ------------------------------------------------------------------
    def _attach_evidence(self, count=1):
        dim = coding.add_dimension(self.db, '证据', 'text', [])
        # 同一 (task,actor,key,dim,sha256) 的上传会去重合并，用不同颜色区分多张图
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        uploads = [coding_evidence.stage_upload(self.db, TASK, ACTOR, 'K1', dim, _png_bytes(colors[i]))
                   for i in range(count)]
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: 'note'},
                                  evidence={dim: {'keep_image_ids': [], 'upload_ids': [u['upload_id'] for u in uploads]}},
                                  trace_context=self.ctx())
        return dim, uploads

    def _migrated_db(self):
        """把 task 目录整体迁移到新位置并执行路径重写，返回新库路径。"""
        source = self.root / 'task'
        stage = self.root / 'stage'
        destination = self.root / 'newtask'
        shutil.copytree(source, stage)
        local_storage._rebase_db_paths(stage / 'user.db', source.resolve(), destination.resolve(), stage)
        os.replace(stage, destination)
        return destination / 'user.db'

    def test_migration_keeps_screen_guard_matching(self):
        """screen 守卫摘要与文件位置无关：迁移改写路径后 screen 撤销仍可用。"""
        self._attach_evidence()
        articles_db.save_decision(self.db, 'K1', 'include', trace_context=self.ctx())
        new_db = self._migrated_db()
        self.db = new_db
        entry = self.latest_entry('screen')
        result = workspace_undo.undo(new_db, self.ctx(source='undo'), 'screen', entry)
        self.assertTrue(result['undone'])

    def test_migration_rewrites_attached_image_journal_and_undo_works(self):
        """审计 t15 场景：只重写表行不重写 journal 时，迁移后撤销被守卫误报阻断。"""
        self._attach_evidence()
        new_db = self._migrated_db()
        self.db = new_db
        with closing(_connect(new_db)) as conn:
            upload_paths = [r[0] for r in conn.execute('SELECT path FROM workflow_evidence_uploads')]
            for raw in upload_paths:
                self.assertTrue(str(raw).startswith(str(new_db.parent)), raw)
                self.assertTrue(Path(raw).is_file(), raw)
            journal_after = json.loads(conn.execute(
                'SELECT after_json FROM workspace_undo ORDER BY id DESC LIMIT 1').fetchone()[0])
            for state in journal_after['evidence'].values():
                for image in state['images']:
                    self.assertTrue(image[2].startswith(str(new_db.parent)), image[2])
        entry = self.latest_entry('coding')
        result = workspace_undo.undo(new_db, self.ctx(source='undo'), 'coding', entry)
        self.assertTrue(result['undone'])
        with closing(_connect(new_db)) as conn:
            self.assertIsNone(conn.execute(
                'SELECT 1 FROM coding_note_images').fetchone())  # 恢复到保存前（无图片）状态

    def test_migration_rewrites_detached_image_and_undo_restores_new_path(self):
        dim, uploads = self._attach_evidence()
        # 迁移前 detach：无 coding_note_images 行可被表级重写，只剩 journal 引用
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: ''},
                                  evidence={dim: {'keep_image_ids': [], 'upload_ids': []}},
                                  trace_context=self.ctx())
        new_db = self._migrated_db()
        self.db = new_db
        entry = self.latest_entry('coding')
        with closing(_connect(new_db)) as conn:
            self.assertIsNone(conn.execute('SELECT 1 FROM coding_note_images').fetchone())
        result = workspace_undo.undo(new_db, self.ctx(source='undo'), 'coding', entry)
        self.assertTrue(result['undone'])
        with closing(_connect(new_db)) as conn:
            paths = [r[0] for r in conn.execute('SELECT path FROM coding_note_images')]
        self.assertEqual(len(paths), 1)
        self.assertTrue(str(paths[0]).startswith(str(new_db.parent)), paths[0])
        self.assertTrue(Path(paths[0]).is_file(), paths[0])

    def test_undo_refuses_dangling_detached_image_after_migration(self):
        dim, uploads = self._attach_evidence()
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: ''},
                                  evidence={dim: {'keep_image_ids': [], 'upload_ids': []}},
                                  trace_context=self.ctx())
        new_db = self._migrated_db()
        self.db = new_db
        # 模拟文件已不在当前数据目录（旧目录被清理/文件被回收）
        for path in (new_db.parent / 'coding_assets').rglob('*.png'):
            path.unlink()
        entry = self.latest_entry('coding')
        with self.assertRaises(ValueError) as caught:
            workspace_undo.undo(new_db, self.ctx(source='undo'), 'coding', entry)
        self.assertEqual(str(caught.exception), workspace_undo.MISSING_EVIDENCE_FILE)

    # ------------------------------------------------------------------
    # U3-P4：撤销后同请求重试重新执行（幂等摘要被清理）
    # ------------------------------------------------------------------
    def test_same_request_retry_after_undo_re_saves(self):
        dim = coding.add_dimension(self.db, '字段', 'text', [])
        request_id = str(uuid.uuid4())
        coding_evidence.save_form(self.db, 'K1', {dim: 'v1'}, notes={dim: 'n1'}, evidence=None,
                                  trace_context=self.ctx(request_id=request_id))
        entry = self.latest_entry('coding')
        workspace_undo.undo(self.db, self.ctx(source='undo'), 'coding', entry)
        with closing(_connect(self.db)) as conn:
            self.assertIsNone(conn.execute(
                'SELECT 1 FROM workflow_form_requests WHERE request_id=?', (request_id,)).fetchone())
        # 同 X-Request-ID 重试：不得命中幂等摘要谎报成功
        coding_evidence.save_form(self.db, 'K1', {dim: 'v1'}, notes={dim: 'n1'}, evidence=None,
                                  trace_context=self.ctx(request_id=request_id))
        with closing(_connect(self.db)) as conn:
            value = conn.execute('SELECT value FROM coding_values WHERE zotero_key=? AND dimension_id=?',
                                 ('K1', dim)).fetchone()
            note = conn.execute('SELECT text FROM coding_notes WHERE zotero_key=? AND dimension_id=?',
                                ('K1', dim)).fetchone()
        self.assertEqual(value[0], 'v1')
        self.assertEqual(note[0], 'n1')

    def test_screen_request_retry_after_undo_re_saves(self):
        request_id = str(uuid.uuid4())
        ctx = self.ctx(request_id=request_id)
        with closing(_connect(self.db)) as conn:
            ensure_default_recording(conn, ctx)
        articles_db.save_decision(self.db, 'K1', 'include', trace_context=ctx)
        entry = self.latest_entry('screen')
        workspace_undo.undo(self.db, self.ctx(request_id=request_id, source='undo'), 'screen', entry)
        with closing(_connect(self.db)) as conn:
            self.assertIsNone(conn.execute(
                'SELECT 1 FROM trace_write_operations WHERE request_id=?', (request_id,)).fetchone())
        articles_db.save_decision(self.db, 'K1', 'include', trace_context=ctx)
        with closing(_connect(self.db)) as conn:
            row = conn.execute('SELECT decision FROM decisions WHERE zotero_key=?', ('K1',)).fetchone()
        self.assertEqual(row[0], 'include')

    # ------------------------------------------------------------------
    # U3-P5：守卫快照瘦身 + journal 每篇文章保留最近 50 条
    # ------------------------------------------------------------------
    def test_screen_snapshot_only_records_data_dimensions(self):
        data_dim = coding.add_dimension(self.db, '有数据', 'text', [])
        for i in range(4):
            coding.add_dimension(self.db, f'空维度{i}', 'text', [])
        coding.save_coding_note(self.db, data_dim, 'K1', '很长的备注' * 2000, trace_context=self.ctx())
        articles_db.save_decision(self.db, 'K1', 'include', trace_context=self.ctx())
        with closing(_connect(self.db)) as conn:
            before_json = conn.execute(
                'SELECT before_json FROM workspace_undo WHERE stage=? ORDER BY id DESC LIMIT 1',
                ('screen',)).fetchone()[0]
        before = json.loads(before_json)
        # 空维度不进入守卫快照；有数据维度只记摘要，不复制备注全文
        self.assertEqual(list(before['coding_evidence']), [str(data_dim)])
        digest = before['coding_evidence'][str(data_dim)]['digest']
        self.assertRegex(digest, r'^[0-9a-f]{64}$')
        self.assertNotIn('很长的备注', before_json)

    def test_journal_keeps_recent_50_per_article(self):
        for i in range(55):
            articles_db.save_decision(self.db, 'K1', 'include', notes=f'n{i}', trace_context=self.ctx())
        with closing(_connect(self.db)) as conn:
            total = conn.execute('SELECT COUNT(*) FROM workspace_undo').fetchone()[0]
            superseded = conn.execute('SELECT COUNT(*) FROM workspace_undo WHERE superseded=1').fetchone()[0]
            active = conn.execute('SELECT COUNT(*) FROM workspace_undo WHERE superseded=0').fetchone()[0]
        self.assertEqual(total, 55)          # 历史不删除，可追溯
        self.assertEqual(superseded, 5)
        self.assertEqual(active, 50)
        # 超出保留窗口的条目不可再撤销
        entry = 1  # 最早的一条，必然已 superseded
        with self.assertRaises(ValueError):
            workspace_undo.undo(self.db, self.ctx(source='undo'), 'screen', entry)

    # ------------------------------------------------------------------
    # U3-P6：撤销事件链接 + 前端/白名单注册
    # ------------------------------------------------------------------
    def test_personal_undo_events_link_to_journal_entry(self):
        dim = coding.add_dimension(self.db, '字段', 'text', [])
        ctx = self.ctx()
        with closing(_connect(self.db)) as conn:
            ensure_default_recording(conn, ctx)
        coding_evidence.save_form(self.db, 'K1', {dim: 'v1'}, notes={dim: 'n1'}, evidence=None,
                                  trace_context=ctx)
        entry = self.latest_entry('coding')
        workspace_undo.undo(self.db, self.ctx(source='undo'), 'coding', entry)
        with closing(_connect(self.db)) as conn:
            rows = conn.execute(
                "SELECT action,source,source_record_id FROM trace_events WHERE source='undo'").fetchall()
        actions = {row[0] for row in rows}
        self.assertIn('coding_values_undone', actions)
        self.assertIn('coding_evidence_undone', actions)
        for _, _, link in rows:
            self.assertEqual(link, f'workspace_undo:{entry}')

    def test_frontend_and_whitelist_register_coding_evidence_undone(self):
        root = Path(__file__).resolve().parents[2]
        trace_js = (root / 'custom_frontend' / 'research_trace.js').read_text(encoding='utf-8')
        self.assertRegex(trace_js, r"actions\.coding_evidence_undone\s*=")
        locale = (root / 'custom_frontend' / 'interface_locale.js').read_text(encoding='utf-8')
        self.assertIn('撤销上次保存', locale)  # 复用已注册的十语言词条
        imports = (root / 'coscreen' / 'review_imports.py').read_text(encoding='utf-8')
        self.assertRegex(imports, r"'coding_note_image_removed','coding_evidence_undone'\)")

    # ------------------------------------------------------------------
    # U3-P8：移除且不被活跃 journal 引用的上传可被 7 天 GC 回收
    # ------------------------------------------------------------------
    def test_detached_uploads_recycled_after_journal_window(self):
        dim, uploads = self._attach_evidence(count=2)
        # 普通保存移除全部图片（不撤销）：仍被活跃撤销快照引用 → 保持 attached=1
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: ''},
                                  evidence={dim: {'keep_image_ids': [], 'upload_ids': []}},
                                  trace_context=self.ctx())
        with closing(_connect(self.db)) as conn:
            attached = [r[0] for r in conn.execute('SELECT attached FROM workflow_evidence_uploads')]
        self.assertEqual(attached, [1, 1])
        # 压栈足够多的后续保存：旧条目 superseded 后不再引用这些文件
        for i in range(60):
            coding_evidence.save_form(self.db, 'K1', {dim: f'v{i}'}, notes=None, evidence=None,
                                      trace_context=self.ctx())
        with closing(_connect(self.db)) as conn:
            attached = [r[0] for r in conn.execute('SELECT attached FROM workflow_evidence_uploads')]
        self.assertEqual(attached, [0, 0], '活跃窗口外的移除上传应复位 attached')
        # 7 天后垃圾回收：文件与上传行都可回收
        with closing(_connect(self.db)) as conn:
            conn.execute('UPDATE workflow_evidence_uploads SET created_at=?',
                         (time.time() - 8 * 86400,))
            conn.commit()
            coding_evidence.cleanup_staging(conn, self.db)
            remaining = conn.execute('SELECT COUNT(*) FROM workflow_evidence_uploads').fetchone()[0]
        self.assertEqual(remaining, 0)
        files = list(coding_evidence.assets_dir(self.db).glob('*.png'))
        self.assertEqual(files, [], '移除且超出活跃撤销窗口的上传文件应被回收')

    def test_attached_uploads_are_never_recycled(self):
        self._attach_evidence(count=1)
        with closing(_connect(self.db)) as conn:
            conn.execute('UPDATE workflow_evidence_uploads SET created_at=?', (time.time() - 8 * 86400,))
            conn.commit()
            coding_evidence.cleanup_staging(conn, self.db)
            remaining = conn.execute('SELECT COUNT(*) FROM workflow_evidence_uploads').fetchone()[0]
        self.assertEqual(remaining, 1)  # 仍被 coding_note_images 引用，永不回收

    # ------------------------------------------------------------------
    # 评审回归 U3-P3：choice 维度（options 必为非空列表）——_definition_view
    # 非幂等曾使第二次归一化抛 TypeError，正式 API 只捕获 ValueError 会 500。
    # ------------------------------------------------------------------
    def test_choice_dimension_undo_after_direct_save(self):
        dim = coding.add_dimension(self.db, '选择字段', 'choice', ['A', 'B'])
        coding.save_coding_value(self.db, dim, 'K1', 'A', trace_context=self.ctx())
        entry = self.latest_entry('coding')
        result = workspace_undo.undo(self.db, self.ctx(source='undo'), 'coding', entry)
        self.assertTrue(result['undone'])
        with closing(_connect(self.db)) as conn:
            self.assertIsNone(conn.execute(
                'SELECT 1 FROM coding_values WHERE zotero_key=? AND dimension_id=?', ('K1', dim)).fetchone())

    def test_choice_dimension_undo_after_form_save(self):
        dim = coding.add_dimension(self.db, '选择字段', 'choice', ['A', 'B'])
        coding_evidence.save_form(self.db, 'K1', {dim: 'A'}, notes={dim: 'n'}, evidence=None,
                                  trace_context=self.ctx())
        entry = self.latest_entry('coding')
        result = workspace_undo.undo(self.db, self.ctx(source='undo'), 'coding', entry)
        self.assertTrue(result['undone'])
        with closing(_connect(self.db)) as conn:
            self.assertIsNone(conn.execute(
                'SELECT 1 FROM coding_values WHERE zotero_key=? AND dimension_id=?', ('K1', dim)).fetchone())

    def test_choice_dimension_section_edit_undo(self):
        dim = coding.add_dimension(self.db, '选择字段', 'choice', ['A', 'B'])
        coding.save_coding_value(self.db, dim, 'K1', 'A', trace_context=self.ctx())
        entry = self.latest_entry('coding')
        coding.update_dimension(self.db, dim, section='新分组', position=7, trace_context=self.ctx())
        result = workspace_undo.undo(self.db, self.ctx(source='undo'), 'coding', entry)
        self.assertTrue(result['undone'])

    def test_choice_dimension_description_edit_blocks_without_crash(self):
        dim = coding.add_dimension(self.db, '选择字段', 'choice', ['A', 'B'])
        coding.save_coding_value(self.db, dim, 'K1', 'A', trace_context=self.ctx())
        entry = self.latest_entry('coding')
        coding.update_dimension(self.db, dim, description='新说明', trace_context=self.ctx())
        with self.assertRaises(ValueError) as caught:
            workspace_undo.undo(self.db, self.ctx(source='undo'), 'coding', entry)
        # U3-P3 仲裁案 B：文案区分"字段说明已变更"（阻断行为不变）
        self.assertEqual(str(caught.exception), workspace_undo.DESCRIPTION_CHANGED)

    def test_legacy_fullrow_journal_with_choice_dimension(self):
        """旧版整行 definitions 快照（SELECT * 含 options_json 字符串）不崩溃。"""
        dim = coding.add_dimension(self.db, '选择字段', 'choice', ['A', 'B'])
        coding.save_coding_value(self.db, dim, 'K1', 'A')  # 不带轨迹：不产生新条目
        with closing(_connect(self.db)) as conn, conn:
            workspace_undo.begin_save(conn)
            after = workspace_undo.snapshot(conn, 'coding', 'K1', [dim], evidence=True)
            full = list(conn.execute('SELECT * FROM coding_dimensions WHERE id=?', (dim,)).fetchone())
            self.assertEqual(len(full), 10)  # 旧整行形态（含布局列）
            before = json.loads(json.dumps(after))
            before['values'][str(dim)] = None
            for doc in (before, after):
                doc['definitions'] = {str(dim): full}
            conn.execute(
                'INSERT INTO workspace_undo(task_id,actor_id,stage,zotero_key,before_json,after_json,request_id) '
                'VALUES(?,?,?,?,?,?,?)',
                (TASK, str(ACTOR), 'coding', 'K1', json.dumps(before, ensure_ascii=False),
                 json.dumps(after, ensure_ascii=False), None))
            self.assertTrue(workspace_undo.snapshot_matches(conn, 'coding', 'K1', before, after))
            entry = conn.execute(
                'SELECT id FROM workspace_undo WHERE stage=? ORDER BY id DESC LIMIT 1', ('coding',)).fetchone()[0]
        result = workspace_undo.undo(self.db, self.ctx(source='undo'), 'coding', entry)
        self.assertTrue(result['undone'])
        with closing(_connect(self.db)) as conn:
            self.assertIsNone(conn.execute(
                'SELECT 1 FROM coding_values WHERE zotero_key=? AND dimension_id=?', ('K1', dim)).fetchone())

    # ------------------------------------------------------------------
    # 评审收口 U3-P5：superseded 条目不参与 latest/状态接口/trace_actions
    # （can_undo 只在 latest['entry_id']==operation[0] 时为 True，
    #  review_workflow_api.py:199-201 即调用 _latest）。
    # ------------------------------------------------------------------
    def test_latest_is_none_after_undoing_whole_active_window(self):
        for i in range(55):
            articles_db.save_decision(self.db, 'K1', 'include', notes=f'n{i}', trace_context=self.ctx())
        self.assertEqual(workspace_undo.latest(self.db, self.ctx(), 'screen')['entry_id'], 55)
        for _ in range(50):  # 撤销全部 50 条活动条目
            entry = workspace_undo.latest(self.db, self.ctx(), 'screen')['entry_id']
            self.assertGreaterEqual(entry, 6)  # 中途 latest 绝不指向 superseded（1-5）
            workspace_undo.undo(self.db, self.ctx(source='undo'), 'screen', entry)
        # 状态接口路径：不再提供可撤销条目（修复前会回退指向 superseded 第 5 条）
        self.assertIsNone(workspace_undo.latest(self.db, self.ctx(), 'screen'))
        # trace_actions 路径：同一 _latest，经只读连接（mode=ro，无自愈）
        from coscreen import review_workflow as workflow_store
        with closing(workflow_store.connect(self.db, read_only=True)) as conn:
            self.assertIsNone(workspace_undo._latest(conn, self.ctx(), 'screen'))

    def test_latest_falls_back_on_legacy_db_without_superseded_column(self):
        """旧库 workspace_undo 缺 superseded 列：只读连接（trace_actions 路径）
        按 PRAGMA 探测回退为不过滤，不抛 no such column。"""
        legacy = self.root / 'legacy.db'
        articles_db.init_db(legacy)
        with closing(_connect(legacy)) as conn, conn:
            conn.execute(
                'CREATE TABLE workspace_undo (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, '
                'actor_id TEXT NOT NULL, stage TEXT NOT NULL, zotero_key TEXT NOT NULL, before_json TEXT NOT NULL, '
                'after_json TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0, request_id TEXT)')
            conn.execute(
                'INSERT INTO workspace_undo(task_id,actor_id,stage,zotero_key,before_json,after_json) '
                "VALUES(?,?,?,?,?,'{}')", (TASK, str(ACTOR), 'coding', 'K1', '{}'))
        from coscreen import review_workflow as workflow_store
        with closing(workflow_store.connect(legacy, read_only=True)) as conn:
            found = workspace_undo._latest(conn, self.ctx(), 'coding')
        self.assertIsNotNone(found)
        self.assertEqual(found['entry_id'], 1)
        self.assertIsNotNone(workspace_undo.latest(legacy, self.ctx(), 'coding'))


if __name__ == '__main__':
    unittest.main()
