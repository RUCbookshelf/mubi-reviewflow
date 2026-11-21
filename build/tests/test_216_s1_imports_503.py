# -*- coding: utf-8 -*-
"""包 S1（导入评审链 17 站收口）红绿测试。

绿侧断言：站点裸 ``sqlite3.DatabaseError``（含 coscreen.review_imports
``StorageUnavailable`` 标记子类）→ typed 503 ``storage_unavailable`` 信封
（error_code/message_key/params/retryable）。红侧：同一文件在未修底版上跑，
站点直抛裸 DatabaseError → 全局 500 / assertIsInstance 失败。

注入手段（避开 context 层自愈/迁移链）：
- 端点站（⑤⑥⑦⑧⑪/⑩）：``_api_frame_boom``——凡 custom_backend/
  review_imports_api.py 栈帧发起的 connect 即引 DatabaseError（context 层
  帧放行；DROP 表会被 context 迁移链重建，不可用）；
- migrate 站（④⑨）：``_write_connect_boom``——仅 coscreen/review_imports.py
  的 migrate 栈帧内写连接引爆；
- process 链（①②/§2.2 函数体）：健康 owner_db + 坏字节**旁库**（has_research
  扫描任务目录触爆）；
- §2.2/§2.3 函数体站：单测直调坏库路径。

运行（runtree，venv312 -I -B）：
    cd <runtree> && COBOOKSHELF_DATA_DIR=<abs tmp> REVIEWFLOW_MODE=server \
      python -I -B -m pytest build/tests/test_216_s1_imports_503.py -q
"""
from __future__ import annotations

import importlib
import inspect
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

OWNER = "s1owner"
_CORRUPT = b"this is not a sqlite database\x00\x01\x02" * 4

_EXPECT_CODE = "storage_unavailable"
_EXPECT_KEY = "import_review.storage_unavailable"


def _write_connect_boom(real_connect):
    """写连接注入 DatabaseError，只读连接放行。

    仅当调用栈经过 coscreen/review_imports.py 的 ``migrate`` 帧（即链内
    store.migrate 站点④⑨①）时引爆；context 层的 import_sources.migrate→
    workflow.migrate 同样走 workflow.connect，但不经过该帧 → 放行。
    """
    def fake(db, *args, **kwargs):
        if not kwargs.get("read_only", False):
            for fi in inspect.stack():
                if fi.function == "migrate" and fi.filename.replace("\\", "/").endswith("coscreen/review_imports.py"):
                    raise sqlite3.DatabaseError("s1: write connect injected")
        return real_connect(db, *args, **kwargs)
    return fake


def _api_frame_boom(real_connect):
    """凡从 custom_backend/review_imports_api.py 栈帧**直接调用**的 connect
    （端点站⑤⑥⑦⑧⑪ + mutate 站⑩事务连接）即引 DatabaseError；context 层
    （migration_state 的 connect 直接调用方是 review_workflow.py）放行。
    只看直接调用方帧：路由函数本身就在 review_imports_api.py，全栈扫描会误伤
    context 链。"""
    def fake(db, *args, **kwargs):
        caller = inspect.stack()[1]
        if caller.filename.replace("\\", "/").endswith("custom_backend/review_imports_api.py"):
            raise sqlite3.DatabaseError("s1: api connect injected")
        return real_connect(db, *args, **kwargs)
    return fake


class S1Imports503Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from fastapi import HTTPException
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.HTTPException = HTTPException
        cls.main_mod = main_mod
        cls.tmp_root = Path(tempfile.mkdtemp(prefix="s1_imports_tests_"))
        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.raw = TestClient(main_mod.app, raise_server_exceptions=False)
        cls.raw.__enter__()
        user = auth_mod.create_user(OWNER, "S1-Imports-Tests-2026")
        cls.user = {"user_id": user["id"], "username": user["username"],
                    "token": auth_mod.create_access_token(user["id"], user["username"])}
        cls.tasks_mod = importlib.import_module("coscreen.tasks")

    @classmethod
    def tearDownClass(cls):
        cls.raw.__exit__(None, None, None)
        cls.client.__exit__(None, None, None)

    def _auth(self):
        return {"Authorization": "Bearer " + self.user["token"]}

    def _new_task(self):
        resp = self.client.post("/api/tasks", json={"name": "s1-" + uuid.uuid4().hex[:8]},
                                headers=self._auth())
        self.assertEqual(resp.status_code, 201, resp.text)
        task_id = resp.json()["task_id"]
        info = self.tasks_mod.find_task(task_id, self.main_mod.DATA_DIR)
        self.assertIsNotNone(info)
        slug = self.tasks_mod.slugify_screener(self.user["username"])
        return task_id, info, info.dir / (slug + ".db")

    def _assert_typed_503(self, resp):
        self.assertEqual(resp.status_code, 503, resp.text)
        detail = resp.json()["detail"]
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail.get("error_code"), _EXPECT_CODE)
        self.assertEqual(detail.get("message_key"), _EXPECT_KEY)
        self.assertIs(detail.get("retryable"), True)

    # ---- 站④⑨：read_context / mutate 的 migrate 族（写连接注入）----

    def test_s4_read_context_write_connect_503(self):
        task_id, info, owner_db = self._new_task()
        from coscreen import review_workflow
        real = review_workflow.connect
        with mock.patch.object(review_workflow, "connect", _write_connect_boom(real)):
            resp = self.raw.get(f"/api/tasks/{task_id}/import-review-summary", headers=self._auth())
        self._assert_typed_503(resp)

    def test_s9_mutate_write_connect_503(self):
        task_id, info, owner_db = self._new_task()
        from coscreen import review_workflow
        real = review_workflow.connect
        with mock.patch.object(review_workflow, "connect", _write_connect_boom(real)):
            resp = self.raw.post(f"/api/tasks/{task_id}/import-batches/whatever/activate",
                                 json={}, headers=self._auth())
        self._assert_typed_503(resp)

    # ---- 站⑤⑥⑦⑧⑪⑩：端点连接/事务注入 ----

    def test_s5_summary_api_connect_503(self):
        task_id, info, owner_db = self._new_task()
        from coscreen import review_workflow
        with mock.patch.object(review_workflow, "connect", _api_frame_boom(review_workflow.connect)):
            resp = self.raw.get(f"/api/tasks/{task_id}/import-review-summary", headers=self._auth())
        self._assert_typed_503(resp)

    def test_s6_batches_api_connect_503(self):
        task_id, info, owner_db = self._new_task()
        from coscreen import review_workflow
        with mock.patch.object(review_workflow, "connect", _api_frame_boom(review_workflow.connect)):
            resp = self.raw.get(f"/api/tasks/{task_id}/import-batches", headers=self._auth())
        self._assert_typed_503(resp)

    def test_s7_detail_api_connect_503(self):
        task_id, info, owner_db = self._new_task()
        from coscreen import review_workflow
        with mock.patch.object(review_workflow, "connect", _api_frame_boom(review_workflow.connect)):
            resp = self.raw.get(f"/api/tasks/{task_id}/import-batches/whatever", headers=self._auth())
        self._assert_typed_503(resp)

    def test_s8_candidates_api_connect_503(self):
        task_id, info, owner_db = self._new_task()
        from coscreen import review_workflow
        with mock.patch.object(review_workflow, "connect", _api_frame_boom(review_workflow.connect)):
            resp = self.raw.get(f"/api/tasks/{task_id}/import-batches/whatever/candidates", headers=self._auth())
        self._assert_typed_503(resp)

    def test_s10_mutate_tx_api_connect_503(self):
        task_id, info, owner_db = self._new_task()
        from coscreen import review_workflow
        with mock.patch.object(review_workflow, "connect", _api_frame_boom(review_workflow.connect)):
            resp = self.raw.post(f"/api/tasks/{task_id}/import-batches/whatever/activate",
                                 json={}, headers=self._auth())
        self._assert_typed_503(resp)

    def test_s11_export_api_connect_503(self):
        task_id, info, owner_db = self._new_task()
        from coscreen import review_workflow
        with mock.patch.object(review_workflow, "connect", _api_frame_boom(review_workflow.connect)):
            resp = self.raw.get(f"/api/tasks/{task_id}/import-batches/whatever/export", headers=self._auth())
        self._assert_typed_503(resp)

    # ---- 站①②：process 链坏旁库（has_research 扫描触爆）----

    def test_s1_s2_process_corrupt_sibling_503(self):
        task_id, info, owner_db = self._new_task()
        (info.dir / "aaa_corrupt.db").write_bytes(_CORRUPT)
        runtime = self.main_mod.app.state.review_imports
        fake_file = {"name": "a.ris", "articles": [], "originals": [], "raw_path": owner_db,
                     "source_id": None, "digest": "0" * 64}
        with self.assertRaises(Exception) as cm:
            runtime.process(task_id, self.user, [fake_file], False, None, None)
        exc = cm.exception
        self.assertIsInstance(exc, self.HTTPException,
                              "red：裸 DatabaseError 逃逸（未译 503）：%r" % (exc,))
        self.assertEqual(exc.status_code, 503)
        self.assertEqual(exc.detail.get("error_code"), _EXPECT_CODE)
        self.assertIs(exc.detail.get("retryable"), True)

    # ---- §2.2/§2.3 函数体站（单测直调坏库）----

    def test_s13_review_imports_migrate_corrupt_marker(self):
        from coscreen import review_imports as store
        bad = self.tmp_root / ("mig_" + uuid.uuid4().hex + ".db")
        bad.write_bytes(_CORRUPT)
        with self.assertRaises(sqlite3.DatabaseError) as cm:
            store.migrate(bad)
        self.assertIs(type(cm.exception).__name__, "StorageUnavailable",
                      "red：函数体站裸 DatabaseError 未标化：%r" % (cm.exception,))

    def test_s14_has_research_corrupt_sibling_marker(self):
        from coscreen import review_imports as store, review_workflow as workflow
        task_id, info, owner_db = self._new_task()
        (info.dir / "aaa_corrupt.db").write_bytes(_CORRUPT)
        store.migrate(owner_db)  # 链内序：process@141 先 migrate 建 workflow_import_state，再 has_research@146
        conn = workflow.connect(owner_db)
        try:
            with self.assertRaises(sqlite3.DatabaseError) as cm:
                store.has_research(info.dir, conn)
        finally:
            conn.close()
        self.assertIs(type(cm.exception).__name__, "StorageUnavailable",
                      "red：has_research 函数体站裸 DatabaseError 未标化：%r" % (cm.exception,))

    def test_s15_undo_migrate_corrupt_marker(self):
        from coscreen import import_review_undo
        bad = self.tmp_root / ("undo_" + uuid.uuid4().hex + ".db")
        bad.write_bytes(_CORRUPT)
        with self.assertRaises(sqlite3.DatabaseError) as cm:
            import_review_undo.migrate(bad)
        self.assertIs(type(cm.exception).__name__, "StorageUnavailable",
                      "red：undo migrate 函数体站裸 DatabaseError 未标化：%r" % (cm.exception,))

    # ---- 零漂移契约（绿侧常真）----

    def test_s16_storage_marker_is_databaseerror_subclass(self):
        from coscreen.review_imports import StorageUnavailable
        self.assertTrue(issubclass(StorageUnavailable, sqlite3.DatabaseError))


if __name__ == "__main__":
    unittest.main()
