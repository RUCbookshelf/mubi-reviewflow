"""r6 撤销协作泳道修复回归测试（受保护撤销 + 协作权限，2.1.4 复审终态）。

覆盖分诊确认的 13 项 still_open 修复，每条用例注明对应编号与修复前的
失败表现（复审 probe 复现手法原样回放）：
- 【undo task_id 口径】restore 预读/upload_evidence/stage_upload 统一
  info.task_id（#22：尾点别名场景可撤销、图片可附着）。
- 【note updated_at】coding_evidence 同值重存幂等 + 守卫比较面剔除
  coding_values.updated_at（#23：同值重存不再误报下游变更）。
- 【MISSING_EVIDENCE_FILE】status/latest 纳入文件存在性（#25：文件缺失
  时 undo_available=false，前端不再可点）。
- 【description 仲裁】方案 B：不改语义键集，409 文案区分"字段说明已变更"
  （#21/#26）。
- 【journal 容量】superseded 行保留策略披露（#24）。
- 【词条钉】workspace_undo 常量↔interface_locale 双键匹配断言（#28）。
- 【U4 存量撞名】invite 与按 user_id 授权口保留名校验（#29/#34）。
- 【collaboration.db 损坏】desktop 列表不剔除+管理操作给明确修库指引（#30）。
- 【legacy 无管理路径】server 模式 legacy 409 文案给出可行操作（#31）。
- 【409 词条】collaboration_owner_unset 文案十语言注册（#32）。
- 【schema_guard legacy】legacy 任务方案编辑恢复 bump_version（#33）。
- 【task_id 分裂】协作路径统一 info.task_id（#35）。
- 【is_participant 口径】读失败与无角色区分（#36）。

全部使用临时目录/任务，不触碰真实数据。
"""
import ast
import io
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 与 test_collaboration_hardening 同法：先设环境再导入 main（多测试模块共享
# 同一 main 实例时以首个导入为准，任务级隔离由每用例新建任务保证）。
fixture = tempfile.TemporaryDirectory(prefix="rf_r6_undocollab_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from coscreen import coding, coding_evidence, db as articles_db, task_collaboration, tasks as tasks_mod
from coscreen import workspace_undo
from coscreen.db import _connect
from coscreen.models import Article
from coscreen.research_trace import TraceContext

ROOT = Path(__file__).resolve().parents[2]
TASK = 'r6-undocollab-task'
ACTOR = 202


def _png_bytes(color=(255, 0, 0)) -> bytes:
    """最小合法 PNG（1x1，魔数校验与 PIL 解码均可通过）。"""
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', (1, 1), color).save(buffer, format='PNG')
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 【note updated_at】【MISSING_EVIDENCE_FILE】【description 仲裁】单元级
# ---------------------------------------------------------------------------

class UndoGuardFixTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory(prefix='rf_r6_undo_')
        self.root = Path(self._dir.name)
        self.db = self.root / 'task' / 'user.db'
        self.db.parent.mkdir(parents=True)
        articles_db.init_db(self.db)
        articles_db.upsert_articles(self.db, [Article(zotero_key='K1', title='Study', doi='10.x/1')], [])

    def tearDown(self):
        self._dir.cleanup()

    def ctx(self, source='manual'):
        return TraceContext(task_id=TASK, actor_id=ACTOR, source=source,
                            request_id=str(uuid.uuid4()))

    def latest_entry(self, stage):
        with closing(_connect(self.db)) as conn:
            row = conn.execute(
                'SELECT id FROM workspace_undo WHERE task_id=? AND actor_id=? AND stage=? AND undone=0 '
                'ORDER BY id DESC LIMIT 1', (TASK, str(ACTOR), stage)).fetchone()
        return row[0] if row else None

    def journal_count(self, stage):
        with closing(_connect(self.db)) as conn:
            return conn.execute('SELECT COUNT(*) FROM workspace_undo WHERE stage=?', (stage,)).fetchone()[0]

    # 【note updated_at】#23 复现：coding 值为空、备注文本前后全同，
    # 修复前 save_form 无条件 ON CONFLICT 刷新 updated_at（:37→:38），
    # 旧 screen 撤销摘要失配被误报"下游已变更"。
    def test_same_note_resave_keeps_screen_undo_available(self):
        dim = coding.add_dimension(self.db, '备注字段', 'text', [])
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: 'same note'}, evidence=None,
                                  trace_context=self.ctx())
        articles_db.save_decision(self.db, 'K1', 'include', trace_context=self.ctx())
        entry = self.latest_entry('screen')
        time.sleep(1.2)  # 跨秒：修复前时间戳必然漂移
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: 'same note'}, evidence=None,
                                  trace_context=self.ctx())
        result = workspace_undo.undo(self.db, self.ctx(source='undo'), 'screen', entry)
        self.assertTrue(result['undone'])
        # 幂等保存不追加 journal（修复前 rows=2）
        self.assertEqual(self.journal_count('coding'), 1)

    # 【note updated_at】AP1/#23 同族：coding 取值同值重存 (v1,t1)→(v1,t2)
    # 不再废掉既有 screen 撤销（R6-U2 验收场景）。
    def test_same_value_resave_keeps_screen_undo_available(self):
        dim = coding.add_dimension(self.db, '数值', 'numeric', [])
        coding_evidence.save_form(self.db, 'K1', {dim: '42'}, notes=None, evidence=None,
                                  trace_context=self.ctx())
        articles_db.save_decision(self.db, 'K1', 'include', trace_context=self.ctx())
        entry = self.latest_entry('screen')
        time.sleep(1.2)
        with closing(_connect(self.db)) as conn:
            before_ts = conn.execute(
                'SELECT updated_at FROM coding_values WHERE zotero_key=? AND dimension_id=?',
                ('K1', dim)).fetchone()[0]
        coding_evidence.save_form(self.db, 'K1', {dim: '42'}, notes=None, evidence=None,
                                  trace_context=self.ctx())
        with closing(_connect(self.db)) as conn:
            after_ts = conn.execute(
                'SELECT updated_at FROM coding_values WHERE zotero_key=? AND dimension_id=?',
                ('K1', dim)).fetchone()[0]
        self.assertEqual(after_ts, before_ts, '同值重存不得刷新 updated_at')
        self.assertEqual(self.journal_count('coding'), 1)
        result = workspace_undo.undo(self.db, self.ctx(source='undo'), 'screen', entry)
        self.assertTrue(result['undone'])

    # 【note updated_at】双保险：旧版本库已被同值重存刷新过时间戳（直接 UPDATE
    # 模拟），守卫比较面剔除 coding_values.updated_at 后 screen 撤销仍可用。
    def test_guard_ignores_preexisting_value_timestamp_drift(self):
        dim = coding.add_dimension(self.db, '数值', 'numeric', [])
        coding.save_coding_value(self.db, dim, 'K1', '42', trace_context=self.ctx())
        articles_db.save_decision(self.db, 'K1', 'include', trace_context=self.ctx())
        entry = self.latest_entry('screen')
        with closing(_connect(self.db)) as conn, conn:
            conn.execute('UPDATE coding_values SET updated_at=? WHERE zotero_key=? AND dimension_id=?',
                         ('2099-01-01T00:00:00', 'K1', dim))
        result = workspace_undo.undo(self.db, self.ctx(source='undo'), 'screen', entry)
        self.assertTrue(result['undone'])

    # 【note updated_at】回归：真实的下游变更（备注文本改变）仍照常阻断。
    def test_real_note_change_still_blocks_screen_undo(self):
        dim = coding.add_dimension(self.db, '备注字段', 'text', [])
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: 'old'}, evidence=None,
                                  trace_context=self.ctx())
        articles_db.save_decision(self.db, 'K1', 'include', trace_context=self.ctx())
        entry = self.latest_entry('screen')
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: 'changed'}, evidence=None,
                                  trace_context=self.ctx())
        with self.assertRaises(ValueError) as caught:
            workspace_undo.undo(self.db, self.ctx(source='undo'), 'screen', entry)
        self.assertIn('downstream work has changed', str(caught.exception))

    # 【MISSING_EVIDENCE_FILE】#25 复现：待恢复图片所在条目（其 before 快照含
    # 图片路径——即"已附着后再保存一次"产生的条目）在文件被删后 latest 修复前
    # 仍返回 {'entry_id':2,...}（前端按钮可点），undo 才抛 ValueError——现在
    # latest 带 undo_available=False。
    def test_latest_reports_missing_evidence_file(self):
        dim = coding.add_dimension(self.db, '证据', 'text', [])
        upload = coding_evidence.stage_upload(self.db, TASK, ACTOR, 'K1', dim, _png_bytes())
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: 'n'},
                                  evidence={dim: {'keep_image_ids': [], 'upload_ids': [upload['upload_id']]}},
                                  trace_context=self.ctx())
        # 第二次保存：其 before 快照携带已附着的图片路径（撤销需恢复它们）
        coding_evidence.save_form(self.db, 'K1', {}, notes={dim: 'n2'},
                                  evidence={dim: {'keep_image_ids': [], 'upload_ids': []}},
                                  trace_context=self.ctx())
        info = workspace_undo.latest(self.db, self.ctx(), 'coding')
        self.assertEqual(info['entry_id'], 2)
        self.assertTrue(info.get('undo_available', True))
        for path in coding_evidence.assets_dir(self.db).glob('*.png'):
            path.unlink()
        info = workspace_undo.latest(self.db, self.ctx(), 'coding')
        self.assertIsNotNone(info)
        self.assertIs(info.get('undo_available'), False)

    # 【description 仲裁】#21/#26：description 编辑仍阻断 coding 撤销，但文案
    # 区分"字段说明已变更"（方案 B：不改语义键集）。
    def test_description_edit_gives_distinguishing_message(self):
        dim = coding.add_dimension(self.db, '年龄', 'numeric', [])
        coding.save_coding_value(self.db, dim, 'K1', '42', trace_context=self.ctx())
        entry = self.latest_entry('coding')
        coding.update_dimension(self.db, dim, description='new description', trace_context=self.ctx())
        with self.assertRaises(ValueError) as caught:
            workspace_undo.undo(self.db, self.ctx(source='undo'), 'coding', entry)
        self.assertEqual(str(caught.exception), workspace_undo.DESCRIPTION_CHANGED)
        # description 编辑不影响 screen 守卫（其比较面不含定义）
        articles_db.save_decision(self.db, 'K1', 'include', trace_context=self.ctx())
        screen_entry = self.latest_entry('screen')
        coding.update_dimension(self.db, dim, description='again changed', trace_context=self.ctx())
        self.assertTrue(workspace_undo.undo(self.db, self.ctx(source='undo'), 'screen', screen_entry)['undone'])

    # 【description 仲裁】其他语义键（name）编辑维持泛化文案——诊断只认
    # description-only 差异。
    def test_name_edit_keeps_generic_block_message(self):
        dim = coding.add_dimension(self.db, '年龄', 'numeric', [])
        coding.save_coding_value(self.db, dim, 'K1', '42', trace_context=self.ctx())
        entry = self.latest_entry('coding')
        coding.update_dimension(self.db, dim, name=' renamed ', trace_context=self.ctx())
        with self.assertRaises(ValueError) as caught:
            workspace_undo.undo(self.db, self.ctx(source='undo'), 'coding', entry)
        self.assertIn('downstream work has changed', str(caught.exception))


# ---------------------------------------------------------------------------
# 【journal 容量】【词条钉】静态守护
# ---------------------------------------------------------------------------

class StaticGuardTests(unittest.TestCase):
    """行为不可测/文档披露类的静态守护（修复总则③）。"""

    @staticmethod
    def _locale_rows():
        """interface_locale.js 的词条行（每行一个 [...]，行内顶层字符串元素；
        本项目词条内嵌引号一律使用排版引号，不与 " 冲突）。"""
        text = (ROOT / 'custom_frontend' / 'interface_locale.js').read_text(encoding='utf-8')
        rows = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith('[') and stripped.endswith('],'):
                strings = re.findall(r'"((?:[^"\\]|\\.)*)"', stripped)
                if strings:
                    rows.append([s.replace('\\"', '"').replace('\\\\', '\\') for s in strings])
        return rows

    def test_workspace_undo_messages_registered_ten_languages(self):
        """【词条钉】#28：workspace_undo 的用户可见 ValueError 消息（常量+字面量）
        必须以完整词条出现在 interface_locale.js 且该行恰为十语言；内部消息
        （API 层 Stage Literal 校验先拦，不可达）列入豁免。任一侧改动而另一侧
        未同步即红（修复前该耦合无任何测试钉住）。"""
        source = (ROOT / 'coscreen' / 'workspace_undo.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        messages = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                    and getattr(node.exc.func, 'id', None) == 'ValueError'
                    and node.exc.args and isinstance(node.exc.args[0], ast.Constant)
                    and isinstance(node.exc.args[0].value, str)):
                messages.add(node.exc.args[0].value)
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id in {'MISSING_EVIDENCE_FILE', 'DESCRIPTION_CHANGED'}
                    and isinstance(node.value, ast.Constant)):
                messages.add(node.value.value)
        internal_only = {'Unknown Undo stage.'}  # Stage 枚举在 API 层先拦，不可达
        self.assertIn('Unknown Undo stage.', messages)  # 豁免名单不得漂移
        messages -= internal_only
        self.assertTrue(messages)
        rows = self._locale_rows()
        registered = {string for row in rows for string in row}
        for message in sorted(messages):
            self.assertIn(message, registered, f'后端消息未注册十语言词典：{message!r}')
            row = next(row for row in rows if message in row)
            self.assertEqual(len(row), 10, f'词条行必须十语言：{message!r}')

    def test_journal_retention_policy_disclosed(self):
        """【journal 容量】#24：superseded 行保留策略（有意永久保留/线性增长/
        无清理开关）在模块源码显式披露，且模块内不存在 DELETE FROM
        workspace_undo（清理开关属产品口径变更，需单独立项）。"""
        source = (ROOT / 'coscreen' / 'workspace_undo.py').read_text(encoding='utf-8')
        self.assertIn('journal 容量与保留策略披露', source)
        self.assertIn('superseded', source)
        self.assertIn('不删除', source)
        self.assertNotIn('DELETE FROM workspace_undo', source,
                         '保留策略为"不删历史"；引入清理需单独立项并更新披露')

    def test_collaboration_messages_registered(self):
        """【409 词条】#32 + #30/#34：三条协作 409 文案（legacy/撞名/损坏库）
        已注册十语言双键（errOf 经 payload.message 或 detail -> T() 命中）。"""
        from custom_backend import main as backend_main
        rows = self._locale_rows()
        registered = {string for row in rows for string in row}
        for message in (backend_main.LEGACY_OWNER_UNSET_MESSAGE,
                        backend_main.COLLABORATION_STORE_CORRUPT_MESSAGE,
                        backend_main.COLLABORATION_RESERVED_NAME_MESSAGE):
            self.assertIn(message, registered, f'协作 409 文案未注册：{message!r}')
            row = next(row for row in rows if message in row)
            self.assertEqual(len(row), 10)

    def test_locale_cache_token_matches_content(self):
        """缓存令牌守护（评审反馈 r6 第二轮；同类遗漏先例见提交 89a73b1）：
        interface_locale.js 内容与 index.html 的 ?v= 令牌必须成对变更。

        launcher 对非 HTML 静态资源下发 Cache-Control: public, max-age=86400
        且无构建期改写——只改词典不上调令牌时，升级用户最长 24h 内命中旧
        词典，新词条（本轮 #28/#30/#31/#32/#34）全部不生效。本守护把
        （令牌, 词典内容 sha256）钉成一对：任一侧单独变更即红，迫使后续
        任何词典改动同步上调令牌并更新本处 PINNED。"""
        import hashlib
        pinned_token = '20261027-r6-undo-collab1'
        pinned_sha256 = 'b7db672d1fb77624df8bea833658ac5315afb0e71a788ca675e2862b0efbc069'
        html = (ROOT / 'custom_frontend' / 'index.html').read_text(encoding='utf-8')
        match = re.search(r'<script src="\./interface_locale\.js\?v=([^"]+)"></script>', html)
        self.assertIsNotNone(match, 'index.html 必须以带 ?v= 令牌的 <script> 引用 interface_locale.js')
        token = match.group(1)
        digest = hashlib.sha256(
            (ROOT / 'custom_frontend' / 'interface_locale.js').read_bytes()).hexdigest()
        self.assertEqual(token, pinned_token,
                         'interface_locale.js 引用令牌与守护钉住的令牌不一致：'
                         '词典内容变更必须同步上调 index.html 的 ?v= 并更新 PINNED')
        self.assertEqual(digest, pinned_sha256,
                         'interface_locale.js 内容已变更但（令牌, 内容）钉对未更新：'
                         '请上调 index.html 的 ?v= 令牌并同步更新本测试的 PINNED 元组')


# ---------------------------------------------------------------------------
# 【is_participant 口径】单元级（#36）
# ---------------------------------------------------------------------------

class IsParticipantSemanticsTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory(prefix='rf_r6_ispart_')
        self.task_dir = Path(self._dir.name) / 'task'
        self.task_dir.mkdir(parents=True)
        task_collaboration.initialize_task_store(
            self.task_dir, task_id='task-A', owner_user_id=1, owner_username='owner')
        self.store = task_collaboration.shared_store_path(self.task_dir)

    def tearDown(self):
        self._dir.cleanup()

    def test_no_role_returns_false(self):
        # 确无角色（绑定匹配但无授权行）：False
        self.assertTrue(task_collaboration.is_participant(self.store, 1, task_id='task-A'))
        self.assertFalse(task_collaboration.is_participant(self.store, 2, task_id='task-A'))
        task_collaboration.grant_role(self.store, actor_user_id=1, user_id=2,
                                      role='coder', task_id='task-A')
        self.assertTrue(task_collaboration.is_participant(self.store, 2, task_id='task-A'))

    def test_read_failure_raises_instead_of_false(self):
        """#36：读失败（库损坏）不得折叠成"不是参与者"——对齐 get_owner 的
        503/修库语义（修复前返回 False -> 403 误报）。"""
        self.store.write_bytes(b'not a sqlite database at all')
        with self.assertRaises(task_collaboration.OwnerUnavailableError):
            task_collaboration.is_participant(self.store, 2, task_id='task-A')
        # 永久损坏进一步区分子类（desktop 409 修库指引的判定依据）
        with self.assertRaises(task_collaboration.CollaborationStoreCorruptError):
            task_collaboration.is_participant(self.store, 2, task_id='task-A')

    def test_missing_store_and_unbound_store_return_false(self):
        # 库不存在 = legacy：确无参与者角色
        self.assertFalse(task_collaboration.is_participant(
            self.task_dir / 'absent.db', 2, task_id='task-A'))
        # 绑定不匹配（整目录拷贝场景）：确无角色（非读取失败）
        copied = self.task_dir.parent / 'task-B'
        copied.mkdir()
        shutil.copy(self.store, copied / task_collaboration.DB_FILENAME)
        self.assertFalse(task_collaboration.is_participant(
            copied / task_collaboration.DB_FILENAME, 1, task_id='task-B'))


# ---------------------------------------------------------------------------
# HTTP 端点级：【U4 存量撞名】【collaboration.db 损坏】【legacy 409 文案】
# 【schema_guard legacy】【undo task_id 口径】【task_id 分裂】
# （单一类生命周期：fixture 由本类的 tearDownClass 统一清理）
# ---------------------------------------------------------------------------

class EndpointFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend.main import app
        cls.client = TestClient(app)
        cls.client.__enter__()
        # 不走 HTTP 注册（共享限流桶）；直接建用户并签发令牌（同既有套件手法）。
        cls.auth_mod = auth_mod
        cls.users = []
        for i in range(2):
            user = auth_mod.create_user(f"r6uc{i}@example.test", "R6-Uc-Fix-2026")
            cls.users.append({"user_id": user["id"], "username": user["username"],
                              "token": auth_mod.create_access_token(user["id"], user["username"])})

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def auth(self, i=0):
        return {"Authorization": "Bearer " + self.users[i]["token"]}

    @contextmanager
    def _mode(self, value):
        old = os.environ.get("REVIEWFLOW_MODE")
        os.environ["REVIEWFLOW_MODE"] = value
        try:
            yield
        finally:
            if old is None:
                os.environ.pop("REVIEWFLOW_MODE", None)
            else:
                os.environ["REVIEWFLOW_MODE"] = old

    def _new_task(self) -> str:
        return self.client.post(
            "/api/tasks", json={"name": "r6uc-" + uuid.uuid4().hex[:8]},
            headers=self.auth()).json()["task_id"]

    def _legacy_task(self) -> str:
        from custom_backend.main import DATA_DIR
        task = self._new_task()
        info = tasks_mod.find_task(task, DATA_DIR)
        task_collaboration.shared_store_path(info.dir).unlink()
        return task

    def _task_ids(self, actor=0):
        return [t["task_id"] for t in
                self.client.get("/api/tasks", headers=self.auth(actor)).json()["tasks"]]

    def _alias(self, task_id: str) -> str:
        """Windows 尾点别名：'T.' 与 'T' 解析到同一物理任务目录。"""
        from custom_backend.main import DATA_DIR
        alias = task_id + '.'
        if tasks_mod.find_task(alias, DATA_DIR) is None:
            self.skipTest("尾点别名仅 Windows 文件系统折叠为同目录")
        return alias

    def _seed_article_and_dim(self, task) -> int:
        """建 owner 库（经 context 迁移）并种入一条文献、一条 include 初筛决策
        （coding 可写性的前置）与一个编码字段。"""
        ctx = self.client.app.state.review_workflow.context(task, self.users[0])
        articles_db.upsert_articles(ctx.owner_db, [Article(zotero_key='K1', title='Alias Probe',
                                                           doi='10.test/alias')], [])
        r = self.client.post(f"/api/tasks/{task}/decisions",
                             json={"zotero_key": "K1", "decision": "include"}, headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post(f"/api/tasks/{task}/coding/dimensions",
                             json={"name": "证据字段", "dtype": "text"}, headers=self.auth())
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    # 【U4 存量撞名】#29/#34 复现（rv5_p2_probe 同法直接落库存量账号）：
    # 修复前 invite {username}->201 / roles {user_id}->201，成员侧队列 500、
    # collaboration.db 被灌入整套表。
    def _ensure_colliding_account(self) -> int:
        with closing(sqlite3.connect(str(self.auth_mod._users_db()))) as conn, conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (username, password_hash, created_at) VALUES (?,?,?)",
                ("collaboration", self.auth_mod.hash_password("Legacy-Pass-2026"), "2026-01-01T00:00:00"))
            return conn.execute("SELECT id FROM users WHERE username='collaboration'").fetchone()[0]

    def test_invite_rejects_existing_reserved_name_account(self):
        task = self._new_task()
        self._ensure_colliding_account()
        r = self.client.post(f"/api/tasks/{task}/collaboration/invite",
                             json={"username": "collaboration", "role": "coder"},
                             headers=self.auth())
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("冲突", r.json()["detail"]["message"])

    def test_grant_role_by_user_id_rejects_reserved_name_account(self):
        from custom_backend.main import DATA_DIR
        task = self._new_task()
        colliding_id = self._ensure_colliding_account()
        r = self.client.post(f"/api/tasks/{task}/collaboration/roles",
                             json={"user_id": colliding_id, "role": "coder"},
                             headers=self.auth())
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["detail"]["error_code"], "collaboration_reserved_name")
        # 未落任何授权行
        info = tasks_mod.find_task(task, DATA_DIR)
        grants = task_collaboration.list_role_grants(
            task_collaboration.shared_store_path(info.dir),
            actor_user_id=self.users[0]["user_id"], task_id=task)
        self.assertEqual([g for g in grants if g["user_id"] == colliding_id], [])

    # 【collaboration.db 损坏】#30（desktop_corrupt.py 复现）：desktop 模式列表
    # 不剔除 + 管理操作给明确修库指引（修复前 200-列表剔除 / 503"稍后重试"）。
    def test_desktop_corrupt_store_keeps_task_visible_with_repair_guidance(self):
        from custom_backend.main import COLLABORATION_STORE_CORRUPT_MESSAGE, DATA_DIR
        task = self._new_task()
        info = tasks_mod.find_task(task, DATA_DIR)
        task_collaboration.shared_store_path(info.dir).write_bytes(b"corrupted store bytes")
        with self._mode("desktop"):
            listing = self.client.get("/api/tasks", headers=self.auth())
            self.assertEqual(listing.status_code, 200, listing.text)
            self.assertIn(task, [t["task_id"] for t in listing.json()["tasks"]],
                          "desktop 模式损坏库不得从列表静默剔除任务")
            r = self.client.put(f"/api/tasks/{task}", json={"name": "renamed"},
                                headers=self.auth())
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(r.json()["detail"], COLLABORATION_STORE_CORRUPT_MESSAGE)
            self.assertIn("collaboration.db", r.json()["detail"])
        # server 模式维持 fail-closed（503 + 列表不可见，既有口径不回归）
        r = self.client.put(f"/api/tasks/{task}", json={"name": "renamed"}, headers=self.auth())
        self.assertEqual(r.status_code, 503, r.text)
        self.assertNotIn(task, self._task_ids())

    # 【legacy 无管理路径】#31：server 模式 legacy 409 文案给出可行操作
    # （desktop 模式删除/重建），不再指向不存在的"属主迁移"流程。
    def test_server_legacy_409_message_gives_feasible_actions(self):
        from custom_backend.main import LEGACY_OWNER_UNSET_MESSAGE
        task = self._legacy_task()
        r = self.client.delete(f"/api/tasks/{task}", headers=self.auth())
        self.assertEqual(r.status_code, 409, r.text)
        detail = r.json()["detail"]
        self.assertEqual(detail["error_code"], "collaboration_owner_unset")
        self.assertEqual(detail["message"], LEGACY_OWNER_UNSET_MESSAGE)
        self.assertIn("单机模式", detail["message"])

    # 【schema_guard legacy】#33：desktop legacy 任务方案编辑恢复 bump
    # （修复前 version 不再随新增维度推进）。
    def test_legacy_task_scheme_edit_bumps_workflow_version(self):
        task = self._legacy_task()
        with self._mode("desktop"):
            r = self.client.post(f"/api/tasks/{task}/coding/dimensions",
                                 json={"name": "字段A", "dtype": "text"},
                                 headers=self.auth())
            self.assertEqual(r.status_code, 201, r.text)
            v1 = self.client.get(f"/api/tasks/{task}/workflow-settings",
                                 headers=self.auth()).json()["version"]
            r = self.client.post(f"/api/tasks/{task}/coding/dimensions",
                                 json={"name": "字段B", "dtype": "text"},
                                 headers=self.auth())
            self.assertEqual(r.status_code, 201, r.text)
            v2 = self.client.get(f"/api/tasks/{task}/workflow-settings",
                                 headers=self.auth()).json()["version"]
            self.assertEqual(v2, v1 + 1, "legacy 任务方案编辑必须推进 workflow version")

    # 【undo task_id 口径】#22：别名路径保存 → 别名路径撤销可用（修复前预读
    # None -> 永久 409 'There is no saved operation to undo'）。
    def test_alias_restore_pre_read_uses_canonical_id(self):
        task = self._new_task()
        dim = self._seed_article_and_dim(task)
        r = self.client.post(f"/api/tasks/{task}/coding/values-batch",
                             json={"zotero_key": "K1", "values": {str(dim): "v1"}},
                             headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        entry_id = self.client.get(f"/api/tasks/{task}/undo/coding",
                                   headers=self.auth()).json()["latest"]["entry_id"]
        alias = self._alias(task)
        r = self.client.post(f"/api/tasks/{alias}/undo/coding",
                             json={"entry_id": entry_id}, headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["undone"])

    # 【undo task_id 口径】#22：别名路径上传图片 → 别名路径保存可附着（修复前
    # save_form 抛 'Staged evidence is unavailable or belongs to another field.'）。
    def test_alias_upload_then_attach_evidence(self):
        from custom_backend.main import DATA_DIR
        task = self._new_task()
        dim = self._seed_article_and_dim(task)
        alias = self._alias(task)
        r = self.client.post(f"/api/tasks/{alias}/coding/evidence/uploads",
                             data={"zotero_key": "K1", "dim_id": str(dim)},
                             files={"file": ("a.png", _png_bytes(), "image/png")},
                             headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        upload_id = r.json()["upload_id"]
        r = self.client.post(f"/api/tasks/{alias}/coding/values-batch",
                             json={"zotero_key": "K1",
                                   "evidence": {str(dim): {"keep_image_ids": [], "upload_ids": [upload_id]}}},
                             headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        info = tasks_mod.find_task(task, DATA_DIR)
        db = info.dir / f"{tasks_mod.slugify_screener(self.users[0]['username'])}.db"
        with closing(_connect(db)) as conn:
            images = conn.execute('SELECT COUNT(*) FROM coding_note_images').fetchone()[0]
            staged = conn.execute('SELECT task_id,attached FROM workflow_evidence_uploads').fetchone()
        self.assertEqual(images, 1)
        self.assertEqual(staged[0], task, '上传行 task_id 必须落规范 id')
        self.assertEqual(staged[1], 1)

    # 【MISSING_EVIDENCE_FILE】#25 端点级：待恢复图片所在条目（第二次保存产生，
    # before 含图片路径）在文件删除后 status 不再标可撤销（前端撤销按钮据
    # latest 禁用，409 循环点击收口）。
    def test_status_hides_entry_when_evidence_file_missing(self):
        from custom_backend.main import DATA_DIR
        task = self._new_task()
        dim = self._seed_article_and_dim(task)
        r = self.client.post(f"/api/tasks/{task}/coding/evidence/uploads",
                             data={"zotero_key": "K1", "dim_id": str(dim)},
                             files={"file": ("a.png", _png_bytes((0, 255, 0)), "image/png")},
                             headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        upload_id = r.json()["upload_id"]
        r = self.client.post(f"/api/tasks/{task}/coding/values-batch",
                             json={"zotero_key": "K1", "notes": {str(dim): "n"},
                                   "evidence": {str(dim): {"keep_image_ids": [], "upload_ids": [upload_id]}}},
                             headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        # 第二次保存：其 before 快照携带已附着的图片路径（撤销需恢复它们）
        r = self.client.post(f"/api/tasks/{task}/coding/values-batch",
                             json={"zotero_key": "K1", "notes": {str(dim): "n2"},
                                   "evidence": {str(dim): {"keep_image_ids": [], "upload_ids": []}}},
                             headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        entry_id = self.client.get(f"/api/tasks/{task}/undo/coding",
                                   headers=self.auth()).json()["latest"]["entry_id"]
        info = tasks_mod.find_task(task, DATA_DIR)
        db = info.dir / f"{tasks_mod.slugify_screener(self.users[0]['username'])}.db"
        for path in coding_evidence.assets_dir(db).glob('*.png'):
            path.unlink()
        payload = self.client.get(f"/api/tasks/{task}/undo/coding", headers=self.auth()).json()
        self.assertIsNone(payload["latest"], '文件缺失后前端不再可点（latest 置空）')
        self.assertIs(payload["undo_available"], False)
        r = self.client.post(f"/api/tasks/{task}/undo/coding",
                             json={"entry_id": entry_id}, headers=self.auth())
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["detail"], workspace_undo.MISSING_EVIDENCE_FILE)

    # 【task_id 分裂】#35：roles/invite/team-progress/workflow-settings 别名路径
    # 与规范 id 同判定（修复前 500/409/折叠 legacy）。
    def test_alias_collaboration_paths_use_canonical_id(self):
        task = self._new_task()
        alias = self._alias(task)
        # 规范 id 邀请（建成员），别名路径 GET roles（修复前 500）
        r = self.client.post(f"/api/tasks/{task}/collaboration/invite",
                             json={"username": self.users[1]["username"], "role": "coder"},
                             headers=self.auth())
        self.assertEqual(r.status_code, 201, r.text)
        r = self.client.get(f"/api/tasks/{alias}/collaboration/roles", headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["roles"]), 1)
        # 别名路径邀请（修复前 grant_role 收原始 id -> 500）
        r = self.client.post(f"/api/tasks/{alias}/collaboration/invite",
                             json={"username": self.users[1]["username"], "role": "reconciler"},
                             headers=self.auth())
        self.assertEqual(r.status_code, 201, r.text)
        # team-progress 别名（修复前 get_owner 折叠 legacy -> 409"属主迁移"）
        r = self.client.get(f"/api/tasks/{alias}/team-progress", headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        # workflow-settings 别名：属主判定不得折叠 legacy（修复前 can_manage=False）
        r = self.client.get(f"/api/tasks/{alias}/workflow-settings", headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["can_manage"], '别名路径属主判定不得折叠 legacy')

    # 【task_id 分裂】#22 锁互斥前置：context/锁键统一规范 id（修复前 'T.' 与
    # 'T' 分键，别名写与规范撤销不互斥）。
    def test_alias_uses_canonical_lock_key(self):
        task = self._new_task()
        alias = self._alias(task)
        runtime = self.client.app.state.review_workflow
        ctx = runtime.context(alias, self.users[0])
        self.assertEqual(ctx.info.task_id, task)
        self.assertIn(task, runtime._locks)
        self.assertNotIn(alias, runtime._locks, '别名路径必须复用规范 id 的任务锁')


if __name__ == "__main__":
    unittest.main()
