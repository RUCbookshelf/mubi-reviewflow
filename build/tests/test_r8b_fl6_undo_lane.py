"""r8 第 2 轮 FL6（撤销协作组）钉子测试 · 受保护撤销泳道（#21-#26/#28）。

对应 out/external_reviews/r6_verify_undocollab.md 的动态复审口径，每条用例
在修复前（a080fde）均为红：
- #21 no-op 保存不落幂等摘要；撤销后重放同 request_id 重新执行（修复前命中
  旧摘要假成功、值不写回、摘要永不清除）；
- #22 证据摘要剔除 note 时间戳（A→B→A 改回原文的 screen 撤销不再被纯时间戳
  差阻断）+ 旧公式存量摘要的 v7 迁移重写（修复前旧库 screen undo 409）；
- #23 GET /undo/{stage} 只读守卫预检（修复前下游写值后 status 仍
  undo_available=true、POST 才 409）；
- #24 latest()/status 只读：不建库、不自愈 DDL（修复前对不存在的库生成
  32 表 DB、DROP 掉的列被补回、删除个人库后 status 重建 49 表）；
- #25 撤销后补偿复位 attached（保护集排除 undone 条目），7 天 staging GC
  可回收（修复前两次撤销后 attached 恒 1、8 天 GC 不回收）；
- #26 trace_actions 的 can_undo 与 undo 的 MISSING_EVIDENCE_FILE 同口径检查
  待恢复图片文件存在性（修复前只比 DB 状态，文件删除后仍可点、点击 409）；
- #28 尾点别名存量 staged 行兼容（修复前 save_form 400 / staged 预览 404）。
"""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

fixture = tempfile.TemporaryDirectory(prefix='rf_r8b_fl6_undo_')
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ.setdefault("REVIEWFLOW_MODE", "desktop")

from coscreen import coding, coding_evidence, db as articles_db, workspace_undo
from coscreen.db import _connect
from coscreen.models import Article
from coscreen.research_trace import TraceContext

TASK = 'r8b-fl6-undo-task'
ACTOR = 202


def _png_bytes(color=(255, 0, 0)) -> bytes:
    import io
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', (1, 1), color).save(buffer, format='PNG')
    return buffer.getvalue()


def _template_db_bytes() -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / 'task' / 'user.db'
        db.parent.mkdir(parents=True)
        articles_db.init_db(db)
        articles_db.upsert_articles(db, [Article(zotero_key='K1', title='Study', doi='10.x/1')], [])
        return db.read_bytes()


_TEMPLATE_DB_BYTES = _template_db_bytes()


class UndoLaneCoreTests(unittest.TestCase):
    """函数级钉子（#21/#22/#24/#25）：真实 save_form/undo/latest + 真实 sqlite。"""

    def setUp(self):
        self._fixture = tempfile.TemporaryDirectory(prefix='rf_r8b_fl6_core_')
        self.root = Path(self._fixture.name)
        self.db = self.root / 'task' / 'user.db'
        self.db.parent.mkdir(parents=True)
        self.db.write_bytes(_TEMPLATE_DB_BYTES)
        coding_evidence.migrate(self.db)

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
    # #21：no-op 保存不落摘要；撤销后重放重新执行
    # ------------------------------------------------------------------
    def test_r8b21_noop_save_leaves_no_summary_and_replay_reexecutes(self):
        dim = coding.add_dimension(self.db, '字段', 'text', [])
        r1, r2 = str(uuid.uuid4()), str(uuid.uuid4())
        coding_evidence.save_form(self.db, 'K1', {dim: 'x'}, notes=None, evidence=None,
                                  trace_context=self.ctx(request_id=r1))
        # 同值重存（不同 request_id）：before==after，不建 journal——也不得落摘要。
        coding_evidence.save_form(self.db, 'K1', {dim: 'x'}, notes=None, evidence=None,
                                  trace_context=self.ctx(request_id=r2))
        with closing(_connect(self.db)) as conn:
            journals = conn.execute('SELECT COUNT(*) FROM workspace_undo').fetchone()[0]
            r2_summary = conn.execute(
                'SELECT 1 FROM workflow_form_requests WHERE request_id=?', (r2,)).fetchone()
        self.assertEqual(journals, 1)          # no-op 不建 journal（r6 既有行为）
        self.assertIsNone(r2_summary, 'no-op 保存不得落幂等摘要（#21）')
        # 撤销 entry1 后重放 r2：必须重新执行保存（值写回），不得命中旧摘要假成功。
        entry = self.latest_entry('coding')
        workspace_undo.undo(self.db, self.ctx(source='undo'), 'coding', entry)
        coding_evidence.save_form(self.db, 'K1', {dim: 'x'}, notes=None, evidence=None,
                                  trace_context=self.ctx(request_id=r2))
        with closing(_connect(self.db)) as conn:
            value = conn.execute('SELECT value FROM coding_values WHERE zotero_key=? AND dimension_id=?',
                                 ('K1', dim)).fetchone()
        self.assertEqual(value[0], 'x', '撤销后重放同 request_id 必须重新写值（#21 假成功）')

    # ------------------------------------------------------------------
    # #22a：A→B→A 改回原文，screen 撤销不再被纯时间戳差阻断
    # ------------------------------------------------------------------
    def test_r8b22_screen_undo_survives_note_timestamp_drift(self):
        dim = coding.add_dimension(self.db, '备注', 'text', [])
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: 'A'}, evidence=None,
                                  trace_context=self.ctx())
        articles_db.save_decision(self.db, 'K1', 'include', trace_context=self.ctx())
        entry = self.latest_entry('screen')
        time.sleep(1.1)  # 跨秒：B 与 A 的备注时间戳必然不同
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: 'B'}, evidence=None,
                                  trace_context=self.ctx())
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: 'A'}, evidence=None,
                                  trace_context=self.ctx())
        result = workspace_undo.undo(self.db, self.ctx(source='undo'), 'screen', entry)
        self.assertTrue(result['undone'], '改回原文的 screen 撤销不得被纯时间戳差阻断（#22）')

    # ------------------------------------------------------------------
    # #22b：旧公式（时间戳入摘要）存量条目的 v7 迁移重写
    # ------------------------------------------------------------------
    @staticmethod
    def _legacy_digest(note_row, image_rows):
        """r8 修复前的摘要公式（note 时间戳参与、图片抹路径）——用于构造旧库状态。"""
        def without_path(image):
            return [field if index != 2 else '' for index, field in enumerate(image)]
        normalized = {'note': list(note_row), 'images': [without_path(i) for i in image_rows]}
        return hashlib.sha256(json.dumps(normalized, ensure_ascii=False,
                                          sort_keys=True).encode('utf-8')).hexdigest()

    def test_r8b22_digest_migration_rewrites_only_provable_entries(self):
        """摘要形态存量条目：只有能证明「内容未变、仅公式变更」时才迁移。

        判据 = 存量摘要恰好等于「当前内容按旧公式重算」。此时可证内容没变，
        改写为新公式摘要既修好本可撤销却被误拒的条目，又不放宽任何真实编辑。
        （必须先编辑、后注入存量摘要：save_form 自身会跑 migrate，顺序反了
        迁移就会在注入之前完成。）"""
        dim = coding.add_dimension(self.db, '备注', 'text', [])
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: 'same note'}, evidence=None,
                                  trace_context=self.ctx())
        articles_db.save_decision(self.db, 'K1', 'include', trace_context=self.ctx())
        entry = self.latest_entry('screen')
        with closing(_connect(self.db)) as conn, conn:
            note = conn.execute('SELECT id,dimension_id,zotero_key,text,created_at,updated_at '
                                'FROM coding_notes WHERE zotero_key=? AND dimension_id=?',
                                ('K1', dim)).fetchone()
            legacy = self._legacy_digest(note, [])
            after = json.loads(conn.execute('SELECT after_json FROM workspace_undo WHERE id=?',
                                            (entry,)).fetchone()[0])
            after['coding_evidence'][str(dim)] = {'digest': legacy}
            conn.execute('UPDATE workspace_undo SET after_json=? WHERE id=?',
                         (json.dumps(after, ensure_ascii=False), entry))
            # 内容与保存时完全一致（差异只在摘要公式）+ 迁移尚未运行。
            conn.execute('DELETE FROM workflow_migrations WHERE version=?',
                         (coding_evidence.MIGRATION_VERSION,))
        # 迁移前：旧公式摘要与当前内容的新公式摘要不同 -> 误判下游变更 -> 409。
        with self.assertRaises(ValueError):
            workspace_undo.undo(self.db, self.ctx(source='undo'), 'screen', entry)
        coding_evidence.migrate(self.db)
        with closing(_connect(self.db)) as conn:
            snap = json.loads(conn.execute('SELECT after_json FROM workspace_undo WHERE id=?',
                                           (entry,)).fetchone()[0])['coding_evidence'][str(dim)]
        self.assertEqual(snap['legacy_digest'], legacy, '旧摘要必须保留可追溯（#22）')
        self.assertNotEqual(snap['digest'], legacy)
        result = workspace_undo.undo(self.db, self.ctx(source='undo'), 'screen', entry)
        self.assertTrue(result['undone'], '内容未变的摘要形态条目迁移后必须可撤销（#22）')

    def test_r8b22_digest_migration_never_waives_unprovable_edits(self):
        """摘要不可逆时不得放宽：存量摘要无法由当前内容按旧公式重现（= 内容真的
        变了，或不可判定）时，迁移必须原样保留、继续按失配阻断（第 3 轮评审的
        保守方向；该残留面已在 RELEASE_NOTES 登记）。"""
        dim = coding.add_dimension(self.db, '备注', 'text', [])
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: 'A'}, evidence=None,
                                  trace_context=self.ctx())
        articles_db.save_decision(self.db, 'K1', 'include', trace_context=self.ctx())
        entry = self.latest_entry('screen')
        # 升级前真实编辑 A -> B（先编辑，再注入"当时"的旧公式摘要）。
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: 'B'}, evidence=None,
                                  trace_context=self.ctx())
        with closing(_connect(self.db)) as conn, conn:
            stale_note = (1, dim, 'K1', 'A', '2026-01-01T00:00:00', '2026-01-01T00:00:00')
            legacy = self._legacy_digest(stale_note, [])
            after = json.loads(conn.execute('SELECT after_json FROM workspace_undo WHERE id=?',
                                            (entry,)).fetchone()[0])
            after['coding_evidence'][str(dim)] = {'digest': legacy}
            conn.execute('UPDATE workspace_undo SET after_json=? WHERE id=?',
                         (json.dumps(after, ensure_ascii=False), entry))
            conn.execute('DELETE FROM workflow_migrations WHERE version=?',
                         (coding_evidence.MIGRATION_VERSION,))
        coding_evidence.migrate(self.db)
        with closing(_connect(self.db)) as conn:
            snap = json.loads(conn.execute('SELECT after_json FROM workspace_undo WHERE id=?',
                                           (entry,)).fetchone()[0])['coding_evidence'][str(dim)]
        self.assertEqual(snap, {'digest': legacy}, '不可判定的条目必须原样保留（#22 保守方向）')
        with self.assertRaises(ValueError):
            workspace_undo.undo(self.db, self.ctx(source='undo'), 'screen', entry)
        with closing(_connect(self.db)) as conn:
            decision = conn.execute('SELECT decision FROM decisions WHERE zotero_key=?',
                                    ('K1',)).fetchone()
        self.assertEqual(decision[0], 'include', '真实编辑必须挡住撤销（不得删掉决策行）')

    def test_r8b22_full_snapshot_entries_still_block_real_edits(self):
        """r6 第二轮复查：v2.1.3 及更早的条目是**全文快照**（evidence_snapshot
        未传 guard=True），迁移不得改写它们、更不得吸收升级前的真实编辑。"""
        dim = coding.add_dimension(self.db, '备注', 'text', [])
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: 'A'}, evidence=None,
                                  trace_context=self.ctx())
        articles_db.save_decision(self.db, 'K1', 'include', trace_context=self.ctx())
        entry = self.latest_entry('screen')
        # 构造成 v2.1.3 落库形态：全文快照（note 行 + images），非 digest。
        with closing(_connect(self.db)) as conn, conn:
            note = conn.execute('SELECT id,dimension_id,zotero_key,text,created_at,updated_at '
                                'FROM coding_notes WHERE zotero_key=? AND dimension_id=?',
                                ('K1', dim)).fetchone()
            after = json.loads(conn.execute('SELECT after_json FROM workspace_undo WHERE id=?',
                                            (entry,)).fetchone()[0])
            after['coding_evidence'][str(dim)] = {'note': list(note), 'images': []}
            snapshot_before = json.dumps(after['coding_evidence'], ensure_ascii=False, sort_keys=True)
            conn.execute('UPDATE workspace_undo SET after_json=? WHERE id=?',
                         (json.dumps(after, ensure_ascii=False), entry))
            conn.execute('DELETE FROM workflow_migrations WHERE version=?',
                         (coding_evidence.MIGRATION_VERSION,))
        # 升级前真实发生的下游编辑（备注 A -> B）。
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: 'B'}, evidence=None,
                                  trace_context=self.ctx())
        coding_evidence.migrate(self.db)
        with closing(_connect(self.db)) as conn:
            stored = json.loads(conn.execute('SELECT after_json FROM workspace_undo WHERE id=?',
                                             (entry,)).fetchone()[0])
        self.assertEqual(json.dumps(stored['coding_evidence'], ensure_ascii=False, sort_keys=True),
                         snapshot_before, '全文快照形态条目不得被迁移改写（审计快照保留）')
        with self.assertRaises(ValueError):
            workspace_undo.undo(self.db, self.ctx(source='undo'), 'screen', entry)
        # 对照：同一条目的时间戳漂移（文本改回 A，仅 updated_at 与历史快照不同）
        # 必须可撤销——旧公式把时间戳算进摘要，基线会误判为下游变更。
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('UPDATE coding_notes SET text=?, updated_at=? '
                         'WHERE zotero_key=? AND dimension_id=?',
                         ('A', '2031-01-01T00:00:00', 'K1', dim))
        with closing(_connect(self.db)) as conn:
            row = conn.execute('SELECT zotero_key,before_json,after_json FROM workspace_undo '
                               'WHERE id=?', (entry,)).fetchone()
            self.assertTrue(workspace_undo.snapshot_matches(
                conn, 'screen', row[0], json.loads(row[1]), json.loads(row[2])),
                '文本改回原文、仅时间戳漂移时必须命中守卫（#22）')
        result = workspace_undo.undo(self.db, self.ctx(source='undo'), 'screen', entry)
        self.assertTrue(result['undone'], '全文快照形态条目在纯时间戳差下必须可撤销（#22）')

    def test_r8b22_migration_semantics_disclosed_in_release_notes(self):
        """第 3 轮：摘要形态迁移的保守取舍必须对运维披露（静默放宽或静默保留
        都不合格——前者弱化守卫，后者让用户遇到无法解释的 409）。"""
        notes = (Path(__file__).resolve().parents[2] / 'RELEASE_NOTES.md').read_text(encoding='utf-8')
        self.assertIn('摘要形态', notes, '#22：迁移语义必须在发布说明登记（中文）')
        self.assertIn('继续按守卫失配阻断', notes, '#22：必须披露“无法证明的条目仍阻断”')
        self.assertIn('digest-form', notes, '#22：英文摘要同样需要登记')

    # ------------------------------------------------------------------
    # #24：latest() 只读——不建库、不自愈 DDL
    # ------------------------------------------------------------------
    def test_r8b24_latest_does_not_create_database(self):
        missing = self.root / 'task' / 'missing.db'
        context = TraceContext(task_id=TASK, actor_id=ACTOR)
        self.assertIsNone(workspace_undo.latest(missing, context, 'coding'))
        self.assertFalse(missing.exists(), 'latest() 不得创建数据库文件（#24）')

    def test_r8b24_latest_does_not_selfheal_dropped_column(self):
        articles_db.save_decision(self.db, 'K1', 'include', trace_context=self.ctx())
        # 用裸 sqlite 连接做删列与验证：coscreen.db._connect 自身带自愈 DDL，
        # 验证连接会把列补回，无法归因到 latest()（r6 #24 复审同口径）。
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('ALTER TABLE decisions DROP COLUMN tags')
        context = TraceContext(task_id=TASK, actor_id=ACTOR)
        workspace_undo.latest(self.db, context, 'screen')
        with closing(sqlite3.connect(self.db)) as conn:
            columns = {r[1] for r in conn.execute('PRAGMA table_info(decisions)')}
        self.assertNotIn('tags', columns, 'latest() 只读连接不得自愈补回被删列（#24）')

    # ------------------------------------------------------------------
    # #25：撤销后补偿复位 attached；undone 快照不再构成引用；GC 可回收
    # ------------------------------------------------------------------
    def test_r8b25_undo_resets_attached_and_gc_reclaims(self):
        dim = coding.add_dimension(self.db, '证据', 'text', [])
        upload = coding_evidence.stage_upload(self.db, TASK, ACTOR, 'K1', dim, _png_bytes())
        uid = upload['upload_id']
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: 'n'},
                                  evidence={dim: {'keep_image_ids': [], 'upload_ids': [uid]}},
                                  trace_context=self.ctx())
        attach_entry = self.latest_entry('coding')
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: 'n'},
                                  evidence={dim: {'keep_image_ids': [], 'upload_ids': []}},
                                  trace_context=self.ctx())
        remove_entry = self.latest_entry('coding')
        workspace_undo.undo(self.db, self.ctx(source='undo'), 'coding', remove_entry)
        workspace_undo.undo(self.db, self.ctx(source='undo'), 'coding', attach_entry)
        with closing(_connect(self.db)) as conn:
            images = conn.execute('SELECT COUNT(*) FROM coding_note_images').fetchone()[0]
            attached = conn.execute('SELECT attached FROM workflow_evidence_uploads WHERE id=?',
                                    (uid,)).fetchone()
        self.assertEqual(images, 0)
        self.assertIsNotNone(attached)
        self.assertEqual(attached[0], 0, '两次撤销回到无图态后 attached 必须复位（#25）')
        # 8 天后 staging GC 必须能回收该上传行与文件（修复前 attached 恒 1）。
        with closing(_connect(self.db)) as conn, conn:
            conn.execute('UPDATE workflow_evidence_uploads SET created_at=? WHERE id=?',
                         (time.time() - 8 * 86400, uid))
            coding_evidence.cleanup_staging(conn, self.db)
            row = conn.execute('SELECT 1 FROM workflow_evidence_uploads WHERE id=?', (uid,)).fetchone()
        self.assertIsNone(row, '撤销回收后的上传必须可被 7 天 GC 清理（#25）')


    def test_r8b25_alias_task_rows_are_released_and_reclaimed(self):
        """#25 x #28：以尾点别名落库的 staged 行也必须被撤销后的复位与 GC 覆盖
        （修复前 release 查询用精确 task_id，别名行永远 attached=1）。"""
        dim = coding.add_dimension(self.db, '证据', 'text', [])
        alias_task = TASK + '.'
        upload = coding_evidence.stage_upload(self.db, alias_task, ACTOR, 'K1', dim, _png_bytes())
        uid = upload['upload_id']
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('UPDATE workflow_evidence_uploads SET task_id=? WHERE id=?',
                         (alias_task, uid))
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: 'n'},
                                  evidence={dim: {'keep_image_ids': [], 'upload_ids': [uid]}},
                                  trace_context=self.ctx())
        attach_entry = self.latest_entry('coding')
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: 'n'},
                                  evidence={dim: {'keep_image_ids': [], 'upload_ids': []}},
                                  trace_context=self.ctx())
        remove_entry = self.latest_entry('coding')
        workspace_undo.undo(self.db, self.ctx(source='undo'), 'coding', remove_entry)
        workspace_undo.undo(self.db, self.ctx(source='undo'), 'coding', attach_entry)
        with closing(sqlite3.connect(self.db)) as conn:
            attached = conn.execute('SELECT attached FROM workflow_evidence_uploads WHERE id=?',
                                    (uid,)).fetchone()
        self.assertIsNotNone(attached)
        self.assertEqual(attached[0], 0, '别名行的 attached 必须被撤销复位（#25×#28）')
        with closing(_connect(self.db)) as conn, conn:
            conn.execute('UPDATE workflow_evidence_uploads SET created_at=? WHERE id=?',
                         (time.time() - 8 * 86400, uid))
            coding_evidence.cleanup_staging(conn, self.db)
            row = conn.execute('SELECT 1 FROM workflow_evidence_uploads WHERE id=?', (uid,)).fetchone()
        self.assertIsNone(row, '别名行必须可被 7 天 GC 回收（#25×#28）')


class UndoLaneApiTests(unittest.TestCase):
    """API 级钉子（#23/#24 API 面/#26/#28）：真实 app + TestClient + 真实 sqlite。"""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend.main import app, DATA_DIR
        cls.auth_mod, cls.data_dir = auth_mod, DATA_DIR
        cls.client = TestClient(app)
        cls.client.__enter__()
        user = auth_mod.create_user('r8fl6u0@example.test', 'R8-Fl6-Pass-2026')
        cls.user = {"user_id": user["id"], "username": user["username"],
                    "token": auth_mod.create_access_token(user["id"], user["username"])}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def auth(self):
        return {"Authorization": "Bearer " + self.user["token"]}

    def _new_task(self) -> str:
        r = self.client.post("/api/tasks", json={"name": "r8fl6-" + uuid.uuid4().hex[:8]},
                             headers=self.auth())
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["task_id"]

    def _seed_article_and_dim(self, task) -> tuple[int, Path]:
        """建 owner 库（经 context 迁移）并种入文献、include 决策与编码字段。"""
        from coscreen import tasks as tasks_mod
        ctx = self.client.app.state.review_workflow.context(task, self.user)
        articles_db.upsert_articles(ctx.owner_db, [Article(zotero_key='K1', title='R8b Probe',
                                                           doi='10.test/r8b')], [])
        r = self.client.post(f"/api/tasks/{task}/decisions",
                             json={"zotero_key": "K1", "decision": "include"}, headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post(f"/api/tasks/{task}/coding/dimensions",
                             json={"name": "证据字段", "dtype": "text"}, headers=self.auth())
        self.assertEqual(r.status_code, 201, r.text)
        info = tasks_mod.find_task(task, self.data_dir)
        slug = tasks_mod.slugify_screener(self.user["username"])
        return r.json()["id"], info.dir / f"{slug}.db"

    # ------------------------------------------------------------------
    # #23：status 只读守卫预检（下游写值后不得再标为可撤销）
    # ------------------------------------------------------------------
    def test_r8b23_status_guard_matches_downstream_change(self):
        task = self._new_task()
        dim, _ = self._seed_article_and_dim(task)
        r = self.client.get(f"/api/tasks/{task}/undo/screen", headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["latest"] is not None)
        entry_id = r.json()["latest"]["entry_id"]
        # 下游变更：写 coding 值（screen 撤销必然 409 的场景）。
        r = self.client.post(f"/api/tasks/{task}/coding/values-batch",
                             json={"zotero_key": "K1", "values": {str(dim): "v"}},
                             headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(f"/api/tasks/{task}/undo/screen", headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertFalse(body["undo_available"], '下游已变更的条目不得标为可撤销（#23）')
        self.assertIsNone(body["latest"], '守卫失配时 latest 必须置空以禁用撤销按钮（#23）')
        r = self.client.post(f"/api/tasks/{task}/undo/screen", json={"entry_id": entry_id},
                             headers=self.auth())
        self.assertEqual(r.status_code, 409, r.text)

    # ------------------------------------------------------------------
    # #24（API 面）：删除个人库后 status 不得重建
    # ------------------------------------------------------------------
    def test_r8b24_status_does_not_recreate_deleted_personal_db(self):
        task = self._new_task()
        self._seed_article_and_dim(task)
        r = self.client.get(f"/api/tasks/{task}/undo/screen", headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        from coscreen import tasks as tasks_mod
        info = tasks_mod.find_task(task, self.data_dir)
        slug = tasks_mod.slugify_screener(self.user["username"])
        db = info.dir / f"{slug}.db"
        self.assertTrue(db.is_file())
        db.unlink()
        r = self.client.get(f"/api/tasks/{task}/undo/screen", headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(db.exists(), 'status 只读：删除个人库后不得重建（#24）')

    def test_r8b24_status_uses_readonly_context(self):
        """#24 的 readonly_context 本身要有钉子（第 3 轮：把 status 改回
        context() 时 21 条钉子全绿 = 破坏不红）。

        断言两层：(a) 端点源码调用 readonly_context 而非 context；
        (b) 行为上 readonly_context 不建库、不跑 ensure_fields——成员请求既
        不创建属主库，也不把删掉的列/表自愈补回。"""
        import inspect
        from custom_backend import workspace_undo_api
        source = inspect.getsource(workspace_undo_api)
        status_src = source[source.index('def status(task_id: str, stage: Stage'):
                            source.index('def restore(task_id: str, stage: Stage')]
        self.assertIn('readonly_context', status_src, '#24：status 必须走只读上下文')
        self.assertNotIn('review_workflow.context(', status_src,
                         '#24：status 不得回退到懒建库的 context()')
        task = self._new_task()
        self._seed_article_and_dim(task)
        from coscreen import tasks as tasks_mod
        info = tasks_mod.find_task(task, self.data_dir)
        slug = tasks_mod.slugify_screener(self.user["username"])
        db = info.dir / f"{slug}.db"
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute('ALTER TABLE decisions DROP COLUMN tags')
            conn.execute('DROP TABLE IF EXISTS coding_note_images')
        self.assertEqual(self.client.get(f"/api/tasks/{task}/undo/screen",
                                         headers=self.auth()).status_code, 200)
        with closing(sqlite3.connect(db)) as conn:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            columns = {r[1] for r in conn.execute('PRAGMA table_info(decisions)')}
        self.assertNotIn('tags', columns, 'status 不得自愈补回被删列（#24）')
        self.assertNotIn('coding_note_images', names, 'status 不得自愈补回被删表（#24）')

    # ------------------------------------------------------------------
    # #26：trace can_undo 检查待恢复图片文件存在性
    # ------------------------------------------------------------------
    def test_r8b26_trace_can_undo_checks_evidence_files(self):
        task = self._new_task()
        dim, actor_db = self._seed_article_and_dim(task)
        r = self.client.post(f"/api/tasks/{task}/coding/evidence/uploads",
                             data={"zotero_key": "K1", "dim_id": str(dim)},
                             files={"file": ("a.png", _png_bytes(), "image/png")},
                             headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        uid = r.json()["upload_id"]
        r = self.client.post(f"/api/tasks/{task}/coding/values-batch",
                             json={"zotero_key": "K1", "values": {},
                                   "evidence": {str(dim): {"keep_image_ids": [], "upload_ids": [uid]}}},
                             headers={**self.auth(), "X-Request-ID": str(uuid.uuid4())})
        self.assertEqual(r.status_code, 200, r.text)
        # 第二次保存：其 before 状态含已附着图片（撤销要恢复到含图态，同 r6
        # 复审口径）。
        request_id = str(uuid.uuid4())
        r = self.client.post(f"/api/tasks/{task}/coding/values-batch",
                             json={"zotero_key": "K1", "values": {str(dim): "v"}},
                             headers={**self.auth(), "X-Request-ID": request_id})
        self.assertEqual(r.status_code, 200, r.text)
        runtime = self.client.app.state.review_workflow
        with closing(sqlite3.connect(actor_db)) as conn:
            image_path = conn.execute(
                'SELECT i.path FROM coding_note_images i JOIN coding_notes n ON n.id=i.note_id '
                'WHERE n.zotero_key=?', ('K1',)).fetchone()[0]
        def probe() -> dict:
            event = {"stage": "coding", "subject_id": "K1", "action": "coding_value_changed",
                     "source": "manual", "partition": "personal", "request_id": request_id,
                     "actor_id": self.user["user_id"]}
            runtime.trace_actions(task, self.user, [event])
            return event["actions"]
        actions = probe()
        self.assertTrue(actions["can_undo"])
        entry_id = actions["undo_entry_id"]
        Path(image_path).unlink()  # 待恢复图片文件被删除
        actions = probe()
        self.assertFalse(actions["can_undo"], '文件缺失后轨迹入口不得再标为可撤销（#26）')
        self.assertEqual(actions["reason"], "missing_evidence_file")
        r = self.client.post(f"/api/tasks/{task}/undo/coding",
                             json={"entry_id": entry_id}, headers=self.auth())
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn('图片文件已不在', r.json()["detail"])

    # ------------------------------------------------------------------
    # #28：尾点别名存量 staged 行兼容（预览 200 + 表单附着 200）
    # ------------------------------------------------------------------
    def test_r8b28_trailing_dot_alias_staged_rows_still_attach(self):
        task = self._new_task()
        dim, actor_db = self._seed_article_and_dim(task)
        r = self.client.post(f"/api/tasks/{task}/coding/evidence/uploads",
                             data={"zotero_key": "K1", "dim_id": str(dim)},
                             files={"file": ("a.png", _png_bytes((0, 255, 0)), "image/png")},
                             headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        uid = r.json()["upload_id"]
        # 模拟修复前（R6-C3 之前）以尾点别名落库的存量 staged 行。
        with closing(sqlite3.connect(actor_db)) as conn, conn:
            conn.execute('UPDATE workflow_evidence_uploads SET task_id=? WHERE id=?',
                         (task + '.', uid))
        r = self.client.get(f"/api/tasks/{task}/coding/evidence/uploads/{uid}", headers=self.auth())
        self.assertEqual(r.status_code, 200, '别名存量 staged 预览不得 404（#28）')
        r = self.client.post(f"/api/tasks/{task}/coding/values-batch",
                             json={"zotero_key": "K1", "values": {},
                                   "evidence": {str(dim): {"keep_image_ids": [], "upload_ids": [uid]}}},
                             headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        with closing(sqlite3.connect(actor_db)) as conn:
            images = conn.execute('SELECT COUNT(*) FROM coding_note_images').fetchone()[0]
        self.assertEqual(images, 1, '别名存量 staged 行必须可被表单保存附着（#28）')


if __name__ == '__main__':
    unittest.main()
