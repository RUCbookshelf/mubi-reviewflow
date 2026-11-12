# -*- coding: utf-8 -*-
"""review67 M-12 · 检索式只读端点零磁盘写钉（readonly=True 复活死参数）。

- 新任务（无 search_strategies.sqlite3）：GET /strategies、GET /snapshots、
  GET /appendix、POST /prisma-prefill 逐文件零变化——此前首读即在任务目录
  建库（探针 probe_w3_get_writes.py 实测 size=28672 落盘）。
- ``_library`` 读分支必须传 ``readonly=True``（core 契约：不建库、不跑 DDL、
  不迁移；库缺失返回空内存库）。
- 带 legacy ``living_review.json`` 的任务：**普通参与者** GET 零写（此前任意
  参与者 GET 都触发一次性迁移写：.bak + meta 标记 + 行写入）；**属主** GET
  保留「首次访问触发一次性迁移」的既有产品行为（test_2_1_5_w3_strategy.py
  两颗钉：迁移成功 + 坏 .bak 409），迁移完成后属主 GET 同样零写。
- 写端点回归：POST /strategies 仍建库写行（readonly 不得泄漏进写分支）。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

fixture = tempfile.TemporaryDirectory(prefix="rf_r67_ssro_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import search_strategy as core  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend.main import DATA_DIR, app  # noqa: E402


def snapshot_dir(task_dir: Path) -> dict[str, tuple[int, int]]:
    """任务目录逐文件指纹（存在性 + size + mtime_ns）——零写判据。"""
    out = {}
    for path in sorted(task_dir.rglob("*")):
        if path.is_file():
            stat = path.stat()
            out[str(path.relative_to(task_dir))] = (stat.st_size, stat.st_mtime_ns)
    return out


class ReadonlyGetsAreZeroWriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        cls.owner, cls.auth = _make_user("r67ssro-owner")
        cls.member, cls.member_auth = _make_user("r67ssro-member")

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        response = self.client.post(
            "/api/tasks", json={"name": f"r67-ssro-{self._testMethodName}"},
            headers=self.auth)
        self.assertIn(response.status_code, (200, 201), response.text)
        self.task = response.json()["task_id"]
        info = tasks_mod.find_task(self.task, DATA_DIR)
        self.task_dir = Path(info.dir)
        invite = self.client.post(
            f"/api/tasks/{self.task}/collaboration/invite",
            json={"username": self.member["username"], "role": "coder"},
            headers=self.auth)
        self.assertEqual(invite.status_code, 201, invite.text)
        self.base = f"/api/tasks/{self.task}/search-strategy"

    def test_fresh_task_readonly_endpoints_write_nothing(self):
        """新任务四个读端点：任务目录逐文件零变化（不建库、不写 mtime）。"""
        probes = [
            ("GET", f"{self.base}/strategies", None, 200),
            ("GET", f"{self.base}/snapshots", None, 200),
            ("GET", f"{self.base}/appendix?format=text", None, 409),  # no_final
            ("POST", f"{self.base}/prisma-prefill", {}, 200),
        ]
        for method, path, body, expected in probes:
            with self.subTest(path=path):
                before = snapshot_dir(self.task_dir)
                response = self.client.request(
                    method, path, headers=self.auth, json=body)
                self.assertEqual(response.status_code, expected, response.text)
                self.assertEqual(
                    snapshot_dir(self.task_dir), before,
                    f"{method} {path} 产生磁盘写（review67 M-12）")
        # 空库返回形状：strategies 空、migration 无（内存库口径）
        listed = self.client.get(f"{self.base}/strategies",
                                 headers=self.auth).json()
        self.assertEqual(listed["counts"]["total"], 0)
        self.assertIsNone(listed["migration"])

    def test_read_branch_passes_readonly_true(self):
        """复活死参数钉：读分支 ensure_library(..., readonly=True)。"""
        seen = []
        real = core.ensure_library

        def spy(task_dir, **kwargs):
            seen.append(kwargs)
            return real(task_dir, **kwargs)

        with mock.patch.object(core, "ensure_library", side_effect=spy):
            response = self.client.get(f"{self.base}/strategies",
                                       headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(seen and seen[0].get("readonly") is True,
                        f"读分支未传 readonly=True：{seen}")

    def test_member_get_on_legacy_task_writes_nothing(self):
        """带未迁移 living_review.json：普通参与者 GET 零写、不触发迁移。"""
        legacy = {"search_records": [
            {"database": "pubmed", "query_string": "legacy[tiab]",
             "last_run_date": "2024-03-01", "total_hits_last": 8,
             "saved_at": "2024-03-02T00:00:00"}]}
        (self.task_dir / "living_review.json").write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        before = snapshot_dir(self.task_dir)
        response = self.client.get(f"{self.base}/strategies",
                                   headers=self.member_auth)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["counts"]["total"], 0)
        self.assertIsNone(body["migration"])
        self.assertEqual(snapshot_dir(self.task_dir), before,
                         "参与者只读 GET 触发迁移写（review67 M-12）")
        self.assertFalse(
            (self.task_dir / "living_review.json.bak").exists(),
            "参与者 GET 不得产生 legacy 备份")

    def test_owner_get_on_legacy_task_migrates_once_then_zero_write(self):
        """属主首访迁移（既有产品行为保持），迁移完成后属主 GET 也零写。"""
        legacy = {"search_records": [
            {"database": "pubmed", "query_string": "legacy[tiab]",
             "last_run_date": "2024-03-01", "total_hits_last": 8,
             "saved_at": "2024-03-02T00:00:00"}]}
        (self.task_dir / "living_review.json").write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        first = self.client.get(f"{self.base}/strategies", headers=self.auth)
        self.assertEqual(first.status_code, 200, first.text)
        body = first.json()
        self.assertEqual(body["counts"]["total"], 1)
        self.assertEqual(body["migration"]["rows_migrated"], 1)
        self.assertTrue((self.task_dir / "living_review.json.bak").is_file())
        # 迁移标记已置 → 属主后续 GET 走只读打开，逐文件零变化。
        before = snapshot_dir(self.task_dir)
        again = self.client.get(f"{self.base}/strategies", headers=self.auth)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(len(again.json()["strategies"]), 1)
        self.assertEqual(snapshot_dir(self.task_dir), before,
                         "已迁移任务的属主 GET 仍产生磁盘写")

    def test_write_endpoint_still_creates_library(self):
        """写分支回归：POST /strategies 仍正常建库写行（201）。"""
        response = self.client.post(
            f"{self.base}/strategies",
            json={"title": "主检索式", "database": "pubmed",
                  "query_text": "network meta-analysis[tiab]"},
            headers=self.auth)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(core.store_path(self.task_dir).is_file(),
                        "写端点必须创建任务级检索式库")


def _make_user(prefix):
    username = f"{prefix}@example.test"
    user = auth_mod.create_user(username, f"{prefix}-Pass-2026!")
    return user, {"Authorization": "Bearer " +
                  auth_mod.create_access_token(user["id"], user["username"])}


if __name__ == "__main__":
    unittest.main()
