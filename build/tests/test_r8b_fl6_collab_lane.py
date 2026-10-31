"""r8 第 2 轮 FL6（撤销协作组）钉子测试 · 协作权限泳道（#29-#32/#34-#37）。

对应 out/external_reviews/r6_verify_undocollab.md 的动态复审口径，每条用例
在修复前（a080fde）均为红：
- #29 升级前已存在的撞名授权成员收敛为 409 撞名改名指引（修复前成员侧
  workflow-settings 200 且 collaboration.db 被灌入整套表、work-queue/articles
  500 裸 ValueError）；
- #30 损坏协作库：列表仅对"可能的参与者"呈现并标 degraded，并提供受支持的
  修库动作（POST .../collaboration/repair 原地隔离损坏文件；修复前任意账号
  可见、degraded=false、PUT/DELETE/articles 全 409、无修库端点）；
- #31 legacy 任务属主修复入口（POST .../collaboration/rebind）：历史参与者
  重新登记属主后恢复 server 模式管理权（修复前无端点、PUT/DELETE 恒 409、
  409 文案指向"单机删除/重建"）；
- #32 撤销回收 screeners 前先判断"全无 grant"（=G-03：修复前 coder+
  reconciler 双角色成员撤销其一即被整体剔除）；
- #34 create_task 撞名 409 带 message（errOf 走 T()）且登录 warning 注册
  十语言词典（修复前无 message → 前端 JSON.stringify 直出、toast 中文硬编码）；
- #35 list_role_grants 不确定故障映射 OwnerUnavailableError/
  CollaborationStoreCorruptError（修复前裸 sqlite3.DatabaseError）；
- #36 authorize_action 区分确定性否定（False）与不确定故障（抛错；=MM-11：
  修复前损坏库/锁窗统一折叠 return False → 403）；
- #37 全无 grant 后隔离成员个人库文件名，被撤成员不再以乱理 slug 残留在
  筛选员名单（修复前 member@example.test -> memberexample.test 残留呈现）。
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

fixture = tempfile.TemporaryDirectory(prefix='rf_r8b_fl6_collab_')
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

from custom_backend import auth as auth_mod
from custom_backend import security as backend_security
from custom_backend.main import app, DATA_DIR
from coscreen import task_collaboration, tasks as tasks_mod


def _locale_rows():
    text = (Path(__file__).resolve().parents[2] / 'custom_frontend' / 'interface_locale.js') \
        .read_text(encoding='utf-8')
    rows = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith('],'):
            strings = re.findall(r'"((?:[^"\\]|\\.)*)"', stripped)
            if strings:
                rows.append([s.replace('\\"', '"').replace('\\\\', '\\') for s in strings])
    return rows


class CollabLaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        for i in range(3):
            user = auth_mod.create_user(f"r8fl6c{i}@example.test", "R8-Fl6-Collab-2026")
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

    @contextmanager
    def _limiter_budget(self):
        events = backend_security.login_limiter._events
        saved = dict(events)
        events.clear()
        try:
            yield
        finally:
            events.clear()
            events.update(saved)

    def _new_task(self, actor=0) -> tuple[str, Path]:
        r = self.client.post("/api/tasks", json={"name": "r8fl6c-" + uuid.uuid4().hex[:8]},
                             headers=self.auth(actor))
        self.assertEqual(r.status_code, 201, r.text)
        task = r.json()["task_id"]
        return task, task_collaboration.shared_store_path(tasks_mod.find_task(task, DATA_DIR).dir)

    def _ensure_colliding_account(self) -> dict:
        with closing(sqlite3.connect(str(auth_mod._users_db()))) as conn, conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (username, password_hash, created_at) VALUES (?,?,?)",
                ("collaboration", auth_mod.hash_password("Legacy-Pass-2026"), "2026-01-01T00:00:00"))
            uid = conn.execute("SELECT id FROM users WHERE username='collaboration'").fetchone()[0]
        return {"user_id": uid, "username": "collaboration",
                "token": auth_mod.create_access_token(uid, "collaboration")}

    # ------------------------------------------------------------------
    # #29：存量撞名授权成员 -> 409 撞名指引（不再 200 灌表 / 500 裸异常）
    # ------------------------------------------------------------------
    def test_r8b29_legacy_colliding_grant_converges_to_409(self):
        task, store = self._new_task()
        member = self._ensure_colliding_account()
        info = tasks_mod.find_task(task, DATA_DIR)
        # 模拟升级前的撞名授权（直调 grant_role + register_screener，同复审探针）。
        task_collaboration.grant_role(store, actor_user_id=self.users[0]["user_id"],
                                      user_id=member["user_id"], role="coder", task_id=task)
        tasks_mod.register_screener(task, member["username"], DATA_DIR)
        before_tables = self._table_count(store)
        member_auth = {"Authorization": "Bearer " + member["token"]}
        r = self.client.get(f"/api/tasks/{task}/workflow-settings", headers=member_auth)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["detail"]["error_code"], "collaboration_reserved_name")
        r = self.client.get(f"/api/tasks/{task}/work-queue/screen", headers=member_auth)
        self.assertEqual(r.status_code, 409, '撞名成员队列不得 500（#29）')
        r = self.client.get(f"/api/tasks/{task}/articles", headers=member_auth)
        self.assertEqual(r.status_code, 409, '撞名成员文献列表不得 500（#29）')
        self.assertEqual(self._table_count(store), before_tables,
                         '撞名成员请求不得把筛选表灌进协作库（#29）')

    @staticmethod
    def _table_count(db_path: Path) -> int:
        with closing(sqlite3.connect(db_path, timeout=5)) as conn:
            return conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]

    # ------------------------------------------------------------------
    # #30：损坏库 degraded 标记 + 参与者可见性 + 受支持修库动作
    # ------------------------------------------------------------------
    def test_r8b30_corrupt_store_degraded_visible_to_participant_only_and_repairable(self):
        with self._mode("desktop"):
            task, store = self._new_task()
            store.write_bytes(b'not a sqlite database at all')
            # 列表：属主可见且 degraded=true；无关账号不可见（修复前任意可见、
            # degraded=false）。
            r = self.client.get("/api/tasks", headers=self.auth(0))
            self.assertEqual(r.status_code, 200, r.text)
            row = next(t for t in r.json()["tasks"] if t["task_id"] == task)
            self.assertTrue(row["degraded"], '损坏协作库任务必须标 degraded（#30）')
            r = self.client.get("/api/tasks", headers=self.auth(2))
            self.assertNotIn(task, [t["task_id"] for t in r.json()["tasks"]],
                             '无关账号不得看到损坏协作库任务（#30）')
            # 受支持的修库动作：隔离损坏文件（备份保留），任务恢复可管理。
            r = self.client.post(f"/api/tasks/{task}/collaboration/repair", headers=self.auth(0))
            self.assertEqual(r.status_code, 200, r.text)
            backup = tasks_mod.find_task(task, DATA_DIR).dir / r.json()["backup"]
            self.assertTrue(backup.is_file(), '修库必须保留损坏库备份（#30）')
            self.assertFalse(store.exists())
            r = self.client.put(f"/api/tasks/{task}", json={"name": "repaired-name"},
                                headers=self.auth(0))
            self.assertEqual(r.status_code, 200, r.text)
            # 修库后任务未启用协作（legacy）：再次修库 409（无可修复对象）。
            r = self.client.post(f"/api/tasks/{task}/collaboration/repair", headers=self.auth(0))
            self.assertEqual(r.status_code, 409, r.text)

    def test_r8b30_server_mode_repair_and_rebind_reachable(self):
        """r6 第二轮复查：server 模式损坏库下 repair/rebind 必须可达（修复前
        require_user 的 fail-closed 把所有任务级请求折成 503，恢复端点自身
        也不可达，任务永久不可管理）。"""
        task, store = self._new_task()
        store.write_bytes(b'not a sqlite database at all')
        # 恢复动作对参与证据持有人开放。
        r = self.client.post(f"/api/tasks/{task}/collaboration/repair", headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(store.exists())
        # 修库后恢复为 legacy -> server 模式管理仍 409，但 rebind 可恢复管理权。
        r = self.client.delete(f"/api/tasks/{task}", headers=self.auth(0))
        self.assertEqual(r.status_code, 409, r.text)
        r = self.client.post(f"/api/tasks/{task}/collaboration/rebind",
                             json={"confirm": True}, headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.delete(f"/api/tasks/{task}", headers=self.auth(0))
        self.assertEqual(r.status_code, 200, 'server 模式修复链必须恢复管理权（#30/#31）')
        # 非参与者的恢复请求仍被拒（server 模式不因恢复端点放宽）。
        task2, store2 = self._new_task()
        store2.write_bytes(b'not a sqlite database at all')
        r = self.client.post(f"/api/tasks/{task2}/collaboration/repair", headers=self.auth(2))
        # 第 3 轮：非参与者得到端点自己的 403（503 会让该分支永远不可达
        # 并把“不是参与者”误报成“稍后重试”）。
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn('仅任务参与者', r.json()['detail'])
        self.assertTrue(store2.exists(), '非参与者不得触发修库（#30）')

    # ------------------------------------------------------------------
    # #31：legacy 任务属主修复入口（rebind）
    # ------------------------------------------------------------------
    def test_r8b31_rebind_restores_management_for_past_participant(self):
        task, store = self._new_task()
        store.unlink()  # 模拟创建于协作机制之前的 legacy 任务
        # 修复前：server 模式管理操作恒 409 collaboration_owner_unset。
        r = self.client.delete(f"/api/tasks/{task}", headers=self.auth(0))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["detail"]["error_code"], "collaboration_owner_unset")
        self.assertIn("重新登记", r.json()["detail"]["message"],
                      '409 文案必须指向受支持的属主重新登记入口（#31 文档对齐）')
        # 非参与者不得认领（legacy 可见性闸门 409 owner_unset 或本端点 403，
        # 关键是绝不 200）。
        r = self.client.post(f"/api/tasks/{task}/collaboration/rebind",
                             json={"confirm": True}, headers=self.auth(2))
        self.assertIn(r.status_code, (403, 409), r.text)
        # 未显式确认 -> 400；确认后历史参与者（创建者在 task.json screeners 内）
        # 重新登记属主并恢复管理权。
        r = self.client.post(f"/api/tasks/{task}/collaboration/rebind",
                             json={"confirm": False}, headers=self.auth(0))
        self.assertEqual(r.status_code, 400, r.text)
        r = self.client.post(f"/api/tasks/{task}/collaboration/rebind",
                             json={"confirm": True}, headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["owner"]["owner_user_id"], self.users[0]["user_id"])
        r = self.client.delete(f"/api/tasks/{task}", headers=self.auth(0))
        self.assertEqual(r.status_code, 200, 'rebind 后原属主必须恢复管理权（#31）')
        # 已登记属主的任务不得重复认领。
        task2, store2 = self._new_task()
        r = self.client.post(f"/api/tasks/{task2}/collaboration/rebind",
                             json={"confirm": True}, headers=self.auth(0))
        self.assertEqual(r.status_code, 409, r.text)

    # ------------------------------------------------------------------
    # #32 + #37：撤销回收先判断全无 grant；全无后隔离成员个人库
    # ------------------------------------------------------------------
    def test_r8b32_37_revoke_keeps_dual_role_and_quarantines_slug_db(self):
        task, store = self._new_task()
        for role in ("coder", "reconciler"):
            r = self.client.post(f"/api/tasks/{task}/collaboration/invite",
                                 json={"username": self.users[1]["username"], "role": role},
                                 headers=self.auth(0))
            self.assertEqual(r.status_code, 201, r.text)
        # 成员访问工作队列（生成个人库，#37 的兜底呈现来源）。
        r = self.client.get(f"/api/tasks/{task}/work-queue/screen", headers=self.auth(1))
        self.assertEqual(r.status_code, 200, r.text)

        def screeners():
            r = self.client.get("/api/tasks", headers=self.auth(0))
            return next(t for t in r.json()["tasks"] if t["task_id"] == task)["screeners"]

        member_name = self.users[1]["username"]
        self.assertIn(member_name, screeners())
        # 撤销 coder：reconciler 仍在 -> 不得回收 screeners 登记（G-03/#32）。
        r = self.client.delete(f"/api/tasks/{task}/collaboration/roles/"
                               f"{self.users[1]['user_id']}/coder", headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn(member_name, screeners(),
                      '成员仍有 reconciler 角色时不得从筛选员名单剔除（#32）')
        r = self.client.get(f"/api/tasks/{task}/collaboration/roles", headers=self.auth(0))
        self.assertEqual([g["role"] for g in r.json()["roles"]], ["reconciler"])
        # 撤销最后一个角色：回收登记并隔离个人库文件名（#37：不得以乱理 slug
        # 兜底呈现，如 r8fl6c1@example.test -> r8fl6c1example.test）。
        r = self.client.delete(f"/api/tasks/{task}/collaboration/roles/"
                               f"{self.users[1]['user_id']}/reconciler", headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        after = screeners()
        self.assertNotIn(member_name, after)
        member_slug = tasks_mod.slugify_screener(member_name)
        self.assertNotIn(member_slug, after, '被撤成员不得以乱理 slug 残留（#37）')
        info = tasks_mod.find_task(task, DATA_DIR)
        self.assertFalse((info.dir / f"{member_slug}.db").exists(),
                         '被撤成员个人库文件名必须被隔离（#37）')
        self.assertTrue(any(p.name.startswith(f"{member_slug}.db.revoked-")
                            for p in info.dir.glob(f"{member_slug}.db.revoked-*")),
                        '隔离备份必须保留（#37 不删数据）')
        # 被撤成员失去访问权。
        r = self.client.get(f"/api/tasks/{task}/work-queue/screen", headers=self.auth(1))
        self.assertEqual(r.status_code, 403, r.text)

    # ------------------------------------------------------------------
    def test_r8b31_ownership_change_is_visible_in_frontend(self):
        """第 3 轮：rebind 广播的事件必须有前端消费面，且用户可见文案要与
        rebind 端点一致（修复前事件无监听、ai_suggest 仍宣称“暂无自助认领入口”）。"""
        root = Path(__file__).resolve().parents[2]
        html = (root / 'custom_frontend' / 'index.html').read_text(encoding='utf-8')
        self.assertIn("addEventListener('task-ownership-changed'", html,
                      '#31：前端必须监听属主变更事件（否则“可观察”只在服务端日志成立）')
        self.assertIn('task-ownership-changed', html)
        ai = (root / 'custom_frontend' / 'ai_suggest.js').read_text(encoding='utf-8')
        self.assertNotIn('暂无自助认领入口', ai,
                         '#31：不得再宣称无自助认领入口（rebind 端点已落地）')
        self.assertIn('重新登记负责人', ai, '#31：文案必须指向真实的属主重新登记入口')

    def test_r8b26_missing_evidence_reason_registered_ten_languages(self):
        """第 3 轮：#26 新增的 reason 文案必须进十语言词典（与 saved_state_changed
        同级先例一致；修复前只在 workflow_controls.js 里有英文键）。"""
        root = Path(__file__).resolve().parents[2]
        message = ("The saved evidence image is no longer in the data directory, "
                   "so this save cannot be undone.")
        controls = (root / 'custom_frontend' / 'workflow_controls.js').read_text(encoding='utf-8')
        self.assertIn(message, controls, 'reason 标签必须在 reasonLabels 内')
        found = None
        for name in ('interface_locale.js', 'workflow_locale.js', 'analysis_locale.js',
                     'research_trace_locale.js'):
            text = (root / 'custom_frontend' / name).read_text(encoding='utf-8')
            for line in text.splitlines():
                if not line.strip().startswith('['):
                    continue
                try:
                    strings = json.loads(line.strip().rstrip(','))
                except ValueError:
                    continue
                if isinstance(strings, list) and message in strings:
                    found = (name, strings)
                    break
            if found:
                break
        self.assertIsNotNone(found, 'missing_evidence_file 文案必须注册进十语言词典（#26）')
        self.assertEqual(len(found[1]), 10,
                         f'{found[0]} 中该词条必须恰为十语言，实为 {len(found[1])}')

    # #34：create_task 撞名 409 带 message；登录 warning 注册十语言
    # ------------------------------------------------------------------
    def test_r8b34_reserved_name_payload_message_and_login_warning_registered(self):
        from custom_backend.main import COLLABORATION_RESERVED_NAME_MESSAGE
        member = self._ensure_colliding_account()
        member_auth = {"Authorization": "Bearer " + member["token"]}
        r = self.client.post("/api/tasks", json={"name": "r8fl6c-collide"},
                             headers=member_auth)
        self.assertEqual(r.status_code, 409, r.text)
        detail = r.json()["detail"]
        self.assertEqual(detail.get("message"), COLLABORATION_RESERVED_NAME_MESSAGE,
                         'create_task 撞名 409 必须带 message（errOf 经 T() 命中，#34）')
        rows = _locale_rows()
        registered = {row[0]: row for row in rows}
        self.assertIn(COLLABORATION_RESERVED_NAME_MESSAGE, registered)
        self.assertEqual(len(registered[COLLABORATION_RESERVED_NAME_MESSAGE]), 10)
        # 登录 warning：响应文案与词典词条逐字一致且十语言注册（toast 走 T()）。
        with self._limiter_budget():
            r = self.client.post("/api/auth/login",
                                 json={"username": "collaboration", "password": "Legacy-Pass-2026"})
        self.assertEqual(r.status_code, 200, r.text)
        warnings = r.json()["warnings"]
        self.assertTrue(warnings)
        self.assertIn(warnings[0], registered, '登录 warning 必须注册词典（#34）')
        self.assertEqual(len(registered[warnings[0]]), 10)

    # ------------------------------------------------------------------
    # #35 / #36：task_collaboration 异常口径（函数级，同复审探针形态）
    # ------------------------------------------------------------------
    def _store(self, owner=1, task_id='r8b-fl6-store') -> Path:
        root = Path(tempfile.mkdtemp(prefix='rf_r8b_fl6_store_'))
        self.addCleanup(lambda: __import__('shutil').rmtree(root, ignore_errors=True))
        store = task_collaboration.shared_store_path(root)
        task_collaboration.initialize_task_store(
            root, task_id=task_id, owner_user_id=owner, owner_username='owner@example.test')
        task_collaboration.grant_role(store, actor_user_id=owner, user_id=99,
                                      role='coder', task_id=task_id)
        return store

    def test_r8b35_list_role_grants_maps_uncertain_failures(self):
        store = self._store()
        # 确定性口径不回归：属主可列、非属主 PermissionError、缺库确定否定。
        grants = task_collaboration.list_role_grants(store, actor_user_id=1, task_id='r8b-fl6-store')
        self.assertEqual([g['role'] for g in grants], ['coder'])
        with self.assertRaises(PermissionError):
            task_collaboration.list_role_grants(store, actor_user_id=2, task_id='r8b-fl6-store')
        with self.assertRaises(PermissionError):
            task_collaboration.list_role_grants(store.with_name('missing.db'),
                                                actor_user_id=1, task_id='r8b-fl6-store')
        # 永久损坏：CollaborationStoreCorruptError（修复前裸 sqlite3.DatabaseError）。
        corrupt = self._store()
        corrupt.write_bytes(b'not a sqlite database')
        with self.assertRaises(task_collaboration.CollaborationStoreCorruptError):
            task_collaboration.list_role_grants(corrupt, actor_user_id=1, task_id='r8b-fl6-store')

    def test_r8b36_authorize_action_distinguishes_denial_from_failure(self):
        store = self._store()
        # 确定性否定保持 False：属主/有角色 True；缺库=legacy False；无角色 False。
        self.assertTrue(task_collaboration.authorize_action(
            store, 1, 'coding:submit', task_id='r8b-fl6-store'))
        self.assertTrue(task_collaboration.authorize_action(
            store, 99, 'coding:submit', task_id='r8b-fl6-store'))
        self.assertFalse(task_collaboration.authorize_action(
            store, 77, 'coding:submit', task_id='r8b-fl6-store'))
        self.assertFalse(task_collaboration.authorize_action(
            store.with_name('missing.db'), 99, 'coding:submit', task_id='r8b-fl6-store'))
        # 不确定故障必须抛错（修复前折叠 return False -> 403）：
        corrupt = self._store()
        corrupt.write_bytes(b'not a sqlite database')
        with self.assertRaises(task_collaboration.CollaborationStoreCorruptError):
            task_collaboration.authorize_action(corrupt, 99, 'coding:submit', task_id='r8b-fl6-store')
        self.assertTrue(task_collaboration.is_participant(
            store, 99, task_id='r8b-fl6-store'))


if __name__ == '__main__':
    unittest.main()
