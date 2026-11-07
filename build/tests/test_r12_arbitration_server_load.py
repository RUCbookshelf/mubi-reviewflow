"""R1（2026-11-07 规格）：联网团队模式「一键加载全员决策」进入 κ+冲突仲裁。

覆盖验收条件：
- 纯函数 load_screener_decision_packages 读取任务目录内各 {slug}.db 的决策，
  输出 {筛选员名: DataFrame}（列形状对齐决策 CSV）；无决策/损坏库剔除；
  同名冲突按确定性规则保留；有效成员 <2 或 >9 与 CSV 2-9 份同口径；
  绝不写入任何筛选员个人库。
- GET /api/tasks/{task_id}/screening-arbitrations/server-merge 与
  POST /api/merge/decisions 返回同形（键/列/κ/一致率/冲突表一致）。
- 权限：非属主且无 consensus:write 裁决权 -> 403；裁决员可读。
- 双盲守卫：team 任务且任一 stage mode != paused -> 409，响应不携带成员判定。
- desktop 模式 -> 404（前端同时隐藏入口）。
- stage2 读取 stage2_decisions。
- 保存路径：JSON server_source 分支由后端现取合并表，CSV 上传路径原样可用。
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

# 与 test_collaboration_hardening 同法：先设环境再导入 main（多测试模块共享
# 同一 main 实例时以首个导入为准；任务级隔离由每用例新建任务保证）。
fixture = tempfile.TemporaryDirectory(prefix="rf_r12_arb_server_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

from custom_backend import auth as auth_mod
from custom_backend.main import app, DATA_DIR
from coscreen import db as db_mod
from coscreen import fulltext as fulltext_mod
from coscreen import review_workflow
from coscreen import tasks as tasks_mod
from coscreen.export import export_decisions, export_stage2_decisions
from coscreen.models import Article

SOCIAL_KEYS = ("K1", "K2", "K3")


def _template_db_bytes() -> bytes:
    """含 3 篇文献的空筛选库（每用例复制后写入各自的决策）。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "user.db"
        db_mod.init_db(path)
        db_mod.upsert_articles(
            path, [Article(zotero_key=key, title=f"Study {key}") for key in SOCIAL_KEYS], [])
        return path.read_bytes()


_TEMPLATE_DB_BYTES = _template_db_bytes()


class ArbitrationServerLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        # 不走 HTTP 注册：多测试模块共享限流桶会撞 429（同既有服务端夹具）。
        cls.users = []
        for i in range(5):
            user = auth_mod.create_user(f"r12arb{i}@example.test", "R12-Arb-Test-2026")
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
        # 邀请两名成员 -> 任务 latched 为 team（成员 1 为裁决员，成员 2 为筛选员）。
        for index, role in ((1, "reconciler"), (2, "coder")):
            r = self.client.post(
                f"/api/tasks/{self.task}/collaboration/invite",
                json={"username": self.users[index]["username"], "role": role},
                headers=self.auth())
            self.assertEqual(r.status_code, 201, r.text)
        # context 建好属主库/工作流表（guard 读取 stages 需要）。
        self.ctx = app.state.review_workflow.context(self.task, self.users[0])
        self.owner_db = Path(self.ctx.owner_db)

    # ---- 夹具 ---------------------------------------------------------

    def _screener_db(self, index: int) -> Path:
        slug = tasks_mod.slugify_screener(self.users[index]["username"])
        return self.info.dir / f"{slug}.db"

    def _seed(self, index: int, decisions: dict[str, str], *, stage: str = "stage1",
              exclusion_reason: str = "", notes: str = "") -> Path:
        path = self._screener_db(index)
        if not path.exists():
            path.write_bytes(_TEMPLATE_DB_BYTES)
        if index == 0:  # context 建出的属主库尚无文献，补齐同源目录。
            db_mod.upsert_articles(
                path, [Article(zotero_key=key, title=f"Study {key}") for key in SOCIAL_KEYS], [])
        for key, decision in decisions.items():
            if stage == "stage1":
                db_mod.save_decision(
                    path, key, decision,
                    exclusion_reason=exclusion_reason if decision == "exclude" else "",
                    notes=notes)
            else:
                fulltext_mod.save_stage2(
                    path, key, decision,
                    exclusion_reason=exclusion_reason if decision == "exclude" else "",
                    notes=notes)
        return path

    def _pause_stages(self) -> None:
        with closing(sqlite3.connect(self.owner_db)) as conn, conn:
            row = conn.execute("SELECT stages_json FROM workflow_state").fetchone()
            stages = json.loads(row[0])
            for value in stages.values():
                value["mode"] = "paused"
            conn.execute(
                "UPDATE workflow_state SET stages_json=?", (json.dumps(stages),))

    def _server_merge(self, *, stage: str = "stage1", actor: int = 0, params: dict | None = None):
        query = {"stage": stage, **(params or {})}
        return self.client.get(
            f"/api/tasks/{self.task}/screening-arbitrations/server-merge",
            params=query, headers=self.auth(actor))

    def _exported_csv(self, index: int, *, stage: str = "stage1") -> bytes:
        path = Path(tempfile.mkdtemp(prefix="r12arb_csv_")) / "x.csv"
        try:
            if stage == "stage1":
                export_decisions(self._screener_db(index), path)
            else:
                export_stage2_decisions(self._screener_db(index), path)
            return path.read_bytes()
        finally:
            import shutil
            shutil.rmtree(path.parent, ignore_errors=True)

    def _csv_merge(self, indexes=(0, 1, 2), *, stage: str = "stage1"):
        files = [
            ("files", (tasks_mod.slugify_screener(self.users[index]["username"]) + ".csv",
                       self._exported_csv(index, stage=stage), "text/csv"))
            for index in indexes
        ]
        return self.client.post("/api/merge/decisions", files=files, headers=self.auth())

    # ---- 1. 同形 -------------------------------------------------------

    def _seed_three(self, *, pause: bool = True) -> None:
        self._seed(0, {"K1": "include", "K2": "exclude", "K3": "include"})
        self._seed(1, {"K1": "include", "K2": "include", "K3": "include"})
        self._seed(2, {"K1": "exclude", "K2": "exclude", "K3": "include"})
        if pause:
            self._pause_stages()

    def test_server_merge_matches_csv_upload_shape(self):
        self._seed_three()
        server = self._server_merge()
        self.assertEqual(server.status_code, 200, server.text)
        uploaded = self._csv_merge()
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        server_data, csv_data = server.json(), uploaded.json()

        self.assertEqual(set(server_data), set(csv_data))
        self.assertEqual(set(server_data["summary"]), set(csv_data["summary"]))
        self.assertEqual(server_data["summary"], csv_data["summary"])
        self.assertEqual(server_data["light_kappa"], csv_data["light_kappa"])
        self.assertEqual(server_data["pairwise_kappa"], csv_data["pairwise_kappa"])
        self.assertEqual(server_data["exclusion_reasons"], csv_data["exclusion_reasons"])
        self.assertEqual(server_data["n_rows_returned"], csv_data["n_rows_returned"])

        server_rows = {row["zotero_key"]: row for row in server_data["merged"]}
        csv_rows = {row["zotero_key"]: row for row in csv_data["merged"]}
        self.assertEqual(set(server_rows), set(csv_rows))
        self.assertEqual(set(next(iter(server_rows.values()))),
                         set(next(iter(csv_rows.values()))))
        for key in SOCIAL_KEYS:
            for row in (server_rows[key], csv_rows[key]):
                self.assertEqual(row["status"], "conflict" if key != "K3" else "agree")
            self.assertEqual(server_rows[key]["n_screened"], csv_rows[key]["n_screened"])
            for name in server_data["summary"]["participation"]:
                self.assertEqual(server_rows[key][f"decision_{name}"],
                                 csv_rows[key][f"decision_{name}"], (key, name))

    # ---- 2. 有效成员不足 ----------------------------------------------

    def test_server_merge_requires_at_least_two_valid_members(self):
        self._seed(0, {"K1": "include"})
        self._pause_stages()
        r = self._server_merge()
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("2 到 9", r.json()["detail"])

    # ---- 3. 双盲守卫 ---------------------------------------------------

    def test_team_independent_screening_is_409_without_member_content(self):
        self._seed(1, {"K1": "include"})
        self._seed(2, {"K1": "exclude"})
        # 不暂停：stages 默认 mode=all -> team 独立筛选进行中。
        r = self._server_merge()
        self.assertEqual(r.status_code, 409, r.text)
        self.assertNotIn("K1", r.text)
        self.assertNotIn("decision_", r.text)
        self.assertNotIn('"include"', r.text)
        self.assertNotIn('"exclude"', r.text)

    def test_server_save_uses_same_double_blind_guard_as_server_merge(self):
        """[BLOCKING 复审] 保存路径不得绕过双盲守卫。"""
        self._seed_three(pause=False)
        merge = self._server_merge()
        self.assertEqual(merge.status_code, 409, merge.text)
        save = self.client.post(
            f"/api/tasks/{self.task}/screening-arbitrations/server-save",
            json={"stage": "stage1", "resolutions": [], "server_source": True},
            headers=self.auth())
        self.assertEqual(save.status_code, 409, save.text)
        # 409 响应只允许引导文案，绝不携带成员判定/键。
        self.assertNotIn("K1", save.text)
        self.assertNotIn("decision_", save.text)
        self.assertNotIn('"include"', save.text)
        self.assertNotIn('"exclude"', save.text)

    def test_server_save_desktop_mode_404(self):
        """[MINOR 复审] server-save 与 server-merge 同一服务器模式闸门。"""
        self._seed_three(pause=True)
        payload = {"stage": "stage1", "resolutions": [], "server_source": True}
        url = f"/api/tasks/{self.task}/screening-arbitrations/server-save"
        self.assertEqual(self.client.post(
            url, json=payload, headers=self.auth()).status_code, 201)
        with self._mode("desktop"):
            r = self.client.post(url, json=payload, headers=self.auth())
            self.assertEqual(r.status_code, 404, r.text)

    # ---- 4. 权限 -------------------------------------------------------

    def test_server_merge_requires_owner_or_adjudicator(self):
        self._seed(0, {"K1": "include"})
        self._seed(2, {"K1": "exclude"})
        self._pause_stages()
        # 非属主、非裁决员、非参与者 -> 403。
        outsider = self._server_merge(actor=3)
        self.assertEqual(outsider.status_code, 403, outsider.text)
        # 被授予 reconciler（consensus:write）的成员可读。
        reconciler = self._server_merge(actor=1)
        self.assertEqual(reconciler.status_code, 200, reconciler.text)

    # ---- 5. desktop 降级 ----------------------------------------------

    def test_desktop_mode_returns_404_and_ui_hides_entry(self):
        with self._mode("desktop"):
            r = self._server_merge()
            self.assertEqual(r.status_code, 404, r.text)
        index = (Path(__file__).resolve().parents[2]
                 / "custom_frontend" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="mergeServerLoad"', index)
        self.assertIn("runtimeMode !== 'server'", index)

    # ---- 6. stage2 口径 ------------------------------------------------

    def test_stage2_reads_stage2_decisions(self):
        self._seed(0, {"K1": "include", "K2": "include"}, stage="stage2")
        self._seed(1, {"K1": "include", "K2": "exclude"}, stage="stage2")
        self._seed(2, {"K1": "exclude", "K2": "include"}, stage="stage2")
        self._pause_stages()
        r = self._server_merge(stage="stage2")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        rows = {row["zotero_key"]: row for row in data["merged"]}
        self.assertEqual(rows["K1"]["status"], "conflict")
        self.assertEqual(rows["K1"]["n_screened"], 3)
        self.assertEqual(set(data["summary"]["participation"].values()), {2})
        # 未写初筛决策时 stage1 仍是「成员不足」口径。
        stage1 = self._server_merge(stage="stage1")
        self.assertEqual(stage1.status_code, 400, stage1.text)
        # 非法 stage 由 FastAPI 拒绝。
        self.assertEqual(self._server_merge(params={"stage": "stage3"}).status_code, 422)

    # ---- 7. >9 ---------------------------------------------------------

    def test_more_than_nine_members_rejected(self):
        self._seed(0, {"K1": "include"})
        for i in range(9):
            path = self.info.dir / f"extra{i}.db"
            path.write_bytes(_TEMPLATE_DB_BYTES)
            db_mod.save_decision(path, "K1", "exclude")
        self._pause_stages()
        r = self._server_merge()
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("2 到 9", r.json()["detail"])

    # ---- 8. 保存路径 ---------------------------------------------------

    def test_csv_upload_save_still_works_and_json_server_source_uses_db(self):
        self._seed_three()
        resolutions = [{"zotero_key": "K1", "decision": "include", "rationale": "evidence"}]
        files = [
            ("files", (tasks_mod.slugify_screener(self.users[index]["username"]) + ".csv",
                       self._exported_csv(index), "text/csv"))
            for index in (0, 1, 2)
        ]
        csv_saved = self.client.post(
            f"/api/tasks/{self.task}/screening-arbitrations",
            files=files,
            data={"stage": "stage1", "resolutions": json.dumps(resolutions)},
            headers=self.auth())
        self.assertEqual(csv_saved.status_code, 201, csv_saved.text)
        self.assertEqual(csv_saved.json()["arbitrator_id"], str(self.users[0]["user_id"]))
        row = next(r for r in csv_saved.json()["records"] if r["zotero_key"] == "K1")
        self.assertEqual(row["final_decision"], "include")

        json_saved = self.client.post(
            f"/api/tasks/{self.task}/screening-arbitrations/server-save",
            json={"stage": "stage1", "resolutions": resolutions, "server_source": True},
            headers=self.auth())
        self.assertEqual(json_saved.status_code, 201, json_saved.text)
        self.assertEqual(len(json_saved.json()["records"]), len(csv_saved.json()["records"]))
        row = next(r for r in json_saved.json()["records"] if r["zotero_key"] == "K1")
        self.assertEqual(row["final_decision"], "include")
        # 服务器来源保存的合表必须来自个人库（三位筛选员的原始投票仍在快照里）。
        self.assertEqual(
            sorted(name for name in row if name.startswith("decision_")),
            sorted(f"decision_{tasks_mod.slugify_screener(user['username'])}"
                   for user in self.users[:3]))

        # 缺少 server_source 标记：不得把任意 JSON 当作服务器合并结果保存。
        rejected = self.client.post(
            f"/api/tasks/{self.task}/screening-arbitrations/server-save",
            json={"stage": "stage1", "resolutions": resolutions},
            headers=self.auth())
        self.assertEqual(rejected.status_code, 400, rejected.text)
        # 非 conflict 行仍由现有校验拒绝。
        bad = self.client.post(
            f"/api/tasks/{self.task}/screening-arbitrations/server-save",
            json={"stage": "stage1",
                  "resolutions": [{"zotero_key": "K3", "decision": "include"}],
                  "server_source": True},
            headers=self.auth())
        self.assertEqual(bad.status_code, 400, bad.text)


class LoadScreenerDecisionPackagesTests(unittest.TestCase):
    """纯函数：只读、确定性、与 CSV 列形状对齐。"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory(prefix="r12arb_pkg_")
        self.root = Path(self._dir.name)
        self.missing = self.root / "ghost.db"

    def tearDown(self):
        self._dir.cleanup()

    def _db(self, name: str, decisions: dict[str, str], *, stage: str = "stage1") -> Path:
        path = self.root / f"{name}.db"
        path.write_bytes(_TEMPLATE_DB_BYTES)
        for key, decision in decisions.items():
            if stage == "stage1":
                db_mod.save_decision(path, key, decision)
            else:
                fulltext_mod.save_stage2(path, key, decision)
        return path

    def test_frames_align_with_decision_csv_and_skip_empty_or_corrupt(self):
        from coscreen.screening_arbitration import load_screener_decision_packages
        a = self._db("alpha", {"K1": "include", "K2": "exclude"})
        b = self._db("beta", {"K1": "exclude"})
        empty = self.root / "empty.db"
        empty.write_bytes(_TEMPLATE_DB_BYTES)  # 建库但无任何决策
        corrupt = self.root / "broken.db"
        corrupt.write_bytes(b"not a sqlite database")
        packages = load_screener_decision_packages([a, b, empty, corrupt])
        self.assertEqual(list(packages), ["alpha", "beta"])
        frame = packages["alpha"]
        self.assertEqual(list(frame.columns),
                         ["zotero_key", "decision", "exclusion_reason",
                          "notes", "tags", "screened_at"])
        self.assertEqual(frame["zotero_key"].tolist(), ["K1", "K2"])
        self.assertEqual(frame["decision"].tolist(), ["include", "exclude"])

    def test_duplicate_stem_keeps_first_path_deterministically(self):
        from coscreen.screening_arbitration import load_screener_decision_packages
        first_dir = self.root / "a"
        second_dir = self.root / "b"
        first_dir.mkdir()
        second_dir.mkdir()
        first = self._db("dup", {"K1": "include"})
        (second_dir / "dup.db").write_bytes(first.read_bytes())
        first_dir_dup = first_dir / "dup.db"
        first.rename(first_dir_dup)
        second = second_dir / "dup.db"
        # 修改 second 的内容，确认保留的是按路径排序的 first。
        db_mod.save_decision(second, "K1", "exclude")
        other = self._db("other", {"K1": "include"})
        packages = load_screener_decision_packages([second, first_dir_dup, other])
        self.assertEqual(list(packages), ["dup", "other"])
        self.assertEqual(packages["dup"]["decision"].tolist(), ["include"])

    def test_duplicate_stem_empty_first_path_is_skipped_then_second_used(self):
        """同名规则文档口径：空库先剔除，保留首个**有决策**的同名库。"""
        from coscreen.screening_arbitration import load_screener_decision_packages
        first_dir = self.root / "a"
        second_dir = self.root / "b"
        first_dir.mkdir()
        second_dir.mkdir()
        empty = first_dir / "dup.db"
        empty.write_bytes(_TEMPLATE_DB_BYTES)  # 排在前面的同名空库
        second = second_dir / "dup.db"
        second.write_bytes(_TEMPLATE_DB_BYTES)
        db_mod.save_decision(second, "K1", "include")
        other = self._db("other", {"K1": "exclude"})
        packages = load_screener_decision_packages([empty, second, other])
        self.assertEqual(list(packages), ["dup", "other"])
        self.assertEqual(packages["dup"]["decision"].tolist(), ["include"])

    def test_fewer_than_two_valid_raises_csv_equivalent_message(self):
        from coscreen.screening_arbitration import load_screener_decision_packages
        only = self._db("only", {"K1": "include"})
        with self.assertRaises(ValueError) as caught:
            load_screener_decision_packages([only, self.missing])
        self.assertIn("2 到 9", str(caught.exception))

    def test_more_than_nine_raises(self):
        from coscreen.screening_arbitration import load_screener_decision_packages
        paths = [self._db(f"s{i}", {"K1": "include"}) for i in range(10)]
        with self.assertRaises(ValueError) as caught:
            load_screener_decision_packages(paths)
        self.assertIn("2 到 9", str(caught.exception))

    def test_read_path_never_creates_or_mutates_screener_dbs(self):
        from coscreen.screening_arbitration import load_screener_decision_packages
        a = self._db("alpha", {"K1": "include", "K2": "exclude"})
        b = self._db("beta", {"K1": "exclude", "K2": "include"})
        before = {path: db_mod.list_decisions(path) for path in (a, b)}
        load_screener_decision_packages([a, b, self.missing])
        self.assertFalse(self.missing.exists(), "只读加载不得创建个人库")
        after = {path: db_mod.list_decisions(path) for path in (a, b)}
        self.assertEqual(before, after)

    def test_invalid_stage_rejected(self):
        from coscreen.screening_arbitration import load_screener_decision_packages
        with self.assertRaises(ValueError):
            load_screener_decision_packages([], stage="stage3")
