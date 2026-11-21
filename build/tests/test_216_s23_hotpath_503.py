# -*- coding: utf-8 -*-
"""包 S2+S3（coding evidence 19 站 + 首开/升级热路径 11 站 + 9 调用点收口）红绿测试。

设计依据：``E:/rf216-store-scan-20261120/REPORT.md`` §2.8（coding_evidence
19 站）/§2.9（import_sources 11 真缺口 + 4 误报）/§0 对照发现（review_workflow_api
包装 migrate/ensure_fields 调用点不在 audit_cover 雷达内）/§4 包划分 S2+S3。

修复口径（勘察定案）：
- **热路径优先**：``_screener_db``（每请求懒建/自愈入口）与
  ``WorkflowRuntime.context``（首开/升级链）在公共层收口 ``DatabaseError``
  → 标准 503 词条——一次性覆盖 import_sources / binary_reset /
  coding_evidence.ensure_fields 三链；
- **coding_evidence 公共入口收口**：``migrate``/``field_rows``/
  ``ensure_fields``/``mapping``/``evidence_state``/``complete_keys`` 经
  ``_storage_guarded`` 出 ``StorageUnavailable``（DatabaseError 子类，
  既有捕获面零漂移）；API 调用点用 ``_coding_evidence_read`` 一处译 503；
- **零行为变更除错误面**：正常首建/升级/读写路径逐字节不变。

红侧：同一文件在未修底版（箱内 ``base``）上跑——坏库/注入失败 → 裸
DatabaseError 逃逸（全局 500 或直调抛错）。绿侧：503 +
``SCREENER_LIBRARY_UNAVAILABLE_MESSAGE``（G/I/L/main503-r2 同款）。

运行（箱内 candidate，venv312 -I -B）：
    cd E:/rf216-s23-hotpath-20261121/candidate
    COBOOKSHELF_DATA_DIR=<abs TEMP/runN> REVIEWFLOW_MODE=server \
      TMP=<abs TEMP/runN> TEMP=<abs TEMP/runN> \
      python -I -B -m pytest build/tests/test_216_s23_hotpath_503.py -q -p no:cacheprovider
"""
from __future__ import annotations

import importlib
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

OWNER = "s23owner"
_CORRUPT = b"this is not a sqlite database\x00\x01\x02" * 8


class S23HotPath503Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.main_mod = main_mod
        cls.HTTPException = importlib.import_module("fastapi").HTTPException
        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        # 红侧形状观测：不向上抛服务端异常，得到真实 HTTP 500。
        cls.raw = TestClient(main_mod.app, raise_server_exceptions=False)
        cls.raw.__enter__()
        user = auth_mod.create_user(OWNER, "S23-HotPath-Tests-2026")
        cls.user = {"user_id": user["id"], "username": user["username"],
                    "token": auth_mod.create_access_token(user["id"], user["username"])}
        cls.tasks_mod = importlib.import_module("coscreen.tasks")
        cls.tmp_root = Path(tempfile.mkdtemp(prefix="s23_hotpath_"))

    @classmethod
    def tearDownClass(cls):
        cls.raw.__exit__(None, None, None)
        cls.client.__exit__(None, None, None)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _auth(self):
        return {"Authorization": "Bearer " + self.user["token"]}

    def _new_task(self):
        resp = self.client.post("/api/tasks", json={"name": "s23-" + uuid.uuid4().hex[:8]},
                                headers=self._auth())
        self.assertEqual(resp.status_code, 201, resp.text)
        task_id = resp.json()["task_id"]
        info = self.tasks_mod.find_task(task_id, self.main_mod.DATA_DIR)
        self.assertIsNotNone(info)
        slug = self.tasks_mod.slugify_screener(self.user["username"])
        return task_id, info, info.dir / f"{slug}.db"

    @staticmethod
    def _corrupt(path: Path) -> None:
        for suffix in ("-wal", "-shm"):
            side = path.with_name(path.name + suffix)
            try:
                side.unlink()
            except OSError:
                pass
        path.write_bytes(_CORRUPT)

    def _assert_typed_503(self, resp, label=""):
        self.assertEqual(resp.status_code, 503, f"{label}: {resp.text[:400]}")
        self.assertEqual(resp.json()["detail"],
                         self.main_mod.SCREENER_LIBRARY_UNAVAILABLE_MESSAGE,
                         f"{label}: 词条不符 {resp.text[:400]}")

    # ==================================================================
    # S3 链：_screener_db / context 首开升级热路径
    # ==================================================================
    def test_s3a_screener_db_corrupt_is_typed_503(self):
        """每请求懒建/自愈入口：坏库字节 → typed 503（不再裸 DatabaseError）。"""
        _task_id, info, owner_db = self._new_task()
        self.assertTrue(owner_db.is_file())
        self._corrupt(owner_db)
        with self.assertRaises(self.HTTPException) as caught:
            self.main_mod._screener_db(info, self.user["username"])
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail,
                         self.main_mod.SCREENER_LIBRARY_UNAVAILABLE_MESSAGE)

    def test_s3b_context_corrupt_owner_db_is_503_not_500(self):
        """首开热路径（真实 HTTP）：坏属主库 → 503 而非全局 500。"""
        task_id, _info, owner_db = self._new_task()
        self._corrupt(owner_db)
        resp = self.raw.get(f"/api/tasks/{task_id}/workflow-settings",
                            headers=self._auth())
        self._assert_typed_503(resp, "workflow-settings/corrupt")

    def test_s3c_legacy_context_migration_failure_is_503(self):
        """legacy 分支：migration_state/migrate 的 DatabaseError → 503。

        直接构造 legacy 位形（任务目录无 collaboration.db ⇒ get_owner=None），
        注入 screener_db 只返回路径（绕开 init_db，专钉 context 自身的迁移链
        捕获面），坏库字节让 migration_state 抛 DatabaseError。
        """
        from custom_backend.review_workflow_api import WorkflowRuntime

        task_dir = self.tmp_root / ("legacy-" + uuid.uuid4().hex[:8])
        task_dir.mkdir(parents=True)
        # context 的 legacy 分支按 user["username"] 的 slug 拼库名——坏库必须
        # 落在同一路径才会进入 migration_state/migrate 分支。
        actor_db = task_dir / (
            self.tasks_mod.slugify_screener(self.user["username"]) + ".db")
        self._corrupt(actor_db)

        fake_info = type("FakeInfo", (), {
            "task_id": "legacy-task", "dir": task_dir,
            "db_files": [actor_db]})()
        runtime = WorkflowRuntime(
            lambda _tid: fake_info,
            lambda _info, _username, **_kw: actor_db,
            self.tmp_root, lambda _info, _user: True)
        with self.assertRaises(self.HTTPException) as caught:
            runtime.context("legacy-task", self.user)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail,
                         self.main_mod.SCREENER_LIBRARY_UNAVAILABLE_MESSAGE)

    def test_s3d_context_import_sources_migration_failure_is_503(self):
        """owner 分支：import_sources.migrate 的 DatabaseError → 503。

        钉 §0 对照发现的核心盲区——包装 migrate 调用点不在 audit_cover
        口径内；坏库/迁移失败此前从 context 直达全局 500。
        """
        runtime = self.main_mod.app.state.review_workflow
        task_id, _info, _owner_db = self._new_task()
        from coscreen import import_sources
        with mock.patch.object(runtime, "screener_db",
                               side_effect=lambda info, username, **kw:
                               info.dir / (self.tasks_mod.slugify_screener(username) + ".db")), \
                mock.patch.object(import_sources, "migrate",
                                  side_effect=sqlite3.DatabaseError("s23: migrate boom")):
            # 版本不齐 ⇒ 走 import_sources.migrate（migration_state 对真实库
            # 返回的是集合；删除迁移账本迫使 None 分支最简，但健康库不应被
            # 破坏——改用直接 patch migration_state 返回 None。）
            from coscreen import review_workflow as store
            with mock.patch.object(store, "migration_state", return_value=None):
                with self.assertRaises(self.HTTPException) as caught:
                    runtime.context(task_id, self.user)
        self.assertEqual(caught.exception.status_code, 503)

    def test_s3e_context_binary_reset_failure_is_503(self):
        """binary_reset 经 context（rwa:358/396/404）的入口 → 503。"""
        runtime = self.main_mod.app.state.review_workflow
        task_id, _info, _owner_db = self._new_task()
        from coscreen import binary_reset, review_workflow as store
        from coscreen import import_sources
        with mock.patch.object(runtime, "screener_db",
                               side_effect=lambda info, username, **kw:
                               info.dir / (self.tasks_mod.slugify_screener(username) + ".db")), \
                mock.patch.object(store, "migration_state", return_value=None), \
                mock.patch.object(import_sources, "migrate"), \
                mock.patch.object(binary_reset, "migrate",
                                  side_effect=sqlite3.DatabaseError("s23: reset boom")):
            with self.assertRaises(self.HTTPException) as caught:
                runtime.context(task_id, self.user)
        self.assertEqual(caught.exception.status_code, 503)

    def test_s3f_import_sources_migrate_corrupt_marker(self):
        """import_sources.migrate 函数体：坏库 → StorageUnavailable 标记。

        标记继承 ``sqlite3.DatabaseError``——既有捕获面（binary_reset_api
        的 except Exception 降级等）零漂移；仅新增可识别类型。
        """
        from coscreen import import_sources
        bad = self.tmp_root / ("src_" + uuid.uuid4().hex + ".db")
        bad.write_bytes(_CORRUPT)
        with self.assertRaises(sqlite3.DatabaseError) as caught:
            import_sources.migrate(bad)
        self.assertEqual(type(caught.exception).__name__, "StorageUnavailable",
                         "红：函数体裸 DatabaseError 未标化")
        self.assertTrue(issubclass(import_sources.StorageUnavailable,
                                   sqlite3.DatabaseError))

    # ==================================================================
    # S2 链：coding_evidence 公共入口 + 调用点
    # ==================================================================
    def test_s2a_coding_dimensions_mapping_failure_is_503(self):
        """main.py coding/dimensions（扫描期 5723）mapping 失败 → 503。"""
        task_id, _info, _db = self._new_task()
        from coscreen import coding_evidence
        with mock.patch.object(coding_evidence, "mapping",
                               side_effect=sqlite3.DatabaseError("s2: mapping boom")):
            resp = self.raw.get(f"/api/tasks/{task_id}/coding/dimensions",
                                headers=self._auth())
        self._assert_typed_503(resp, "coding/dimensions/mapping")

    def test_s2b_coding_dimensions_field_rows_failure_is_503(self):
        """main.py coding/dimensions（扫描期 5724）field_rows 失败 → 503。"""
        task_id, _info, _db = self._new_task()
        from coscreen import coding_evidence
        with mock.patch.object(coding_evidence, "field_rows",
                               side_effect=sqlite3.DatabaseError("s2: rows boom")):
            resp = self.raw.get(f"/api/tasks/{task_id}/coding/dimensions",
                                headers=self._auth())
        self._assert_typed_503(resp, "coding/dimensions/rows")

    def test_s2c_export_scheme_failure_is_503(self):
        """main.py coding/scheme GET（扫描期 5882/5883）→ 503。"""
        task_id, _info, _db = self._new_task()
        from coscreen import coding_evidence
        with mock.patch.object(coding_evidence, "field_rows",
                               side_effect=sqlite3.DatabaseError("s2: scheme boom")):
            resp = self.raw.get(f"/api/tasks/{task_id}/coding/scheme",
                                headers=self._auth())
        self._assert_typed_503(resp, "coding/scheme/field_rows")

    def test_s2d_workflow_settings_evidence_fields_failure_is_503(self):
        """rwa settings（扫描期 435-442）evidence_fields/mapping 失败 → 503。"""
        task_id, _info, _db = self._new_task()
        from coscreen import coding_evidence
        for attr, label in (("field_rows", "field_rows"), ("mapping", "mapping")):
            with self.subTest(station=label):
                with mock.patch.object(coding_evidence, attr,
                                       side_effect=sqlite3.DatabaseError(
                                           f"s2: settings {label} boom")):
                    resp = self.raw.get(
                        f"/api/tasks/{task_id}/workflow-settings",
                        headers=self._auth())
                self._assert_typed_503(resp, f"workflow-settings/{label}")

    def test_s2e_evidence_state_call_site_failure_is_503(self):
        """rwa evidence_state 调用点（coding/values 详情）→ 503。"""
        task_id, _info, _db = self._new_task()
        from coscreen import coding_evidence
        with mock.patch.object(coding_evidence, "evidence_state",
                               side_effect=sqlite3.DatabaseError("s2: ev boom")):
            resp = self.raw.get(f"/api/tasks/{task_id}/coding/values/KEY-S2E",
                                headers=self._auth())
        self._assert_typed_503(resp, "coding/values/evidence_state")

    def test_s2f_complete_keys_call_site_failure_is_503(self):
        """rwa coding_result（扫描期 complete_keys 站，推定）→ 503。

        直调 runtime.coding_result：evidence_state 走真实健康库，注入
        complete_keys 失败，断言 503（红：裸 DatabaseError）。
        """
        runtime = self.main_mod.app.state.review_workflow
        task_id, _info, _db = self._new_task()
        ctx = runtime.context(task_id, self.user)
        from coscreen import coding_evidence
        with mock.patch.object(coding_evidence, "complete_keys",
                               side_effect=sqlite3.DatabaseError("s2: keys boom")):
            with self.assertRaises(self.HTTPException) as caught:
                runtime.coding_result(ctx, "KEY-S2F")
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail,
                         self.main_mod.SCREENER_LIBRARY_UNAVAILABLE_MESSAGE)

    def test_s2g_import_scheme_migrate_failure_is_503(self):
        """coding.py:908（唯一生产调用）经 coding/scheme POST → 503。"""
        task_id, _info, _db = self._new_task()
        from coscreen import coding_evidence
        with mock.patch.object(coding_evidence, "migrate",
                               side_effect=sqlite3.DatabaseError("s2: import migrate boom")):
            resp = self.raw.post(
                f"/api/tasks/{task_id}/coding/scheme",
                json={"scheme": {"version": 1, "dimensions": []}, "replace": True},
                headers=self._auth())
        self._assert_typed_503(resp, "coding/scheme POST")

    def test_s2h_ensure_fields_public_entry_marker(self):
        """ensure_fields 公共入口：坏库 → StorageUnavailable（DatabaseError 子类）。"""
        from coscreen import coding_evidence
        bad = self.tmp_root / ("ce_" + uuid.uuid4().hex + ".db")
        bad.write_bytes(_CORRUPT)
        with self.assertRaises(sqlite3.DatabaseError) as caught:
            coding_evidence.ensure_fields(bad)
        self.assertEqual(type(caught.exception).__name__, "StorageUnavailable")
        self.assertTrue(issubclass(coding_evidence.StorageUnavailable,
                                   sqlite3.DatabaseError))

    def test_s2i_programming_error_not_disguised_as_503(self):
        """ProgrammingError 是调用写法缺陷，不得伪装成库不可用 503。

        先例：tables_api 复审 #05、screening_arbitration 的
        ``except ProgrammingError: raise``。
        """
        task_id, _info, _db = self._new_task()
        from coscreen import coding_evidence
        defect = sqlite3.ProgrammingError("Cannot operate on a closed database")
        with mock.patch.object(coding_evidence, "field_rows", side_effect=defect):
            resp = self.raw.get(f"/api/tasks/{task_id}/coding/dimensions",
                                headers=self._auth())
        self.assertNotEqual(resp.status_code, 503,
                            "ProgrammingError 不得被映射成库不可用 503")
        self.assertEqual(resp.status_code, 500, resp.text)

    # ==================================================================
    # 零行为变更（绿侧常真；红侧同真——钉正常路径不回归）
    # ==================================================================
    def test_healthy_paths_unchanged(self):
        task_id, _info, _db = self._new_task()
        dims = self.client.get(f"/api/tasks/{task_id}/coding/dimensions",
                               headers=self._auth())
        self.assertEqual(dims.status_code, 200, dims.text)
        self.assertIn("dimensions", dims.json())
        self.assertIn("mapping_issues", dims.json())
        scheme = self.client.get(f"/api/tasks/{task_id}/coding/scheme",
                                 headers=self._auth())
        self.assertEqual(scheme.status_code, 200, scheme.text)
        self.assertEqual(scheme.json()["version"], 1)
        settings = self.client.get(f"/api/tasks/{task_id}/workflow-settings",
                                   headers=self._auth())
        self.assertEqual(settings.status_code, 200, settings.text)
        self.assertIn("stages", settings.json())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
