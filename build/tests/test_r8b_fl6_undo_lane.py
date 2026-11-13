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
        # 2026-11-13 按 r67 波 F3 裁定（16c5b17）反转：回执无条件落——
        # 旧口径「no-op 不留回执」会让旧请求的迟到重试覆盖后改（F3 根因）。
        self.assertIsNotNone(
            r2_summary, 'no-op 保存也须落幂等回执（F3：防旧请求重试覆盖后改）')
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



# ---------------------------------------------------------------------------
# #22 原始目标的可证钉子：**真实 v2.1.3 库**（由 v2.1.3 tag 代码生成）升级后可撤销
# ---------------------------------------------------------------------------
#
# 夹具来源（可复现；生成过程不 import HEAD 任何代码）：`git archive v2.1.3`
# （501e1f3）导出代码树，只用该树的 coscreen.db.init_db / coding.add_dimension /
# coding_evidence.save_form / db.save_decision 生成：
#   1) 备注 'same note' 保存（coding journal id=1）；
#   2) save_decision('include')（screen journal id=2；v2.1.3 的 snapshot() 调
#      evidence_snapshot(conn, key) **不带 guard=True**，落的是**全文快照**形态，
#      即 note 行 + images 列表，不是摘要）；
#   3) 跨秒同值重存 'same note'（v2.1.3 无条件刷新 updated_at，夹具内
#      created_at=14:58:57、updated_at=14:58:58——#22 的原始触发条件）。
# 库形态自证：trace_events.app_version 全为 '2.1.3'、workflow_migrations={1,3}、
# workspace_undo 无 superseded 列（见 test_r8b22_real_v213_fixture_is_genuine）。
# 生成器自证（运行时实际 import 到的模块）：coscreen.__file__ 指向导出树、
# __version__=='2.1.3'、MIGRATION_VERSION==3，且 workspace_undo.evidence_snapshot
# 的签名里**没有** guard 形参——这正是 v2.1.3 落全文快照（而非摘要）的原因。
# 数据仅含 DOI 10.2139/fl6.legacy 的虚构文献；无文件系统路径、无个人信息。
_LEGACY_V213_DB_B64 = (
    'H4sIAAAAAAACCu3dDXAb14EneECkAJISRSuOg/FoNG7TowAdAyJBiqQgRdZQFEQzJEGJBGMpGk1vs/uBbKnRDXU3KNGOJiYp'
    'S/JknNgzSpzETiaeceJkYmuSimNnnKxTNXu3W7O7tV93s7t1V3tVN2VS0tZWZe+qdnbvrvZ2X38AaADdICV5kwjz/4mi0O+9'
    'fv2+uxtNQjMnJiSDMDlVy/MG0x94ILBlS+C3GSYQCITo388FKj5D/7a6toOBjYUCe7/0V1vpi3DXvzO3P9f1/3X9bdf/1fUf'
    'um52/U3Xv2v9dmh5y9utQvD4lqVgJBgJ/KoY2hmKjO8JBiRFJBf18zJtI44vGqq1zQmqKCnznKIaROeS7q2PrHx8RzgSjQYv'
    'bzP4OZk4cYu8XCR61UbXyHR6OJtmssNHJtJMVRQT62AYUcoTRZdUhZNEZiyTTY+mp5nMVJbJzE5MMNPpY+npdGYkPVPatZxe'
    'j0kiG6c5PE0LpKncObLEZNMns54785ohCTLRY5XE1r5WQWp2O5o+Njw7kWWi0ThTLIi8QUSOjpmqROa+x6fHJoenTzHj6VMx'
    'dy3irhKxbKorFJmMbtDCdnuUm9jefGA1vj0c6ekJXgm727jSAHUBnV5tXYm22tvVyq4KMMOz2amxDN1/Mp3JxhmFz9c2y2xm'
    '7MRs2qy4aCwVamNHnkyPjMfsmLEME4sKC6okkGg8apCLBv1HKeaJJglRlo0zasEwy8Od1VXFr/FPn4max8oXZUPidEKrb9QP'
    'j1LqXtpTiuT0kbv/RDoaNck6XF0kzV2nuXpFMQVVl6yY2kPGGUEjnkOCHe4MRWZ7NujpSm+Ue7sStGP1ZEc4kkgErwhWj+sG'
    'P0/6OJEIkhVfu72tqr9rY63urp0c7j7fxPwoZVYz+hmXRIKJSoogF0USZT7LRMnFyus8vzRHojQfK9CaH7T1dO/OsBYWrwha'
    'r/pwhqkpxO1nX1774rtr167c/tr1W1f/7Ob1H6x/+er6q2+uf/Pqzbfe//lPr33w4y+sXfv6zbde+eDHz966+vbPf/q8OQJo'
    'dxLFa4a78x6bmdo/2JtkaHZrr35//eV3br9M9792+9tfXn/ztbXXX6CZ2tn5rRcd7OFtochMwm901PYdl6wN2b462R6OPPpo'
    '8AqxxsaCNL8g07+GXnnVUTUeKuGbn/j3uJwWaJk9pkxNTyWZWz96d+2Nz99+7Uc3v7lsdjBdIhqMsFIHf+8rH7x34+bXLq99'
    '4Y9p195+9vn1Gz+iewuqrGp+y8hZng7FuDlGrVfmoOTzc0SzXtGya3zUGXqbGGG0vG9ev3Xj+2bXP/v82mvfszvdb0XoiDMa'
    'XWDc61wle/ZyWzgceeSR4FXO6s9cUZbNdtDLL9qqerMc/GFM65wkk/olvnZa03amvdSz/vb7N6+bLf7BT95Ze/EFWvm1F7+3'
    '/vx3137wJWvzeavjjYWNe/D40WPMB+/9wa2fvbJ27Z21G9+4/dXn1t76yvr192+9++dr7182p+MC3zcw2DAnmo9TlivPrd34'
    'i/WXvnLr28tmob7wk9tXXzAn+Re/sv7K2+t/+uz6jz5vl07hzIGpNzp/0FwLSwUxZ3by+pfMHr71xp+uf+PFtdd/cOvtb33w'
    '4/eYXjsvXhQ9urq2hD4Lxvqrz9762Ut0/TGXjStfXXvuW2ambLItFEk/4rc2lHueS5Zfth8PhSKPPx48ZZ8n7F10cr5IFKF2'
    'M1x9lqiOjJnDIE632JWPbA1HBgaCq6NWnoZkyIQTiwVZEujo5oqKqHqFhapy90phDVh6hM0tPxpZlMgF82Kw7oKLF4zaiW4G'
    'zxF6VU88LiasfXJ04PvEmYVTSINR0cGuLLWEIwcPBp9r92yUPDHXEt0nuLVh0ziJrNbxqbR7Ktfubu+ix8q7ssxUhhZ9Ik2P'
    'ODI8MzJ8NH0vy7l3blK+oGrmWicp3BxvCAv1rWdfCXolZGK98STL1l47l2tQdeHcwU62hiL8Qb9Z4dOcXNInYuvqbwXtruQ8'
    'u9JpT5/gLQ270knk1ZWuitrXOPT+U5aepg1jZVI/KOl53yjqnlfXTpR1eV0ginnxSC+s6Sim1wh0UaKvy0UyL7hVo1LEqN3s'
    'pUbyng/nCCnQyVI/VKovx0qXJNxc43R+p8V4w+ukyS130ut2a9MbWdX8dnqOfjtynH7bm6Lffuvj9NtHd9NvD+7oNO92HwgA'
    'AAAAAAAAQBPrwP0/AAAAAAAAQNPD838AAAAAAACA5ofn/wAAAAAAAADND8//AQAAAAAAAP5u3P9v7Xo50LXW9c+7/n7X9+hL'
    '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'
    'pYhRu9lLjeQ9H84RUqCTpX6oVF9NlS7puLnG6XzvPBte5kxuuZNed1q+vtediBZ6+95m3sObP//fFfivga7/eccPdlzsHNr+'
    'D7Zf2fYPtz3f9mxoPtTXcrPlT4L/PPBfw/8lMBO4scl3Fk49EorkR/1K6oyD80Ve5swSzavaEpeTrF6j5XWiVwZ+MxwZHQ2u'
    'StagbLhTw8hHqwZoTdLlqd3hSCoVXEn5HUUiunco0yjfUprN3xTK/ByR64eDnS+pXdz9b5RWj/+GdbK6MlJXI6/mKoU94lub'
    'Uoo7ulTWjaK41HDVdg5hJqQNVXrbQVeLmkDu7hLbKWhtS0kyvT0yNN7wuNeSVdpXXmdO2ryiNCfRkbvkueC4461Vp6jQywZp'
    'keYn0xXGjpaJtQjpxYK94jtLzR136ciuUOSp5AbTSTR4+lKnd/SVOeQK+83LT/56OJJMBq8ddI8LV4r6kN1eY8IVb1++3GVP'
    'O6sWvRGvbQeD1+aJwQkq7U6PTjPvihZoCRZUufZKiB4qR2tGL97MdwQUkdc8rpWMgt+Z2Sg8caiXjTM53xQ5O4U52BTfNIqd'
    'i+GbwlBKudzLcHf29RzCNecb9zVFqb/ilQ6I17Z4vNzA8foWZdn+h0OR0Uc3GI+aOlceh/T1b1z+2K9ZN+lXL7jHH42pvNrl'
    'Nd5o+D2NM0HNF3hNqj+tM2rRoJF1d3qGlCcFWpe6cZnT6A3BBVU7t/lVWlTzvOT5Xhw9/CLReNljyXciOHvJ4j0vhX4B42bT'
    'b9OWR5Srrcut62pPVwvGyy3GsldCkXCEZYOfH3UPDJLLmW8QVG/9utcAceKcS0xzYeL+R5+lPrQx1ZEnvF7UvN/vLcVZ55fp'
    'aXoumTK/TR+l3ybNbzPW9yetQNosHP02NkU3jo3NPJme5j5jpksPZ+g/E1OjY1nu+PTUcXvDTJqmL48Pj02nj3JWvs7raTvn'
    'kWn7n5HSDlOTZponx8yDcekTs2Of5oaP0xxP0oAxq3RO9k7WmUzWOd/RFUbK05FE23V4wtUoek1I6VqeME8w9748SkqhaDR8'
    'E/yZS3SQE8Wg14p5Yiyool/CPK8UzdO6dbNuvldORL+l3RVfuqfb7IRjD34sFJliN1pWiXktUTnFO9sPLx96yJpFKwNVy6sd'
    'W731a57LrB33YbyFdjezij340U1U3tmhXHlnO6I9aNV9+RF33Z3I6q2PedXdiStd4NbdnXpdm7MrT3zEup66HK199MnR0T5f'
    '9QDUCXnI7zGoE38nj5CsvTb1bM9677+0cnm9G+n9zqIqaJznc1PzDEXjPO/Gy8nKN+H+V7VDO0OR8T0bPMOzCl9+fGdtfQQP'
    'nT/Uh874/H8AAAAAAACA5of7fwAAAAAAAIDmh///DwAAAAAAAKD54fk/AAAAAAAAQPPD838AAAAAAACA5ofn/wAAAAAAAAC4'
    '/wcAAAAAAACA+x9+/h8AAAAAAACg+eH5PwAAAAAAAEDzw/N/AAAAAAAAgOaH5/8AAAAAAAAAuP8HAAAAAAAAANz/AwAAAAAA'
    'AMCvPvz+PwAAAAAAAEDzw/N/AAAAAAAAgL8D9/9tnwi0Bx4PdP5te0tbX2g1/FetvxN6IvB4++9va93WaiW55iS9tndPODI8'
    'HPzivMHPyUQjixK5wOmSMi8Tbl5TiwVOUIuKofvHsCPT6eFsmskOH5lIM/7pmFgHQ6P1omxwksiMZbLp0fQ0c3x6bHJ4+hQz'
    'nj7FDM9mp8YyNL/JdCYbp8l1oygumamz6ZNZJjNF/85OTDDT6WPp6XRmJD1TPh5NKBE9JomsuZ+g5gu8JumqUrPnyJPpkfGY'
    'TJR5YyFmaFI+VknKsk/0WnurRYOGkg13ddKV9zOkPCmokmJsuGc5ZXnfc5Iieu5mRYxlmFi0oKkFVTMkVYnGoxpvkChr7UoW'
    'idm8pQat2d9YKhA1F7MTsYei9KBknmhRZjhz1Nn1iUNO+VWDl8v5OLvbYTN2llPTTClDK6I2PyuQ1sjKrkA02qqcWVfaY8Ol'
    'ArmDXfm6gl3NyRUVqbY5j6aPDc9OZJlo1BoialETCHeOLPkPEp62miDT4fG0ahBNNROzrn1lVeANVWt8GCITwSCiXzO74plY'
    'bzxpN4EdZ/XhIXf/VRrLaoJSVmaoV/NYqUutcSjq7rtPHrI7gjYhPZ7NOZ41RGqO5HcUdwlqB6p1VGugsh3sKvdYOJJMBq9I'
    '7gVDVHXCCUVtkej1IVGvBcIVby0MdzXTvWdqg6kYZ/KE14ta7R408xyti0IHg1kwe7i687OKWz8W43SlUQyN1w2dO1u32pix'
    'i3Rt4c18PaLvZfQyklIoGh65VsbtM5esketaYGOlVo47DRcvt1PcaRiWHfmtUOSpZDBAxxC5qJ+XJYNwfNFQrW2uvvu4ZH1Y'
    'bGXk0XBkcjK4uuQeJueLvMzpS4qxQHRJ5+isI/OqJpUHjW/8Hq8h5JvaHlDlCMlj0taPK3du9vBipjK0MSfS9LgjwzMjw0fT'
    '1qnFPszSnWRbKVrpBFXVKa6Sxl3ZsyzXHYrokxt0hW8zlDvGN8XH23830GZeBixnmHBkaCi4ctCvuzz7iOiPbdQxTm9s6oTP'
    '5GjF6IVD/TSxMyZa3fzSCK2MyPE185I99Ugokh/dTMuVG9w5eE2z1UV3d+0NxCOjo8FVqa6t6hJfkz4ejvT0BF8KWmnnNV6k'
    '5dBpq+h5c/WuC/hEVXvWRW++Lc01kNfP1a2oja6O/NZT39W0cq3EmVcFntcvVQms6xhBk+iCxsv0KkbKm2dEXjHoa0U1uHKU'
    'ffpMJJjRaTrvmA/eu377qy/cfO+NtVe+ufbWi/H1P//i7df+gqH7KCT62ahONEkt6vTVIqGtX7NZFWYOJnWOrlUXFKt1Pctc'
    'ncIqtHWkePlI8eojxT2OZFdBUugZgs46g55clvwOaycUJY1eQii0rxumyxdoMkmnlxGNkhWKczJtSfNqg5uT+IZZ0mZee+Fq'
    'uXVjt95YWfvzP15/9o2b37x+87vvsDSJzGvzhCO5HC0hVyy4MiqfbuwWKp0r6YV+gVacbJS4IPNFXaJzg94lKDl6o2DOm412'
    'ogW+dfXP1n7/O7f/5PLaa9+7+bXLH/zstbUvvrv+9W+tv/di7FMzUxmz0Lo0r5grkXWO1Tc4TdI8b17/zvq1d299+8rNV75s'
    '57/2k/du3bhB8197/er6D79jjnfax5pEZyUdrv55Wjna81bd9KJl7jObGTsxm445UzfumqrlyemajvHyMeiV2crAb4Y3uy41'
    'jHzUd02vS2qtR/d+RvQ+0zoHuZNsS+XyOs26ihmvZM2y9PT3ryKpVHAl5dduNRcorguNjVpKuqPTn8zPEflDOPnh+T8AAAAA'
    'AABA88Pv/wMAAAAAAAA0Pzz/BwAAAAAAAGh+eP4PAAAAAAAA0Pzw/B8AAAAAAAAA9/8AAAAAAAAAcP/Dz/8DAAAAAAAAND88'
    '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'
    'n+t8yOC3f8bg52RirJw7E46kUsHL5+1tjRcId0GTDMKpBaLxhqQquneoODKdHs6mmezwkYk0452GiRm8fo6TRCabPpllMlP0'
    '7+zERJzhBUPVPMI1cr5IdMMjRjf4eVIbWD5SXeri3FkieOVT4JdklRc5fYHvGxisP75elA3urF6fpaAR3iAixxu1McenxyaH'
    'p08x4+lTpfrGSxWMV2rEsuO/G4pwqWBAUkRyUT8vm43FFw3V2ua8m5BLeoeTlc7TVtetDri6TpAJr9V1XW3onEfX1aa5464r'
    '7/phNnrDpnUfkTbu72zcuLWVLDVubbiw/MypcCSRCK6yrsatbdbK9t/zaND7uCkPfyYUmUk0bsr6RqyE8CtHngpHWDZ4+bCr'
    '+XRiGJIyr1dv/a5H05Xi7rjhrEVC96w+jTKKenUoM/JkemQ85kSNZZhYVM3lovGoRgRVE2kR6OsCX9SJGGXZxv2ySDTdXIjG'
    'Mtn0aHraFVMsiHexcLDswZOhyBTbuBdKLVXqg/L2cujT4chjjwWXZ9w9QLN2vzzj1fZmBBPTaS50N1eFXIUttVspzaEkazYw'
    '3bGuYdiVvll7KDxiFeSCqp3TC+aBioqoVm/9TlVxquOYGM3ZqyjDs9mpsQzdczKdycaZuxkwtYFPqwbRVO4cWaqNmSM5s5Je'
    '44vP0Z08Y8ziK6RuYDBH08eGZyeyTG/daY9dFmastX2FLzdaTlYvcPToec5JrHuHnqprxPo0PtPKp5E6/M/KokSnW+2w3vzp'
    'MNtoxfYuOpf0Dv/M9r8KPGxeC52YDkXIYVemJ07UBHgfhSxKIlHM0VYw11f3gWqjnhriQ5HxPY1nJlkkCi1un3trYejvbXrH'
    'pHtr/jmRC0f27Al+fptrOttx7tc5jwltx9AZbbYUrcVmZpG1T113M7OZsROzab+p/uFMPpr4jq7naqdk3UyMMzmJyKI7QFeL'
    'mlB3ZDuUs9f+0pFcwYuS7lr66ZF5Q1iobNZMEjPAzMhz4deFBZLnudI5o7YJCgW/KFWT5iWFq+qAcmhVr1XS1vR7OaKqW+KM'
    'lC+omus8VUknS7xeHeRqy9JRKpcjcWeceFxlVJc+XlNulg0Egt+5m7na5xG1kAtHko8Gl7dZO0viRW5Bml+Q6V86u+jaXtnK'
    'O7NmLHM0fZKpT8lMZZhKSKxyejCvxuYJu8yScGTsYHD5dPlQhmTIhBNp+SSBnvy5PMnP0UFJ9/GO0eW6Mvhn0cGYJfLJqKp4'
    '5T6lrWwNV3b5Y2I4MkzLesi3rPblUG2oOf7JBf2cVc66RfYe+smOOrmSmrPvRDtdK5w9c2UpR4Qleo2ue4dKHqtebZo7vpSU'
    'yTwvLDn51Efb4cVifYxIcpIiWVeJd3YbKRJabff8a3girRSAZc2f/2/peCyw46Pbp+k/AAAAAACX4y3bAz0PPrzzMv3TFtD2'
    'zyVy8mDCfG8qYV5b9vX2Car5jud48pnuRV6mt7LdB5hnupP0u1KU5UtxprtyZVuJO52kEesvv7H23h+tvX51/Yff6abbBrlo'
    'mP+ePkO/99IX3ZW/5iY92GAi2ZvoT2aT+w4M7D8wMNR9xjxC6a6gnP0z3Qq9nXGOYx5qPGnmo/N5wlgxPrn5HYSGS3nzPWIz'
    'yzOXzGPq1v2GfaHvBDdtC+zfbAusHt5iD5cVYZV4Dxed3skQxRwu9hsdTjPR/OyBZGVU3uJ+GV2bo+WxesLpQVdRT3dLiiAX'
    'RdJd3Tl3eshf2bquSkG7B1e33ZcT3hlLf5dn66bXK/P+P9j1bwL0CwAAAAAAAADuL9FgS2B2TpxL8aR/f4LsT5LEvsGhocT+'
    'JL8vISaTKWFwcH9SGOiz7///MEC/AAAAAAAAAOAXRg629Tw49khP+yGvH7w4XfqxkXjlxzbi3fYP1HSfKf/yqUhyfFE2EuUA'
    '148I9A7aPyKwd3Bo4DMd1v3/zQD9AgAAAAAAAID726+19jzY7vV+gvX8f8fXAvQLAAAAAAAA4JdgRTAfhPcsd64ued241j3h'
    'JoMDYlIYGBD2iXOkLzm4b1AQU7lecShFxN7BPj4l7BP2JVMpQRwShgb4wf5UkpDeFEn29dF9BoVnuksfkNd9wPyN+u54t/tD'
    'm2lg3SHNJ++G/Sv2jZ/LW+mMIk3XXb2z/WmWNHgzP6tP93A+y8/Mp7ZNaGzl46LtKng+8afpnE8I7T6QvGQ////rAP0CAAAA'
    'AAAAgPvJJ7b2PNjTvql3Tczn/4EH0GQAAAAAAAAAzawD9/8AAAAAAAAATQ/P/wEAAAAAAACaH57/AwAAAAAAADQ/8/l/KPR4'
    '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'
    'd+eduikA8AcA'
)


class UndoLaneLegacyV213FixtureTests(unittest.TestCase):
    """真实 v2.1.3 旧库夹具（#22 原始目标）：升级后 screen 撤销可用，真实编辑仍阻断。"""

    def setUp(self):
        self._fixture = tempfile.TemporaryDirectory(prefix='rf_r8b_fl6_v213_')
        self.db = Path(self._fixture.name) / 'task' / 'user.db'
        self.db.parent.mkdir(parents=True)
        import base64
        import gzip
        self.db.write_bytes(gzip.decompress(base64.b64decode(''.join(_LEGACY_V213_DB_B64))))

    def tearDown(self):
        self._fixture.cleanup()

    def ctx(self, source='undo'):
        return TraceContext(task_id=TASK, actor_id=ACTOR, source=source, request_id=str(uuid.uuid4()))

    def _rows(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return {
                'migrations': sorted(r[0] for r in conn.execute('SELECT version FROM workflow_migrations')),
                'app_versions': sorted(r[0] for r in conn.execute('SELECT DISTINCT app_version FROM trace_events')),
                'journal_columns': {r[1] for r in conn.execute('PRAGMA table_info(workspace_undo)')},
                'screen': conn.execute("SELECT id,undone FROM workspace_undo WHERE stage='screen'").fetchall(),
                'screen_after': json.loads(conn.execute(
                    "SELECT after_json FROM workspace_undo WHERE stage='screen' ORDER BY id DESC LIMIT 1").fetchone()[0]),
                'note': conn.execute('SELECT text,created_at,updated_at FROM coding_notes WHERE zotero_key=?',
                                     ('K1',)).fetchone(),
                'decision': conn.execute('SELECT decision FROM decisions WHERE zotero_key=?', ('K1',)).fetchone(),
            }

    def test_r8b22_real_v213_fixture_is_genuine(self):
        """夹具必须确实是 2.1.3 形态（否则可撤销结论不针对旧库）。

        同时钉住 #22 的原始触发条件：v2.1.3 的跨秒同值重存确实刷新了 updated_at，
        且 screen 条目存的是**全文快照**（不是摘要）——这正是修复前 409 的来源。"""
        rows = self._rows()
        self.assertEqual(rows['migrations'], [1, 3], 'v2.1.3 库只有 workflow(1) 与 coding_evidence(3) 迁移')
        self.assertEqual(rows['app_versions'], ['2.1.3'], 'trace_events 必须记录 2.1.3 产生的数据')
        self.assertNotIn('superseded', rows['journal_columns'], 'v2.1.3 的 journal 没有 superseded 列')
        self.assertEqual([r[1] for r in rows['screen']], [0])
        note = rows['note']
        self.assertEqual(note[0], 'same note')
        self.assertNotEqual(note[1], note[2], '夹具必须复现「同值重存刷新时间戳」（#22 原始触发条件）')
        shaped = rows['screen_after']['coding_evidence']['1']
        self.assertIn('note', shaped, 'v2.1.3 screen 条目必须是全文快照形态（note 行在快照内）')
        self.assertNotIn('digest', shaped, 'v2.1.3 不落摘要形态')

    def test_r8b22_real_v213_screen_undo_after_upgrade(self):
        """#22 原始目标：真实 v2.1.3 库升级到 HEAD 后，纯时间戳漂移的 screen 撤销必须可用。

        修复前基线在此夹具上 409（旧公式摘要把时间戳算入、且无任何迁移）；HEAD 的
        _guard_state 对全文快照按当前公式重算两侧 → 命中，条目可撤销。"""
        coding_evidence.migrate(self.db)
        rows = self._rows()
        self.assertIn(coding_evidence.MIGRATION_VERSION, rows['migrations'], '升级必须落到 v7')
        self.assertIn('note', rows['screen_after']['coding_evidence']['1'],
                      '全文快照形态不得被迁移改写（审计快照原样保留）')
        result = workspace_undo.undo(self.db, self.ctx(), 'screen', rows['screen'][0][0])
        self.assertTrue(result['undone'], '真实 v2.1.3 库的 screen 撤销不得被纯时间戳差阻断（#22 原始目标）')
        after = self._rows()
        self.assertIsNone(after['decision'], '撤销必须删除 include 决策')
        self.assertEqual(after['screen'][0][1], 1, '条目必须标记 undone')

    def test_r8b22_real_v213_real_edit_still_blocks(self):
        """同一真实夹具上的对照：升级前真的改过备注内容，撤销必须继续阻断。

        证明「全文快照可撤销」不是放宽守卫——内容在快照里，可以精确判定。"""
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE coding_notes SET text='edited downstream', updated_at='2031-01-01T00:00:00' "
                         'WHERE zotero_key=?', ('K1',))
        coding_evidence.migrate(self.db)
        screen_id = self._rows()['screen'][0][0]
        with self.assertRaises(ValueError) as caught:
            workspace_undo.undo(self.db, self.ctx(), 'screen', screen_id)
        self.assertIn('downstream work has changed', str(caught.exception))
        after = self._rows()
        self.assertEqual(after['decision'][0], 'include', '真实编辑必须挡住撤销（不得删掉决策行）')
        self.assertEqual(after['screen'][0][1], 0, '被阻断的条目不得标记 undone')



# 真实 2.1.4 早期（a080fde，摘要形态窗口末）库夹具：screen 条目存的是
# {'digest': ...} 的**摘要形态**，摘要公式含 note 时间戳（257d96c..326df44 之间
# 的构建尚未在保存侧做同值幂等）。同一生成配方、同一 task/actor，仅用该提交的
# 代码生成；trace_events.app_version='2.1.4'，journal 已有 superseded 列。
# 生成器自证（运行时实际 import 到的模块，而非注释声明）：coscreen.__file__ 指向
# 导出树、__version__=='2.1.4'、MIGRATION_VERSION==3，且
# workspace_undo._evidence_digest **没有** legacy_timestamps 形参——即夹具确由
# 「时间戳入摘要」的旧公式写出，HEAD 的当前公式无法复现该 digest。
_LEGACY_PRE214_DB_B64 = (
    'H4sIAAAAAAACCu3df3Ab14EneICUAJISRStOgnE0itv0KEDHgEjwN63IGoqCaIYkKJFgLEXR9Da7H8iWGt1Qd4MS5SgxSVmS'
    'J+PEmVFiJ3Yy8cSJMxNbk1QcO+PknKrZ2r2aubvd292Z2qu62v1jy6Skra3y7v0xt3dXe3evfwBoAN0gZXkTC/P9mKTR771+'
    '/X53N5qEZo9PSgZhsqqW4w2mN3BfoKkp8PsMEwgEQvT7y4GyU/R7m2s7GNhcKLD/G3+znb4Id/w7c/vLHf9Pxz90/JeO/9Rx'
    's+M/dPy7bT8MrTS9sU0IHmtaDkaCkcCHxeDuUGRiXzAgKSK5oJ+TaRtxfMFQrW1OUEVJWeAU1SA6l3RvfWT1U7vCkWg0eHmH'
    'wc/LxIlb4uUC0Ss2OkZnUiOZFJMZOTyZYiqimFgbw4hSjii6pCqcJDLj6UxqLDXDpKczTHpucpKZSR1NzaTSo6nZ4q6l9HpM'
    'Etk4zeEiLZCmcmfJMpNJnch47sxrhiTIRI+VE1v7WgWp2u1I6ujI3GSGiUbjTCEv8gYROTpmKhKZ+x6bGZ8amTnJTKROxty1'
    'iLtKxLLDHaHIVHSTFrbbo9TE9uZ9a/Gd4UhXV/BK2N3G5QaoCWj3autytNXerlZ2VYAZmctMj6fp/lOpdCbOKHyuulnm0uPH'
    '51JmxUVjOV8dO/p4anQiZseMp5lYVFhUJYFE41GDXDDo/5RCjmiSEGXZOKPmDbM83BldVfwa/9TpqHmsXEE2JE4ntPpG7fAo'
    'pu6mPaVITh+5+0+ko1GTrMPVRNLcdZqrVxSTV3XJiqk+ZJwRNOI5JNiR9lBkrmuTni73Rqm3y0G71k60hSOJRPCKYPW4bvAL'
    'pIcTiSBZ8dXbOyr6uzrW6u7qyeHu8y3Mj2JmVaOfcUkkmKikCHJBJFHmi0yUXCi/zvHL8yRK87ECrflBW0/37gxrYfGKoPWq'
    'DWeYqkLcfur59a+9tX7tyu1vX7919S9uXv/pxjevbrz02sb3rt58/Z33fnXt3V98df3ad26+/uK7v3jq1tU33vvVM+YIoN1J'
    'FK8Z7s57fHZ6aKA7ydDs1l/6ycbzb95+nu5/7fYPv7nx2svrrzxLM7Wz81sv2thDO0KR2YTf6KjuOy5ZHbJzbao1HHnooeAV'
    'Yo2NRWlhUabfhl5+1VYxHsrhW5/4d7mc5mmZPaZMVU8lmVs/f2v91a/cfvnnN7+3YnYwXSLqjLBiB//4hXffvnHz25fXv/qn'
    'tGtvP/XMxo2f070FVVY1v2XkDE+HYtwco9Yrc1DyuXmiWa9o2TU+6gy9LYwwWt7Xrt+68ROz6596Zv3lH9ud7rcitMUZjS4w'
    '7nWunD17uSUcjjz4YPAqZ/VntiDLZjvopRctFb1ZCv4gpnVWkkntEl89rWk7017q2njjnZvXzRZ/95dvrj/3LK38+nM/3njm'
    'z9d/+g1r8xmr443FzXvw2JGjzLtv/9GtX7+4fu3N9Rvfvf2tp9dff2Hj+ju33vrL9Xcum9Nxke/pH6ibE83HKcuVp9dv/NXG'
    '11+49cMVs1Bf/eXtq8+ak/xrL2y8+MbG95/a+PlX7NIpnDkw9XrnD5prfjkvZs1O3viG2cO3Xv3+xnefW3/lp7fe+MG7v3ib'
    '6bbz4kXRo6urS+izYGy89NStX3+drj/msnHlW+tP/8DMlE22hCKpB/3WhlLPc8nSy9ZjoVDkkUeCJ+3zhL2LTs4ViCJUb4Yr'
    'zxKVkTFzGMTpFrv6ke3hSH9/cG3MytOQDJlwYiEvSwId3VxBEVWvsFBF7l4prAFLj7C15UcjSxI5b14M1lxw8YJRPdHN4HlC'
    'r+qJx8WEtU+WDnyfOLNwCqkzKtrY1eXmcOTAgeDTrZ6NkiPmWqL7BG+r2zROIqt1fCrtnsrVu9u76LHSriwznaZFn0zRI46O'
    'zI6OHEndzXLunZuUy6uaudZJCjfPG8JibevZV4JeCZlYdzzJstXXzqUaVFw4t7FT20IR/oDfrPBpTi7pE7F97feCdldynl3p'
    'tKdPcFPdrnQSeXWlq6L2NQ69/5Sli7RhrExqByU97xsF3fPq2omyLq/zRDEvHumFNR3F9BqBLkr0dalI5gW3apSLGLWbvdhI'
    '3vPhLCF5Ollqh0rl5VjxkoSbr5/O77QYr3udNNV0J71utza9kVXNH6fm6Y/Dx+iP/cP0x+99iv746F764/5d7ebd7n0BAAAA'
    'AAAAAGhgbbj/BwAAAAAAAGh4eP4PAAAAAAAA0Pjw/B8AAAAAAACg8eH5PwAAAAAAAMA/jvv/7R0vBDrWO/5lx//Q8Rp9CQAA'
    'AAAAAAC/IR/Z3swGz6vaWT3PC/a/F9LUsa15X6v7X5u7r7l5X9DQzARkiSiGHvpoU3NXa82/n/VAsHm4xfkHGEg2SwSDWyKa'
    '/U+jmQfD838AAAAAAACAhoa//wcAAAAAAABofHj+DwAAAAAAAID7fwAAAAAAAAC49+H3/wEAAAAAAAAan/n8P9jxLwL0CwAA'
    'AAAAAADuCd3BHYF9H9vd0t7e3tK78fyr62//yforVzd+9iODXDBOne7p7hlIJLsTvclMsu/R/uFH+5Nt1v3/fw7QLwAAAAAA'
    'AABoEB3N+1rdbwzg7/8BAAAAAAAAGh/+/h8AAAAAAACg8dl////PA/QLAAAAAAAAAD7UhoLhQOvuB3t7J5I6nyOMohrE44/9'
    'ff/+//8K0C8AAAAAAAAAaDShba27WyeS7R3/NbAr+JXArqaOP9852P6zHZ1t/2vLH7d9J/TX225u+2HT/9n0RvArwWOBzwQj'
    'tVms7r4vHNm3L7hGDH5eJoIqSsoCZ773oLtf7x6dSY1kUkxm5PBkinHHMLE2hpFEZjydSY2lZphjM+NTIzMnmYnUSWZkLjM9'
    'nqa7TqXSmTgjSjmi6JKqcK706ekMk56bnIwzF2l2msqdJctMJnUiU46hBzA/+9AOPZI6OjI3mWGi0TgjaIQ3iMjxRtUeTCEv'
    'esfQvObS48fnUjF3adwHZ9nhjlBkKhoMSIpILujnZMkgHF8wVGubcyq/xMsFonPJis37Vj+1KxyJRoOXd7ib046s2OjwalA7'
    'ymrRum3FzKSOpmZS6dHUbHHXUno9JomsWU2/5nTvzGuGJMhEj7mqb+5rFaRqN3fD12te1wCo08Yj7aHIXNcmbVyuVKmdy0G7'
    '1uI7w5GuruCVsLutywlqAtq92rwcfQcjWTHfoKtsHntUmQ0gGsv56tjRx1OjEzE7ZjzNxKLCoioJJBqPmiOb/k8p5IgmCVGW'
    'jTNq3rDqfEZXFb9OOHU6ah4rV5ANidMJrb5RO0yKqbtpjymSxwQS6ajUJOtwNZE0d53m6hXF5FVdsmJqZ7HfnGQP7QhFZhN+'
    'Pa4b/ALp4UQiSE6HV4fsXDvRFo4kEsErgtXf1fHV2zsqers61urs6ini7vEtzJJiZtWLj0siwUQlRZALIokyX2Si5EL5dY5f'
    'nidRmo8VaM0S2na6d1fYa61HBK1XbTjDVBXi9lPPr3/trfVrV25/+/qtq39x8/pPN755deOl1za+d/Xm6++896tr7/7iq+vX'
    'vnPz9Rff/cVTt66+8d6vnjH7n3YmUTwXWFfe47PTQwPdSYZmt/7STzaef/P283T/a7d/+M2N115ef+VZmqmdnd+q0cauTbWG'
    'Iw89FLxin4UWpYVFmX4bevlVW0V3lsO3Pmvvck3M0wHkMd6rGjrJ3Pr5W+uvfuX2yz+/+b2VijOX9z5O//z4hXffvnHz25fX'
    'v/qntGduP/XMxo2f070FVVY1vzXgDE9HUtwcYtYrc0zxuXmiWa9o2TU+6oycLQwQWt7Xrt+68ROz5556Zv3lH9t95jed2+KM'
    'RlcH9yJVzp693BIORx58MHiVs/ozW5Blsx300ouWit4sBX8QszIryaR2fa6elbSdaS91bbzxzs3rZou/+8s31597llZ+/bkf'
    'bzzz5+s//Ya1+YzV8cbi5j147MhR5t23/+jWr19cv/bm+o3v3v7W0+uvv7Bx/Z1bb/3l+juXzdm0yPf0D9TNiebjlOXK0+s3'
    '/mrj6y/c+uGKWaiv/vL21WfNOfq1FzZefGPj+09t/PwrdukUzhyYer3Fn+aaX86LWbOTN75h9vCtV7+/8d3n1l/56a03fvDu'
    'L95muu28eFH06OrqEvrM942Xnrr166/T5cOc9Ve+tf70D8xM2WRLKJJ60G/ZL/U8lyy9bD0WCkUeeSR40l7m7V10cq5AFKF6'
    'M1y5yFdGxsxhEKdb7OpHtocj/f3BtTErT0MyZMKJhbwsCXR0cwVFVL3CQhW5e6WwBiw9wtaWH40sSeS8eUVXc9XEC0b1RDeD'
    '50lW1YjHlYC1T5YOfJ84s3AKqTMq2tjV5eZw5MCB4NOtno2SI+ZaovsEb6vbNE4iq3V8Ku2eytW727vosdKuLDOdpkWfTNEj'
    'jo7Mjo4cSd3Ncu6dm5TLq5q51kkKN88bwmJt69mXcV4JmVh3PMmy1RfApRpUXP22sVPbQhH+gN+s8GlOLukTsX3t94J2V3Ke'
    'Xem0p09wU92udBJ5daWrovYlipbjZekibRgrk9pBSS/CjILueWnsRFnXxnmimJfm9KqYjmJ6jUUXJfq6VCTzalk1ykWM2s1e'
    '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'
    'ADQ8PP8HAAAAAAAAaHx4/g8AAAAAAADQ+Mzn/6HQI4H2L+88t+NLrbvDj9ENAACAstX7QnsDc3Nd9z+Q2B1Y6Qg8YAYeHI0U'
    '4wcHs4N8X7I30U/4ZKKvt3sgwfcN9SeSA/PZXr4nOTDED27lY269PrfG/iBdkQiS+cG1HN0yP+h2IvlkZzGs89FOSRHkgkg6'
    '453kAn1lpaQJdSuShiqqYX5Ub6f1OboL1qtLOV4p8LLnZ+X29X9eI0sSOZ+V1fP7NaITXhMWE4bGC6Qr2bM/ub9v7dh2p0l6'
    'dz/cvzZR3STZPpp2eEBMJEWhJ9FHxN7EsNA3kBjo658fFAe6e3v6yfttEvvzhu2fnFk1d6tYnz9s1q/0UudzhDGTmWFOypoP'
    'Ck722ZWnLSQS2uZS3pCKrZeViCzanz3c1z800J9M9if6+5J8oq9nWEwMDQjdiWFa4u6sOES6h82PT5bJAi8sW7sk4525gmxI'
    'nE5kItCjZnlZJ7RLaKlohhvPv7r+9p+sv3J142c/sj532Tys+anKp+OdeVWX7FJ0xzt1unOpRMZy3tzZ+azlgiIZm3Vpz8Cm'
    'XXr5zLZP2l3KzgXWtq/RDq7s074k7ZOhgd7EAMkKib7ubG9ivn9IoG0hiqRH6O5PDgzeXZ/O87SVJIVwVpNvpbGfvIe7614u'
    'e7Gn/D5re7PBtpJv/l17sHXtDRwKVA+13sEkT9fOocT8kCgm6JpBV1Sxly6w/bw5LJLJ+b7uu1tRS0Mtr6mGKqhy8f9PdhZf'
    'caVPC1cKsnzXVV7d0eQsmVF6FgnX1LlP7O3mewd7Esnh3n46vQaTiaHBPj7R0zfUOy+IyZ5ekvyA6iwZJGedQlSJ9maye39P'
    'sne4i+603x5OtKfNJJzT+XTzIl0/NZU7S5bp9kTy7gfA5S8F7dVmT9fe1bNry4E9Vc0xPNSf5IeHswleGKbNwfMiPamKfGIo'
    '292XFXoEIdk79P5XG8XQVLn0WXCcbvAaPS3oxDDopr7pJ+KbA8L1Efjuj7lXs9n/zh9wbx+8NDi7L/3j/AB/pxPf7wA0n/+H'
    'OnYEOv5tx42Opzu+0NFHNwAAAAAAAAAaV6x5LriVN+y3R5vnWrfyvoyZ4VYeioTMhFt536vJSriF95+3mQm38u5hs33/fzLQ'
    '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'
)


class UndoLaneDigestFormLegacyFixtureTests(unittest.TestCase):
    """真实 2.1.4 摘要形态旧库（#22 收窄声明的边界）：可证明才重写，其余继续阻断。"""

    def setUp(self):
        self._fixture = tempfile.TemporaryDirectory(prefix='rf_r8b_fl6_pre214_')
        self.db = Path(self._fixture.name) / 'task' / 'user.db'
        self.db.parent.mkdir(parents=True)
        import base64
        import gzip
        self.db.write_bytes(gzip.decompress(base64.b64decode(''.join(_LEGACY_PRE214_DB_B64))))

    def tearDown(self):
        self._fixture.cleanup()

    def ctx(self):
        return TraceContext(task_id=TASK, actor_id=ACTOR, source='undo', request_id=str(uuid.uuid4()))

    def _state(self):
        with closing(sqlite3.connect(self.db)) as conn:
            entry = conn.execute("SELECT id,after_json,undone FROM workspace_undo WHERE stage='screen'").fetchone()
            note = conn.execute('SELECT text,created_at,updated_at FROM coding_notes WHERE zotero_key=?',
                                ('K1',)).fetchone()
            decision = conn.execute('SELECT decision FROM decisions WHERE zotero_key=?', ('K1',)).fetchone()
            versions = sorted(r[0] for r in conn.execute('SELECT version FROM workflow_migrations'))
            app = sorted(r[0] for r in conn.execute('SELECT DISTINCT app_version FROM trace_events'))
        return {'id': entry[0], 'evidence': json.loads(entry[1])['coding_evidence'],
                'undone': entry[2], 'note': note, 'decision': decision,
                'migrations': versions, 'app_versions': app}

    def test_r8b22_real_digest_form_fixture_is_genuine(self):
        """夹具必须是摘要形态 + 2.1.4 形态，否则下面两条结论不成立。"""
        state = self._state()
        self.assertEqual(state['app_versions'], ['2.1.4'])
        self.assertNotIn(7, state['migrations'], '夹具必须是升级前的库（尚未落 v7）')
        shaped = state['evidence']['1']
        self.assertEqual(sorted(shaped), ['digest'], '本条目必须是摘要形态（无 note 全文）')
        self.assertEqual(len(shaped['digest']), 64)

    def test_r8b22_real_digest_form_provable_entry_is_rewritten(self):
        """可证明分支在**真实旧库**上成立：内容未变 -> 重写为新公式、保留 legacy_digest、可撤销。

        这条不依赖测试内自造的旧公式：摘要由 a080fde 代码写库，迁移的判据
        （stored == f_old(当前内容)）必须在真实数据上逐字节成立。"""
        legacy = self._state()['evidence']['1']['digest']
        coding_evidence.migrate(self.db)
        state = self._state()
        self.assertEqual(sorted(state['evidence']['1']), ['digest', 'legacy_digest'])
        self.assertEqual(state['evidence']['1']['legacy_digest'], legacy, '旧摘要必须保留可追溯')
        self.assertNotEqual(state['evidence']['1']['digest'], legacy)
        result = workspace_undo.undo(self.db, self.ctx(), 'screen', state['id'])
        self.assertTrue(result['undone'], '真实可证明的摘要形态条目迁移后必须可撤销')
        self.assertIsNone(self._state()['decision'])

    def test_r8b22_real_digest_form_drift_stays_blocked(self):
        """**收窄声明的边界（如实钉住）**：摘要形态 + 时间戳漂移无法证明，仍阻断。

        摘要不可逆：旧摘要含旧 updated_at，升级时已无法由当前内容重建，
        因此这条不能在升级中修复——与 RELEASE_NOTES 的披露一致（宁可 409，
        不得静默覆盖较新的下游工作）。本钉子防止把声明写成「旧库全部可撤销」。"""
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE coding_notes SET updated_at='2031-01-01T00:00:00' WHERE zotero_key=?", ('K1',))
        legacy = self._state()['evidence']['1']['digest']
        coding_evidence.migrate(self.db)
        state = self._state()
        self.assertEqual(state['evidence']['1'], {'digest': legacy}, '无法证明的条目必须原样保留')
        with self.assertRaises(ValueError) as caught:
            workspace_undo.undo(self.db, self.ctx(), 'screen', state['id'])
        self.assertIn('downstream work has changed', str(caught.exception))
        after = self._state()
        self.assertEqual(after['decision'][0], 'include', '不得为清理误拒而放宽守卫')
        self.assertEqual(after['undone'], 0)

    def test_r8b22_migration_boundary_is_documented_in_code(self):
        """边界必须写在代码 docstring 里（不只是测试/发布说明），防止声明漂移。"""
        source = (Path(__file__).resolve().parents[2] / 'coscreen' / 'coding_evidence.py').read_text(encoding='utf-8')
        self.assertIn('可证明才重写', source, '#22：迁移函数必须自述「可证明才重写」边界')
        self.assertIn('legacy_digest', source)
        digest_doc = (Path(__file__).resolve().parents[2] / 'coscreen' / 'workspace_undo.py').read_text(encoding='utf-8')
        self.assertIn('legacy_timestamps', digest_doc)
        self.assertIn('仅用于升级迁移判定', digest_doc, '旧公式只用于迁移判定，不得回流到守卫比较')


if __name__ == '__main__':
    unittest.main()
