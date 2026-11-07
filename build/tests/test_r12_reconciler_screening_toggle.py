"""R2（2026-11-07 用户裁定语义）：裁决员「参与筛选」开关。

逐条落钉（红钉先行，先红后绿）：
1) 反向钉：仲裁权保底——无论筛选开关怎么关，reconciler 对全部 consensus:*
   动作（含 consensus:write）的授权不受影响，且不存在关闭入口。
2) 迁移钉：升级前已存在的 reconciler 授权 → 一次性回填后默认「开」。
3) 新授权默认「关」：grant_role 之后立即查询即为关（不是读时猜新旧）。
4) 仅属主可改开关：成员/他人 PUT -> 403；属主 -> 200；存储层非属主
   PermissionError。
5) 关时 POST 初筛决策 -> 403 结构化体（error_code=screening_not_enabled、
   message_key=team.screening_not_enabled）；开启后恢复可写；读取类端点不拦。
6) 复筛（stage2）同口径。
7) coder 成员不受影响。
8) 属主不受影响（即便自身也持 reconciler grant 且开关为关）。
9) legacy 无协作库任务零回归（desktop 历史口径：初筛/进度/读取照常）。
10) 前端契约：成员卡开关、mutateTeamMember 并发防护、十语言词条齐全。
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 与 R1 同法：先设环境再导入 main（多测试模块共享首个导入的环境）。
fixture = tempfile.TemporaryDirectory(prefix="rf_r12_screening_toggle_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

from custom_backend import auth as auth_mod
from custom_backend.main import app, DATA_DIR
from coscreen import db as db_mod
from coscreen import task_collaboration
from coscreen import tasks as tasks_mod
from coscreen.models import Article

KEYS = ("K1", "K2", "K3")


def _template_db_bytes() -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "user.db"
        db_mod.init_db(path)
        db_mod.upsert_articles(
            path, [Article(zotero_key=key, title=f"Study {key}") for key in KEYS], [])
        return path.read_bytes()


_TEMPLATE_DB_BYTES = _template_db_bytes()


class ReconcilerScreeningToggleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        for i in range(4):
            user = auth_mod.create_user(f"r12scr{i}@example.test", "R12-Toggle-Test-2026")
            cls.users.append({
                "user_id": user["id"],
                "username": user["username"],
                "token": auth_mod.create_access_token(user["id"], user["username"]),
            })

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

    def setUp(self):
        self.task = self.client.post(
            "/api/tasks", json={"name": self._testMethodName},
            headers=self.auth()).json()["task_id"]
        self.info = tasks_mod.find_task(self.task, DATA_DIR)
        # 成员 1 = reconciler，成员 2 = coder。
        for index, role in ((1, "reconciler"), (2, "coder")):
            response = self.client.post(
                f"/api/tasks/{self.task}/collaboration/invite",
                json={"username": self.users[index]["username"], "role": role},
                headers=self.auth())
            self.assertEqual(response.status_code, 201, response.text)
        self.ctx = app.state.review_workflow.context(self.task, self.users[0])
        self.store = task_collaboration.shared_store_path(self.info.dir)
        for index in (0, 1, 2):
            db = self._member_db(index)
            if index == 0:
                db_mod.upsert_articles(
                    db, [Article(zotero_key=key, title=f"Study {key}") for key in KEYS], [])
            else:
                db.write_bytes(_TEMPLATE_DB_BYTES)
        self.owner_id = self.users[0]["user_id"]
        self.reconciler_id = self.users[1]["user_id"]
        self.coder_id = self.users[2]["user_id"]

    # ---- 夹具 ---------------------------------------------------------

    def _member_db(self, index: int) -> Path:
        slug = tasks_mod.slugify_screener(self.users[index]["username"])
        return self.info.dir / f"{slug}.db"

    def _decide(self, index: int, *, stage: str = "stage1", key: str = "K1",
                decision: str = "include"):
        suffix = "/stage2/decisions" if stage == "stage2" else "/decisions"
        return self.client.post(
            f"/api/tasks/{self.task}{suffix}",
            json={"zotero_key": key, "decision": decision}, headers=self.auth(index))

    def _set_screening(self, target_index: int, enabled: bool, *, actor: int = 0):
        return self.client.put(
            f"/api/tasks/{self.task}/collaboration/members/"
            f"{self.users[target_index]['user_id']}/screening",
            json={"screening_enabled": enabled}, headers=self.auth(actor))

    def _team_progress(self, actor: int = 0):
        return self.client.get(f"/api/tasks/{self.task}/team-progress",
                               headers=self.auth(actor))

    def _drop_upgrade_tables(self) -> None:
        """把已升级库还原成「升级前」形态：仅有旧表与旧授权。"""
        with closing(sqlite3.connect(self.store)) as conn, conn:
            conn.execute("DROP TABLE IF EXISTS collaboration_member_settings")
            conn.execute("DROP TABLE IF EXISTS collaboration_settings")

    # ---- 1. 反向钉：仲裁权保底不可关 ----------------------------------

    def test_consensus_privileges_never_disabled_by_screening_toggle(self):
        response = self._set_screening(1, False)
        self.assertEqual(response.status_code, 200, response.text)
        # 存储层：开关为关，但全部 consensus:* 授权保持为真。
        self.assertFalse(task_collaboration.screening_allowed(
            self.store, self.reconciler_id, task_id=self.task))
        for action in ("consensus:write", "consensus:read", "consensus:freeze",
                       "coding:read_all"):
            self.assertTrue(
                task_collaboration.authorize_action(
                    self.store, self.reconciler_id, action, task_id=self.task),
                action)
        # 协作角色仍完整可见，开关不改变角色/仲裁授权面。
        roles = self.client.get(
            f"/api/tasks/{self.task}/collaboration/roles", headers=self.auth()).json()
        self.assertIn(
            {"user_id": self.reconciler_id, "role": "reconciler"},
            [{"user_id": grant["user_id"], "role": grant["role"]} for grant in roles["roles"]])

    # ---- 2. 迁移钉：升级前已有 reconciler -> 默认开 --------------------

    def test_existing_reconciler_is_backfilled_enabled(self):
        self._drop_upgrade_tables()
        # 未迁移存储按「升级前」口径：既有授权默认开（不是读时猜新旧）。
        self.assertTrue(task_collaboration.screening_allowed(
            self.store, self.reconciler_id, task_id=self.task))
        # 属主打开团队进度即触发一次性回填：写 marker + 存量 reconciler -> 开。
        progress = self._team_progress()
        self.assertEqual(progress.status_code, 200, progress.text)
        members = {member["user_id"]: member for member in progress.json()["members"]}
        self.assertTrue(members[self.reconciler_id]["screening_enabled"])
        with closing(sqlite3.connect(self.store)) as conn:
            marker = conn.execute(
                "SELECT value FROM collaboration_settings WHERE key=?",
                (task_collaboration.SCREENING_BACKFILL_KEY,)).fetchone()
            row = conn.execute(
                "SELECT screening_enabled FROM collaboration_member_settings WHERE user_id=?",
                (self.reconciler_id,)).fetchone()
        self.assertIsNotNone(marker, "回填完成时刻未落库")
        self.assertEqual(row[0], 1)
        # 迁移后既有裁决员照常写初筛。
        self.assertEqual(self._decide(1).status_code, 200)

    # ---- 3. 新授权默认关 ----------------------------------------------

    def test_new_reconciler_grant_defaults_disabled(self):
        self.assertFalse(task_collaboration.screening_allowed(
            self.store, self.reconciler_id, task_id=self.task))
        progress = self._team_progress().json()
        member = next(m for m in progress["members"] if m["user_id"] == self.reconciler_id)
        self.assertFalse(member["screening_enabled"])
        # 属主 / coder 的 screening_enabled 恒为真（不受该开关约束）。
        owner = next(m for m in progress["members"] if m["user_id"] == self.owner_id)
        coder = next(m for m in progress["members"] if m["user_id"] == self.coder_id)
        self.assertTrue(owner["screening_enabled"])
        self.assertTrue(coder["screening_enabled"])
        self.assertEqual(self._decide(1).status_code, 403)

    # ---- 4. 仅属主可改 -------------------------------------------------

    def test_only_owner_can_toggle_screening(self):
        for actor in (1, 2, 3):
            response = self._set_screening(1, True, actor=actor)
            self.assertEqual(response.status_code, 403, (actor, response.text))
        with self.assertRaises(PermissionError):
            task_collaboration.set_member_screening(
                self.store, actor_user_id=self.reconciler_id,
                user_id=self.reconciler_id, enabled=True, task_id=self.task)
        response = self._set_screening(1, True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["member"]["screening_enabled"])
        self.assertEqual(response.json()["member"]["updated_by"], self.owner_id)
        # 留痕：updated_at 非空。
        self.assertTrue(response.json()["member"]["updated_at"])

    # ---- 5. 初筛拒绝：结构化 403；开启恢复 -----------------------------

    def test_disabled_reconciler_initial_screening_403_and_recovery(self):
        self.assertEqual(self._set_screening(1, False).status_code, 200)
        # 读取类端点不拦（队列/进度/已存决策照常可读）。
        self.assertEqual(self.client.get(
            f"/api/tasks/{self.task}/progress", headers=self.auth(1)).status_code, 200)
        self.assertEqual(self.client.get(
            f"/api/tasks/{self.task}/decisions", headers=self.auth(1)).status_code, 200)
        self.assertEqual(self.client.get(
            f"/api/tasks/{self.task}/work-queue/screen", headers=self.auth(1)).status_code, 200)
        blocked = self._decide(1)
        self.assertEqual(blocked.status_code, 403, blocked.text)
        detail = blocked.json()["detail"]
        self.assertEqual(detail["error_code"], "screening_not_enabled")
        self.assertEqual(detail["message_key"], "team.screening_not_enabled")
        self.assertTrue(detail["message"])
        self.assertNotIn("K1", json.dumps(detail, ensure_ascii=False))
        # 开启后恢复写入。
        self.assertEqual(self._set_screening(1, True).status_code, 200)
        saved = self._decide(1)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()["saved"])

    # ---- 6. 复筛同口径 -------------------------------------------------

    def test_disabled_reconciler_stage2_same_contract(self):
        # stage2 队列只收初筛 include/maybe：先开启并写入一条初筛纳入。
        self.assertEqual(self._set_screening(1, True).status_code, 200)
        self.assertEqual(self._decide(1).status_code, 200)
        self.assertEqual(self._set_screening(1, False).status_code, 200)
        blocked = self._decide(1, stage="stage2")
        self.assertEqual(blocked.status_code, 403, blocked.text)
        detail = blocked.json()["detail"]
        self.assertEqual(detail["error_code"], "screening_not_enabled")
        self.assertEqual(detail["message_key"], "team.screening_not_enabled")
        self.assertEqual(self.client.get(
            f"/api/tasks/{self.task}/stage2/decisions", headers=self.auth(1)).status_code, 200)
        self.assertEqual(self._set_screening(1, True).status_code, 200)
        self.assertEqual(self._decide(1, stage="stage2").status_code, 200)

    # ---- 7. coder 不受影响 ---------------------------------------------

    def test_coder_unaffected_by_screening_switch(self):
        # 属主改裁决员开关，coder 的初筛/复筛均可写。
        self.assertEqual(self._set_screening(1, False).status_code, 200)
        self.assertEqual(self._decide(2).status_code, 200)
        self.assertEqual(self._decide(2, stage="stage2").status_code, 200)
        # 存储层对 coder 恒为可写。
        self.assertTrue(task_collaboration.screening_allowed(
            self.store, self.coder_id, task_id=self.task))

    # ---- 8. 属主不受影响 -----------------------------------------------

    def test_owner_unaffected_even_with_disabled_reconciler_grant(self):
        # 属主也可持 reconciler grant；即便开关被置关，写初筛仍放行。
        with closing(sqlite3.connect(self.store)) as conn, conn:
            conn.execute(
                "INSERT OR IGNORE INTO collaboration_role_grants(user_id,role,granted_by,granted_at) "
                "VALUES(?,?,?,?)", (self.owner_id, "reconciler", self.owner_id, "2026-01-01T00:00:00Z"))
            conn.execute(
                "INSERT INTO collaboration_member_settings"
                "(user_id,screening_enabled,updated_by,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET screening_enabled=0",
                (self.owner_id, 0, self.owner_id, "2026-01-01T00:00:00Z"))
        self.assertTrue(task_collaboration.screening_allowed(
            self.store, self.owner_id, task_id=self.task))
        self.assertEqual(self._decide(0).status_code, 200)

    # ---- 9. legacy 无协作库任务零回归 ----------------------------------

    def test_legacy_task_without_store_is_unaffected(self):
        legacy = self.client.post(
            "/api/tasks", json={"name": "legacy-toggle"},
            headers=self.auth()).json()["task_id"]
        legacy_info = tasks_mod.find_task(legacy, DATA_DIR)
        task_collaboration.shared_store_path(legacy_info.dir).unlink()
        with self._mode("desktop"):
            legacy_db = legacy_info.dir / (
                tasks_mod.slugify_screener(self.users[0]["username"]) + ".db")
            legacy_db.write_bytes(_TEMPLATE_DB_BYTES)
            self.assertEqual(self.client.get(
                f"/api/tasks/{legacy}/progress", headers=self.auth()).status_code, 200)
            self.assertEqual(self.client.get(
                f"/api/tasks/{legacy}/decisions", headers=self.auth()).status_code, 200)
            saved = self.client.post(
                f"/api/tasks/{legacy}/decisions",
                json={"zotero_key": "K1", "decision": "include"}, headers=self.auth())
            self.assertEqual(saved.status_code, 200, saved.text)
            # 团队进度仍是 server-only：desktop legacy 不受新开关波及。
            self.assertEqual(self.client.get(
                f"/api/tasks/{legacy}/team-progress", headers=self.auth()).status_code, 404)

    # ---- 10. 撤销路径同口径（复审 Blocking ①） -------------------------

    def test_undo_screen_and_fulltext_gated_for_disabled_reconciler(self):
        self.assertEqual(self._set_screening(1, True).status_code, 200)
        self.assertEqual(self._decide(1, key="K1").status_code, 200)
        latest = self.client.get(
            f"/api/tasks/{self.task}/undo/screen", headers=self.auth(1)).json()["latest"]
        self.assertIsNotNone(latest)
        entry_id = latest["entry_id"]
        self.assertEqual(self._set_screening(1, False).status_code, 200)
        blocked = self.client.post(
            f"/api/tasks/{self.task}/undo/screen",
            json={"entry_id": entry_id}, headers=self.auth(1))
        self.assertEqual(blocked.status_code, 403, blocked.text)
        detail = blocked.json()["detail"]
        self.assertEqual(detail["error_code"], "screening_not_enabled")
        self.assertEqual(detail["message_key"], "team.screening_not_enabled")
        # 撤销被拒后决策仍在（未发生半截写入）。
        decisions = self.client.get(
            f"/api/tasks/{self.task}/decisions", headers=self.auth(1)).json()
        self.assertEqual(decisions["total"], 1)
        # 复筛撤销同口径。
        self.assertEqual(self._set_screening(1, True).status_code, 200)
        self.assertEqual(self._decide(1, stage="stage2", key="K1").status_code, 200)
        ft_latest = self.client.get(
            f"/api/tasks/{self.task}/undo/fulltext", headers=self.auth(1)).json()["latest"]
        self.assertIsNotNone(ft_latest)
        self.assertEqual(self._set_screening(1, False).status_code, 200)
        ft_blocked = self.client.post(
            f"/api/tasks/{self.task}/undo/fulltext",
            json={"entry_id": ft_latest["entry_id"]}, headers=self.auth(1))
        self.assertEqual(ft_blocked.status_code, 403, ft_blocked.text)
        self.assertEqual(ft_blocked.json()["detail"]["error_code"], "screening_not_enabled")
        # coding 撤销不属于筛选面：开关关闭不影响（此处无条目 -> 409 而非 403）。
        coding = self.client.post(
            f"/api/tasks/{self.task}/undo/coding",
            json={"entry_id": 1}, headers=self.auth(1))
        self.assertNotEqual(coding.status_code, 403, coding.text)

    # ---- 11. 双角色（coder+reconciler）按 coder 口径不受影响 ------------

    def test_dual_role_member_is_not_blocked_by_screening_switch(self):
        self.assertFalse(task_collaboration.screening_allowed(
            self.store, self.reconciler_id, task_id=self.task))
        granted = self.client.post(
            f"/api/tasks/{self.task}/collaboration/roles",
            json={"user_id": self.reconciler_id, "role": "coder"}, headers=self.auth())
        self.assertEqual(granted.status_code, 201, granted.text)
        self.assertTrue(task_collaboration.screening_allowed(
            self.store, self.reconciler_id, task_id=self.task))
        self.assertEqual(self._decide(1, key="K1").status_code, 200)
        # team-progress 对双角色成员报 True（前端据此不渲染无效开关）。
        member = next(m for m in self._team_progress().json()["members"]
                      if m["user_id"] == self.reconciler_id)
        self.assertEqual(sorted(member["roles"]), ["coder", "reconciler"])
        self.assertTrue(member["screening_enabled"])

    # ---- 12. 撤销角色后再授权 = 新授权默认关 ---------------------------

    def test_revoke_then_regrant_reconciler_defaults_disabled(self):
        self.assertEqual(self._set_screening(1, True).status_code, 200)
        self.assertTrue(task_collaboration.screening_allowed(
            self.store, self.reconciler_id, task_id=self.task))
        revoked = self.client.delete(
            f"/api/tasks/{self.task}/collaboration/roles/{self.reconciler_id}/reconciler",
            headers=self.auth())
        self.assertEqual(revoked.status_code, 200, revoked.text)
        regranted = self.client.post(
            f"/api/tasks/{self.task}/collaboration/roles",
            json={"user_id": self.reconciler_id, "role": "reconciler"}, headers=self.auth())
        self.assertEqual(regranted.status_code, 201, regranted.text)
        self.assertFalse(task_collaboration.screening_allowed(
            self.store, self.reconciler_id, task_id=self.task))
        self.assertEqual(self._decide(1).status_code, 403)

    # ---- 13. 严格布尔与目标角色校验 -------------------------------------

    def test_strict_boolean_and_target_role_validation(self):
        loose = self.client.put(
            f"/api/tasks/{self.task}/collaboration/members/{self.reconciler_id}/screening",
            json={"screening_enabled": "yes"}, headers=self.auth())
        self.assertEqual(loose.status_code, 422, loose.text)
        wrong_role = self._set_screening(2, False)
        self.assertEqual(wrong_role.status_code, 400, wrong_role.text)

    # ---- 14. 读路径不因回填写锁/失败变成 503 ---------------------------

    def test_team_progress_read_survives_backfill_write_failure(self):
        # marker 已在创建时写入：属主读不得进入写事务。
        original_write = task_collaboration._write

        def forbidden_write(*args, **kwargs):
            raise AssertionError("team-progress 对已迁移库不得获取写锁")

        task_collaboration._write = forbidden_write
        try:
            response = self._team_progress()
            self.assertEqual(response.status_code, 200, response.text)
        finally:
            task_collaboration._write = original_write

        # 未迁移库 + 写锁忙：回填异常降级，读取仍 200（按未迁移默认开）。
        original_ensure = task_collaboration.ensure_screening_backfill

        def busy_backfill(*args, **kwargs):
            raise task_collaboration.OwnerUnavailableError("database is locked")

        task_collaboration.ensure_screening_backfill = busy_backfill
        try:
            response = self._team_progress()
            self.assertEqual(response.status_code, 200, response.text)
        finally:
            task_collaboration.ensure_screening_backfill = original_ensure

    # ---- 15. 写端点异常口径：锁忙 503 / 损坏 desktop 409 server 503 -----

    def test_screening_write_exception_mapping(self):
        original_write = task_collaboration._write

        def locked_write(*args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        task_collaboration._write = locked_write
        try:
            response = self._set_screening(1, True)
            self.assertEqual(response.status_code, 503, response.text)
        finally:
            task_collaboration._write = original_write
        # 永久损坏：存储层抛 CollaborationStoreCorruptError；server 模式 HTTP 503，
        # 绝不折叠成 403（desktop 端点本身 404，属既有 server-only 口径）。
        self.store.write_bytes(b"not a sqlite database")
        with self.assertRaises(task_collaboration.CollaborationStoreCorruptError):
            task_collaboration.set_member_screening(
                self.store, actor_user_id=self.owner_id,
                user_id=self.reconciler_id, enabled=True, task_id=self.task)
        with self._mode("server"):
            server = self._set_screening(1, True)
            self.assertEqual(server.status_code, 503, server.text)

    # ---- 16. frontend contract -----------------------------------------

    def test_frontend_toggle_and_ten_language_entries(self):
        root = Path(__file__).resolve().parents[2]
        index = (root / "custom_frontend" / "index.html").read_text(encoding="utf-8")
        locale = (root / "custom_frontend" / "interface_locale.js").read_text(encoding="utf-8")
        # 成员卡开关 + 既有 mutateTeamMember 并发防护接线。
        self.assertIn("setMemberScreening", index)
        self.assertIn("collaboration/members/", index)
        self.assertIn("screening_enabled", index)
        self.assertRegex(index, r"setMemberScreening[\s\S]{0,600}mutateTeamMember")
        # 筛选页引导文案经 message_key 渲染；初筛 + 复筛页均常驻。
        self.assertIn("team.screening_not_enabled", index)
        self.assertIn("ftScreeningNotice", index)
        self.assertIn("syncScreeningNoticeElement('ftScreeningNotice')", index)
        # 被改动的 workflow_controls.js 缓存令牌必须已上调（24h 旧 JS 不得残留）。
        self.assertNotRegex(
            index, r'workflow_controls\.js\?v=20261101-r10u1')
        self.assertRegex(index, r'workflow_controls\.js\?v=20261107-r12arb3')
        # 十语言词条：四类新文案每行必须 10 个非空槽位。
        sources = (
            "允许参与筛选",
            "筛选参与权状态暂时无法确认，请稍后重试。",
            "已更新 {0} 的筛选参与权",
            "你尚未获得参与本任务筛选的权限，请联系任务负责人开启「允许参与筛选」后再试。",
        )
        rows = {}
        for line in locale.splitlines():
            stripped = line.strip().rstrip(",")
            if not stripped.startswith("["):
                continue
            try:
                row = json.loads(stripped)
            except ValueError:
                continue
            if isinstance(row, list) and row:
                rows[row[0]] = row
        for source in sources:
            self.assertIn(source, rows, source)
            self.assertEqual(len(rows[source]), 10, source)
            self.assertTrue(all(isinstance(value, str) and value.strip()
                                for value in rows[source]), source)


if __name__ == "__main__":
    unittest.main()
