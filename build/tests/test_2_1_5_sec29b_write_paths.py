# -*- coding: utf-8 -*-
"""sec29b 回归钉（W2 组·写闸两条最热路径：导入提交 + 评审 mutate）。

复审实测确认级【中】：review_imports_api 的导入提交（process 内最终
UPDATE）与评审 mutate（decide/metadata/undo/bulk/activate 共用事务的
版本推进）此前是无条件 ``UPDATE workflow_state SET version=version+1``——
sec29 的条件推进（expected_version → ``UPDATE ... AND version=?`` →
rowcount=0 抛 409 workflow_version_conflict、靠事务回滚）只装在
review_workflow_api 的 settings PUT / save_source 两处。修复把两条路径的
版本推进改为复用同一 ``WorkflowRuntime.bump_version``（runtime.workflow
即 app.state.review_workflow 同一实例，无需跨模块 import）；write_guard
前置比对保留（快失败），提交时条件推进原子兜底；expected_version=None
缺省保持无条件旧行为。

钉三层：
① 两条路径各一例「前置读版本与提交之间被另一连接连推两版」→ 409
   workflow_version_conflict + 本事务数据写随 with conn 回滚（对照已修
   settings PUT 行为）。竞态注入=包装 write_guard：前置检查通过后、
   业务事务开启前，用独立连接连推两版（模拟跨进程写者）。
② 正常路径 200 且版本恰 +1（导入提交的快照版本恒为 int；mutate 携带
   expected_policy_version）。
③ mutate 的 expected_policy_version=None 缺省不回归（无条件推进 +1）。
"""
import json
import os
import sys
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest import mock

fixture = tempfile.TemporaryDirectory(prefix="rf_sec29b_w2_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import db as db_mod, review_imports as store, review_workflow as wf  # noqa: E402
from coscreen import import_review_undo as review_undo  # noqa: E402
from coscreen.models import Article  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend.main import app  # noqa: E402

#: 最小可解析 RIS（同 test_zotero_local_api 手工导入形态）
RIS_ONE = (
    "TY  - JOUR\n"
    "T1  - Sec29b Race Probe {tag}\n"
    "AU  - Lovelace, Ada\n"
    "JO  - J Example\n"
    "PY  - 2024\n"
    "DO  - 10.test/sec29b-{tag}\n"
    "ER  - \n"
)


class ConcurrentBumpProxy:
    """竞态注入：带 expected_version 的 write_guard 在前置检查通过后、
    业务事务开启前，用**另一条独立连接**连推两版——精确复现「前置读版本
    与提交之间」的跨进程并发写窗口。其余属性/无条件守卫原样透传。"""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def write_guard(self, *args, **kwargs):
        expected = kwargs.get("expected_version", args[4] if len(args) > 4 else None)
        guard = self._real.write_guard(*args, **kwargs)
        if expected is None:
            return guard

        @contextmanager
        def racing():
            with guard as ctx:
                for _ in range(2):  # 连推两版：两次独立提交
                    with closing(wf.connect(ctx.owner_db)) as other, other:
                        other.execute(
                            "UPDATE workflow_state SET version=version+1 "
                            "WHERE singleton=1")
                yield ctx
        return racing()


class Sec29bWritePathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        # 直建用户+签发令牌（不走 /api/auth/register：TestClient host 固定，
        # 多套件共享限流桶会撞 429——同 test_2_1_5_sec29_write_guard 手法）。
        user = auth_mod.create_user("sec29bw2@example.test", "Sec29-Test-2026")
        cls.user = {"user_id": user["id"], "username": user["username"],
                    "token": auth_mod.create_access_token(user["id"], user["username"])}
        cls.headers = {"Authorization": "Bearer " + cls.user["token"]}
        cls.task_id = cls.client.post(
            "/api/tasks", json={"name": "sec29b-write-paths"},
            headers=cls.headers).json()["task_id"]
        cls.runtime = app.state.review_workflow

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    # -- 小工具 ----------------------------------------------------------
    def _owner_db(self):
        db = self.runtime.context(self.task_id, self.user).owner_db
        store.migrate(db)
        review_undo.migrate(db)
        return db

    def _version(self, db):
        with closing(wf.connect(db, read_only=True)) as conn:
            return conn.execute(
                "SELECT version FROM workflow_state WHERE singleton=1"
            ).fetchone()[0]

    def _scalar(self, db, sql, params=()):
        with closing(wf.connect(db, read_only=True)) as conn:
            return conn.execute(sql, params).fetchone()[0]

    def _seed_batch(self, batch, cid):
        """直插一个待审 title 组（同 test_import_review_actions 模板形态）。"""
        db = self._owner_db()
        db_mod.init_db(db)
        with closing(wf.connect(db)) as c, c:
            c.execute("INSERT OR IGNORE INTO workflow_sources VALUES(?,?,?,?,?)",
                      ("s", "01-Wiley.ris", "01-wiley.ris", 1, "now"))
            c.execute("INSERT INTO workflow_import_batches VALUES(?,?,?,?,?,?,?)",
                      (batch, str(self.user["user_id"]), "h", "batch", 1, "staged", "now"))
            c.execute("INSERT INTO workflow_import_files VALUES(?,?,?,?,?)",
                      (batch, 0, "01-Wiley.ris", "d", "s"))
            for key in (cid + "a", cid + "b"):
                article = Article(zotero_key=key, title="Synthetic " + key,
                                  doi="10.test/same").to_dict()
                c.execute("INSERT INTO workflow_source_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (key, "s", key, 1, "01-Wiley.ris", json.dumps(article), json.dumps(article),
                           key, key, None, None, "new", "staged", "now"))
                c.execute("INSERT INTO workflow_import_members VALUES(?,?,?)", (batch, key, 0))
            c.execute("INSERT INTO workflow_import_candidates(id,signature,kind,status,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,?)", (cid, cid, "title", "pending", "now", "now"))
            for key in (cid + "a", cid + "b"):
                c.execute("INSERT INTO workflow_import_candidate_members VALUES(?,?,?)",
                          (cid, "record", key))

    # -- 钉 ①a：mutate 竞态 → 409 + 本事务回滚 --------------------------
    def test_mutate_race_two_bumps_409_and_rollback(self):
        batch, cid = "brA", "ca"
        self._seed_batch(batch, cid)
        db = self._owner_db()
        base = self._version(db)
        undo_before = self._scalar(db, "SELECT COUNT(*) FROM workflow_import_undo")
        proxy = ConcurrentBumpProxy(self.runtime)
        with mock.patch.object(app.state, "review_workflow", proxy):
            resp = self.client.post(
                f"/api/tasks/{self.task_id}/import-batches/{batch}/candidates/{cid}",
                json={"status": "not_duplicate", "expected_policy_version": base},
                headers=self.headers)
        self.assertEqual(resp.status_code, 409, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["code"], "workflow_version_conflict")
        self.assertEqual(detail["version"], base + 2)  # 并发方连推的两版
        # 本事务数据写随 409 回滚：候选仍 pending、无新增 undo 记账、版本
        # 未被失败方推进（仍是并发方的 base+2）。
        self.assertEqual(self._scalar(
            db, "SELECT status FROM workflow_import_candidates WHERE id=?", (cid,)), "pending")
        self.assertEqual(self._scalar(db, "SELECT COUNT(*) FROM workflow_import_undo"), undo_before)
        self.assertEqual(self._version(db), base + 2)

    # -- 钉 ①b：导入提交竞态 → 409 + 本事务回滚 -------------------------
    def test_import_submit_race_two_bumps_409_and_rollback(self):
        db = self._owner_db()
        base = self._version(db)
        batches_before = self._scalar(db, "SELECT COUNT(*) FROM workflow_import_batches")
        records_before = self._scalar(db, "SELECT COUNT(*) FROM workflow_source_records")
        proxy = ConcurrentBumpProxy(self.runtime)
        with mock.patch.object(app.state, "review_workflow", proxy):
            resp = self.client.post(
                f"/api/tasks/{self.task_id}/import",
                files={"file": ("sec29b-race.ris", RIS_ONE.format(tag="race").encode("utf-8"),
                                "application/octet-stream")},
                headers=self.headers)
        self.assertEqual(resp.status_code, 409, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["code"], "workflow_version_conflict")
        self.assertEqual(detail["version"], base + 2)
        # 本事务导入写入（copy_prepared 的批次/记录行）随 409 整体回滚；
        # 版本未被失败方推进。
        self.assertEqual(self._scalar(db, "SELECT COUNT(*) FROM workflow_import_batches"), batches_before)
        self.assertEqual(self._scalar(db, "SELECT COUNT(*) FROM workflow_source_records"), records_before)
        self.assertEqual(self._version(db), base + 2)

    # -- 钉 ②a：mutate 正常路径 200 且版本恰 +1 --------------------------
    def test_mutate_normal_advances_exactly_one(self):
        batch, cid = "brB", "cb"
        self._seed_batch(batch, cid)
        db = self._owner_db()
        base = self._version(db)
        resp = self.client.post(
            f"/api/tasks/{self.task_id}/import-batches/{batch}/candidates/{cid}",
            json={"status": "not_duplicate", "expected_policy_version": base},
            headers=self.headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["policy_version"], base + 1)
        self.assertEqual(self._version(db), base + 1)
        self.assertEqual(self._scalar(
            db, "SELECT status FROM workflow_import_candidates WHERE id=?", (cid,)), "not_duplicate")

    # -- 钉 ②b：导入提交正常路径 200 且版本恰 +1 -------------------------
    def test_import_submit_normal_advances_exactly_one(self):
        db = self._owner_db()
        base = self._version(db)
        resp = self.client.post(
            f"/api/tasks/{self.task_id}/import",
            files={"file": ("sec29b-ok.ris", RIS_ONE.format(tag="ok").encode("utf-8"),
                            "application/octet-stream")},
            headers=self.headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["policy_version"], base + 1)
        self.assertEqual(self._version(db), base + 1)

    # -- 钉 ③：expected_policy_version=None 缺省不回归 --------------------
    def test_mutate_none_default_keeps_unconditional_bump(self):
        batch, cid = "brC", "cc"
        self._seed_batch(batch, cid)
        db = self._owner_db()
        base = self._version(db)
        resp = self.client.post(
            f"/api/tasks/{self.task_id}/import-batches/{batch}/candidates/{cid}",
            json={"status": "not_duplicate"},  # 不携带 expected_policy_version
            headers=self.headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["policy_version"], base + 1)
        self.assertEqual(self._version(db), base + 1)  # 无条件推进旧行为保持


if __name__ == "__main__":
    unittest.main()
