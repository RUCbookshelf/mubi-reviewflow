# -*- coding: utf-8 -*-
"""包 E 红钉：盲裁决**进行中**载荷剥离筛选员理由（2026-11-20 用户裁定
“盲到底”，传输层加固）。

语义（存储零变化——只钉“进行中阶段发给浏览器的内容”）：

1. 裁决进行中（条目 pending、未揭示）：发给仲裁员的载荷**不携带**双方
   筛选员的排除理由（结构性缺席：连 ``reason`` 键都不发）；
2. 揭示/已决定后：reveal 载荷照常带各筛选员决定 + 理由（既有断言不动，
   本文件复验不回退）；
3. 提交仲裁决定（仲裁员自己的理由）不受影响。

覆盖面：

- generic B1 会话（2.1.5）：open/decide 响应全文不含未决条目的筛选员理由
  文本；``_reveal_payload`` 对未决行剥离（单元级直命中 pending 分支），
  已决行逐字段原样；
- P19 team 仲裁：preview 全文零理由文本；未决键 reveal 409 且零理由；
  decide/GET reveal 已决键理由照常；``_team_screeners_payload`` 未决分支
  剥离 + int/str 两种票面键域兼容（单元级）。

夹具说明：与既有两套夹具（test_2_1_5_arbitration_session /
test_216_team_arbitration_session）**同一配方**但独立建用户/建队（用户名
前缀 ``pkgE``），不子类化——避免重跑继承用例与固定用户名冲突。

运行：pytest build/tests/test_216_pkg_e_blind_payload.py
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
import unittest
import uuid
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 触发既有模块级环境准备（与 pytest 直接收集同效）并复用其模块工件。
import test_2_1_5_arbitration_session as generic_fixture
import test_216_team_arbitration_session as team_fixture
from test_216_team_arbitration_session import (
    _INSERT_ARTICLE,
    _LATCH,
    _UPSERT_LIBRARY,
    KEY_A,
    KEY_B,
    KEY_C,
    KEY_D,
    _borrowed_writer,
    _close_quietly,
)

from coscreen import arbitration_session as session_mod
from custom_backend.arbitration_session_api import _team_screeners_payload

#: generic 夹具里排除票写死的筛选员理由文本。
GENERIC_REASON = "人群不符"
#: 本文件为 P19 团队仲裁现造的、带真实理由文本的分歧票。
TEAM_REASON = "人群不符P19"


# ---------------------------------------------------------------------------
# 1. generic B1 会话（2.1.5）——队列/decide 载荷 + `_reveal_payload` 单元级
# ---------------------------------------------------------------------------

class BlindPendingPayloadTests(unittest.TestCase):
    """进行中载荷零理由；已决 reveal 理由照常（不回退先判后示）。"""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        cls.client = TestClient(generic_fixture.app)
        cls.client.__enter__()
        cls.users = []
        for i in range(3):
            user = generic_fixture.auth_mod.create_user(
                f"pkgEgen{i}@example.test", "PkgE-Blind-2026")
            cls.users.append({
                "user_id": user["id"],
                "username": user["username"],
                "slug": generic_fixture.tasks_mod.slugify_screener(user["username"]),
                "token": generic_fixture.auth_mod.create_access_token(
                    user["id"], user["username"]),
            })

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def auth(self, i=0):
        return {"Authorization": "Bearer " + self.users[i]["token"]}

    def setUp(self):
        self.task = self.client.post(
            "/api/tasks", json={"name": "pkgE-" + self._testMethodName},
            headers=self.auth()).json()["task_id"]
        self.info = generic_fixture.tasks_mod.find_task(
            self.task, generic_fixture.DATA_DIR)
        for index, role in ((1, "reconciler"), (2, "coder")):
            response = self.client.post(
                f"/api/tasks/{self.task}/collaboration/invite",
                json={"username": self.users[index]["username"], "role": role},
                headers=self.auth())
            self.assertEqual(response.status_code, 201, response.text)
        # 2.1.6：邀请只授角色，业务角色需显式配置（否则仲裁守卫 409
        # team.role.unconfigured——与 P19 团队夹具同法补 set_business_role）。
        collaboration = generic_fixture.collaboration_mod
        store = collaboration.shared_store_path(self.info.dir)
        owner_uid = self.users[0]["user_id"]
        for index, business in ((0, "reconciler"), (1, "reconciler"), (2, "coder")):
            collaboration.set_business_role(
                store, actor_user_id=owner_uid,
                user_id=self.users[index]["user_id"],
                business_role=business, task_id=self.task)
        self.ctx = generic_fixture.app.state.review_workflow.context(
            self.task, self.users[0])
        self.owner_db = Path(self.ctx.owner_db)

    # ---- 夹具（与 2.1.5 夹具同法） ------------------------------------

    def _screener_db(self, index: int) -> Path:
        return self.info.dir / f"{self.users[index]['slug']}.db"

    def _seed(self, index: int, decisions: dict[str, str]) -> Path:
        path = self._screener_db(index)
        if not path.exists():
            path.write_bytes(generic_fixture._TEMPLATE_DB_BYTES)
        db_mod = generic_fixture.db_mod
        db_mod.upsert_articles(path, generic_fixture.ARTICLES, [])
        for key, decision in decisions.items():
            reason = GENERIC_REASON if decision == "exclude" else ""
            db_mod.save_decision(path, key, decision, exclusion_reason=reason, notes="")
        return path

    def _seed_three(self) -> None:
        self._seed(0, {"K1": "include", "K2": "exclude", "K3": "include"})
        self._seed(1, {"K1": "include", "K2": "include", "K3": "include"})
        self._seed(2, {"K1": "exclude", "K2": "exclude", "K3": "include"})
        with closing(sqlite3.connect(self.owner_db)) as conn, conn:
            row = conn.execute("SELECT stages_json FROM workflow_state").fetchone()
            stages = json.loads(row[0])
            for value in stages.values():
                value["mode"] = "paused"
            conn.execute("UPDATE workflow_state SET stages_json=?",
                         (json.dumps(stages),))

    # ---- HTTP 助手 -----------------------------------------------------

    def _session_url(self) -> str:
        return f"/api/tasks/{self.task}/screening-arbitrations/session"

    def _open(self):
        return self.client.get(self._session_url(), params={"stage": "stage1"},
                               headers=self.auth())

    def _decide(self, key: str, decision):
        resp = self.client.post(
            self._session_url() + "/item",
            json={"stage": "stage1", "zotero_key": key, "decision": decision,
                  "reason": None},
            headers=self.auth())
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp

    def _reveal(self, key: str):
        return self.client.get(
            self._session_url() + "/reveal",
            params={"stage": "stage1", "zotero_key": key}, headers=self.auth())

    # ---- 用例 -----------------------------------------------------------

    def test_open_and_decide_payloads_strip_pending_screener_reasons(self):
        self._seed_three()
        opened = self._open().json()
        # open（队列 + 汇总）全文零理由文本：未决条目不携带筛选员理由。
        self.assertNotIn(GENERIC_REASON, json.dumps(opened, ensure_ascii=False))

        # decide K1（include）：K1 已决 -> reveal 照常带各筛选员决定+理由；
        # 同一响应的其余部分（K2 仍 pending）不新增任何理由文本。
        decided = self._decide("K1", "include").json()
        self.assertEqual(decided["item"], {"zotero_key": "K1", "status": "decided"})
        reveal = decided["reveal"]
        self.assertEqual(reveal["zotero_key"], "K1")
        screeners = {entry["name"]: entry for entry in reveal["screeners"]}
        self.assertEqual(screeners[self.users[2]["slug"]]["decision"], "exclude")
        self.assertEqual(screeners[self.users[2]["slug"]]["reason"], GENERIC_REASON)

        # K2 仍 pending：reveal 通道 409，正文零理由文本。
        pending = self._reveal("K2")
        self.assertEqual(pending.status_code, 409, pending.text)
        self.assertEqual(
            pending.json()["detail"]["error_code"],
            "arbitration_session_reveal_not_ready")
        self.assertNotIn(GENERIC_REASON, pending.text)

    def test_reveal_payload_builder_strips_undecided_rows(self):
        """单元级直命中 `_reveal_payload` 的未决分支（HTTP 层有 409 先行，
        此处钉住载荷构造器本身：未决行结构性缺席 reason，已决行原样）。"""
        votes_json = json.dumps([
            {"name": "s1", "decision": "include", "reason": "R1"},
            {"name": "s2", "decision": "exclude", "reason": GENERIC_REASON},
        ], ensure_ascii=False)
        pending = {
            "zotero_key": "K1", "decision": None, "reason": "",
            "decided_at": None, "revealed": 0, "votes_json": votes_json,
        }
        payload = session_mod._reveal_payload(pending)
        self.assertEqual(len(payload["screeners"]), 2)
        for entry in payload["screeners"]:
            self.assertIn("name", entry)
            self.assertIn("decision", entry)
            self.assertNotIn("reason", entry, "未决行必须结构性缺席 reason 键")
        self.assertNotIn(GENERIC_REASON, json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("R1", json.dumps(payload, ensure_ascii=False))

        decided = {
            "zotero_key": "K1", "decision": "include", "reason": "",
            "decided_at": "t", "revealed": 1, "votes_json": votes_json,
        }
        payload = session_mod._reveal_payload(decided)
        reasons = {entry["name"]: entry["reason"] for entry in payload["screeners"]}
        self.assertEqual(reasons, {"s1": "R1", "s2": GENERIC_REASON})


# ---------------------------------------------------------------------------
# 2. P19 team 仲裁——preview/decide/GET reveal 载荷 + helper 单元级
# ---------------------------------------------------------------------------

class TeamBlindPendingPayloadTests(unittest.TestCase):
    """团队仲裁进行中载荷零理由；已决 reveal 理由照常。"""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.main_mod = main_mod
        cls.users = {}
        for name in ("pkgEowner", "pkgEscreenerA", "pkgEscreenerB",
                     "pkgEscreenerC"):
            user = auth_mod.create_user(name, "PkgE-Team-2026")
            cls.users[name] = {
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(user["id"], user["username"])}
        cls._team_task()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    @classmethod
    def _auth(cls, name):
        return {"Authorization": "Bearer " + cls.users[name]["token"]}

    @classmethod
    def _team_task(cls):
        """真实建队（P16/P18 夹具配方，同 test_216_team_arbitration_session）
        + 四键 stage1 冲突种子（B 票带真实理由文本——专测理由传输）。"""
        collab = importlib.import_module("coscreen.task_collaboration")
        cdb = importlib.import_module("coscreen.db")
        wf = importlib.import_module("coscreen.review_workflow")
        tasks = importlib.import_module("coscreen.tasks")
        tf = importlib.import_module("coscreen.team_terminal_facts")
        tr = importlib.import_module("coscreen.team_terminal_registry")
        receipts = importlib.import_module("coscreen.team_flow_receipts")
        tsl = importlib.import_module("custom_backend.team_source_lifecycle")

        owner = cls.users["pkgEowner"]
        owner_uid = owner["user_id"]
        cls.task_id = cls.client.post(
            "/api/tasks", json={"name": "pkgE-team-arb-" + uuid.uuid4().hex[:8]},
            headers=cls._auth("pkgEowner")).json()["task_id"]
        cls.info = tasks.find_task(cls.task_id, cls.main_mod.DATA_DIR)
        task_dir = cls.info.dir
        cls.collab_path = collab.shared_store_path(task_dir)
        if not cls.collab_path.is_file():
            collab.initialize_task_store(
                task_dir, task_id=cls.task_id, owner_user_id=owner_uid,
                owner_username=owner["username"])
        collab.set_business_role(
            cls.collab_path, actor_user_id=owner_uid, user_id=owner_uid,
            business_role="reconciler", task_id=cls.task_id)
        slug = tasks.slugify_screener
        cls.user_db = {name: task_dir / f"{slug(u['username'])}.db"
                       for name, u in cls.users.items()
                       if name != "pkgEscreenerC"}
        for name in ("pkgEscreenerA", "pkgEscreenerB"):
            uid = cls.users[name]["user_id"]
            collab.grant_role(cls.collab_path, actor_user_id=owner_uid,
                              user_id=uid, role="coder", task_id=cls.task_id)
            collab.set_business_role(
                cls.collab_path, actor_user_id=owner_uid, user_id=uid,
                business_role="coder", task_id=cls.task_id)
        articles = [(k, f"PkgE {k}", i)
                    for i, k in enumerate((KEY_A, KEY_B, KEY_C, KEY_D))]
        for name, path in cls.user_db.items():
            cdb.init_db(path)
            wf.migrate(path)
            tf.prepare_team_terminal_facts(path)
            receipts.prepare_team_flow_receipts(path)
            conn = sqlite3.connect(str(path), timeout=15)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                uid = cls.users[name]["user_id"]
                if name != "pkgEowner":
                    conn.execute(_UPSERT_LIBRARY, ("%032x" % uid,))
                conn.executemany(_INSERT_ARTICLE, articles)
                if name == "pkgEowner":
                    conn.execute(_LATCH)
                conn.commit()
            finally:
                conn.close()
        tr.prepare_team_terminal_registry(cls.collab_path)
        with closing(sqlite3.connect(str(cls.collab_path))) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT task_instance_id FROM main.team_task_protocol"
                " LIMIT 1").fetchone()
        assert row is not None, (
            "create_task must have registered the team protocol row (P18)")
        cls.instance_id = row["task_instance_id"]
        tf.prepare_team_terminal_facts(cls.collab_path)
        receipts.prepare_team_flow_receipts(cls.collab_path)
        for name in ("pkgEscreenerA", "pkgEscreenerB"):
            uid = cls.users[name]["user_id"]
            library = "%032x" % uid
            token = tsl.make_source_identity(
                task_dir=task_dir, source_path=cls.user_db[name],
                source_user_id=uid, source_library_id=library)
            writer = _borrowed_writer(cls.collab_path)
            try:
                tr.register_source_on(
                    writer, task_instance_id=cls.instance_id, source_user_id=uid,
                    source_library_id=library, source_path_identity=token,
                    registration_id=f"reg-pkgE-{name}")
                writer.commit()
            finally:
                _close_quietly(writer)
        # stage1 冲突种子：A include / B exclude+理由（四键全部冲突，
        # 且每键都带真实理由文本——传输层敏感值全覆盖）。
        for key in (KEY_A, KEY_B, KEY_C, KEY_D):
            cls._vote("pkgEscreenerA", key, "include")
            cls._vote("pkgEscreenerB", key, "exclude", reason=TEAM_REASON)

    @classmethod
    def _vote(cls, name, key, decision, reason=""):
        resp = cls.client.post(
            f"/api/tasks/{cls.task_id}/decisions",
            json={"zotero_key": key, "decision": decision,
                  "reason": reason, "notes": "", "tags": None},
            headers=cls._auth(name))
        assert resp.status_code == 200, resp.text

    # ---- HTTP 助手 -----------------------------------------------------

    def _preview(self, stage="stage1"):
        resp = self.client.get(
            f"/api/tasks/{self.task_id}/team-arbitration/session",
            params={"stage": stage}, headers=self._auth("pkgEowner"))
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _decide(self, key, version, decision="include", reason=""):
        resp = self.client.post(
            f"/api/tasks/{self.task_id}/team-arbitration/session/item",
            json={"stage": "stage1", "zotero_key": key,
                  "decision": decision, "reason": reason,
                  "expected_key_version": version},
            headers=self._auth("pkgEowner"))
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    # ---- 用例 -----------------------------------------------------------

    def test_pending_payloads_carry_no_screener_reason(self):
        # 进行中：preview（含 key_version 派生数据）全文零理由文本。
        preview = self._preview()
        self.assertNotIn(TEAM_REASON, json.dumps(preview, ensure_ascii=False))

        # 未决键 reveal：结构化 409，正文零理由文本、无 screeners。
        not_ready = self.client.get(
            f"/api/tasks/{self.task_id}/team-arbitration/session/reveal",
            params={"stage": "stage1", "zotero_key": KEY_B,
                    "expected_key_version": preview["keys"][KEY_B]["key_version"]},
            headers=self._auth("pkgEowner"))
        self.assertEqual(not_ready.status_code, 409, not_ready.text)
        self.assertNotIn(TEAM_REASON, not_ready.text)
        self.assertNotIn("screeners", not_ready.json())

        # decide KEY_B 后（已决）：decide 响应的 reveal 照常带 B 的理由
        # （仲裁员自己的 decision/reason 提交不受影响）。
        decide = self._decide(KEY_B, preview["keys"][KEY_B]["key_version"])
        screeners = {entry["name"]: entry for entry in decide["reveal"]["screeners"]}
        self.assertEqual(len(screeners), 2)
        self.assertEqual(screeners["pkgEscreenerB"]["decision"], "exclude")
        self.assertEqual(screeners["pkgEscreenerB"]["reason"], TEAM_REASON)

        # GET reveal（已决键 + 匹配版本）：理由照常在。
        reveal = self.client.get(
            f"/api/tasks/{self.task_id}/team-arbitration/session/reveal",
            params={"stage": "stage1", "zotero_key": KEY_B,
                    "expected_key_version": decide["key_version"],
                    "session_id": decide["session_id"]},
            headers=self._auth("pkgEowner"))
        self.assertEqual(reveal.status_code, 200, reveal.text)
        body = reveal.json()
        screeners = {entry["name"]: entry for entry in body["screeners"]}
        self.assertEqual(screeners["pkgEscreenerB"]["reason"], TEAM_REASON)

        # 其余键仍未决：preview 依旧全文零理由文本。
        again = self._preview()
        self.assertNotIn(TEAM_REASON, json.dumps(again, ensure_ascii=False))

    def test_screeners_payload_helper_blind_until_decided(self):
        """单元级：helper 未决分支剥离（reason 键缺席）、已决分支原样，
        且兼容 int(uid)（票面快照）与 str(uid)（ready opinions）键域。"""
        required = {7: "alice", 3: "bob"}
        votes_int = {7: {"decision": "include", "exclusion_reason": "RI7"},
                     3: {"decision": "exclude", "exclusion_reason": "RI3"}}
        pending = _team_screeners_payload(required, votes_int, item_decided=False)
        self.assertEqual([entry["name"] for entry in pending], ["bob", "alice"])
        for entry in pending:
            self.assertNotIn("reason", entry, "未决条目必须结构性缺席 reason 键")
            self.assertTrue(entry["decision"])  # 票向照常携带：盲于理由，不盲于票
        decided = _team_screeners_payload(required, votes_int, item_decided=True)
        reasons = {entry["name"]: entry["reason"] for entry in decided}
        self.assertEqual(reasons, {"alice": "RI7", "bob": "RI3"})

        votes_str = {"7": {"decision": "include", "exclusion_reason": "RS7"}}
        mixed = _team_screeners_payload(required, votes_str, item_decided=True)
        reasons = {entry["name"]: entry["reason"] for entry in mixed}
        self.assertEqual(reasons, {"alice": "RS7", "bob": ""})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
