# -*- coding: utf-8 -*-
"""2.1.5 G4 遗留修订 · I-18 锁定钉：server 模式 legacy 任务实际返回哪个键。

背景（交接文档 §4.5 I-18）：W2/W3/W5 契约把「server 模式 legacy（无属主记录）
任务」的 409 写成各模块自己的 ``*.legacy_owner_unset``；W8/W9 契约为首的实测
结论是：任务级请求在**到达端点之前**就被 ``require_user`` 的全局闸门以
``409 collaboration_owner_unset`` + ``message_key=coding.collaboration_owner_unset``
拦下（``custom_backend/main.py:930-976``、``:1570-1577``）。

本文件用实测把三个模块的代表端点钉在**真实可观测行为**上：

- W2  ``GET /api/tasks/{id}/pilot/settings``
- W3  ``GET /api/tasks/{id}/search-strategy/strategies``
- W5  ``GET /api/tasks/{id}/package/preflight``

排在闸门前的读端点也不例外（这是「一切任务级请求」的直接证据）。三模块的
``pilot./search_strategy./task_package.legacy_owner_unset`` 因此是**防御性代码**：
server 模式被全局闸门抢先、desktop 模式 legacy 按既有口径放行，均不可达。
契约按本文件更正（I-18）。

运行：
    python -m pytest build/tests/test_2_1_5_g4_followups.py -q
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

fixture = tempfile.TemporaryDirectory(prefix="rf_g4fix_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import tasks as tasks_mod  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend.main import DATA_DIR, app  # noqa: E402

#: 三个模块的代表端点（读路径；写路径同样被全局闸门拦下）。
ENDPOINTS = (
    ("w2-pilot", "/api/tasks/{task_id}/pilot/settings"),
    ("w3-search-strategy", "/api/tasks/{task_id}/search-strategy/strategies"),
    ("w5-task-package", "/api/tasks/{task_id}/package/preflight"),
)


class LegacyGlobalGatePins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        cls.user = auth_mod.create_user(
            "g4followups@example.test", "G4Follow-Pass-2026!")
        cls.headers = {"Authorization": "Bearer " + auth_mod.create_access_token(
            cls.user["id"], cls.user["username"])}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        response = self.client.post(
            "/api/tasks", json={"name": "g4fix-legacy"}, headers=self.headers)
        self.assertIn(response.status_code, (200, 201), response.text)
        self.task = response.json()["task_id"]
        self.info = tasks_mod.find_task(self.task, DATA_DIR)

    def _make_legacy(self):
        """把任务变成 legacy：删掉属主行（run 在库可读，但无属主记录）。"""
        collab = self.info.dir / "collaboration.db"
        with closing(sqlite3.connect(str(collab))) as conn:
            conn.execute("DELETE FROM collaboration_task")
            conn.commit()

    def test_module_representatives_return_the_global_legacy_key(self):
        self._make_legacy()
        for label, template in ENDPOINTS:
            path = template.format(task_id=self.task)
            response = self.client.get(path, headers=self.headers)
            self.assertEqual(response.status_code, 409, f"{label}: {response.text}")
            detail = response.json()["detail"]
            self.assertEqual(detail["error_code"], "collaboration_owner_unset",
                             f"{label}: {detail}")
            self.assertEqual(detail["message_key"],
                             "coding.collaboration_owner_unset", f"{label}: {detail}")
            # 模块自有 legacy 键在此路径下不可达
            for module_key in ("pilot.legacy_owner_unset",
                               "search_strategy.legacy_owner_unset",
                               "task_package.legacy_owner_unset"):
                self.assertNotEqual(detail["message_key"], module_key, label)


def tearDownModule():
    fixture.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
