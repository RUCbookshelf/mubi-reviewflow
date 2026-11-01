"""r9 L3 证据附件载体钉子（b04·F3-16/F3-21、b11·F3-15/F3-19/F3-20/F3-23）。

每条用例复刻 `out/release_plan/inventory_夜扫286确认分组.md` §3.3 同款探针形态
（真实 stage_upload/save_form/cleanup_staging + 真实临时库），修复前为红：

- F3-16：暂存文件被截断后 exists() 为真即跳过重写，附着也不校验磁盘字节与
  行内 sha256 是否一致 —— 库内审计身份与实际内容永久分叉（探针：磁盘 36B
  sha≠行内 73B sha，save_form 仍成功）。
- F3-21：仅恢复 db 后同字节重传只写新文件、不回写 path 列 —— 同一 uid 永远
  无法附着（探针：path unchanged=True、attach 两次均 ValueError）。
- F3-15：写盘先于 INSERT，失败后行已回滚而文件留存，cleanup_staging 只扫
  DB 行，孤儿文件 7 天 GC 也看不见（探针：0 行 + 75 字节孤儿）。
- F3-19：workflow_form_requests 无保留上限，常态保存每次永久 +1 行
  （探针：5 次保存 → 5 行）。
- F3-20：stage_upload 不校验 zotero_key 存在于 articles，也无每操作人配额
  （探针：3 个假 key 全部接受、0 篇文章匹配）。
- F3-23：keep 语义「未列全删」且没有"全部保留"的表达方式（探针：101 张图
  keep 前 100 → 第 101 张被删）。

对照组（同文件内 *_control_* 用例）钉住合法路径逐位不变。
"""
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_BASE = Path(os.environ.get('RF_R9_TMP', 'E:/rf214tmp/r9/l3')).resolve()
_BASE.mkdir(parents=True, exist_ok=True)
_fixture = tempfile.TemporaryDirectory(prefix='rf_r9_l3_ev_', dir=str(_BASE))
os.environ['COBOOKSHELF_DATA_DIR'] = _fixture.name
os.environ.setdefault('REVIEWFLOW_MODE', 'desktop')

from coscreen import coding, coding_evidence, db as articles_db, review_workflow as workflow
from coscreen.db import _connect
from coscreen.models import Article
from coscreen.research_trace import TraceContext

TASK = 'r9-l3-evidence-task'
ACTOR = 903


def _png(color=(255, 0, 0)) -> bytes:
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', (8, 8), color).save(buffer, format='PNG')
    return buffer.getvalue()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ctx(request_id=None):
    return TraceContext(task_id=TASK, actor_id=ACTOR, source='manual',
                        request_id=request_id or str(uuid.uuid4()))


_TEMPLATE = None


def _template_db() -> Path:
    global _TEMPLATE
    if _TEMPLATE is None:
        root = Path(tempfile.mkdtemp(dir=str(_BASE), prefix='r9l3_tpl_'))
        db = root / 'task' / 'user.db'
        db.parent.mkdir(parents=True)
        articles_db.init_db(db)
        articles_db.upsert_articles(db, [Article(zotero_key='K1', title='Study', doi='10.x/1')], [])
        coding_evidence.migrate(db)
        _TEMPLATE = db
    return _TEMPLATE


class EvidenceTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory(prefix='rf_r9_l3_case_', dir=str(_BASE))
        self.db = Path(self._dir.name) / 'task' / 'user.db'
        self.db.parent.mkdir(parents=True)
        shutil.copyfile(_template_db(), self.db)
        self.dim = coding.add_dimension(self.db, '证据', 'text', [])

    def tearDown(self):
        self._dir.cleanup()

    def row(self, uid):
        with closing(_connect(self.db)) as conn:
            return conn.execute('SELECT path,sha256,attached,task_id FROM workflow_evidence_uploads WHERE id=?',
                                (uid,)).fetchone()


# ---------------------------------------------------------------------------
# b04·F3-16
# ---------------------------------------------------------------------------
class TruncatedStagedFileTests(EvidenceTestCase):
    def test_f3_16_truncated_file_is_rewritten_on_same_byte_reupload(self):
        data = _png()
        up = coding_evidence.stage_upload(self.db, TASK, ACTOR, 'K1', self.dim, data)
        path = Path(self.row(up['upload_id'])[0])
        path.write_bytes(path.read_bytes()[:36])          # 模拟断电半截写
        self.assertNotEqual(_sha(path.read_bytes()), up['sha256'])

        again = coding_evidence.stage_upload(self.db, TASK, ACTOR, 'K1', self.dim, data)
        self.assertEqual(again['upload_id'], up['upload_id'], '同字节重传必须命中同一行')
        self.assertEqual(_sha(path.read_bytes()), up['sha256'], '截断文件必须被重写为完整字节')
        self.assertEqual(self.row(up['upload_id'])[1], _sha(path.read_bytes()))

        coding_evidence.save_form(self.db, 'K1', {}, notes={self.dim: 'n'},
                                  evidence={self.dim: {'keep_image_ids': [], 'upload_ids': [up['upload_id']]}},
                                  trace_context=_ctx())
        with closing(_connect(self.db)) as conn:
            image = conn.execute('SELECT sha256 FROM coding_note_images').fetchone()
        self.assertEqual(image[0], _sha(path.read_bytes()), '附着记录的 sha256 必须等于磁盘字节')

    def test_f3_16_attach_rejects_staged_file_whose_bytes_drift(self):
        """未重传即附着：磁盘字节与行内 sha 不符时不得静默入库（审计身份分叉）。"""
        data = _png((0, 128, 0))
        up = coding_evidence.stage_upload(self.db, TASK, ACTOR, 'K1', self.dim, data)
        path = Path(self.row(up['upload_id'])[0])
        path.write_bytes(path.read_bytes()[:36])
        with self.assertRaises(ValueError):
            coding_evidence.save_form(
                self.db, 'K1', {}, notes={self.dim: 'n'},
                evidence={self.dim: {'keep_image_ids': [], 'upload_ids': [up['upload_id']]}},
                trace_context=_ctx())
        with closing(_connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM coding_note_images').fetchone()[0], 0)

    def test_f3_16_control_intact_upload_attaches_byte_identically(self):
        data = _png((0, 0, 255))
        up = coding_evidence.stage_upload(self.db, TASK, ACTOR, 'K1', self.dim, data)
        coding_evidence.save_form(self.db, 'K1', {}, notes={self.dim: 'n'},
                                  evidence={self.dim: {'keep_image_ids': [], 'upload_ids': [up['upload_id']]}},
                                  trace_context=_ctx())
        path = Path(self.row(up['upload_id'])[0])
        with closing(_connect(self.db)) as conn:
            rows = conn.execute('SELECT id,path,sha256 FROM coding_note_images').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], str(path))
        self.assertEqual(rows[0][2], _sha(data))


# ---------------------------------------------------------------------------
# b04·F3-21
# ---------------------------------------------------------------------------
class RestoredDbStagedPathTests(EvidenceTestCase):
    def test_f3_21_reupload_repairs_path_column_after_db_only_restore(self):
        data = _png()
        up = coding_evidence.stage_upload(self.db, TASK, ACTOR, 'K1', self.dim, data)
        old_path = Path(self.row(up['upload_id'])[0])
        old_root = old_path.parent
        # 仅恢复 db 文件到新目录（不经 local_storage 的路径 rebase 工具）
        new_dir = Path(tempfile.mkdtemp(dir=str(_BASE), prefix='r9l3_restore_'))
        new_db = new_dir / 'task' / 'user.db'
        new_db.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.db)) as src, closing(sqlite3.connect(new_db)) as dst:
            src.backup(dst)
        shutil.rmtree(old_root, ignore_errors=True)
        self.db = new_db

        again = coding_evidence.stage_upload(new_db, TASK, ACTOR, 'K1', self.dim, data)
        self.assertEqual(again['upload_id'], up['upload_id'])
        new_path = Path(self.row(again['upload_id'])[0])
        self.assertEqual(new_path.parent, coding_evidence.assets_dir(new_db), 'path 列必须回写到当前 assets 根')
        self.assertTrue(new_path.is_file())
        coding_evidence.save_form(new_db, 'K1', {}, notes={self.dim: 'n'},
                                  evidence={self.dim: {'keep_image_ids': [], 'upload_ids': [again['upload_id']]}},
                                  trace_context=_ctx())
        with closing(_connect(new_db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM coding_note_images').fetchone()[0], 1)

    def test_f3_21_control_path_row_matches_disk_without_restore(self):
        up = coding_evidence.stage_upload(self.db, TASK, ACTOR, 'K1', self.dim, _png((1, 2, 3)))
        path = Path(self.row(up['upload_id'])[0])
        self.assertEqual(path.parent, coding_evidence.assets_dir(self.db))
        self.assertTrue(path.is_file())


# ---------------------------------------------------------------------------
# b11·F3-15
# ---------------------------------------------------------------------------
class OrphanStagedFileTests(EvidenceTestCase):
    def test_f3_15_failed_insert_leaves_no_orphan_file(self):
        root = coding_evidence.assets_dir(self.db)
        root.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("CREATE TRIGGER r9_boom BEFORE INSERT ON workflow_evidence_uploads "
                         "BEGIN SELECT RAISE(ABORT,'simulated insert failure'); END")
            conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            coding_evidence.stage_upload(self.db, TASK, ACTOR, 'K1', self.dim, _png())
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('DROP TRIGGER r9_boom')
            conn.commit()
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM workflow_evidence_uploads').fetchone()[0], 0)
        self.assertEqual(sorted(p.name for p in root.iterdir()), [], '失败上传不得留下无行引用的孤儿文件')

    def test_f3_15_cleanup_sweeps_orphan_file_without_row(self):
        """崩溃路径（无异常可捕获）留下的无行文件由清扫按龄期反向回收。"""
        root = coding_evidence.assets_dir(self.db)
        root.mkdir(parents=True, exist_ok=True)
        orphan = root / f'{uuid.uuid4()}.png'
        orphan.write_bytes(_png())
        stale = 8 * 86400
        os.utime(orphan, (orphan.stat().st_atime - stale, orphan.stat().st_mtime - stale))
        with closing(_connect(self.db)) as conn:
            coding_evidence.cleanup_staging(conn, self.db)
            conn.commit()
        self.assertFalse(orphan.exists(), '无行引用的过期文件必须被清扫')

    def test_f3_15_control_live_row_and_fresh_orphan_are_kept(self):
        up = coding_evidence.stage_upload(self.db, TASK, ACTOR, 'K1', self.dim, _png())
        root = coding_evidence.assets_dir(self.db)
        fresh = root / f'{uuid.uuid4()}.png'
        fresh.write_bytes(_png((9, 9, 9)))
        with closing(_connect(self.db)) as conn:
            coding_evidence.cleanup_staging(conn, self.db)
            conn.commit()
        self.assertTrue(Path(self.row(up['upload_id'])[0]).is_file(), '有行引用的暂存文件不得被清扫')
        self.assertTrue(fresh.exists(), '龄期内的新文件（并发上传刚写盘）不得被清扫')


# ---------------------------------------------------------------------------
# b11·F3-20
# ---------------------------------------------------------------------------
class UploadQuotaTests(EvidenceTestCase):
    def test_f3_20_upload_requires_existing_article_key(self):
        for index in range(3):
            with self.assertRaises(ValueError):
                coding_evidence.stage_upload(self.db, TASK, ACTOR, f'FAKE-NOT-IN-ARTICLES-{index}',
                                             self.dim, _png((index * 30, 0, 0)))
        with closing(_connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM workflow_evidence_uploads').fetchone()[0], 0)

    def test_f3_20_staged_budget_bounds_per_actor(self):
        with mock.patch.object(coding_evidence, 'MAX_STAGED_UPLOADS_PER_ACTOR', 2, create=True):
            coding_evidence.stage_upload(self.db, TASK, ACTOR, 'K1', self.dim, _png((10, 0, 0)))
            coding_evidence.stage_upload(self.db, TASK, ACTOR, 'K1', self.dim, _png((20, 0, 0)))
            with self.assertRaises(ValueError):
                coding_evidence.stage_upload(self.db, TASK, ACTOR, 'K1', self.dim, _png((30, 0, 0)))

    def test_f3_20_byte_budget_bounds_per_actor(self):
        one = _png()
        with mock.patch.object(coding_evidence, 'MAX_STAGED_BYTES_PER_ACTOR', len(one), create=True):
            coding_evidence.stage_upload(self.db, TASK, ACTOR, 'K1', self.dim, one)
            with self.assertRaises(ValueError):
                coding_evidence.stage_upload(self.db, TASK, ACTOR, 'K1', self.dim, _png((0, 60, 0)))

    def test_f3_20_control_attach_flow_and_other_actor_unaffected(self):
        # 配额按（任务, 操作人）计：另一个操作人的暂存不占本操作人的额度。
        with mock.patch.object(coding_evidence, 'MAX_STAGED_UPLOADS_PER_ACTOR', 2, create=True):
            coding_evidence.stage_upload(self.db, TASK, ACTOR + 1, 'K1', self.dim, _png((9, 9, 9)))
            up = coding_evidence.stage_upload(self.db, TASK, ACTOR, 'K1', self.dim, _png((1, 1, 0)))
            coding_evidence.stage_upload(self.db, TASK, ACTOR, 'K1', self.dim, _png((2, 2, 0)))
        # 已附着的上传继续正常附着（预算只约束新增暂存，不拦截既有行）
        coding_evidence.save_form(self.db, 'K1', {}, notes={self.dim: 'n'},
                                  evidence={self.dim: {'keep_image_ids': [], 'upload_ids': [up['upload_id']]}},
                                  trace_context=_ctx())
        with closing(_connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM coding_note_images').fetchone()[0], 1)


# ---------------------------------------------------------------------------
# b11·F3-23
# ---------------------------------------------------------------------------
class KeepImageSemanticsTests(EvidenceTestCase):
    def _seed_images(self, count):
        up = coding_evidence.stage_upload(self.db, TASK, ACTOR, 'K1', self.dim, _png())
        coding_evidence.save_form(self.db, 'K1', {}, notes={self.dim: 'n'},
                                  evidence={self.dim: {'keep_image_ids': [], 'upload_ids': [up['upload_id']]}},
                                  trace_context=_ctx())
        with closing(_connect(self.db)) as conn, conn:
            note_id = conn.execute('SELECT id FROM coding_notes').fetchone()[0]
            base = self.row(up['upload_id'])[0]
            for index in range(count - 1):
                conn.execute("INSERT INTO coding_note_images(note_id,path,sha256,ocr_text,ocr_status,created_at) "
                             "VALUES(?,?,?,'','pending','2020-01-01')",
                             (note_id, f'{base}.m{index}', f'sha-{index}'))
            ids = [r[0] for r in conn.execute('SELECT id FROM coding_note_images ORDER BY id')]
        return note_id, ids

    def test_f3_23_omitted_keep_list_preserves_every_image(self):
        note_id, ids = self._seed_images(101)
        self.assertEqual(len(ids), 101)
        # 不携带 keep_image_ids 键 = 保留现有全部图片（"全部保留"的表达方式）
        coding_evidence.save_form(self.db, 'K1', {}, notes=None,
                                  evidence={self.dim: {'upload_ids': []}}, trace_context=_ctx())
        with closing(_connect(self.db)) as conn:
            remaining = [r[0] for r in conn.execute('SELECT id FROM coding_note_images ORDER BY id')]
        self.assertEqual(remaining, ids, '未列出 keep_image_ids 时不得删除任何图片')

    def test_f3_23_control_explicit_keep_list_still_prunes(self):
        note_id, ids = self._seed_images(5)
        coding_evidence.save_form(self.db, 'K1', {}, notes=None,
                                  evidence={self.dim: {'keep_image_ids': ids[:3], 'upload_ids': []}},
                                  trace_context=_ctx())
        with closing(_connect(self.db)) as conn:
            remaining = [r[0] for r in conn.execute('SELECT id FROM coding_note_images ORDER BY id')]
        self.assertEqual(remaining, ids[:3], '显式列出的保留集仍是精确集合（既有裁剪语义不变）')


# ---------------------------------------------------------------------------
# b11·F3-23（API 面，r9 L3 登记补齐）
# ---------------------------------------------------------------------------
class KeepImageSemanticsApiTests(unittest.TestCase):
    """模块层已改成"缺键=全部保留"，但 API 的 ``EvidenceIn`` 仍把
    ``keep_image_ids`` 默认成空列表并限 100 条——101 张图的备注既发不出列表
    （422），省略该键又被翻译成空集：界面"全部保留"的保存会把 101 张图全删
    （基线探针：``omit -> 200 remaining 0/101``、``explicit 101 -> 422``）。

    修后：``keep_image_ids`` 可选（None/缺省=全部保留，转换时**省略该键**），
    显式列表仍是精确集合，100 条上限不变。
    """

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        from custom_backend import auth as auth_mod
        from custom_backend.main import app, DATA_DIR

        cls.auth_mod, cls.data_dir = auth_mod, DATA_DIR
        cls.client = TestClient(app)
        cls.client.__enter__()
        email = f"r9l3keep-{uuid.uuid4().hex[:8]}@example.test"
        user = auth_mod.create_user(email, "R9-L3-Keep-Pass-2026")
        cls.user = {"user_id": user["id"], "username": user["username"],
                    "token": auth_mod.create_access_token(user["id"], user["username"])}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def auth(self):
        return {"Authorization": "Bearer " + self.user["token"]}

    def _task_with_images(self, count=101):
        from coscreen import tasks as tasks_mod

        r = self.client.post("/api/tasks", json={"name": "r9l3keep-" + uuid.uuid4().hex[:8]},
                             headers=self.auth())
        self.assertEqual(201, r.status_code, r.text)
        task = r.json()["task_id"]
        ctx = self.client.app.state.review_workflow.context(
            task, {"user_id": self.user["user_id"], "username": self.user["username"]})
        articles_db.upsert_articles(ctx.owner_db,
                                    [Article(zotero_key='K1', title='Keep', doi='10.test/keep')], [])
        r = self.client.post(f"/api/tasks/{task}/decisions",
                             json={"zotero_key": "K1", "decision": "include"}, headers=self.auth())
        self.assertEqual(200, r.status_code, r.text)
        r = self.client.post(f"/api/tasks/{task}/coding/dimensions",
                             json={"name": "证据", "dtype": "text"}, headers=self.auth())
        self.assertEqual(201, r.status_code, r.text)
        dim = r.json()["id"]
        info = tasks_mod.find_task(task, self.data_dir)
        personal = info.dir / f"{tasks_mod.slugify_screener(self.user['username'])}.db"
        with closing(sqlite3.connect(personal)) as conn, conn:
            conn.execute('INSERT INTO coding_notes(dimension_id,zotero_key,text,created_at,updated_at) '
                         "VALUES(?,?,?,?,?)", (dim, 'K1', 'n', '2020-01-01', '2020-01-01'))
            note_id = conn.execute('SELECT id FROM coding_notes').fetchone()[0]
            for index in range(count):
                conn.execute("INSERT INTO coding_note_images(note_id,path,sha256,ocr_text,ocr_status,"
                             "created_at) VALUES(?,?,?,'','pending','2020-01-01')",
                             (note_id, f'/tmp/r9l3keep-{index}.png', f'sha-{index}'))
            ids = [row[0] for row in conn.execute('SELECT id FROM coding_note_images ORDER BY id')]
        return task, dim, personal, ids

    def _remaining(self, personal):
        with closing(sqlite3.connect(personal)) as conn:
            return [row[0] for row in conn.execute('SELECT id FROM coding_note_images ORDER BY id')]

    def test_omitting_keep_list_keeps_every_image_over_the_old_cap(self):
        """修前：省略 keep_image_ids -> 200 但 101 张图全被删除。"""
        task, dim, personal, ids = self._task_with_images(101)
        self.assertEqual(101, len(ids))
        r = self.client.post(f"/api/tasks/{task}/coding/values-batch",
                             json={"zotero_key": "K1", "notes": {str(dim): "n"},
                                   "evidence": {str(dim): {"upload_ids": []}}},
                             headers=self.auth())
        self.assertEqual(200, r.status_code, r.text)
        self.assertEqual(ids, self._remaining(personal),
                         '省略 keep_image_ids 必须保留全部 101 张图')

    def test_explicit_keep_list_is_still_exact_and_cap_is_unchanged(self):
        """对照：显式列表仍是精确集合；>100 条显式列表仍按既有上限拒绝。"""
        task, dim, personal, ids = self._task_with_images(101)
        r = self.client.post(f"/api/tasks/{task}/coding/values-batch",
                             json={"zotero_key": "K1", "notes": {str(dim): "n"},
                                   "evidence": {str(dim): {"keep_image_ids": ids[:3],
                                                           "upload_ids": []}}},
                             headers=self.auth())
        self.assertEqual(200, r.status_code, r.text)
        self.assertEqual(ids[:3], self._remaining(personal), '显式列出即精确集合')

        task2, dim2, personal2, ids2 = self._task_with_images(3)
        too_many = list(range(1, 102))
        r = self.client.post(f"/api/tasks/{task2}/coding/values-batch",
                             json={"zotero_key": "K1", "notes": {str(dim2): "n"},
                                   "evidence": {str(dim2): {"keep_image_ids": too_many,
                                                           "upload_ids": []}}},
                             headers=self.auth())
        self.assertEqual(422, r.status_code, '显式列表 100 条上限保持（旧行为不变）')
        self.assertEqual(ids2, self._remaining(personal2))

    def test_null_keep_list_means_keep_everything(self):
        """``null`` 与缺键同义：转换层必须省略该键（否则模块层 set(None) 500）。"""
        from custom_backend.workspace_undo_api import EvidenceIn, _evidence_intents

        self.assertIsNone(EvidenceIn().keep_image_ids)
        self.assertIsNone(EvidenceIn(keep_image_ids=None).keep_image_ids)
        self.assertNotIn("keep_image_ids", _evidence_intents({7: EvidenceIn(keep_image_ids=None)})[7])
        self.assertEqual([], _evidence_intents({7: EvidenceIn(keep_image_ids=[])})[7]["keep_image_ids"], 
                         '显式空列表仍是"删光"，语义未被 null 合并')

        task, dim, personal, ids = self._task_with_images(3)
        r = self.client.post(f"/api/tasks/{task}/coding/values-batch",
                             json={"zotero_key": "K1", "notes": {str(dim): "n"},
                                   "evidence": {str(dim): {"keep_image_ids": None,
                                                           "upload_ids": []}}},
                             headers=self.auth())
        self.assertEqual(200, r.status_code, r.text)
        self.assertEqual(ids, self._remaining(personal), 'keep_image_ids=null 必须等于全部保留')


# ---------------------------------------------------------------------------
# b11·F3-23（前端面，r9 L3 登记补齐）
# ---------------------------------------------------------------------------
NODE_EXE = (
    os.environ.get('RF_NODE')
    or shutil.which('node')
    or r'C:\Users\16073\AppData\Local\hermes\node\node.exe'
)

#: 「保存时到底下发了什么」的 driver：真模块 + 受控 DOM/api。101 张已存图，
#: 先原样保存一次，再点 Remove image 删一张后保存一次。
EVIDENCE_SAVE_DRIVER = r"""
'use strict';
const fs=require('fs'),vm=require('vm'),path=require('path');
const ROOT=process.argv[2],N=101;
class El{
  constructor(tag){this.tagName=String(tag).toLowerCase();this.nodeType=1;this.attrs={};this.children=[];
    this.parentNode=null;this._l={};this.dataset={};this.style={};this.value='';this.textContent='';
    this.hidden=false;this.disabled=false;this.checked=false;}
  setAttribute(k,v){this.attrs[k]=String(v);if(k==='id')this.id=String(v);
    const m=/^data-(.*)$/.exec(k);if(m)this.dataset[m[1].replace(/-([a-z])/g,(x,c)=>c.toUpperCase())]=String(v);}
  getAttribute(k){return Object.prototype.hasOwnProperty.call(this.attrs,k)?this.attrs[k]:null;}
  removeAttribute(k){delete this.attrs[k];}
  _flat(ns){const out=[];for(const n of ns){if(n==null)continue;if(Array.isArray(n))out.push(...this._flat(n));else out.push(n);}return out;}
  append(...ns){for(const n of this._flat(ns)){if(n.parentNode)n.parentNode._detach(n);n.parentNode=this;this.children.push(n);}}
  appendChild(n){this.append(n);return n;}
  _detach(n){const i=this.children.indexOf(n);if(i>=0)this.children.splice(i,1);}
  remove(){if(this.parentNode)this.parentNode._detach(this);}
  replaceChildren(...ns){this.children=[];this.append(...ns);}
  after(...ns){this._after=this._after||[];this._after.push(...this._flat(ns));}
  prepend(...ns){this.children.unshift(...this._flat(ns));}
  addEventListener(t,f){(this._l[t]=this._l[t]||[]).push(f);}
  dispatch(t,ev){ev=ev||{};ev.type=t;if(!ev.preventDefault)ev.preventDefault=()=>{};(this._l[t]||[]).forEach(f=>f.call(this,ev));}
  click(){this.dispatch('click');}focus(){}
  get isConnected(){return true;}
  _all(){const out=[];const walk=n=>{for(const c of n.children||[]){out.push(c);walk(c);}};walk(this);return out;}
  _match(sel){return sel.split(',').some(part=>{part=part.trim();
    if(part.startsWith('#'))return this.attrs.id===part.slice(1);
    if(part.startsWith('.'))return String(this.attrs['class']||'').split(/\s+/).includes(part.slice(1));
    return this.tagName===part.toLowerCase();});}
  querySelector(sel){return this._all().find(n=>n.nodeType===1&&n._match(sel))||null;}
  querySelectorAll(sel){return this._all().filter(n=>n.nodeType===1&&n._match(sel));}
}
const out={bodies:[],errors:[]};
globalThis.window=globalThis;
globalThis.toast=t=>{out.toast=String(t);};
globalThis.T=(s,...a)=>String(s).replace(/\{(\d+)\}/g,(m,d)=>a[+d]===undefined?m:String(a[+d]));
globalThis.el=(tag,attrs,...kids)=>{const n=new El(tag);
  for(const [k,v] of Object.entries(attrs||{})){if(v==null)continue;
    if(k==='text')n.textContent=String(v);else if(k==='value')n.value=String(v);
    else if(k==='hidden')n.hidden=!!v;else if(k.startsWith('on')&&typeof v==='function')n.addEventListener(k.slice(2),v);
    else n.setAttribute(k,v);}
  n.append(...kids);return n;};
const byId={},docQ={};
globalThis.$=sel=>byId[String(sel).replace(/^#/,'')]||null;
globalThis.$$=sel=>{const m=/^#cbForms\s+(.*)$/.exec(sel);
  return m?byId.cbForms.querySelectorAll(m[1]):[];};
globalThis.document={nodeType:9,createElement:t=>new El(t),
  createTextNode:t=>({nodeType:3,textContent:String(t),children:[]}),
  getElementById:id=>byId[id]||null,querySelector:sel=>docQ[sel]||null,querySelectorAll:()=>[],
  addEventListener:()=>{},dispatchEvent:()=>true,
  head:new El('head'),body:new El('body'),documentElement:new El('html')};
globalThis.localStorage={getItem:()=>null,setItem:()=>{}};
globalThis.URL={createObjectURL:()=>'blob:x',revokeObjectURL:()=>{}};
globalThis.FormData=class{append(){}};
globalThis.CustomEvent=class{constructor(t,o){this.type=t;this.detail=(o||{}).detail;}};
for(const id of ['cbLabel','cbTitle','cbForms','cbSave','cbStatus']){byId[id]=new El('div');byId[id].setAttribute('id',id);}
globalThis.S={task:{task_id:'t1'},taskVersion:1,screener:'u',cbIdx:0,
  cbQueue:[{zotero_key:'K1',title:'Study'}],
  dims:[{id:1,name:'证据',dtype:'text',options:[],required:false,field_id:'f1'}],
  codingFormReady:false};
globalThis.taskApi=()=>'/api/tasks/t1';
globalThis.scrQ=()=>'';
globalThis.openBenchPdf=()=>{};
globalThis.applyCodingProgress=()=>{};
globalThis.WorkspaceUndo={isBusy:()=>false,beginSave:()=>{},endSave:()=>{}};
const images=[];
for(let i=1;i<=N;i++)images.push({id:i,url:'/img/'+i,objectURL:'blob:'+i});
const item={article:{zotero_key:'K1'},writable:true,policy_version:4,dimensions:[],mapping_issues:[],
  evidence:{1:{text:'n',required:false,images}},saved:{1:''}};
globalThis.WorkflowControls={scope:()=>'u|personal|K1',version:()=>4,refreshing:()=>false,
  renderAllowed:()=>true,settings:()=>({stages:{coding:{mode:'active'}},task_kind:'personal'}),
  item:async()=>JSON.parse(JSON.stringify(item)),queue:async()=>{},guard:async()=>true,
  refresh:async()=>{},updateProgress:async()=>{},errorText:e=>String(e),
  error:e=>out.errors.push(String(e)),reason:r=>String(r)};
globalThis.api=async(url,opts)=>{
  if(String(url).indexOf('/coding/values-batch')>=0){out.bodies.push(opts&&opts.body);
    return {evidence:item.evidence,progress:null,complete:true};}
  return {};};
vm.runInThisContext(fs.readFileSync(path.join(ROOT,'custom_frontend','workflow_evidence.js'),'utf8'),
  {filename:'workflow_evidence.js'});
(async()=>{
  const E=globalThis.WorkflowEvidence;
  await E.render();
  await E.save();
  const clean=out.bodies[0]&&out.bodies[0].evidence['1'];
  out.cleanHasKeep=clean?Object.prototype.hasOwnProperty.call(clean,'keep_image_ids'):'NO-BODY';
  out.cleanKeepLen=clean&&clean.keep_image_ids?clean.keep_image_ids.length:0;
  const host=byId.cbForms.querySelector('.wf-evidence-images');
  const removeBtn=host&&host.querySelectorAll('button').find(b=>b.textContent==='Remove image');
  out.foundRemove=!!removeBtn;
  if(removeBtn)removeBtn.click();
  await E.save();
  const pruned=out.bodies[1]&&out.bodies[1].evidence['1'];
  out.prunedHasKeep=pruned?Object.prototype.hasOwnProperty.call(pruned,'keep_image_ids'):'NO-BODY';
  out.prunedKeepLen=pruned&&pruned.keep_image_ids?pruned.keep_image_ids.length:0;
  out.prunedFirstId=pruned&&pruned.keep_image_ids?pruned.keep_image_ids[0]:null;
  /* 再走一遍"删光"：不中途保存（保存后 render 会按服务端结果重填），
     连点 Remove image 直到没有按钮，再保存 —— 必须显式下发空列表。 */
  await E.render();
  let guard=0;
  for(;;){
    const box=byId.cbForms.querySelector('.wf-evidence-images');
    const btn=box&&box.querySelectorAll('button').find(b=>b.textContent==='Remove image');
    if(!btn||guard++>N+5)break;
    btn.click();
  }
  await E.save();
  const emptied=out.bodies[2]&&out.bodies[2].evidence['1'];
  out.emptiedHasKeep=emptied?Object.prototype.hasOwnProperty.call(emptied,'keep_image_ids'):'NO-BODY';
  out.emptiedKeepLen=emptied&&emptied.keep_image_ids?emptied.keep_image_ids.length:'NO-KEY';
  out.errors=out.errors;
  console.log(JSON.stringify(out));
})();
"""


class KeepImageFrontendNails(unittest.TestCase):
    """前端面：界面「全部保留」的保存不得下发 101 条 id。

    修前（基线探针）：无论用户是否删图，``E.save`` 都把当前全部图片 id 塞进
    ``keep_image_ids``——101 张图时请求体必然超过接口的 max_length=100，
    界面永远存不下这一篇（探针 ``cleanKeepLen=101``、``prunedKeepLen=100``）。
    修后：没删图时不带该键（= 全部保留），删过图才下发精确列表。
    """

    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[2]

    def _run(self):
        if not Path(NODE_EXE).exists():
            raise unittest.SkipTest('node runtime not found')
        script = Path(tempfile.mkdtemp(dir=str(_BASE), prefix='r9l3_js_')) / 'save_probe.cjs'
        script.write_text(EVIDENCE_SAVE_DRIVER, encoding='utf-8')
        proc = subprocess.run([NODE_EXE, str(script), str(self.root)], capture_output=True,
                              text=True, encoding='utf-8', timeout=120, cwd=str(self.root))
        self.assertEqual(0, proc.returncode, proc.stderr[-1500:])
        lines = [line for line in proc.stdout.strip().splitlines() if line.strip()]
        self.assertTrue(lines, proc.stderr[-800:])
        return json.loads(lines[-1])

    def test_unchanged_field_sends_no_keep_list(self):
        out = self._run()
        self.assertEqual([], out['errors'])
        self.assertFalse(out['cleanHasKeep'],
                         '未删图时不得下发 keep_image_ids（101 张会被 100 上限拒绝）')

    def test_after_one_removal_the_exact_list_is_sent(self):
        out = self._run()
        self.assertTrue(out['foundRemove'], '未渲染出 Remove image 按钮')
        self.assertTrue(out['prunedHasKeep'], '删过图必须下发精确保留集')
        self.assertEqual(100, out['prunedKeepLen'])
        self.assertEqual(2, out['prunedFirstId'], '下发的是精确集合（被删的 1 不在其中）')

    def test_removing_every_image_sends_an_explicit_empty_list(self):
        """删光必须下发空列表（= 精确集合），不得退回"省略该键=全部保留"。"""
        out = self._run()
        self.assertTrue(out['emptiedHasKeep'], '删光时必须显式下发 keep_image_ids')
        self.assertEqual(0, out['emptiedKeepLen'], '空集必须真的下发，而不是被当成缺省')


# ---------------------------------------------------------------------------
# b11·F3-19
# ---------------------------------------------------------------------------
class FormRequestRetentionTests(EvidenceTestCase):
    def test_f3_19_form_request_summaries_are_bounded(self):
        with mock.patch.object(coding_evidence, 'FORM_REQUEST_RETAIN_PER_ACTOR', 5, create=True):
            for index in range(8):
                coding_evidence.save_form(self.db, 'K1', {self.dim: f'v{index}'}, notes=None,
                                          evidence=None, trace_context=_ctx())
            with closing(_connect(self.db)) as conn:
                rows = conn.execute('SELECT COUNT(*) FROM workflow_form_requests').fetchone()[0]
        self.assertLessEqual(rows, 5, '幂等摘要表必须按操作人有上限，不得随保存次数无限增长')

    def test_f3_19_control_latest_request_id_still_idempotent(self):
        with mock.patch.object(coding_evidence, 'FORM_REQUEST_RETAIN_PER_ACTOR', 5, create=True):
            for index in range(6):
                coding_evidence.save_form(self.db, 'K1', {self.dim: f'v{index}'}, notes=None,
                                          evidence=None, trace_context=_ctx())
            request_id = 'latest-replay'
            coding_evidence.save_form(self.db, 'K1', {self.dim: 'final'}, notes=None, evidence=None,
                                      trace_context=_ctx(request_id))
            with closing(_connect(self.db)) as conn:
                journals = conn.execute('SELECT COUNT(*) FROM workspace_undo').fetchone()[0]
            # 重放同一 request_id：命中摘要直接返回，不产生新的保存/撤销条目
            coding_evidence.save_form(self.db, 'K1', {self.dim: 'final'}, notes=None, evidence=None,
                                      trace_context=_ctx(request_id))
            with closing(_connect(self.db)) as conn:
                after = conn.execute('SELECT COUNT(*) FROM workspace_undo').fetchone()[0]
        self.assertEqual(after, journals, '最新 request_id 的幂等语义不得被裁剪破坏')


# ---------------------------------------------------------------------------
# b11·F3-17
# ---------------------------------------------------------------------------
class UpgradeBackupIntegrityTests(unittest.TestCase):
    """.pre-workflow-v3.bak 的「存在即跳过」不再信任不可用残留（b11·F3-17）。

    探针形态同 b11 复审：删掉 v7 版本行与本节新建的三张表触发升级链，预置
    53 字节截断/0 字节备份 → 修复前迁移照常完成而备份逐字节未变、打开报
    ``file is not a database``。
    """

    def _prepare(self, payload: bytes | None):
        # 用模板库副本：新路径不在本进程的「首建库」登记里（is_fresh_database
        # 为 False），因此会真正走升级前备份分支——与复审在多进程里跑迁移等价。
        db = Path(tempfile.mkdtemp(dir=str(_BASE), prefix='rf_r9_l3_bak_')) / 'task' / 'user.db'
        db.parent.mkdir(parents=True)
        shutil.copyfile(_template_db(), db)
        with closing(sqlite3.connect(db)) as conn:
            for table in ('workflow_coding_fields', 'workflow_evidence_uploads', 'workflow_form_requests'):
                conn.execute(f'DROP TABLE {table}')
            conn.execute('DELETE FROM workflow_migrations WHERE version=?', (coding_evidence.MIGRATION_VERSION,))
            conn.commit()
        backup = db.with_name(db.name + '.pre-workflow-v3.bak')
        if payload is None:
            full = db.read_bytes()
            backup.write_bytes(full[:max(1, len(full) // 3)])
        else:
            backup.write_bytes(payload)
        return db, backup

    def _tables(self, backup: Path):
        with closing(sqlite3.connect(backup)) as conn:
            return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

    def test_f3_17_truncated_backup_is_replaced(self):
        db, backup = self._prepare(None)
        coding_evidence.migrate(db)
        with closing(sqlite3.connect(db)) as conn:
            versions = {r[0] for r in conn.execute('SELECT version FROM workflow_migrations')}
        self.assertIn(coding_evidence.MIGRATION_VERSION, versions, '迁移本身照常完成')
        self.assertIn('articles', self._tables(backup), '截断残留备份必须被可用备份替换')

    def test_f3_17_empty_backup_is_replaced(self):
        db, backup = self._prepare(b'')
        coding_evidence.migrate(db)
        self.assertGreater(backup.stat().st_size, 0, '0 字节残留备份必须被真备份替换')
        self.assertIn('articles', self._tables(backup))

    def test_f3_17_control_usable_backup_is_not_rewritten(self):
        db, _ = self._prepare(None)
        coding_evidence.migrate(db)          # 用真实迁移产出一份可用备份
        backup = db.with_name(db.name + '.pre-workflow-v3.bak')
        good = backup.read_bytes()
        with closing(sqlite3.connect(db)) as conn:
            conn.execute('DROP TABLE workflow_coding_fields')
            conn.execute('DELETE FROM workflow_migrations WHERE version=?', (coding_evidence.MIGRATION_VERSION,))
            conn.commit()
        coding_evidence.migrate(db)
        self.assertEqual(backup.read_bytes(), good, '已可用的备份不得被重写（既有语义不变）')


# ---------------------------------------------------------------------------
# b11·F3-18
# ---------------------------------------------------------------------------
class _PeerWinsProxy:
    """连接代理：在落败方执行播种 INSERT 之前，让"另一个 worker"先提交同一行。

    与 b11 复审的 Barrier 手法等价但确定化——对落败方 SQLite 连接而言，
    "对方先写完并提交，我这条写事务里的读快照里还没有该行"与真实交错不可区分。
    """

    def __init__(self, inner, dim, signature):
        self._inner = inner
        self._dim = dim
        self._signature = signature

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def execute(self, sql, *args):
        head = sql.strip().upper()
        if head.startswith('INSERT') and 'WORKFLOW_CODING_FIELDS' in head:
            self._inner.execute('INSERT INTO workflow_coding_fields VALUES(?,?,?)',
                                (self._dim, 'peer-stable-field-id', self._signature))
        return self._inner.execute(sql, *args)

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *exc):
        return self._inner.__exit__(*exc)


class EnsureFieldsRaceTests(unittest.TestCase):
    def _db(self, prefix):
        db = Path(tempfile.mkdtemp(dir=str(_BASE), prefix=prefix)) / 'task' / 'user.db'
        db.parent.mkdir(parents=True)
        shutil.copyfile(_template_db(), db)
        with closing(sqlite3.connect(db)) as conn:
            conn.execute('DELETE FROM workflow_coding_fields')
            conn.commit()
        return db

    def test_f3_18_losing_writer_does_not_raise(self):
        db = self._db('rf_r9_l3_race_')
        dim = coding.add_dimension(db, '并发字段', 'text', [])
        dimension = next(row for row in coding_evidence.field_rows(db) if row['id'] == dim)
        signature = coding_evidence.definition(dimension)
        real_connect = coding_evidence.workflow.connect

        def proxied(path, **kwargs):
            return _PeerWinsProxy(real_connect(path, **kwargs), dim, signature)

        with mock.patch.object(coding_evidence.workflow, 'connect', proxied):
            coding_evidence.ensure_fields(db)          # 修复前此处抛裸 IntegrityError
        with closing(_connect(db)) as conn:
            rows = conn.execute('SELECT field_id,definition_json FROM workflow_coding_fields WHERE dimension_id=?',
                                (dim,)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 'peer-stable-field-id', '并发播种不得改写对方已发布的稳定 field_id')
        self.assertEqual(rows[0][1], signature)

    def test_f3_18_control_sequential_seeding_unchanged(self):
        db = self._db('rf_r9_l3_seq_')
        dim = coding.add_dimension(db, '顺序字段', 'text', [])
        coding_evidence.ensure_fields(db)
        dimension = next(row for row in coding_evidence.field_rows(db) if row['id'] == dim)
        with closing(_connect(db)) as conn:
            row = conn.execute('SELECT field_id,definition_json FROM workflow_coding_fields WHERE dimension_id=?',
                               (dim,)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[1], coding_evidence.definition(dimension), '顺序播种的签名口径不变')
        self.assertNotEqual(row[0], 'peer-stable-field-id')


if __name__ == '__main__':
    unittest.main()
