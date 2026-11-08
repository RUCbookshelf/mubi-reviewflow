"""2.1.5 W1 红钉：初筛/复筛二选一、轮次移除、存量「暂不确定」重置。

钉随行为（旧钉 → 新钉对照）：
- 旧 r15：``maybe`` 只在初筛第一轮可存、第 2 轮 409 ``maybe_first_round_only``
  → 新：两阶段全程 409 ``maybe_disabled`` + ``message_key='workflow.maybe_disabled'``。
- 旧 r15：复筛入队 ``IN ('include','maybe')``
  → 新：复筛入队/进度仅 ``include``。
- 旧 r15：``POST /screening-rounds`` start/finish 200
  → 新：路由未注册，恒 404；queue/settings 载荷无轮次字段。
- 旧 r15：``show_maybe`` 任务开关可关可开
  → 新：settings 读取剥离、PUT 忽略、不再参与写闸。
- 旧 r15：存量 maybe 永远保留
  → 新：一次性幂等迁移把初筛/复筛 maybe 行重置为未筛，级联复筛、备份可校验、
    下游编码数据休眠不删、重筛 include 后资格恢复。

本文件先于实现写成（红钉先行）。运行：
    python -m pytest build/tests/test_2_1_5_w1_binary.py -q
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 与既有服务端夹具同法：先设环境再导入 main。
fixture = tempfile.TemporaryDirectory(prefix="rf_2_1_5_w1_binary_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

from custom_backend import auth as auth_mod
from custom_backend.main import app, DATA_DIR
from coscreen import binary_reset
from coscreen import db as db_mod
from coscreen import fulltext as fulltext_mod
from coscreen import research_trace
from coscreen import review_workflow as store
from coscreen import tasks as tasks_mod
from coscreen.models import Article

USERS = ("w1owner@example.test", "w1member@example.test",
         "w1outsider@example.test")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class W1BinaryBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        for name in USERS:
            try:
                user = auth_mod.create_user(name, "W1-Binary-Test-2026")
            except auth_mod.AuthError:
                # 同一模块的多个测试类共享 DATA_DIR：第二个类复用首个类
                # 已创建的账号（token 重新签发，口令不参与本测试）。
                user = auth_mod.get_user(name)
            cls.users.append({
                "user_id": user["id"],
                "username": user["username"],
                "token": auth_mod.create_access_token(user["id"], user["username"]),
            })

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def auth(self, index: int = 0) -> dict:
        return {"Authorization": "Bearer " + self.users[index]["token"]}

    def new_task(self, name: str | None = None) -> None:
        response = self.client.post(
            "/api/tasks", json={"name": name or self._testMethodName},
            headers=self.auth())
        self.assertEqual(response.status_code, 201, response.text)
        self.task = response.json()["task_id"]
        self.info = tasks_mod.find_task(self.task, DATA_DIR)
        self.owner_db = self.info.dir / (
            tasks_mod.slugify_screener(self.users[0]["username"]) + ".db")

    def member_db(self, index: int) -> Path:
        return self.info.dir / (
            tasks_mod.slugify_screener(self.users[index]["username"]) + ".db")

    def seed_owner(self, articles=("K1", "K2", "K3", "K4", "K5")) -> None:
        db_mod.init_db(self.owner_db)
        store.migrate(self.owner_db)
        db_mod.upsert_articles(
            self.owner_db,
            [Article(zotero_key=key, title=f"Study {key}") for key in articles], [])

    def save(self, key: str, decision: str, *, stage: str = "screen") -> None:
        if stage == "screen":
            db_mod.save_decision(self.owner_db, key, decision)
        else:
            fulltext_mod.save_stage2(self.owner_db, key, decision)

    def api(self, path: str, *, index: int = 0):
        return self.client.get(f"/api/tasks/{self.task}{path}", headers=self.auth(index))

    def post(self, path: str, body: dict, *, index: int = 0):
        return self.client.post(f"/api/tasks/{self.task}{path}", json=body,
                                headers=self.auth(index))


class NotImplementedApiPins(W1BinaryBase):
    def setUp(self):
        self.new_task()

    def test_maybe_rejected_on_both_stages_with_structured_409(self):
        self.seed_owner(("K1", "K2"))
        # 先让 actor 库完成迁移（新库标记 v8），再直接种入测试数据。
        self.assertEqual(self.api("/workflow-settings").status_code, 200)
        for key in ("K1",):
            refused = self.post("/decisions", {"zotero_key": key, "decision": "maybe"})
            self.assertEqual(refused.status_code, 409, refused.text)
            detail = refused.json()["detail"]
            self.assertEqual(detail.get("code"), "maybe_disabled", detail)
            self.assertEqual(detail.get("message_key"), "workflow.maybe_disabled", detail)
            self.assertIsInstance(detail.get("version"), int, detail)
            self.assertEqual(detail["workflow_settings"]["version"], detail["version"], detail)
            self.assertNotIn("show_maybe", detail["workflow_settings"]["stages"]["screen"], detail)
        # 决定不落盘
        self.assertEqual(self.api("/decisions").json()["decisions"], [])

        # 复筛：先合法 include，再 maybe 被同一形状拒绝
        self.assertEqual(
            self.post("/decisions", {"zotero_key": "K2", "decision": "include"}).status_code,
            200)
        refused = self.post("/stage2/decisions", {"zotero_key": "K2", "decision": "maybe"})
        self.assertEqual(refused.status_code, 409, refused.text)
        detail = refused.json()["detail"]
        self.assertEqual(detail.get("code"), "maybe_disabled", detail)
        self.assertEqual(detail.get("message_key"), "workflow.maybe_disabled", detail)
        rows = self.api("/stage2/decisions").json()["decisions"]
        self.assertEqual(rows, [], "被拒的复筛 maybe 不得落盘")

    def test_maybe_allowed_is_false_in_both_formal_stages(self):
        self.seed_owner(("K1",))
        self.assertEqual(self.api("/workflow-settings").status_code, 200)
        self.assertEqual(
            self.post("/decisions", {"zotero_key": "K1", "decision": "include"}).status_code,
            200)
        self.assertIs(self.api("/work-items/screen/K1").json()["maybe_allowed"], False)
        self.assertIs(self.api("/work-items/fulltext/K1").json()["maybe_allowed"], False)

    def test_round_route_is_gone_and_queue_settings_payloads_strip_round_fields(self):
        self.seed_owner(("K1",))
        self.assertEqual(self.api("/workflow-settings").status_code, 200)
        removed = self.post("/screening-rounds",
                            {"action": "start", "expected_policy_version": 1})
        self.assertEqual(removed.status_code, 404, removed.text)

        queue = self.api("/work-queue/screen").json()
        self.assertNotIn("remaining_uncertain", queue)
        self.assertNotIn("screening_round", queue)

        settings = self.api("/workflow-settings").json()
        self.assertNotIn("screening_round_marker", settings)
        for stage in ("screen", "fulltext"):
            self.assertNotIn("show_maybe", settings["stages"][stage], settings["stages"])

        # PUT 携带 show_maybe：忽略而不是 422；读回仍无该键，存储值不再被写入。
        stages = {name: {"mode": value["mode"], "source_ids": value["source_ids"]}
                  for name, value in settings["stages"].items()}
        stages["screen"]["show_maybe"] = False
        stages["fulltext"]["show_maybe"] = False
        put = self.client.put(
            f"/api/tasks/{self.task}/workflow-settings",
            json={"expected_version": settings["version"], "stages": stages,
                  "evidence_required_field_ids": settings["evidence_required_field_ids"]},
            headers=self.auth())
        self.assertEqual(put.status_code, 200, put.text)
        self.assertNotIn("show_maybe", put.json()["stages"]["screen"])
        with closing(store.connect(self.owner_db, read_only=True)) as conn:
            stored = json.loads(conn.execute(
                "SELECT stages_json FROM workflow_state WHERE singleton=1").fetchone()[0])
        self.assertNotIn("show_maybe", stored["screen"])
        self.assertNotIn("show_maybe", stored["fulltext"])

    def test_fulltext_queue_and_progress_are_include_only(self):
        self.seed_owner(("K1", "K2", "K3"))
        self.assertEqual(self.api("/workflow-settings").status_code, 200)
        self.save("K1", "maybe")
        self.save("K2", "include")
        self.save("K3", "exclude")
        self.assertEqual(fulltext_mod.stage2_queue(self.owner_db), ["K2"])
        self.assertEqual(fulltext_mod.get_stage2_progress(self.owner_db)["total"], 1)
        keys = {row["zotero_key"] for row in self.api("/work-queue/fulltext").json()["queue"]}
        self.assertEqual(keys, {"K2"})


class ResetMigrationPins(unittest.TestCase):
    """迁移语义（不依赖 HTTP 环境，直接对独立 SQLite 库执行）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf_w1_reset_")
        self.db = Path(self._tmp.name) / "owner.db"
        db_mod.init_db(self.db)
        store.migrate(self.db)
        db_mod.upsert_articles(
            self.db,
            [Article(zotero_key=f"K{i}", title=f"Study {i}") for i in range(1, 6)], [])

    def tearDown(self):
        self._tmp.cleanup()

    def _seed_migration_fixture(self):
        # K1: 初筛 maybe + 复筛 include（级联重置）
        # K2: 初筛 maybe + 复筛 maybe（两阶段都计）
        # K3: 初筛 include + 复筛 include（零触碰）
        # K4: 初筛 exclude（零触碰）
        # K5: 初筛 include（零触碰，无复筛行）
        db_mod.save_decision(self.db, "K1", "maybe")
        fulltext_mod.save_stage2(self.db, "K1", "include")
        db_mod.save_decision(self.db, "K2", "maybe")
        fulltext_mod.save_stage2(self.db, "K2", "maybe")
        db_mod.save_decision(self.db, "K3", "include")
        fulltext_mod.save_stage2(self.db, "K3", "include")
        db_mod.save_decision(self.db, "K4", "exclude")
        db_mod.save_decision(self.db, "K5", "include")
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO coding_dimensions"
                "(name,dtype,options_json,multi_select,unit,description,section,position,created_at)"
                " VALUES('dose','text','[]',0,'','','',1,'2026-01-01T00:00:00Z')")
            conn.execute("INSERT INTO coding_values(dimension_id,zotero_key,value,updated_at)"
                         " VALUES(1,'K1','12 mg','2026-01-01T00:00:00Z')")
            research_trace.ensure_schema(conn)

    def _rows(self, table: str) -> list[tuple]:
        with closing(sqlite3.connect(self.db)) as conn:
            return [tuple(row) for row in conn.execute(
                f"SELECT * FROM {table} ORDER BY zotero_key")]

    def _summary_row(self) -> dict:
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM workflow_binary_reset").fetchone()
            return dict(row)

    def test_reset_counts_cascade_idempotence_backup_and_audit(self):
        self._seed_migration_fixture()
        screen_before = {r[0]: r for r in self._rows("decisions") if r[1] != "maybe"}
        # 级联删除的复筛行虽非 maybe，但按设计随初筛 maybe 一并重置；
        # 审计钉只覆盖「与 maybe 无关」的非 maybe 行。
        screen_maybe = {r[0] for r in self._rows("decisions") if r[1] == "maybe"}
        stage2_before = {r[0]: r for r in self._rows("stage2_decisions")
                         if r[1] != "maybe" and r[0] not in screen_maybe}

        first = binary_reset.migrate(self.db, task_id="t1", actor_id=1)
        self.assertEqual(first["stage1_reset"], 2, first)
        self.assertEqual(first["stage2_reset"], 1, first)
        self.assertEqual(first["stage2_cascade_reset"], 1, first)

        self.assertEqual({r[0] for r in self._rows("decisions")}, {"K3", "K4", "K5"})
        self.assertEqual({r[0] for r in self._rows("stage2_decisions")}, {"K3"})
        # 审计钉：非 maybe 决定逐字节不变
        self.assertEqual({r[0]: r for r in self._rows("decisions")}, screen_before)
        self.assertEqual({r[0]: r for r in self._rows("stage2_decisions")}, stage2_before)
        # 下游编码数据保留（休眠不删）
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT value FROM coding_values WHERE zotero_key='K1'").fetchone()[0], "12 mg")

        summary = self._summary_row()
        backup = self.db.with_name(self.db.name + ".pre-binary-reset.bak")
        self.assertTrue(backup.is_file(), "重置必须写 .pre-binary-reset.bak")
        self.assertEqual(summary["backup_sha256"], _sha256(backup))
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            # 备份是重置前快照：maybe 行仍在
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE decision='maybe'").fetchone()[0], 2)
        # 轨迹留痕（谁/何时/N 条）
        with closing(sqlite3.connect(self.db)) as conn:
            event = conn.execute(
                "SELECT actor_id,action,after_json FROM trace_events "
                "WHERE action='binary_maybe_reset'").fetchall()
        self.assertEqual(len(event), 1, event)
        self.assertEqual(event[0][0], "1")
        self.assertEqual(json.loads(event[0][2])["stage1_reset"], 2)
        # 幂等标记
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM workflow_migrations WHERE version=?",
                (binary_reset.RESET_VERSION,)).fetchone()[0], 1)

        backup_stat = backup.stat()
        second = binary_reset.migrate(self.db, task_id="t1", actor_id=1)
        self.assertEqual(second["applied_at"], first["applied_at"])
        self.assertEqual(self._summary_row()["applied_at"], summary["applied_at"])
        self.assertEqual(backup.stat().st_mtime_ns, backup_stat.st_mtime_ns)
        self.assertEqual({r[0] for r in self._rows("decisions")}, {"K3", "K4", "K5"})
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM trace_events WHERE action='binary_maybe_reset'"
            ).fetchone()[0], 1, "幂等二跑不得重复留痕")

    def test_non_maybe_database_is_untouched_and_needs_no_backup(self):
        db_mod.save_decision(self.db, "K1", "include")
        db_mod.save_decision(self.db, "K2", "exclude")
        fulltext_mod.save_stage2(self.db, "K1", "include")
        before = (self._rows("decisions"), self._rows("stage2_decisions"))
        result = binary_reset.migrate(self.db, task_id="t1", actor_id=1)
        self.assertEqual((result["stage1_reset"], result["stage2_reset"],
                          result["stage2_cascade_reset"]), (0, 0, 0))
        self.assertEqual((self._rows("decisions"), self._rows("stage2_decisions")), before)
        self.assertFalse(self.db.with_name(
            self.db.name + ".pre-binary-reset.bak").exists(),
            "没有 maybe 的库无需备份")
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM workflow_migrations WHERE version=?",
                (binary_reset.RESET_VERSION,)).fetchone()[0], 1)

    def test_downstream_sleeps_then_reappears_after_rescreen_include(self):
        self._seed_migration_fixture()
        binary_reset.migrate(self.db, task_id="t1", actor_id=1)
        from custom_backend.review_workflow_api import WorkflowContext
        info = tasks_mod.create_task("w1-downstream", "", DATA_DIR)
        user = {"user_id": 1, "username": "owner@example.test"}
        ctx = WorkflowContext(
            info=info, user=user,
            owner={"owner_user_id": 1, "owner_username": user["username"]},
            owner_db=self.db, actor_db=self.db, can_manage=True,
            task_kind="personal", collaboration_db=info.dir / "collaboration.db")
        runtime = app.state.review_workflow

        def coding_keys() -> set[str]:
            rows, _saved = runtime.eligible(ctx, "coding")
            return {row["zotero_key"] for row in rows}

        # 重置 → 回到待筛队列：K1/K2 未筛，非 maybe 的 K3/K4/K5 保持已决。
        screen_rows, screen_saved = runtime.eligible(ctx, "screen")
        pending = {row["zotero_key"] for row in screen_rows
                   if not screen_saved.get(row["zotero_key"])}
        self.assertEqual(pending, {"K1", "K2"})

        self.assertNotIn("K1", coding_keys(), "级联重置后 K1 不得保有编码资格")
        self.assertIn("K3", coding_keys(), "非 maybe 链路不受影响")
        # 重筛：初筛纳入 → 重新进入复筛队列；复筛纳入 → 编码资格恢复，
        # 既有编码值仍在（下游数据休眠，不删）。
        db_mod.save_decision(self.db, "K1", "include")
        ft_rows, _ = runtime.eligible(ctx, "fulltext")
        self.assertIn("K1", {row["zotero_key"] for row in ft_rows},
                      "重筛 include 后 K1 必须重新进入复筛")
        fulltext_mod.save_stage2(self.db, "K1", "include")
        self.assertIn("K1", coding_keys())
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT value FROM coding_values WHERE zotero_key='K1'").fetchone()[0], "12 mg")


class ResetSummaryApiPins(W1BinaryBase):
    def setUp(self):
        self.new_task()
        self.seed_owner(("K1",))
        self.save("K1", "maybe")
        # invite 会触发属主 context（=首次打开任务），顺带完成属主库重置
        invited = self.client.post(
            f"/api/tasks/{self.task}/collaboration/invite",
            json={"username": self.users[1]["username"], "role": "coder"},
            headers=self.auth())
        self.assertEqual(invited.status_code, 201, invited.text)
        # 成员库在成员首次打开前不存在；这里直接造一个已存 maybe 的成员库
        member = self.member_db(1)
        db_mod.init_db(member)
        store.migrate(member)
        db_mod.upsert_articles(
            member, [Article(zotero_key="K2", title="Study K2")], [])
        db_mod.save_decision(member, "K2", "maybe")

    def test_owner_sees_all_members_member_sees_self_only_and_outsider_403(self):
        response = self.api("/binary-reset-summary")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["can_manage"])
        members = {row["username"]: row for row in body["members"]}
        self.assertEqual(set(members), {self.users[0]["username"],
                                        self.users[1]["username"]})
        owner = members[self.users[0]["username"]]
        self.assertEqual(owner["stage1_reset"], 1)
        self.assertTrue(owner["backup"]["exists"])
        self.assertTrue(owner["backup"]["sha256"])
        member = members[self.users[1]["username"]]
        self.assertEqual(member["stage1_reset"], 1)
        self.assertTrue(member["migrated"])
        self.assertTrue(member["backup"]["exists"])
        self.assertEqual(body["totals"]["stage1_reset"], 2)

        own = self.api("/binary-reset-summary", index=1)
        self.assertEqual(own.status_code, 200, own.text)
        own_body = own.json()
        self.assertFalse(own_body["can_manage"])
        self.assertEqual([row["username"] for row in own_body["members"]],
                         [self.users[1]["username"]])

        refused = self.api("/binary-reset-summary", index=2)
        self.assertEqual(refused.status_code, 403, refused.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
