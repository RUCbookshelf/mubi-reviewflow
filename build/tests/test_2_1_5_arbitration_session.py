"""2.1.5 B1 红钉：仲裁逐条盲裁决会话（先判后示 / 两道闸 / 断点续作）。

钉住产品设计定稿七条里后端可验证的部分：

1. 队列零泄露：``GET .../screening-arbitrations/session`` 的载荷绝不包含
   任何筛选员姓名、账号、``decision_*`` 字段或其单篇决定；汇总只有聚合
   一致率/κ（匿名配对标签）。
2. 先判后示：未裁决条 reveal -> 结构化 409；裁决后才返回各筛选员原决定
   与理由。
3. r18 两道闸逐条生效：``maybe`` -> 400 ``arbitration_decision_invalid``；
   ``exclude`` 无理由 -> 400 ``arbitration_reason_required``；被拒条目保持
   pending 且不落任何仲裁记录。
4. 断点续作：``decide`` 两条后重新 ``open``，进度与已裁决条目原样保留。
5. ``finish`` 必须全裁决才成功，落盘记录与既有 append-only 格式逐字段
   一致（含全员决策列）；未完成 -> 结构化 409；重复 finish 幂等。
6. stage2 同口径：读 stage2_decisions、条目带 ``fulltext_available``。
7. 权限/双盲/503 守卫与 r12/r14 既有端点复用同一函数。

运行：pytest build/tests/test_2_1_5_arbitration_session.py
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 与 r12/r18 同法：先设环境再导入 main（多测试模块共享同一 main 实例，
# 任务级隔离由每用例新建任务保证）。
fixture = tempfile.TemporaryDirectory(prefix="rf_215b_arb_session_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

from custom_backend import auth as auth_mod
from custom_backend import main as main_mod
from custom_backend.main import app, DATA_DIR
from coscreen import db as db_mod
from coscreen import arbitration_session as session_mod
from coscreen import screening_arbitration as arbitration_mod
from coscreen import task_collaboration as collaboration_mod
from coscreen import tasks as tasks_mod
from coscreen.models import Article

SOCIAL_KEYS = ("K1", "K2", "K3")

ARTICLES = [
    Article(zotero_key="K1", title="Study K1", journal="Journal One", year=2020,
            doi="10.1000/k1", abstract="Abstract for K1"),
    Article(zotero_key="K2", title="Study K2", journal="Journal Two", year=2021,
            doi="10.1000/k2", abstract="Abstract for K2"),
    Article(zotero_key="K3", title="Study K3", journal="Journal Three", year=2022,
            doi="10.1000/k3", abstract="Abstract for K3"),
]


def _template_db_bytes() -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "user.db"
        db_mod.init_db(path)
        db_mod.upsert_articles(path, ARTICLES, [])
        return path.read_bytes()


_TEMPLATE_DB_BYTES = _template_db_bytes()


class ArbitrationSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        for i in range(5):
            user = auth_mod.create_user(f"b1arb{i}@example.test", "B1-Arb-Test-2026")
            cls.users.append({
                "user_id": user["id"],
                "username": user["username"],
                "slug": tasks_mod.slugify_screener(user["username"]),
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
            response = self.client.post(
                f"/api/tasks/{self.task}/collaboration/invite",
                json={"username": self.users[index]["username"], "role": role},
                headers=self.auth())
            self.assertEqual(response.status_code, 201, response.text)
        self.ctx = app.state.review_workflow.context(self.task, self.users[0])
        self.owner_db = Path(self.ctx.owner_db)

    # ---- 夹具（与 r12 服务端夹具同法） ---------------------------------

    def _screener_db(self, index: int) -> Path:
        return self.info.dir / f"{self.users[index]['slug']}.db"

    def _seed(self, index: int, decisions: dict[str, str], *, stage: str = "stage1") -> Path:
        path = self._screener_db(index)
        if not path.exists():
            path.write_bytes(_TEMPLATE_DB_BYTES)
        db_mod.upsert_articles(path, ARTICLES, [])
        for key, decision in decisions.items():
            reason = "人群不符" if decision == "exclude" else ""
            if stage == "stage1":
                db_mod.save_decision(path, key, decision, exclusion_reason=reason, notes="")
            else:
                from coscreen import fulltext as fulltext_mod
                fulltext_mod.save_stage2(path, key, decision, exclusion_reason=reason, notes="")
        return path

    def _seed_three(self, *, pause: bool = True, stage: str = "stage1") -> None:
        self._seed(0, {"K1": "include", "K2": "exclude", "K3": "include"}, stage=stage)
        self._seed(1, {"K1": "include", "K2": "include", "K3": "include"}, stage=stage)
        self._seed(2, {"K1": "exclude", "K2": "exclude", "K3": "include"}, stage=stage)
        if pause:
            self._pause_stages()

    def _pause_stages(self) -> None:
        with closing(sqlite3.connect(self.owner_db)) as conn, conn:
            row = conn.execute("SELECT stages_json FROM workflow_state").fetchone()
            stages = json.loads(row[0])
            for value in stages.values():
                value["mode"] = "paused"
            conn.execute("UPDATE workflow_state SET stages_json=?", (json.dumps(stages),))

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
    def _exclusive_lock(self, path: Path):
        """模拟外部进程独占筛选员个人库（rollback journal 下真正锁忙）。"""
        with closing(sqlite3.connect(path)) as setup:
            setup.execute("PRAGMA journal_mode=DELETE")
        holder = sqlite3.connect(path, timeout=1)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            yield
        finally:
            holder.rollback()
            holder.close()

    # ---- HTTP 助手 -----------------------------------------------------

    def _session_url(self) -> str:
        return f"/api/tasks/{self.task}/screening-arbitrations/session"

    def _open(self, *, stage: str = "stage1", actor: int = 0):
        return self.client.get(self._session_url(), params={"stage": stage},
                               headers=self.auth(actor))

    def _decide(self, key: str, decision, *, reason=None, stage: str = "stage1",
                actor: int = 0):
        return self.client.post(
            self._session_url() + "/item",
            json={"stage": stage, "zotero_key": key, "decision": decision,
                  "reason": reason},
            headers=self.auth(actor))

    def _reveal(self, key: str, *, stage: str = "stage1", actor: int = 0):
        return self.client.get(
            self._session_url() + "/reveal",
            params={"stage": stage, "zotero_key": key}, headers=self.auth(actor))

    def _finish(self, *, stage: str = "stage1", actor: int = 0):
        return self.client.post(self._session_url() + "/finish",
                                json={"stage": stage}, headers=self.auth(actor))

    def _store_path(self) -> Path:
        return collaboration_mod.shared_store_path(self.info.dir)

    def _arbitration_record_count(self) -> int:
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

    def _session_rows(self) -> int:
        path = self._store_path()
        if not path.is_file():
            return 0
        with closing(sqlite3.connect(path)) as conn:
            has_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='arbitration_session'"
            ).fetchone()
            if not has_table:
                return 0
            return conn.execute("SELECT COUNT(*) FROM arbitration_session").fetchone()[0]

    def _assert_no_leak(self, payload: dict) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        for user in self.users[:3]:
            self.assertNotIn(user["username"], text, "队列载荷泄露筛选员账号")
            self.assertNotIn(user["slug"], text, "队列载荷泄露筛选员库名")
        self.assertNotIn("decision_", text, "队列载荷泄露逐人决策列")
        self.assertNotIn('"include"', text)
        self.assertNotIn('"exclude"', text)
        self.assertNotIn("participation", text)
        # 理由同盲：筛选员的排除理由同样不得出现在队列里。
        self.assertNotIn("人群不符", text, "队列载荷泄露筛选员的排除理由")

    # ---- 1. 队列零泄露 + 逐篇阅读卡 ------------------------------------

    def test_queue_is_metadata_only_and_never_leaks_reviewers(self):
        self._seed_three()
        response = self._open()
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()

        self.assertEqual(payload["stage"], "stage1")
        self.assertEqual(payload["status"], "open")
        self._assert_no_leak(payload)

        items = payload["items"]
        # K3 一致，不入冲突队列。
        self.assertEqual([item["zotero_key"] for item in items], ["K1", "K2"])
        for item in items:
            self.assertEqual(
                set(item),
                {"zotero_key", "title", "journal", "year", "doi", "abstract", "status"},
                "stage1 条目只含阅读卡字段")
            self.assertEqual(item["status"], "pending")
        first = items[0]
        self.assertEqual(first["title"], "Study K1")
        self.assertEqual(first["journal"], "Journal One")
        self.assertEqual(first["year"], 2020)
        self.assertEqual(first["doi"], "10.1000/k1")
        self.assertEqual(first["abstract"], "Abstract for K1")

        # 汇总只聚合：计数 + 一致率/κ，无 participation、无姓名。
        summary = payload["summary"]
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["decided"], 0)
        self.assertEqual(summary["remaining"], 2)
        self.assertEqual(summary["n_conflict"], 2)
        self.assertEqual(summary["n_agree"], 1)
        self.assertIn("agreement_rate", summary)
        self.assertIn("light_kappa", summary)
        self.assertTrue(summary["pairwise_kappa"])
        self.assertTrue(all(pair["pair"].startswith("pair_") and "name" not in pair
                            for pair in summary["pairwise_kappa"]))

        # 已裁决后的队列仍然零泄露（reveal 只走 decide/reveal 通道）。
        self.assertEqual(self._decide("K1", "include").status_code, 200)
        after = self._open().json()
        self._assert_no_leak(after)
        self.assertEqual(
            {item["zotero_key"]: item["status"] for item in after["items"]},
            {"K1": "decided", "K2": "pending"})

    # ---- 2. 先判后示 ---------------------------------------------------

    def test_reveal_is_withheld_until_item_is_decided(self):
        self._seed_three()
        self.assertEqual(self._open().status_code, 200)

        hidden = self._reveal("K1")
        self.assertEqual(hidden.status_code, 409, hidden.text)
        detail = hidden.json()["detail"]
        self.assertEqual(detail["error_code"], "arbitration_session_reveal_not_ready")
        self.assertTrue(detail.get("message_key"))
        self.assertNotIn(self.users[0]["username"], hidden.text)
        self.assertNotIn("Study K1", hidden.text)

        decided = self._decide("K1", "include")
        self.assertEqual(decided.status_code, 200, decided.text)
        reveal = decided.json()["reveal"]
        self.assertEqual(reveal["zotero_key"], "K1")
        self.assertEqual(reveal["decision"], "include")
        screeners = {entry["name"]: entry for entry in reveal["screeners"]}
        self.assertEqual(set(screeners), {user["slug"] for user in self.users[:3]})
        self.assertEqual(screeners[self.users[0]["slug"]]["decision"], "include")
        self.assertEqual(screeners[self.users[2]["slug"]]["decision"], "exclude")
        self.assertEqual(screeners[self.users[2]["slug"]]["reason"], "人群不符")

        # 之后可重复读取同一条 reveal（断点续作 / 阅读卡回看）。
        again = self._reveal("K1")
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json(), reveal)

        missing = self._reveal("K3")
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(missing.json()["detail"]["error_code"],
                         "arbitration_session_item_not_found")

    # ---- 3. r18 两道闸逐条生效 -----------------------------------------

    def test_r18_gates_apply_per_item_and_rejections_do_not_persist(self):
        self._seed_three()
        self.assertEqual(self._open().status_code, 200)

        for bad in ("maybe", "", "   ", "pending", None):
            response = self._decide("K1", bad, reason="待定")
            self.assertEqual(response.status_code, 400, (bad, response.text))
            self.assertEqual(response.json()["detail"]["error_code"],
                             "arbitration_decision_invalid")

        for reason in (None, "", "   "):
            response = self._decide("K1", "exclude", reason=reason)
            self.assertEqual(response.status_code, 400, (reason, response.text))
            self.assertEqual(response.json()["detail"]["error_code"],
                             "arbitration_reason_required")

        # 被拒条保持 pending，reveal 仍不可用，且没有 append-only 记录。
        queue = {item["zotero_key"]: item["status"] for item in self._open().json()["items"]}
        self.assertEqual(queue["K1"], "pending")
        self.assertEqual(self._reveal("K1").status_code, 409)
        self.assertEqual(self._arbitration_record_count(), 0)

        ok = self._decide("K1", "exclude", reason="主题不符合")
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["reveal"]["decision"], "exclude")
        self.assertEqual(ok.json()["reveal"]["reason"], "主题不符合")

    # ---- 3b. 会话状态机与冲突集边界 -------------------------------------

    def test_actions_before_open_are_rejected_with_not_open(self):
        self._seed_three()  # 双盲守卫先放行，才轮到会话状态机
        decide = self._decide("K1", "include")
        self.assertEqual(decide.status_code, 409, decide.text)
        self.assertEqual(decide.json()["detail"]["error_code"],
                         "arbitration_session_not_open")
        self.assertEqual(self._reveal("K1").json()["detail"]["error_code"],
                         "arbitration_session_not_open")
        finish = self._finish()
        self.assertEqual(finish.status_code, 409, finish.text)
        self.assertEqual(finish.json()["detail"]["error_code"],
                         "arbitration_session_not_open")
        self.assertEqual(self._arbitration_record_count(), 0)

    def test_decide_rejects_key_outside_conflict_set(self):
        self._seed_three()
        self.assertEqual(self._open().status_code, 200)
        response = self._decide("K3", "include")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"]["error_code"],
                         "arbitration_session_item_not_found")
        queue = {item["zotero_key"]: item["status"] for item in self._open().json()["items"]}
        self.assertEqual(queue, {"K1": "pending", "K2": "pending"})

    # ---- 4. 断点续作 ---------------------------------------------------

    def test_reopen_preserves_decided_items_and_progress(self):
        self._seed_three()
        self.assertEqual(self._open().status_code, 200)
        self.assertEqual(self._decide("K1", "include").status_code, 200)
        self.assertEqual(self._decide("K2", "exclude", reason="设计不符").status_code, 200)

        reopened = self._open()
        self.assertEqual(reopened.status_code, 200, reopened.text)
        payload = reopened.json()
        self.assertEqual(payload["summary"]["total"], 2)
        self.assertEqual(payload["summary"]["decided"], 2)
        self.assertEqual(payload["summary"]["remaining"], 0)
        self.assertEqual(
            {item["zotero_key"]: item["status"] for item in payload["items"]},
            {"K1": "decided", "K2": "decided"})
        # 已裁决条目重开后 reveal 仍可用，理由不丢。
        self._assert_no_leak(payload)
        reveal = self._reveal("K2").json()
        self.assertEqual(reveal["decision"], "exclude")
        self.assertEqual(reveal["reason"], "设计不符")
        # 重开不产生额外会话行（同一 task+stage+arbitrator 单例）。
        self.assertEqual(self._session_rows(), 1)

    # ---- 5. finish 全裁决才成功，格式与既有记录一致 ---------------------

    def test_finish_requires_all_decisions_and_matches_append_only_format(self):
        self._seed_three()
        self.assertEqual(self._open().status_code, 200)
        self.assertEqual(self._decide("K1", "exclude", reason="主题不符合").status_code, 200)

        incomplete = self._finish()
        self.assertEqual(incomplete.status_code, 409, incomplete.text)
        detail = incomplete.json()["detail"]
        self.assertEqual(detail["error_code"], "arbitration_session_incomplete")
        self.assertEqual(detail["params"]["remaining"], 1)
        self.assertEqual(self._arbitration_record_count(), 0)

        self.assertEqual(self._decide("K2", "include").status_code, 200)
        finished = self._finish()
        self.assertEqual(finished.status_code, 200, finished.text)
        payload = finished.json()
        self.assertFalse(payload["already_completed"])
        self.assertEqual(payload["status"], "completed")
        record = payload["record"]

        # 与既有 append_decision_arbitration 输出逐字段一致（顶层键 + 记录内容）。
        self.assertEqual(
            set(record),
            {"id", "stage", "merge_sha256", "result_sha256", "supersedes_id",
             "arbitrator_id", "created_at", "records"})
        self.assertEqual(record["stage"], "stage1")
        self.assertEqual(record["arbitrator_id"], str(self.users[0]["user_id"]))
        self.assertEqual(payload["snapshot_id"], record["id"])

        expected_merged = arbitration_mod.merge_decision_packages(
            arbitration_mod.load_screener_decision_packages(self.info.db_files, stage="stage1"))
        resolutions = [
            {"zotero_key": "K1", "decision": "exclude", "rationale": "主题不符合"},
            {"zotero_key": "K2", "decision": "include", "rationale": ""},
        ]
        expected = json.loads(arbitration_mod._canonical_json(
            arbitration_mod.resolve_decision_conflicts(expected_merged, resolutions).to_dict("records")))
        self.assertEqual(record["records"], expected)

        rows = {row["zotero_key"]: row for row in record["records"]}
        self.assertEqual(rows["K1"]["final_decision"], "exclude")
        self.assertEqual(rows["K1"]["arbitration_status"], "adjudicated")
        self.assertEqual(rows["K1"]["arbitration_reason"], "主题不符合")
        self.assertEqual(rows["K2"]["final_decision"], "include")
        self.assertEqual(rows["K3"]["arbitration_status"], "agreed")
        for name in (user["slug"] for user in self.users[:3]):
            self.assertIn(f"decision_{name}", rows["K1"], "记录必须保留全员原始决策列")

        # 重复 finish 幂等：不追加第二条记录，返回同一快照。
        again = self._finish()
        self.assertEqual(again.status_code, 200, again.text)
        self.assertTrue(again.json()["already_completed"])
        self.assertEqual(again.json()["record"]["id"], record["id"])
        self.assertEqual(self._arbitration_record_count(), 1)

        # 完成后冻结：再裁决被结构化拒绝；重开队列保持 completed。
        blocked = self._decide("K1", "include")
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(blocked.json()["detail"]["error_code"],
                         "arbitration_session_completed")
        reopened = self._open()
        self.assertEqual(reopened.status_code, 200, reopened.text)
        self.assertEqual(reopened.json()["status"], "completed")
        self.assertEqual(reopened.json()["snapshot_id"], record["id"])
        self.assertTrue(all(item["status"] == "decided"
                            for item in reopened.json()["items"]))

    # ---- 6. stage2 同口径 + 连全文 -------------------------------------

    def test_stage2_uses_stage2_decisions_and_marks_fulltext(self):
        self._seed_three(stage="stage2")
        pdf = Path(tempfile.mkdtemp(prefix="b1arb_pdf_")) / "K1.pdf"
        pdf.write_bytes(b"%PDF-1.4\n% test fixture\n")
        with closing(sqlite3.connect(self.owner_db)) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO fulltexts(zotero_key,filename,path,sha256,n_pages,added_at) "
                "VALUES(?,?,?,?,?,?)",
                ("K1", "K1.pdf", str(pdf), "0" * 64, 0, "2026-11-08T00:00:00Z"))

        stage2 = self._open(stage="stage2")
        self.assertEqual(stage2.status_code, 200, stage2.text)
        items = {item["zotero_key"]: item for item in stage2.json()["items"]}
        self.assertEqual(set(items), {"K1", "K2"})
        self.assertTrue(items["K1"]["fulltext_available"])
        self.assertFalse(items["K2"]["fulltext_available"])
        self.assertIn("fulltext_available", items["K1"])
        self._assert_no_leak(stage2.json())

        # 全文可见性按当前裁决者自己的库判定（与既有 fulltext 端点同口径）：
        # 同一篇 K2 的 PDF 在成员 1 的库里，裁决者 1 看到可用、裁决者 0 看不到。
        pdf2 = Path(tempfile.mkdtemp(prefix="b1arb_pdf2_")) / "K2.pdf"
        pdf2.write_bytes(b"%PDF-1.4\n% test fixture\n")
        with closing(sqlite3.connect(self._screener_db(1))) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO fulltexts(zotero_key,filename,path,sha256,n_pages,added_at) "
                "VALUES(?,?,?,?,?,?)",
                ("K2", "K2.pdf", str(pdf2), "1" * 64, 0, "2026-11-08T00:00:00Z"))
        other = {item["zotero_key"]: item
                 for item in self._open(stage="stage2", actor=1).json()["items"]}
        self.assertTrue(other["K2"]["fulltext_available"])
        self.assertFalse(other["K1"]["fulltext_available"])

        self.assertEqual(self._decide("K1", "include", stage="stage2").status_code, 200)
        self.assertEqual(self._decide("K2", "include", stage="stage2").status_code, 200)
        finished = self._finish(stage="stage2")
        self.assertEqual(finished.status_code, 200, finished.text)
        record = finished.json()["record"]
        self.assertEqual(record["stage"], "stage2")
        row = next(row for row in record["records"] if row["zotero_key"] == "K1")
        self.assertEqual(row["final_decision"], "include")
        self.assertEqual(row["arbitration_status"], "adjudicated")

    # ---- 7. 守卫复用：权限 / 双盲 / 模式 / 503 -------------------------

    def test_requires_owner_or_adjudicator_like_existing_endpoints(self):
        self._seed_three()
        outsider = self._open(actor=3)
        self.assertEqual(outsider.status_code, 403, outsider.text)
        reconciler = self._open(actor=1)
        self.assertEqual(reconciler.status_code, 200, reconciler.text)

    def test_double_blind_guard_blocks_queue_without_member_content(self):
        self._seed_three(pause=False)
        response = self._open()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertNotIn("K1", response.text)
        self.assertNotIn("decision_", response.text)
        self.assertNotIn('"include"', response.text)
        self.assertNotIn('"exclude"', response.text)
        self.assertEqual(self._session_rows(), 0, "双盲未暂停时不得建立会话")

    def test_desktop_mode_returns_404(self):
        self._seed_three()
        with self._mode("desktop"):
            response = self._open()
        self.assertEqual(response.status_code, 404, response.text)

    def test_locked_screener_library_returns_503_without_partial_session(self):
        self._seed_three()
        with self._exclusive_lock(self._screener_db(2)), mock.patch.object(
                arbitration_mod, "SCREENER_READ_TIMEOUT", 0.2):
            response = self._open()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertNotIn("K1", response.text)
        self.assertNotIn('"include"', response.text)
        self.assertNotIn('"exclude"', response.text)
        self.assertEqual(self._session_rows(), 0,
                         "锁忙时不得落半成品会话/缺员冲突集")

    def test_finish_with_locked_member_returns_503_and_keeps_session_open(self):
        self._seed_three()
        self.assertEqual(self._open().status_code, 200)
        self.assertEqual(self._decide("K1", "include").status_code, 200)
        self.assertEqual(self._decide("K2", "include").status_code, 200)
        from coscreen import screening_arbitration
        with self._exclusive_lock(self._screener_db(2)), mock.patch.object(
                screening_arbitration, "SCREENER_READ_TIMEOUT", 0.2):
            response = self._finish()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(self._arbitration_record_count(), 0)
        # 会话仍是 open，进度保留，可稍后重试。
        reopened = self._open()
        self.assertEqual(reopened.status_code, 200, reopened.text)
        self.assertEqual(reopened.json()["status"], "open")
        self.assertEqual(reopened.json()["summary"]["decided"], 2)

    def test_store_lock_during_finish_is_503_and_session_stays_open(self):
        """归档写入遇到协作库锁忙/IO 故障：503 重试语义，绝不 400 透出底层错误。"""
        self._seed_three()
        self.assertEqual(self._open().status_code, 200)
        self.assertEqual(self._decide("K1", "include").status_code, 200)
        self.assertEqual(self._decide("K2", "include").status_code, 200)
        with mock.patch.object(
                session_mod, "append_decision_arbitration",
                side_effect=sqlite3.OperationalError("database is locked")):
            response = self._finish()
        self.assertEqual(response.status_code, 503, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "arbitration_session_store_unavailable")
        self.assertNotIn("database is locked", response.text)
        self.assertEqual(self._arbitration_record_count(), 0)
        reopened = self._open()
        self.assertEqual(reopened.status_code, 200, reopened.text)
        self.assertEqual(reopened.json()["status"], "open")
        self.assertEqual(reopened.json()["summary"]["decided"], 2)

    # ---- 8. 路由顺序：/session 不被 /{record_id} 吞掉 -------------------

    def test_session_route_is_not_shadowed_by_record_wildcard(self):
        self._seed_three()
        # 注册顺序上 /{record_id} 在前；若未提升，这里会是 422 而非 200。
        self.assertEqual(self._open().status_code, 200)
        # 既有的记录详情路由行为不变：不存在的整数 -> 404。
        missing = self.client.get(
            f"/api/tasks/{self.task}/screening-arbitrations/999999",
            headers=self.auth())
        self.assertEqual(missing.status_code, 404, missing.text)


if __name__ == "__main__":
    unittest.main()
