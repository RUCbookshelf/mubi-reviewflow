"""r18 红钉：仲裁提交强制二选一（纳入/排除），排除必须附理由。

用户裁定（2026-11-07）：仲裁不能保存为「暂不确定」，必须做出选择；排除
必须附理由。本钉钉住后端契约（前端下拉/必填由前端线按契约文档落地）：

1. ``maybe`` 裁决被结构化拒绝（400 ``arbitration_decision_invalid``）且不落盘；
2. ``exclude`` 未带非空理由被结构化拒绝（400 ``arbitration_reason_required``）
   且不落盘（缺键、空串、纯空白、null 同口径）；
3. ``exclude`` 带理由成功落盘；
4. ``include`` 无理由成功落盘；
5. CSV 上传保存与 server-save 两条提交路径同口径；
6. 已保存（append-only）的历史仲裁记录读取不受影响、零改写；
7. 筛选员个人投票里的 ``maybe`` 仍可合并（闸只作用于仲裁结论）。
"""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 与 test_r12_arbitration_server_load 同法：先设环境再导入 main。
fixture = tempfile.TemporaryDirectory(prefix="rf_r18_arb_binary_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

from custom_backend import auth as auth_mod
from custom_backend import main as main_mod
from custom_backend.main import app, DATA_DIR
from coscreen import db as db_mod
from coscreen import screening_arbitration as arbitration_mod
from coscreen import tasks as tasks_mod
from coscreen.export import export_decisions
from coscreen.models import Article

SOCIAL_KEYS = ("K1", "K2", "K3")


def _template_db_bytes() -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "user.db"
        db_mod.init_db(path)
        db_mod.upsert_articles(
            path, [Article(zotero_key=key, title=f"Study {key}") for key in SOCIAL_KEYS], [])
        return path.read_bytes()


_TEMPLATE_DB_BYTES = _template_db_bytes()


class ArbitrationBinaryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        for i in range(5):
            user = auth_mod.create_user(f"r18arb{i}@example.test", "R18-Arb-Test-2026")
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

    def setUp(self):
        self.task = self.client.post(
            "/api/tasks", json={"name": self._testMethodName},
            headers=self.auth()).json()["task_id"]
        self.info = tasks_mod.find_task(self.task, DATA_DIR)
        for index, role in ((1, "reconciler"), (2, "coder")):
            r = self.client.post(
                f"/api/tasks/{self.task}/collaboration/invite",
                json={"username": self.users[index]["username"], "role": role},
                headers=self.auth())
            self.assertEqual(r.status_code, 201, r.text)
        self.ctx = app.state.review_workflow.context(self.task, self.users[0])
        self.owner_db = Path(self.ctx.owner_db)

    # ---- 夹具（与 r12 服务端夹具同法） ---------------------------------

    def _screener_db(self, index: int) -> Path:
        slug = tasks_mod.slugify_screener(self.users[index]["username"])
        return self.info.dir / f"{slug}.db"

    def _seed(self, index: int, decisions: dict[str, str], *, stage: str = "stage1") -> Path:
        path = self._screener_db(index)
        if not path.exists():
            path.write_bytes(_TEMPLATE_DB_BYTES)
        if index == 0:
            db_mod.upsert_articles(
                path, [Article(zotero_key=key, title=f"Study {key}") for key in SOCIAL_KEYS], [])
        for key, decision in decisions.items():
            db_mod.save_decision(
                path, key, decision,
                exclusion_reason="人群不符" if decision == "exclude" else "",
                notes="")
        return path

    def _seed_three(self) -> None:
        self._seed(0, {"K1": "include", "K2": "exclude", "K3": "include"})
        self._seed(1, {"K1": "include", "K2": "include", "K3": "include"})
        self._seed(2, {"K1": "exclude", "K2": "exclude", "K3": "include"})
        self._pause_stages()

    def _pause_stages(self) -> None:
        with closing(sqlite3.connect(self.owner_db)) as conn, conn:
            row = conn.execute("SELECT stages_json FROM workflow_state").fetchone()
            stages = json.loads(row[0])
            for value in stages.values():
                value["mode"] = "paused"
            conn.execute("UPDATE workflow_state SET stages_json=?", (json.dumps(stages),))

    def _exported_csv(self, index: int) -> bytes:
        path = Path(tempfile.mkdtemp(prefix="r18arb_csv_")) / "x.csv"
        try:
            export_decisions(self._screener_db(index), path)
            return path.read_bytes()
        finally:
            import shutil
            shutil.rmtree(path.parent, ignore_errors=True)

    # ---- 两条提交路径 ---------------------------------------------------

    def _server_save(self, resolutions, *, stage: str = "stage1", actor: int = 0):
        return self.client.post(
            f"/api/tasks/{self.task}/screening-arbitrations/server-save",
            json={"stage": stage, "resolutions": resolutions, "server_source": True},
            headers=self.auth(actor))

    def _csv_save(self, resolutions, *, stage: str = "stage1", actor: int = 0,
                  indexes=(0, 1, 2)):
        files = [
            ("files", (tasks_mod.slugify_screener(self.users[index]["username"]) + ".csv",
                       self._exported_csv(index), "text/csv"))
            for index in indexes
        ]
        return self.client.post(
            f"/api/tasks/{self.task}/screening-arbitrations",
            files=files,
            data={"stage": stage, "resolutions": json.dumps(resolutions)},
            headers=self.auth(actor))

    def _submit_both(self, resolutions):
        return [("server-save", self._server_save(resolutions)),
                ("csv", self._csv_save(resolutions))]

    # ---- 落盘探针 -------------------------------------------------------

    def _store_path(self) -> Path:
        return Path(main_mod._arbitration_store(
            self.task, {"user_id": self.users[0]["user_id"]}))

    def _record_count(self) -> int:
        path = self._store_path()
        if not path.is_file():
            return 0
        with closing(sqlite3.connect(path)) as conn:
            has_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='screening_arbitrations'"
            ).fetchone()
            if not has_table:
                return 0
            return conn.execute("SELECT COUNT(*) FROM screening_arbitrations").fetchone()[0]

    def _latest(self, *, stage: str = "stage1", actor: int = 0):
        return self.client.get(
            f"/api/tasks/{self.task}/screening-arbitrations/latest",
            params={"stage": stage}, headers=self.auth(actor))

    # ---- 1/2. 拒绝并结构化 ---------------------------------------------

    def test_maybe_rejected_on_both_save_paths_and_not_persisted(self):
        self._seed_three()
        for label, response in self._submit_both(
                [{"zotero_key": "K1", "decision": "maybe", "rationale": "待定"}]):
            with self.subTest(path=label):
                self.assertEqual(response.status_code, 400, response.text)
                detail = response.json()["detail"]
                self.assertIsInstance(detail, dict, "拒绝体必须是结构化对象")
                self.assertEqual(detail["error_code"], "arbitration_decision_invalid")
                self.assertTrue(detail.get("message_key"), "必须带指引文案键")
                self.assertIn("纳入", detail["message"])
                self.assertIn("排除", detail["message"])
        self.assertEqual(self._record_count(), 0, "被拒的 maybe 仲裁不得落盘")
        self.assertEqual(self._latest().status_code, 404, "被拒后仍应无仲裁记录")

    def test_blank_or_unknown_decision_rejected_on_both_paths(self):
        self._seed_three()
        for bad in ("", "   ", "pending", None):
            for label, response in self._submit_both(
                    [{"zotero_key": "K1", "decision": bad, "rationale": "x"}]):
                with self.subTest(decision=bad, path=label):
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertEqual(
                        response.json()["detail"]["error_code"],
                        "arbitration_decision_invalid")
        self.assertEqual(self._record_count(), 0)

    def test_exclude_without_reason_rejected_on_both_paths_and_not_persisted(self):
        self._seed_three()
        variants = (
            {"zotero_key": "K1", "decision": "exclude"},                       # 缺键
            {"zotero_key": "K1", "decision": "exclude", "rationale": ""},      # 空串
            {"zotero_key": "K1", "decision": "exclude", "rationale": "   "},   # 纯空白
            {"zotero_key": "K1", "decision": "exclude", "rationale": None},    # null
        )
        for resolutions in variants:
            for label, response in self._submit_both([resolutions]):
                with self.subTest(resolution=resolutions, path=label):
                    self.assertEqual(response.status_code, 400, response.text)
                    detail = response.json()["detail"]
                    self.assertEqual(detail["error_code"], "arbitration_reason_required")
                    self.assertTrue(detail.get("message_key"))
                    self.assertIn("理由", detail["message"])
        self.assertEqual(self._record_count(), 0, "无理由的排除仲裁不得落盘")
        self.assertEqual(self._latest().status_code, 404)

    # ---- 3/4. 合法提交成功 ---------------------------------------------

    def test_exclude_with_reason_saved_on_both_paths(self):
        self._seed_three()
        for label, response in self._submit_both(
                [{"zotero_key": "K1", "decision": "exclude", "rationale": "主题不符合"}]):
            with self.subTest(path=label):
                self.assertEqual(response.status_code, 201, response.text)
                row = next(r for r in response.json()["records"] if r["zotero_key"] == "K1")
                self.assertEqual(row["final_decision"], "exclude")
                self.assertEqual(row["arbitration_reason"], "主题不符合")
        # 两条路径各自落成自己的 append-only 快照（合并指纹可能不同），
        # 只要都成功落盘且最近记录可读即满足「同口径」。
        self.assertGreaterEqual(self._record_count(), 1)
        self.assertEqual(self._latest().status_code, 200)

    def test_include_without_reason_saved_on_both_paths(self):
        self._seed_three()
        for label, response in self._submit_both(
                [{"zotero_key": "K1", "decision": "include"}]):
            with self.subTest(path=label):
                self.assertEqual(response.status_code, 201, response.text)
                row = next(r for r in response.json()["records"] if r["zotero_key"] == "K1")
                self.assertEqual(row["final_decision"], "include")
                self.assertEqual(row["arbitration_reason"], "")
        self.assertEqual(self._latest().status_code, 200)

    def test_rejection_shape_is_identical_on_both_paths(self):
        self._seed_three()
        responses = dict(self._submit_both(
            [{"zotero_key": "K1", "decision": "maybe"}]))
        left = responses["server-save"].json()["detail"]
        right = responses["csv"].json()["detail"]
        self.assertEqual(set(left), set(right), "两条路径的错误形状必须同口径")
        self.assertEqual(left["error_code"], right["error_code"])
        self.assertEqual(left["message_key"], right["message_key"])

    # ---- 5. 库层同口径（不依赖 HTTP） -----------------------------------

    def test_library_layer_enforces_same_contract(self):
        import pandas as pd

        frame_a = pd.DataFrame([{"zotero_key": "K1", "decision": "include"}])
        frame_b = pd.DataFrame([{"zotero_key": "K1", "decision": "exclude"}])
        merged = arbitration_mod.merge_decision_packages({"a": frame_a, "b": frame_b})
        store = Path(tempfile.mkdtemp(prefix="r18arb_store_")) / "team.db"
        with self.assertRaises(arbitration_mod.ArbitrationDecisionInvalid) as caught:
            arbitration_mod.append_decision_arbitration(
                store, stage="stage1", merged=merged,
                resolutions=[{"zotero_key": "K1", "decision": "maybe"}],
                arbitrator_id="u1")
        self.assertEqual(caught.exception.code, "arbitration_decision_invalid")
        self.assertIsNone(arbitration_mod.latest_decision_arbitration(store, "stage1"))

        with self.assertRaises(arbitration_mod.ArbitrationReasonRequired) as caught:
            arbitration_mod.append_decision_arbitration(
                store, stage="stage1", merged=merged,
                resolutions=[{"zotero_key": "K1", "decision": "exclude"}],
                arbitrator_id="u1")
        self.assertEqual(caught.exception.code, "arbitration_reason_required")
        self.assertIsNone(arbitration_mod.latest_decision_arbitration(store, "stage1"))

        saved = arbitration_mod.append_decision_arbitration(
            store, stage="stage1", merged=merged,
            resolutions=[{"zotero_key": "K1", "decision": "exclude", "rationale": "领域不符合"}],
            arbitrator_id="u1")
        self.assertEqual(saved["records"][0]["final_decision"], "exclude")

    # ---- 6. 历史记录零改写 ----------------------------------------------

    def test_historical_arbitration_record_read_unchanged(self):
        """历史快照里若已有 maybe 结论，读取路径必须原样返回、绝不因新闸被拒。"""
        self._seed_three()
        store = self._store_path()
        store.parent.mkdir(parents=True, exist_ok=True)
        legacy_records = [{
            "zotero_key": "K1",
            "final_decision": "maybe",
            "arbitration_status": "adjudicated",
            "arbitration_reason": "历史留档（2.1.4 之前）",
        }]
        records_json = json.dumps(legacy_records, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"))
        with closing(sqlite3.connect(store)) as conn, conn:
            conn.executescript(arbitration_mod._ARBITRATION_SCHEMA)
            cursor = conn.execute(
                "INSERT INTO screening_arbitrations "
                "(stage,merge_sha256,result_sha256,supersedes_id,arbitrator_id,records_json,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                ("stage1", "a" * 64, "b" * 64, None, "legacy",
                 records_json, "2026-01-01T00:00:00.000Z"))
            legacy_id = int(cursor.lastrowid)
        before = hashlib.sha256(records_json.encode("utf-8")).hexdigest()

        latest = self._latest()
        self.assertEqual(latest.status_code, 200, latest.text)
        self.assertEqual(latest.json()["id"], legacy_id)
        self.assertEqual(latest.json()["records"], legacy_records)

        single = self.client.get(
            f"/api/tasks/{self.task}/screening-arbitrations/{legacy_id}",
            headers=self.auth())
        self.assertEqual(single.status_code, 200, single.text)
        self.assertEqual(single.json()["records"], legacy_records)

        # 新保存（合法裁决）不得改写历史行；历史行的 records_json 逐字节不变。
        saved = self._server_save(
            [{"zotero_key": "K1", "decision": "include"}]).json()
        self.assertGreater(saved["id"], legacy_id)
        with closing(sqlite3.connect(store)) as conn:
            stored = conn.execute(
                "SELECT records_json, created_at, arbitrator_id FROM screening_arbitrations "
                "WHERE id=?", (legacy_id,)).fetchone()
        self.assertEqual(stored[0], records_json)
        self.assertEqual(hashlib.sha256(stored[0].encode("utf-8")).hexdigest(), before)
        self.assertEqual(stored[1], "2026-01-01T00:00:00.000Z")
        self.assertEqual(stored[2], "legacy")

    # ---- 7. 筛选员 maybe 不受影响 ---------------------------------------

    def test_screener_maybe_votes_still_merge_and_are_arbitrable(self):
        self._seed(0, {"K1": "include"})
        self._seed(1, {"K1": "maybe"})
        self._seed(2, {"K1": "exclude"})
        self._pause_stages()
        merge = self.client.get(
            f"/api/tasks/{self.task}/screening-arbitrations/server-merge",
            params={"stage": "stage1"}, headers=self.auth())
        self.assertEqual(merge.status_code, 200, merge.text)
        row = next(r for r in merge.json()["merged"] if r["zotero_key"] == "K1")
        self.assertEqual(row["status"], "conflict", "筛选员的 maybe 仍参与合并判定")
        saved = self._server_save(
            [{"zotero_key": "K1", "decision": "exclude", "rationale": "主题不符合"}])
        self.assertEqual(saved.status_code, 201, saved.text)


if __name__ == "__main__":
    unittest.main()
