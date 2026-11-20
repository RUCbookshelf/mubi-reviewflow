"""P19 team arbitration session tests (C12 author side; 2.1.6 contracts C10).

Six P19-named tests over the **real** app routes: real HTTP via TestClient,
real team building (transplanted from test_216_member_write_routes.py=1993c128:
real roster + create_task auto protocol registration + P04 ledger + F1 facts +
F2 registry with true source tokens — zero mocks on the request path), real
P10 window / single-target collab commit, and the real P05 attested append.
The only wrapping is a **passthrough recorder** around the reservation
manager's single commit point (``ReservedWriteConnections._commit_target``)
so the P19 anchor

    assert committed_targets == ['collab']

is observed on the genuine finish path (five artifacts, one collab commit),
and around ``_server_mode`` via the live ``REVIEWFLOW_MODE`` env toggle for
the desktop-reachability test.  Product imports are deferred to setUpClass
(loadable under ``python -I -B``).
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
from unittest import mock

_REQ_ID = "7f19a1b2-3c4d-4e5f-8a90-bcdef01234ff"  # finish retry anchor: UUID
KEY_A = "KEY1"   # item A: finishes while B waits
KEY_B = "KEY2"   # item B: waits; decided late; B-vote-change must not stale it
KEY_C = "KEY3"   # spare: A-change/roster stale + atomic finish
KEY_D = "KEY4"   # B-vote-change carrier (never finished)
_UPSERT_LIBRARY = (
    "INSERT INTO team_vote_revision_meta (singleton, format_version,"
    " library_id, seeded_stage1, seeded_stage2, advances)"
    " VALUES (1, 2, ?, 0, 0, 0) ON CONFLICT(singleton) DO UPDATE SET"
    " library_id=excluded.library_id")
_INSERT_ARTICLE = (
    "INSERT INTO articles (zotero_key, title, is_duplicate_of, import_order)"
    " VALUES (?, ?, NULL, ?)")
_LATCH = "UPDATE workflow_state SET team_latched=1 WHERE singleton=1"


def _borrowed_writer(path):
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _close_quietly(conn):
    try:
        conn.close()
    except Exception:
        pass


class TeamArbitrationSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.main_mod = main_mod
        cls.users = {}
        for name in ("p19owner", "p19screenerA", "p19screenerB", "p19screenerC"):
            user = auth_mod.create_user(name, "P19-Team-Arb-2026")
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
        """真实建队（P16/P18 夹具配方）+ 四键 stage1 冲突种子。"""
        collab = importlib.import_module("coscreen.task_collaboration")
        cdb = importlib.import_module("coscreen.db")
        wf = importlib.import_module("coscreen.review_workflow")
        tasks = importlib.import_module("coscreen.tasks")
        tf = importlib.import_module("coscreen.team_terminal_facts")
        tr = importlib.import_module("coscreen.team_terminal_registry")
        receipts = importlib.import_module("coscreen.team_flow_receipts")
        tsl = importlib.import_module("custom_backend.team_source_lifecycle")

        owner = cls.users["p19owner"]
        owner_uid = owner["user_id"]
        cls.task_id = cls.client.post(
            "/api/tasks", json={"name": "p19-team-arb-" + uuid.uuid4().hex[:8]},
            headers=cls._auth("p19owner")).json()["task_id"]
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
                       if name != "p19screenerC"}
        for name in ("p19screenerA", "p19screenerB"):
            uid = cls.users[name]["user_id"]
            collab.grant_role(cls.collab_path, actor_user_id=owner_uid,
                              user_id=uid, role="coder", task_id=cls.task_id)
            collab.set_business_role(
                cls.collab_path, actor_user_id=owner_uid, user_id=uid,
                business_role="coder", task_id=cls.task_id)
        articles = [(k, f"P19 {k}", i)
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
                if name != "p19owner":
                    conn.execute(_UPSERT_LIBRARY, ("%032x" % uid,))
                conn.executemany(_INSERT_ARTICLE, articles)
                if name == "p19owner":
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
        for name in ("p19screenerA", "p19screenerB"):
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
                    registration_id=f"reg-p19-{name}")
                writer.commit()
            finally:
                _close_quietly(writer)
        # stage1 冲突种子：A include / B exclude（四键全部冲突）。
        for key in (KEY_A, KEY_B, KEY_C, KEY_D):
            cls._vote("p19screenerA", key, "include")
            cls._vote("p19screenerB", key, "exclude")

    @classmethod
    def _vote(cls, name, key, decision):
        resp = cls.client.post(
            f"/api/tasks/{cls.task_id}/decisions",
            json={"zotero_key": key, "decision": decision,
                  "reason": "", "notes": "", "tags": None},
            headers=cls._auth(name))
        assert resp.status_code == 200, resp.text

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _preview(self, stage="stage1"):
        resp = self.client.get(
            f"/api/tasks/{self.task_id}/team-arbitration/session",
            params={"stage": stage}, headers=self._auth("p19owner"))
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _decide(self, key, version, decision="include", reason="",
                expect=200, name="p19owner"):
        resp = self.client.post(
            f"/api/tasks/{self.task_id}/team-arbitration/session/item",
            json={"stage": "stage1", "zotero_key": key,
                  "decision": decision, "reason": reason,
                  "expected_key_version": version},
            headers=self._auth(name))
        self.assertEqual(resp.status_code, expect, resp.text)
        return resp

    def _finish(self, keys, versions, request_id=None, expect=200,
                name="p19owner"):
        headers = self._auth(name)
        if request_id:
            headers = {**headers, "X-Request-Id": request_id}
        resp = self.client.post(
            f"/api/tasks/{self.task_id}/team-arbitration/session/finish",
            json={"stage": "stage1", "item_keys": keys,
                  "expected_key_versions": versions},
            headers=headers)
        self.assertEqual(resp.status_code, expect, resp.text)
        return resp

    def _collab_row(self, sql, args=()):
        with closing(sqlite3.connect(str(self.collab_path))) as conn:
            return conn.execute(sql, args).fetchone()

    def _collab_count(self, table):
        return self._collab_row(f"SELECT COUNT(*) FROM {table}")[0]  # noqa: S608

    def _commit_recorder(self):
        """passthrough recorder：记录唯一提交点的目标标签（不改行为）。"""
        reservation = importlib.import_module("coscreen.team_write_reservation")
        real = reservation.ReservedWriteConnections._commit_target
        seen: dict[str, list] = {"targets": []}

        def spy(self):
            # 目标身份以保留管理器的 collab 连接判定（标签集实现细节不参与锚）。
            seen["targets"].append(
                "collab" if self._target is self._collab_conn else "<other>")
            return real(self)

        patcher = mock.patch.object(
            reservation.ReservedWriteConnections, "_commit_target", spy)
        return seen, patcher

    # ------------------------------------------------------------------
    # A 未完成输入变化 / 名册变化 -> 未完成会话 stale（409 refresh）
    # ------------------------------------------------------------------
    def test_a_change_or_roster_change_stales_unfinished_session(self):
        preview = self._preview()
        v1 = preview["keys"][KEY_C]["key_version"]
        self._decide(KEY_C, v1)
        # A 票重投（同值 include）：revision 前进 -> KEY_C 版本变化。
        self._vote("p19screenerA", KEY_C, "include")
        stale = self._finish([KEY_C], {KEY_C: v1}, expect=409)
        self.assertEqual(
            stale.json()["detail"]["error_code"],
            "arbitration_refresh_required", stale.text)
        refreshed = self._preview()
        v2 = refreshed["keys"][KEY_C]["key_version"]
        self.assertNotEqual(v1, v2)
        self._decide(KEY_C, v2)  # 刷新后可继续（准备中结论可重下）
        # 名册变化：新增必需筛选人（无票）-> 旧版本不能再 finish。
        collab = importlib.import_module("coscreen.task_collaboration")
        owner_uid = self.users["p19owner"]["user_id"]
        uid_c = self.users["p19screenerC"]["user_id"]
        collab.grant_role(self.collab_path, actor_user_id=owner_uid,
                          user_id=uid_c, role="coder", task_id=self.task_id)
        collab.set_business_role(
            self.collab_path, actor_user_id=owner_uid, user_id=uid_c,
            business_role="coder", task_id=self.task_id)
        try:
            entry = self._preview()["keys"][KEY_C]
            v3 = entry.get("key_version", "")  # 名册扩员后可能不再就绪
            roster_stale = self._finish([KEY_C], {KEY_C: v3}, expect=409)
            self.assertEqual(roster_stale.status_code, 409, roster_stale.text)
        finally:
            collab.revoke_role(self.collab_path, actor_user_id=owner_uid,
                               user_id=uid_c, role="coder",
                               task_id=self.task_id)

    # ------------------------------------------------------------------
    # A 键 finish 完成、B 键仍等待（部分结束）
    # ------------------------------------------------------------------
    def test_a_finishes_while_b_waits(self):
        preview = self._preview()
        va = preview["keys"][KEY_A]["key_version"]
        decided = self._decide(KEY_A, va).json()
        done = self._finish([KEY_A], {KEY_A: decided["key_version"]}).json()
        self.assertEqual(done["finished_keys"], [KEY_A])
        self.assertEqual(done["pending_keys"], [KEY_B, KEY_C, KEY_D])
        after = self._preview()
        self.assertEqual(after["finished_keys"], [KEY_A])
        self.assertIn(KEY_B, after["ready_keys"])  # 其余键仍可准备
        self.assertEqual(after["status"], "partial")
        row = self._collab_row(
            "SELECT decision FROM team_arbitration_decision"
            " WHERE session_id=? AND zotero_key=?",
            (done["session_id"], KEY_A))
        self.assertEqual(row[0], "include")
        # F1 已封（D1 正确语义）：finished 键不被重列 ready——preview 标
        # already_final（缺 BEGIN 的 fail-open 曾把它重新列为 ready）。
        self.assertFalse(after["keys"][KEY_A]["ready"])
        self.assertEqual(after["keys"][KEY_A]["reason"], "already_final")
        self.assertNotIn(KEY_A, after["ready_keys"])
        # 且不可再裁决（已有 F1 永不作废）：任意版本 -> 409 not_ready。
        again = self._decide(KEY_A, "0" * 64, expect=409).json()
        self.assertEqual(again["detail"]["error_code"],
                         "team_arbitration_key_not_ready")

    # ------------------------------------------------------------------
    # 五工件同一次 collab commit + 客户端重试幂等
    # ------------------------------------------------------------------
    def test_atomic_five_artifacts_and_retry(self):
        # 自洽步骤：先 decide（隔离复跑也稳定），再 finish（五工件）。
        preview = self._preview()
        vc0 = preview["keys"][KEY_C]["key_version"]
        # D2：decide 以固定 X-Request-Id 发起，紧接同 id 同 body 重放——
        # 回执幂等命中且与首提交逐字段同形（含 reason/decided_at/reveal/
        # session；revision 不因重放再推进）。
        replay_headers = {**self._auth("p19owner"), "X-Request-Id": _REQ_ID}
        first_decide = self.client.post(
            f"/api/tasks/{self.task_id}/team-arbitration/session/item",
            json={"stage": "stage1", "zotero_key": KEY_C,
                  "decision": "include", "reason": "",
                  "expected_key_version": vc0},
            headers=replay_headers)
        self.assertEqual(first_decide.status_code, 200, first_decide.text)
        replay = self.client.post(
            f"/api/tasks/{self.task_id}/team-arbitration/session/item",
            json={"stage": "stage1", "zotero_key": KEY_C,
                  "decision": "include", "reason": "",
                  "expected_key_version": vc0},
            headers=replay_headers)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json(), first_decide.json())  # 重放同形
        decided_c = replay.json()
        vc = decided_c["key_version"]  # 已决键：decide 后版本（revision 已推进）
        seen, patcher = self._commit_recorder()
        patcher.start()
        try:
            done = self._finish([KEY_C], {KEY_C: vc},
                                request_id=_REQ_ID).json()
        finally:
            patcher.stop()
        record_id = done["record_id"]
        attestation_id = done["attestation_id"]
        # 锚：唯一提交点只对 collab 目标 commit 一次（不允许 generic 先
        # 提交后再另开 session 事务）。
        committed_targets = seen["targets"]
        self.assertEqual(committed_targets, ["collab"])
        # 五工件事实核验（同一 collab 库）。
        self.assertIsNotNone(self._collab_row(
            "SELECT id FROM screening_arbitrations WHERE id=?", (record_id,)))
        self.assertIsNotNone(self._collab_row(
            "SELECT id FROM screening_arbitration_attestations WHERE id=?",
            (attestation_id,)))
        seal = self._collab_row(
            "SELECT kind, origin, payload_json FROM team_terminal_seals"
            " WHERE task_instance_id=? AND stage=? AND item_key=?",
            (self.instance_id, "stage1", KEY_C))
        self.assertEqual((seal[0], seal[1]), ("adjudicated", "collab"))
        self.assertEqual(
            json.loads(seal[2])["trigger"], "arbitration")
        session = self._collab_row(
            "SELECT status, record_id, attestation_id FROM"
            " team_arbitration_session WHERE session_id=?",
            (done["session_id"],))
        self.assertEqual(session[0], "partial")
        self.assertEqual((session[1], session[2]), (record_id, attestation_id))
        receipts_before = self._collab_count("team_flow_receipts")
        seals_before = self._collab_count("team_terminal_seals")
        # 同 request_id 同内容重试：C09 幂等重放——回原结果（200），
        # 不产生第二个 receipt/seal/写（空事务仍走唯一提交点一次）。
        seen2, patcher2 = self._commit_recorder()
        patcher2.start()
        try:
            retry = self._finish([KEY_C], {KEY_C: vc}, request_id=_REQ_ID)
        finally:
            patcher2.stop()
        retry_body = retry.json()
        self.assertEqual(retry_body["record_id"], done["record_id"])
        self.assertEqual(retry_body, done)  # D2：重放与首提交逐字段同形
        self.assertEqual(retry_body["attestation_id"], done["attestation_id"])
        self.assertEqual(retry_body["pending_keys"], done["pending_keys"])
        self.assertEqual(retry_body["finished_keys"], done["finished_keys"])
        self.assertEqual(self._collab_count("team_flow_receipts"),
                         receipts_before)
        self.assertEqual(self._collab_count("team_terminal_seals"),
                         seals_before)
        self.assertEqual(seen2["targets"], ["collab"])  # 无第二笔业务提交

    # ------------------------------------------------------------------
    # B 键票变不废 A（逐键 proof/版本，绝不用整批 merge SHA）
    # ------------------------------------------------------------------
    def test_b_change_does_not_stale_a(self):
        preview = self._preview()
        # D1 正确语义：KEY_A 已 finished -> 不再列 ready（already_final），
        # F1 冻结不随 B 票变化（版本只对 ready 键发布）。
        self.assertFalse(preview["keys"][KEY_A]["ready"])
        self.assertEqual(preview["keys"][KEY_A]["reason"], "already_final")
        vb = preview["keys"][KEY_B]["key_version"]
        decided_b = self._decide(KEY_B, vb).json()
        vb_decided = decided_b["key_version"]  # revision 0->1 后的当前版本
        # B 票重投在**另一个键** KEY_D（同值 exclude，revision 前进）。
        self._vote("p19screenerB", KEY_D, "exclude")
        after = self._preview()
        self.assertEqual(after["keys"][KEY_B]["key_version"], vb_decided)
        self.assertFalse(after["keys"][KEY_A]["ready"])  # F1 冻结不受 B 票影响
        self.assertNotEqual(after["keys"][KEY_D]["key_version"],
                            preview["keys"][KEY_D]["key_version"])
        # KEY_B 用旧版本仍可 finish（B 键变化不 stale A 键）。
        done = self._finish([KEY_B], {KEY_B: decided_b["key_version"]}).json()
        self.assertEqual(done["finished_keys"], [KEY_B])
        # stage2 seal 分支真实可达（D1 回归钉）：KEY_C 的 stage1 已有持久
        # adjudicated seal（atomic 落），stage1 当前票仍不一致——上游"当前
        # 一致"为 False，就绪只能经 read_seal_on 持久 seal 分支成立。成员
        # 投出 stage2 冲突票（stage1 已完成 -> fulltext 链开闸）。
        for name, decision in (("p19screenerA", "include"),
                               ("p19screenerB", "exclude")):
            seeded = self.client.post(
                f"/api/tasks/{self.task_id}/stage2/decisions",
                json={"zotero_key": KEY_C, "decision": decision,
                      "reason": "", "notes": "", "tags": None},
                headers=self._auth(name))
            self.assertEqual(seeded.status_code, 200, seeded.text)
        stage2_preview = self.client.get(
            f"/api/tasks/{self.task_id}/team-arbitration/session",
            params={"stage": "stage2"}, headers=self._auth("p19owner"))
        self.assertEqual(stage2_preview.status_code, 200, stage2_preview.text)
        self.assertTrue(stage2_preview.json()["keys"][KEY_C]["ready"],
                        stage2_preview.text)  # seal 分支可达＝真 ready

    # ------------------------------------------------------------------
    # desktop team 可达 + legacy CSV/generic 旧路径行为不变
    # ------------------------------------------------------------------
    def test_desktop_team_and_legacy_csv(self):
        team_resp = self.client.get(
            f"/api/tasks/{self.task_id}/team-arbitration/session",
            params={"stage": "stage1"}, headers=self._auth("p19owner"))
        self.assertEqual(team_resp.status_code, 200, team_resp.text)
        previous = os.environ.get("REVIEWFLOW_MODE")
        os.environ["REVIEWFLOW_MODE"] = "desktop"
        try:
            # legacy 会话端点维持 server-only（desktop 404）——旧路径不删不改。
            legacy = self.client.get(
                f"/api/tasks/{self.task_id}/screening-arbitrations/session",
                params={"stage": "stage1"}, headers=self._auth("p19owner"))
            self.assertEqual(legacy.status_code, 404, legacy.text)
            # team 分支 desktop 同样可达（不做 server_mode 门）。
            desktop_team = self.client.get(
                f"/api/tasks/{self.task_id}/team-arbitration/session",
                params={"stage": "stage1"}, headers=self._auth("p19owner"))
            self.assertEqual(desktop_team.status_code, 200, desktop_team.text)
            self.assertTrue(desktop_team.json()["ready_keys"])
        finally:
            if previous is None:
                os.environ.pop("REVIEWFLOW_MODE", None)
            else:
                os.environ["REVIEWFLOW_MODE"] = previous

    # ------------------------------------------------------------------
    # reveal 隐私：先判后示；只含本键人票；未裁决零泄露
    # ------------------------------------------------------------------
    def test_reveal_privacy_all_actions(self):
        preview = self._preview()
        vd = preview["keys"][KEY_D]["key_version"]
        # 未裁决：任何 reveal 都不返回人票。
        not_ready = self.client.get(
            f"/api/tasks/{self.task_id}/team-arbitration/session/reveal",
            params={"stage": "stage1", "zotero_key": KEY_D,
                    "expected_key_version": vd},
            headers=self._auth("p19owner"))
        self.assertEqual(not_ready.status_code, 409, not_ready.text)
        # G7：无已存会话的 reveal 语义＝结构化 reveal_not_ready（零人票泄露）。
        self.assertEqual(
            not_ready.json()["detail"]["error_code"],
            "arbitration_session_reveal_not_ready", not_ready.text)
        self.assertNotIn("screeners", not_ready.json())
        decide = self._decide(KEY_D, vd).json()
        session_id = decide["session_id"]
        # decide 响应的 reveal 只含 KEY_D 本键人票。
        self.assertEqual(decide["reveal"]["zotero_key"], KEY_D)
        self.assertEqual(len(decide["reveal"]["screeners"]), 2)
        # GET reveal：已裁决键返回本键结论+人票；版本必须匹配。
        reveal = self.client.get(
            f"/api/tasks/{self.task_id}/team-arbitration/session/reveal",
            params={"stage": "stage1", "zotero_key": KEY_D,
                    "expected_key_version": decide["key_version"],
                    "session_id": session_id},
            headers=self._auth("p19owner"))
        self.assertEqual(reveal.status_code, 200, reveal.text)
        body = reveal.json()
        self.assertEqual(body["zotero_key"], KEY_D)
        self.assertEqual(len(body["screeners"]), 2)
        # 缺版本 -> 409 refresh_required（不豁免 reveal）。
        missing = self.client.get(
            f"/api/tasks/{self.task_id}/team-arbitration/session/reveal",
            params={"stage": "stage1", "zotero_key": KEY_D,
                    "expected_key_version": ""},
            headers=self._auth("p19owner"))
        self.assertEqual(missing.status_code, 409, missing.text)
        # 队列 preview（GET session）不含任何人票/筛选员姓名。
        queue = self._preview()
        dumped = json.dumps(queue)
        self.assertNotIn("screeners", dumped)
        for name in ("p19screenerA", "p19screenerB"):
            self.assertNotIn(name, dumped)
        # G5：finish 未传 keys 兼容分支——服务端取「本会话已决定且仍就绪」
        # 的键（KEY_D 刚 decide 且仍为冲突就绪；版本不豁免由仍就绪判定承载）。
        compat = self.client.post(
            f"/api/tasks/{self.task_id}/team-arbitration/session/finish",
            json={"stage": "stage1", "item_keys": None,
                  "expected_key_versions": None},
            headers=self._auth("p19owner"))
        self.assertEqual(compat.status_code, 200, compat.text)
        self.assertEqual(compat.json()["finished_keys"], [KEY_D])
        # 全部键已完成后再传 None：空集拒绝（409 finish_empty）。
        empty = self.client.post(
            f"/api/tasks/{self.task_id}/team-arbitration/session/finish",
            json={"stage": "stage1", "item_keys": None,
                  "expected_key_versions": None},
            headers=self._auth("p19owner"))
        self.assertEqual(empty.status_code, 409, empty.text)
        self.assertEqual(
            empty.json()["detail"]["error_code"],
            "team_arbitration_finish_empty", empty.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
