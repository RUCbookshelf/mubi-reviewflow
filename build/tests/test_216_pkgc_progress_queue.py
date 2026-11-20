"""C12 包 C（P24 收尾）：写窗进度队列团队化——按键 keyed 评估验收。

修复对象：``coding_result`` team 分支的进度队列（review_workflow_api.py
:538 族）此前 ``chain_cleared_keys(team_chain_view(scope, "coding"))`` 全域
数票，对 pre-join-sealed/归档键显示错误进度（seal 已终局的键被当未清出
队、total 虚低）。修复后经 ``keyed_progress_queue``（team_screening_
runtime.py）：完成看 seal、未完成看票，与 HIGH#1 落地面（B1/B2）读写同
语义。

场景（复用 P25 矩阵 a6080470 真实建队配方，只读参考）：

    c1  pre-join-sealed 键：stage1+stage2 在 D 加入前由 A+B 全票封印
        （seal 落最后投票人 B 的成员库）；D 加入后保存 coding 值 →
        响应 progress.total 必须把该键计入队列（修复前全域数票 D 无票
        → 队列漏计）。
    c2  归档键：真实退休 API 把 B 的库改名移走、seal 归档进 collab；
        D 再保存另一键 coding 值 → 两键均经 seal（holder 库＋collab
        归档域）计入队列。
    c3  solo 不变锚：solo 任务保存 coding 值 → progress 与既有
        ``coding.coding_progress``（path 口径）逐字相等（solo 分支零
        回归）。
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import unittest
import uuid
from contextlib import closing
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in os.environ.get("PYTHONPATH", ""):
    pass
import sys

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_UPSERT_LIBRARY = (
    "INSERT INTO team_vote_revision_meta (singleton, format_version,"
    " library_id, seeded_stage1, seeded_stage2, advances)"
    " VALUES (1, 2, ?, 0, 0, 0) ON CONFLICT(singleton) DO UPDATE SET"
    " library_id=excluded.library_id")
_INSERT_ARTICLE = (
    "INSERT INTO articles (zotero_key, title, is_duplicate_of, import_order)"
    " VALUES (?, ?, NULL, ?)")
_LATCH = "UPDATE workflow_state SET team_latched=1 WHERE singleton=1"

KEY_PRE = "PKGC1"   # c1 pre-join-sealed leg
KEY_ARC = "PKGC2"   # c2 archived-into-collab leg
KEY_SOLO = "PKCG3"  # c3 solo anchor

_TEAM_KEYS = (KEY_PRE, KEY_ARC)


def _close_quietly(conn):
    try:
        conn.close()
    except Exception:
        pass


def _borrowed_writer(path):
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    return conn


class _EnvGuard:
    def __init__(self, value, restore):
        self._value = value
        self._restore = restore

    def __enter__(self):
        os.environ["REVIEWFLOW_MODE"] = self._value
        return self

    def __exit__(self, *exc_info):
        self._restore()
        return False


class PkgcProgressQueueTests(unittest.TestCase):
    """One real team (pre-join seals) + one real solo task; name order."""

    task_id = {}
    collab_path = {}
    user_db = {}

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.main_mod = main_mod
        cls.users = {}
        for name in ("pkowner", "pka", "pkb", "pkd", "pksolo"):
            user = auth_mod.create_user(name, "PkgC-Progress-2026")
            cls.users[name] = {
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(
                    user["id"], user["username"])}
        cls._build_team("main", "pkowner", ("pka", "pkb"),
                        extra_libraries=("pkd",), keys=_TEAM_KEYS)
        # pre-join stage1+stage2 seals：A+B 全票 include（seal 落最后投票人
        # B 的成员库）——D 加入前完成。
        for key in _TEAM_KEYS:
            cls._vote_setup("pka", key, "screen")
            cls._vote_setup("pkb", key, "screen")
            cls._vote_setup("pka", key, "fulltext")
            cls._vote_setup("pkb", key, "fulltext")
        # D joins AFTER the seals landed（invite + business role；协调者注册
        # D 的 source——与 E 矩阵 e 系列同法）。
        with cls._server_mode_setup():
            resp = cls.client.post(
                f"/api/tasks/{cls.task_id['main']}/collaboration/invite",
                json={"username": cls.users["pkd"]["username"],
                      "role": "coder"},
                headers=cls._auth_setup("pkowner"))
            assert resp.status_code == 201, resp.text
            resp = cls.client.put(
                f"/api/tasks/{cls.task_id['main']}/collaboration/business-role/"
                f"{cls.users['pkd']['user_id']}",
                json={"business_role": "coder"},
                headers=cls._auth_setup("pkowner"))
            assert resp.status_code == 200, resp.text
        # coding 维度：owner + D 同一维度（成员私有方案各自添加）。
        coding = importlib.import_module("coscreen.coding")
        cls.dim_id = coding.add_dimension(
            cls.user_db["main"]["pkd"], "PkgC 维度", "text", [])
        cls._build_solo()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    # ------------------------------------------------------------------
    # fixture helpers（P25 矩阵配方，只读参考）
    # ------------------------------------------------------------------
    @classmethod
    def _auth_setup(cls, name):
        return {"Authorization": "Bearer " + cls.users[name]["token"]}

    @classmethod
    def _server_mode_setup(cls):
        saved = os.environ.get("REVIEWFLOW_MODE")

        def restore():
            if saved is None:
                os.environ.pop("REVIEWFLOW_MODE", None)
            else:
                os.environ["REVIEWFLOW_MODE"] = saved
        return _EnvGuard("server", restore)

    def _server_mode(self):
        return self._server_mode_setup()

    def _auth(self, name):
        return {"Authorization": "Bearer " + self.users[name]["token"]}

    @classmethod
    def _task_dir(cls, label):
        tasks = importlib.import_module("coscreen.tasks")
        return tasks.find_task(cls.task_id[label],
                               cls.main_mod.DATA_DIR).dir

    @classmethod
    def _build_team(cls, label, owner_name, coder_names, *, keys,
                    extra_libraries=()):
        collab = importlib.import_module("coscreen.task_collaboration")
        cdb = importlib.import_module("coscreen.db")
        wf = importlib.import_module("coscreen.review_workflow")
        tasks = importlib.import_module("coscreen.tasks")
        tf = importlib.import_module("coscreen.team_terminal_facts")
        tr = importlib.import_module("coscreen.team_terminal_registry")
        receipts = importlib.import_module("coscreen.team_flow_receipts")
        coding_evidence = importlib.import_module("coscreen.coding_evidence")

        owner = cls.users[owner_name]
        owner_uid = owner["user_id"]
        task_id = cls.client.post(
            "/api/tasks", json={"name": "pkgc-" + uuid.uuid4().hex[:8]},
            headers=cls._auth_setup(owner_name)).json()["task_id"]
        info = tasks.find_task(task_id, cls.main_mod.DATA_DIR)
        task_dir = info.dir
        collab_path = collab.shared_store_path(task_dir)
        if not collab_path.is_file():
            collab.initialize_task_store(
                task_dir, task_id=task_id, owner_user_id=owner_uid,
                owner_username=owner["username"])
        collab.set_business_role(
            collab_path, actor_user_id=owner_uid, user_id=owner_uid,
            business_role="reconciler", task_id=task_id)
        slug = tasks.slugify_screener
        member_names = tuple(coder_names) + tuple(extra_libraries)
        cls.user_db[label] = {
            owner_name: task_dir / f"{slug(owner['username'])}.db"}
        cls.user_db[label].update({
            name: task_dir / f"{slug(cls.users[name]['username'])}.db"
            for name in member_names})
        for name in coder_names:
            uid = cls.users[name]["user_id"]
            collab.grant_role(collab_path, actor_user_id=owner_uid,
                              user_id=uid, role="coder", task_id=task_id)
            collab.set_business_role(
                collab_path, actor_user_id=owner_uid, user_id=uid,
                business_role="coder", task_id=task_id)
        articles = [(k, f"PkgC {k}", i) for i, k in enumerate(keys)]
        for name, path in cls.user_db[label].items():
            cdb.init_db(path)
            wf.migrate(path)
            tf.prepare_team_terminal_facts(path)
            receipts.prepare_team_flow_receipts(path)
            coding_evidence.ensure_fields(path)
            conn = sqlite3.connect(str(path), timeout=15)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                if name != owner_name:
                    conn.execute(_UPSERT_LIBRARY, (
                        "%032x" % cls.users[name]["user_id"],))
                conn.executemany(_INSERT_ARTICLE, articles)
                if name == owner_name:
                    conn.execute(_LATCH)
                conn.commit()
            finally:
                conn.close()
        tr.prepare_team_terminal_registry(collab_path)
        importlib.import_module(
            "coscreen.screening_arbitration"
        ).prepare_arbitration_attestation_storage(collab_path)
        with closing(sqlite3.connect(str(collab_path))) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT task_instance_id FROM main.team_task_protocol"
                " LIMIT 1").fetchone()
        assert row is not None, "team protocol row missing (P18)"
        instance_id = row["task_instance_id"]
        tf.prepare_team_terminal_facts(collab_path)
        receipts.prepare_team_flow_receipts(collab_path)
        for name in coder_names:
            cls._register_source(label, name, collab_path, task_dir,
                                 instance_id)
        cls.task_id[label] = task_id
        cls.collab_path[label] = collab_path
        cls.instance_id = getattr(cls, "instance_id", {})
        cls.instance_id[label] = instance_id

    @classmethod
    def _register_source(cls, label, name, collab_path, task_dir,
                         instance_id):
        tr = importlib.import_module("coscreen.team_terminal_registry")
        tsl = importlib.import_module("custom_backend.team_source_lifecycle")
        uid = cls.users[name]["user_id"]
        library = "%032x" % uid
        token = tsl.make_source_identity(
            task_dir=task_dir, source_path=cls.user_db[label][name],
            source_user_id=uid, source_library_id=library)
        writer = _borrowed_writer(collab_path)
        try:
            tr.register_source_on(
                writer, task_instance_id=instance_id, source_user_id=uid,
                source_library_id=library, source_path_identity=token,
                registration_id=f"reg-pkgc-{label}-{name}")
            writer.commit()
        finally:
            _close_quietly(writer)

    @classmethod
    def _vote_setup(cls, name, key, stage):
        route = "decisions" if stage == "screen" else "stage2/decisions"
        resp = cls.client.post(
            f"/api/tasks/{cls.task_id['main']}/{route}",
            json={"zotero_key": key, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=cls._auth_setup(name))
        assert resp.status_code == 200, (name, key, stage, resp.text)

    @classmethod
    def _build_solo(cls):
        tasks = importlib.import_module("coscreen.tasks")
        wf = importlib.import_module("coscreen.review_workflow")
        coding = importlib.import_module("coscreen.coding")
        cls.task_id["solo"] = cls.client.post(
            "/api/tasks", json={"name": "pkgc-solo-" + uuid.uuid4().hex[:8]},
            headers=cls._auth_setup("pksolo")).json()["task_id"]
        info = tasks.find_task(cls.task_id["solo"], cls.main_mod.DATA_DIR)
        db = info.dir / (
            tasks.slugify_screener(cls.users["pksolo"]["username"]) + ".db")
        wf.migrate(db)
        stamp = "2026-11-20T00:00:00+00:00"
        with closing(sqlite3.connect(str(db))) as conn:
            conn.execute(_INSERT_ARTICLE, (KEY_SOLO, "PkgC solo", 0))
            # solo coding 队列口径：stage2 include（无 stage2 时 screen
            # include）才入队——直接播种两级 include 决策。
            for table in ("decisions", "stage2_decisions"):
                conn.execute(
                    f"INSERT OR REPLACE INTO {table}"
                    "(zotero_key,decision,exclusion_reason,notes,tags,"
                    "screened_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (KEY_SOLO, "include", "", "", "", stamp, stamp))
            conn.commit()
        cls.user_db["solo"] = {"pksolo": db}
        cls.solo_dim_id = coding.add_dimension(
            db, "PkgC solo 维度", "text", [])

    # ------------------------------------------------------------------
    # c1: pre-join-sealed key must be counted in the coding progress queue
    # ------------------------------------------------------------------
    def test_c1_prejoin_sealed_key_in_coding_progress_queue(self):
        seals = self._stage1_seals_in_library("pkb")
        self.assertEqual(set(seals), set(_TEAM_KEYS), seals)
        resp = self.client.post(
            f"/api/tasks/{self.task_id['main']}/coding/values",
            json={"zotero_key": KEY_PRE, "dim_id": self.dim_id,
                  "value": "pkc-1"},
            headers=self._auth("pkd"))
        self.assertEqual(resp.status_code, 200, resp.text)
        progress = resp.json()["progress"]
        # 修复前：全域数票把两键全部误出队（D 无 stage1/2 票）→ total=0；
        # 修复后：seal 终局 → 两 pre-join-sealed 键均计入队列（完成看
        # seal），KEY_PRE 已填值 → coded=1（未完成看票/看值）。
        self.assertEqual(progress["total"], 2, progress)
        self.assertEqual(progress["per_dimension"], {str(self.dim_id): 1},
                         progress)
        self.assertEqual(progress["coded"], 1, progress)
        self.assertEqual(progress["remaining"], 1, progress)
        self.assertEqual(progress["remaining"],
                         progress["total"] - progress["coded"], progress)

    # ------------------------------------------------------------------
    # c2: archived (retired-holder) seal key still counted; roster shrinks
    # ------------------------------------------------------------------
    def test_c2_archived_seal_key_in_coding_progress_queue(self):
        with self._server_mode():
            retire = self.client.delete(
                f"/api/tasks/{self.task_id['main']}/collaboration/roles/"
                f"{self.users['pkb']['user_id']}/coder",
                headers=self._auth("pkowner"))
        self.assertEqual(retire.status_code, 200, retire.text)
        body = retire.json()
        self.assertTrue(body.get("file_cleanup", {}).get("member_db_renamed"),
                        body)
        # B 库已改名移走：KEY_PRE/KEY_ARC 的 seal 只剩 collab 归档域。
        self.assertFalse(self.user_db["main"]["pkb"].is_file())
        resp = self.client.post(
            f"/api/tasks/{self.task_id['main']}/coding/values",
            json={"zotero_key": KEY_ARC, "dim_id": self.dim_id,
                  "value": "pkc-2"},
            headers=self._auth("pkd"))
        self.assertEqual(resp.status_code, 200, resp.text)
        progress = resp.json()["progress"]
        # 两键均经 seal 计入（KEY_PRE holder 库 seal 已随退休归档 collab；
        # KEY_ARC 同源）——归档键不再从进度队列消失。
        self.assertEqual(progress["total"], 2, progress)
        self.assertEqual(progress["per_dimension"], {str(self.dim_id): 2},
                         progress)
        self.assertEqual(progress["coded"], 2, progress)
        self.assertEqual(progress["remaining"], 0, progress)
        self.assertEqual(progress["remaining"],
                         progress["total"] - progress["coded"], progress)

    # ------------------------------------------------------------------
    # c3: solo anchor — the solo branch stays the path-based progress
    # ------------------------------------------------------------------
    def test_c3_solo_progress_anchor_unchanged(self):
        db = self.user_db["solo"]["pksolo"]
        resp = self.client.post(
            f"/api/tasks/{self.task_id['solo']}/coding/values",
            json={"zotero_key": KEY_SOLO, "dim_id": self.solo_dim_id,
                  "value": "pkc-solo"},
            headers=self._auth("pksolo"))
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        coding = importlib.import_module("coscreen.coding")
        expected = coding.coding_progress(db, owner_db=db)
        # 响应经 JSON 序列化（per_dimension 键变文本）——归一后必须与
        # 既有 path 口径逐字相等（solo 分支零回归锚）。
        self.assertEqual(body["progress"], json.loads(json.dumps(expected)))
        self.assertEqual(body["progress"]["total"], 1, body["progress"])

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _stage1_seals_in_library(self, name):
        path = self.user_db["main"][name]
        with closing(sqlite3.connect(str(path))) as conn:
            rows = [json.loads(r[0]) for r in conn.execute(
                "SELECT payload_json FROM team_terminal_seals")]
        return {payload["item_key"] for payload in rows
                if payload.get("kind") in ("unanimous", "adjudicated")
                and payload.get("stage") == "stage1"}


if __name__ == "__main__":
    unittest.main()
