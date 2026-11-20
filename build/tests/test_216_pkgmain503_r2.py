"""包 main503-r2：main.py 六处 sqlite3.connect 站点 503/词条收口。

任务来源：I+L 复审后置项——L10336 ``_collect_manuscript_data`` 真缺口必补，
其余五站（roster×2 / ``_sqlite_tables_present`` / register×2）第二轮统一词条口径。

修复口径（逐站，见 REPORT.md 对照表）：
1. ``_collect_manuscript_data``：DB 读取块整体 try/except -> 503 +
   ``SCREENER_LIBRARY_UNAVAILABLE_MESSAGE``（G/I/L 同款；稿件两端点不再 500）。
2/3. ``_read_task_roster_snapshot`` 两连接：站内捕 ``sqlite3.DatabaseError``
   返回 ``("unavailable", [])``——与 ``_task_roster_projection`` 外层既有降级
   同口径（status=unavailable、省略 active_screeners），不新增 503。
4. ``_sqlite_tables_present``：既有上层 ``_ensure_owner_team_storage``
   ``except (sqlite3.Error, OSError)`` typed 503 等价保护，仅对齐词条说明，
   结构不改（站内仍抛 DatabaseError，由上层收口）。
5/6. ``_register_fresh_team_protocol`` 两连接：站内收口为 503 + 标准词条；
   两调用点 ``except Exception`` 记日志继续的「登记失败不阻塞创建/rebind」
   语义不变（由 create 任务仍 201 的断言钉住）。

红侧：本文件在未修底版（candidate/main.before.py 字节）上跑，站点直抛
``sqlite3.DatabaseError`` / 稿件端点全局 500；绿侧：修后 503 + 标准词条。
运行（箱内 run 树，venv312）：
    cd E:/rf216-main503-r2-20261120/run1
    COBOOKSHELF_DATA_DIR=... REVIEWFLOW_MODE=server \
      python -I -B -m pytest build/tests/test_216_pkgmain503_r2.py -q
"""
from __future__ import annotations

import importlib
import sqlite3
import unittest
import uuid
from pathlib import Path
from unittest import mock

OWNER = "m503owner"
_CORRUPT = b"this is not a sqlite database\x00\x01\x02" * 4


class Main503Round2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        # 红侧形状观测用：不向上抛服务端异常，得到真实 HTTP 500。
        cls.raw_client = TestClient(main_mod.app, raise_server_exceptions=False)
        cls.raw_client.__enter__()
        cls.main_mod = main_mod
        cls.auth_mod = auth_mod
        user = auth_mod.create_user(OWNER, "Main503-R2-Tests-2026")
        cls.user = {"user_id": user["id"], "username": user["username"],
                    "token": auth_mod.create_access_token(
                        user["id"], user["username"])}
        cls.tasks_mod = importlib.import_module("coscreen.tasks")

    @classmethod
    def tearDownClass(cls):
        cls.raw_client.__exit__(None, None, None)
        cls.client.__exit__(None, None, None)

    def _auth(self):
        return {"Authorization": "Bearer " + self.user["token"]}

    def _new_task(self):
        resp = self.client.post(
            "/api/tasks", json={"name": "m503-" + uuid.uuid4().hex[:8]},
            headers=self._auth())
        self.assertEqual(resp.status_code, 201, resp.text)
        task_id = resp.json()["task_id"]
        info = self.tasks_mod.find_task(task_id, self.main_mod.DATA_DIR)
        self.assertIsNotNone(info)
        slug = self.tasks_mod.slugify_screener(self.user["username"])
        return info, info.dir / f"{slug}.db"

    @staticmethod
    def _make_corrupt(path: Path) -> None:
        path.write_bytes(_CORRUPT)

    # ------------------------------------------------------------------
    # 站 1：_collect_manuscript_data —— 真缺口（修复前端点 500）
    # ------------------------------------------------------------------
    def test_s1_manuscript_preview_corrupt_db_is_503(self):
        info, owner_db = self._new_task()
        self._make_corrupt(owner_db)
        resp = self.raw_client.get(
            f"/api/tasks/{info.task_id}/analysis/manuscript/preview",
            headers=self._auth())
        self.assertEqual(resp.status_code, 503, resp.text)
        self.assertEqual(resp.json()["detail"],
                         self.main_mod.SCREENER_LIBRARY_UNAVAILABLE_MESSAGE)

    def test_s1_manuscript_generate_corrupt_db_is_503(self):
        info, owner_db = self._new_task()
        self._make_corrupt(owner_db)
        resp = self.raw_client.post(
            f"/api/tasks/{info.task_id}/analysis/manuscript/generate",
            json={"format": "markdown", "sections": ["all"],
                  "include_figures": False, "reference_style": "ama"},
            headers=self._auth())
        self.assertEqual(resp.status_code, 503, resp.text)
        self.assertEqual(resp.json()["detail"],
                         self.main_mod.SCREENER_LIBRARY_UNAVAILABLE_MESSAGE)

    # ------------------------------------------------------------------
    # 站 2：_read_task_roster_snapshot（协作库连接）
    # ------------------------------------------------------------------
    def test_s2_roster_collab_connect_database_error_degrades(self):
        info, _owner_db = self._new_task()
        collab = info.dir / "collaboration.db"
        self.assertTrue(collab.is_file(), "前置失败：建任务应已建协作库")
        target = collab.resolve().as_uri() + "?mode=ro"
        real_connect = sqlite3.connect

        def scoped(database, *args, **kwargs):
            if str(database) == target:
                raise sqlite3.DatabaseError("file is not a database")
            return real_connect(database, *args, **kwargs)

        with mock.patch.object(self.main_mod.sqlite3, "connect", new=scoped):
            status, participants = self.main_mod._read_task_roster_snapshot(info)
        self.assertEqual((status, participants), ("unavailable", []))
        # 真实坏库字节（语句级失败）仍由外层既有降级兜底——回归不回归。
        self._make_corrupt(collab)
        projection = self.main_mod._task_roster_projection(info)
        self.assertEqual(projection,
                         {"active_screeners_status": "unavailable"})

    # ------------------------------------------------------------------
    # 站 3：_read_task_roster_snapshot（属主库连接）
    # ------------------------------------------------------------------
    def test_s3_roster_owner_connect_database_error_degrades(self):
        info, owner_db = self._new_task()
        target = owner_db.resolve().as_uri() + "?mode=ro"
        real_connect = sqlite3.connect

        def scoped(database, *args, **kwargs):
            if str(database) == target:
                raise sqlite3.DatabaseError("file is not a database")
            return real_connect(database, *args, **kwargs)

        with mock.patch.object(self.main_mod.sqlite3, "connect", new=scoped):
            status, participants = self.main_mod._read_task_roster_snapshot(info)
        self.assertEqual((status, participants), ("unavailable", []))

    # ------------------------------------------------------------------
    # 站 4：_sqlite_tables_present —— 站内原样，上层 typed 503 等价保护
    # ------------------------------------------------------------------
    def test_s4_station_raises_raw_and_upper_maps_typed_503(self):
        info, owner_db = self._new_task()
        self._make_corrupt(owner_db)
        # 红侧锚：站内不捕获（既有等价保护在上层），直调抛 DatabaseError。
        with self.assertRaises(sqlite3.DatabaseError):
            self.main_mod._sqlite_tables_present(owner_db)
        # 绿侧：真实 HTTP team-progress（属主 can_manage -> _ensure_owner_
        # team_storage 就绪检查）如实 typed 503，details 携带异常族。
        resp = self.client.get(
            f"/api/tasks/{info.task_id}/team-progress", headers=self._auth())
        self.assertEqual(resp.status_code, 503, resp.text)
        body = resp.json()["detail"]
        self.assertEqual(body["error_code"], "team_owner_storage_unprepared")
        self.assertEqual(body["message_key"], "team.owner_storage_unprepared")
        self.assertIn("readiness check failed", body["details"])
        self.assertIn("DatabaseError", body["details"])

    # ------------------------------------------------------------------
    # 站 5：_register_fresh_team_protocol（属主库连接）
    # ------------------------------------------------------------------
    def test_s5_register_owner_corrupt_is_typed_503(self):
        info, owner_db = self._new_task()
        self._make_corrupt(owner_db)
        with self.assertRaises(self.main_mod.HTTPException) as caught:
            self.main_mod._register_fresh_team_protocol(info, self.user)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail,
                         self.main_mod.SCREENER_LIBRARY_UNAVAILABLE_MESSAGE)

    # ------------------------------------------------------------------
    # 站 6：_register_fresh_team_protocol（协作库登记连接）
    # ------------------------------------------------------------------
    def test_s6_register_collab_connect_corrupt_is_typed_503(self):
        info, _owner_db = self._new_task()
        collab = info.dir / "collaboration.db"
        self._make_corrupt(collab)
        # 仅跳过前置 prepare（本用例目标=登记连接本身）；prepare 自身对坏库
        # 的失败面不在本包六站口径内（另见 REPORT 遗留节）。
        with mock.patch(
                "custom_backend.team_source_lifecycle"
                ".prepare_team_protocol_storage"):
            with self.assertRaises(self.main_mod.HTTPException) as caught:
                self.main_mod._register_fresh_team_protocol(info, self.user)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail,
                         self.main_mod.SCREENER_LIBRARY_UNAVAILABLE_MESSAGE)

    # ------------------------------------------------------------------
    # 语义保持：登记失败不阻塞创建（调用点 except Exception 继续）
    # ------------------------------------------------------------------
    def test_register_failure_does_not_block_task_creation(self):
        with mock.patch.object(
                self.main_mod, "_register_fresh_team_protocol",
                side_effect=self.main_mod.HTTPException(
                    503, self.main_mod.SCREENER_LIBRARY_UNAVAILABLE_MESSAGE)):
            resp = self.client.post(
                "/api/tasks", json={"name": "m503-defer-" + uuid.uuid4().hex[:8]},
                headers=self._auth())
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertTrue(resp.json()["task_id"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
